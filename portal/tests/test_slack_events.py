from __future__ import annotations

import hashlib
import hmac
import json
import time

from portal import slack
from portal.app import create_app
from portal.auth import store
from portal.features.slack_events import routes as events
from portal.storage.repo import Repository
from portal.tests.conftest import onboarded_member

SECRET = "test-signing-secret"


def _signed_headers(body: bytes, secret=SECRET, ts=None):
    ts = str(int(ts if ts is not None else time.time()))
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig,
            "Content-Type": "application/json"}


def _round(db_path):
    """Two confirmed-round players with Slack IDs; returns (repo-closed ids)."""
    repo = Repository(db_path)
    a = repo.add_player("Ava Lin")
    b = repo.add_player("Ben Ortiz")
    c = repo.add_player("Cara Diaz")  # not in this round
    sid = repo.create_draft(1, 0.0, [[a.id, b.id]])
    repo.confirm_draft(sid, 0.0)
    repo.close()
    store.set_slack_user(a.id, "U_AVA", None, db_path=db_path)
    store.set_slack_user(b.id, "U_BEN", None, db_path=db_path)
    store.set_slack_user(c.id, "U_CARA", None, db_path=db_path)
    return a.id, b.id, c.id, sid


def _photo_event(user, channel="C_LUNCH", text=""):
    return {"type": "message", "subtype": "file_share", "user": user, "channel": channel,
            "ts": "1.0", "text": text, "files": [{"mimetype": "image/jpeg"}]}


# --- endpoint security ---------------------------------------------------------


def test_events_endpoint_rejects_bad_signature(auth_env, db_path, monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SECRET)
    client = create_app(db_path).test_client()
    body = json.dumps({"type": "url_verification", "challenge": "abc"}).encode()
    assert client.post("/slack/events", data=body, content_type="application/json").status_code == 401
    bad = _signed_headers(body, secret="wrong")
    assert client.post("/slack/events", data=body, headers=bad).status_code == 401
    stale = _signed_headers(body, ts=time.time() - 3600)
    assert client.post("/slack/events", data=body, headers=stale).status_code == 401


def test_events_endpoint_bypasses_login_and_answers_challenge(auth_env, db_path, monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SECRET)
    client = create_app(db_path).test_client()  # no session at all
    body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
    resp = client.post("/slack/events", data=body, headers=_signed_headers(body))
    assert resp.status_code == 200
    assert resp.get_json() == {"challenge": "abc123"}


def test_events_endpoint_skips_slack_retries(auth_env, db_path, monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SECRET)
    called = []
    monkeypatch.setattr(events, "handle_event", lambda e, db_path=None: called.append(e))
    client = create_app(db_path).test_client()
    body = json.dumps({"type": "event_callback", "event": {"type": "message"}}).encode()
    headers = {**_signed_headers(body), "X-Slack-Retry-Num": "1"}
    assert client.post("/slack/events", data=body, headers=headers).status_code == 200
    assert called == []
    client.post("/slack/events", data=body, headers=_signed_headers(body))
    assert len(called) == 1


# --- completion tally ----------------------------------------------------------


def test_photo_in_channel_credits_whole_group_once(auth_env, db_path, monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    a, b, c, sid = _round(db_path)
    replies = []
    monkeypatch.setattr(slack, "reply_in_thread", lambda ch, ts, text: (replies.append(text), "sent")[1])
    app = create_app(db_path)
    with app.app_context():
        assert events.handle_event(_photo_event("U_AVA"), db_path=db_path) == "credited:2"
        assert events.handle_event(_photo_event("U_BEN"), db_path=db_path) == "duplicate"
        assert events.handle_event(_photo_event("U_AVA"), db_path=db_path) == "duplicate"
    assert len(replies) == 1
    assert "Ava Lin & Ben Ortiz" in replies[0]
    assert "/leaderboard" in replies[0]
    repo = Repository(db_path)
    board = {p.name: n for p, n in repo.leaderboard()}
    repo.close()
    assert board == {"Ava Lin": 1, "Ben Ortiz": 1, "Cara Diaz": 0}


def test_text_phrase_counts_too(auth_env, db_path, monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.setattr(slack, "reply_in_thread", lambda *a: "sent")
    a, b, c, sid = _round(db_path)
    app = create_app(db_path)
    with app.app_context():
        ev = {"type": "message", "user": "U_BEN", "channel": "C", "ts": "2.0", "text": "Had LunchTag!! 🎉"}
        assert events.handle_event(ev, db_path=db_path) == "credited:2"


def test_ignores_non_completions_and_unknowns(auth_env, db_path, monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    a, b, c, sid = _round(db_path)
    app = create_app(db_path)
    with app.app_context():
        chat = {"type": "message", "user": "U_AVA", "channel": "C", "ts": "3.0", "text": "anyone free thursday?"}
        assert events.handle_event(chat, db_path=db_path) == "ignored:not-a-completion"
        assert events.handle_event(_photo_event("U_STRANGER"), db_path=db_path) == "ignored:unknown-member"
        assert events.handle_event(_photo_event("U_CARA"), db_path=db_path) == "ignored:not-in-round"
        bot = {**_photo_event("U_AVA"), "bot_id": "B1"}
        assert events.handle_event(bot, db_path=db_path) == "ignored:bot"
    repo = Repository(db_path)
    assert all(n == 0 for _, n in repo.leaderboard())
    repo.close()


def test_only_the_lunchtag_channel_counts(auth_env, db_path, monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(slack, "channel_name", lambda cid: {"C_LUNCH": "lunchtag", "C_GEN": "general"}[cid])
    monkeypatch.setattr(slack, "reply_in_thread", lambda *a: "sent")
    a, b, c, sid = _round(db_path)
    app = create_app(db_path)
    with app.app_context():
        assert events.handle_event(_photo_event("U_AVA", channel="C_GEN"), db_path=db_path) == "ignored:other-channel"
        assert events.handle_event(_photo_event("U_AVA", channel="C_LUNCH"), db_path=db_path) == "credited:2"


# --- onboarding usher ----------------------------------------------------------


def test_dm_opt_in_gets_website_link(auth_env, db_path, monkeypatch):
    dms = []
    monkeypatch.setattr(slack, "send_dm_to_user", lambda uid, text: (dms.append((uid, text)), "sent")[1])
    app = create_app(db_path)
    with app.app_context():
        ev = {"type": "message", "channel_type": "im", "user": "U_NEW", "channel": "D1",
              "text": "hey, I'd like to opt into lunchtag"}
        assert events.handle_event(ev, db_path=db_path) == "usher"
        chat = {**ev, "text": "what time is practice"}
        assert events.handle_event(chat, db_path=db_path) == "ignored:dm"
    assert len(dms) == 1 and dms[0][0] == "U_NEW"
    assert "Join LunchTag" in dms[0][1]
    assert "http" in dms[0][1]


# --- leaderboard page ----------------------------------------------------------


def test_leaderboard_tab_ranks_and_highlights_me(auth_env, db_path):
    app = create_app(db_path)
    me = onboarded_member(app, "me@yale.edu", "Me Person")
    repo = Repository(db_path)
    ids = {p.name: p.id for p in repo.list_players()}
    star = repo.add_player("Star Player")
    other = repo.add_player("Other One")
    sid1 = repo.create_draft(1, 0.0, [[star.id, other.id]]); repo.confirm_draft(sid1, 0.0)
    sid2 = repo.create_draft(2, 0.0, [[star.id, ids["Me Person"]]]); repo.confirm_draft(sid2, 0.0)
    repo.record_completions(sid1, [star.id, other.id], "slack")
    repo.record_completions(sid2, [star.id, ids["Me Person"]], "slack")
    repo.close()
    html = me.get("/leaderboard").get_data(as_text=True)
    assert html.index("Star Player") < html.index("Me Person")  # 2 lunches ranks above 1
    assert "🥇" in html and "(you)" in html
    assert "4 lunches logged" in html
    assert 'class="tab active"' in html or "Leaderboard" in html  # tab exists


# --- Slack identity matching -----------------------------------------------------


MEMBERS = [
    {"id": "U1", "name": "Faith", "display_name": "", "email": "faitheduncan@gmail.com"},
    {"id": "U2", "name": "Justin Kim", "display_name": "", "email": "jkim1@yale.edu"},
    {"id": "U3", "name": "Justin Kim", "display_name": "jk", "email": "jkim2@yale.edu"},
    {"id": "U4", "name": "Bo Xin Zhao", "display_name": "", "email": "boxinzhao25@gmail.com"},
    {"id": "U5", "name": "Ava L", "display_name": "ava", "email": "ava.lin@yale.edu"},
]


def test_match_member_loose_but_never_ambiguous():
    match = slack.match_member
    assert match(MEMBERS, "Faith Duncan")["id"] == "U1"              # first name only in Slack
    assert match(MEMBERS, "Bo Xin Zhao", "boxin.zhao@yale.edu")["id"] == "U4"  # full name
    assert match(MEMBERS, "Ava Lin", "ava.lin@yale.edu")["id"] == "U5"          # email beats name
    assert match(MEMBERS, "Justin Kim", "jkim2@yale.edu")["id"] == "U3"         # email disambiguates
    assert match(MEMBERS, "Justin Kim") is None                                  # two Justins -> no guess
    assert match(MEMBERS, "Nobody Here") is None


def test_onboarding_records_slack_identity(auth_env, db_path, monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(slack, "list_members", lambda: MEMBERS)
    app = create_app(db_path)
    onboarded_member(app, "faith.duncan@yale.edu", "Faith Duncan")
    pid = store.get_player_id_for_email("faith.duncan@yale.edu", db_path=db_path)
    assert store.slack_users_by_player(db_path=db_path) == {pid: "U1"}


def test_onboarding_survives_slack_outage(auth_env, db_path, monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    def boom():
        raise RuntimeError("slack down")
    monkeypatch.setattr(slack, "list_members", boom)
    app = create_app(db_path)
    client = onboarded_member(app, "new@yale.edu", "New Person")
    assert client.get("/profile").status_code == 200  # onboarded fine, just no Slack ID


# --- /quit ----------------------------------------------------------------------


def test_quit_dm_opts_member_out(auth_env, db_path, monkeypatch):
    dms = []
    monkeypatch.setattr(slack, "send_dm_to_user", lambda uid, text: (dms.append(text), "sent")[1])
    app = create_app(db_path)
    onboarded_member(app, "ava@yale.edu", "Ava Lin")
    pid = store.get_player_id_for_email("ava@yale.edu", db_path=db_path)
    store.set_slack_user(pid, "U_AVA", None, db_path=db_path)
    with app.app_context():
        ev = {"type": "message", "channel_type": "im", "user": "U_AVA", "channel": "D1", "text": "/quit"}
        assert events.handle_event(ev, db_path=db_path) == "quit"
        stranger = {**ev, "user": "U_WHO", "text": "please opt out"}
        assert events.handle_event(stranger, db_path=db_path) == "quit:unknown-member"
    repo = Repository(db_path)
    assert repo.get_profile(pid).lunchtag_opt_in is False
    repo.close()
    assert "opted out" in dms[0]
    assert "couldn't find" in dms[1].lower()


def test_quit_slash_command(auth_env, db_path, monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SECRET)
    monkeypatch.setattr(slack, "send_dm_to_user", lambda uid, text: "sent")
    app = create_app(db_path)
    onboarded_member(app, "ava@yale.edu", "Ava Lin")
    pid = store.get_player_id_for_email("ava@yale.edu", db_path=db_path)
    store.set_slack_user(pid, "U_AVA", None, db_path=db_path)
    client = app.test_client()
    body = b"command=%2Fquit&user_id=U_AVA"
    headers = {**_signed_headers(body), "Content-Type": "application/x-www-form-urlencoded"}
    resp = client.post("/slack/commands", data=body, headers=headers)
    assert resp.status_code == 200
    assert "opted out" in resp.get_json()["text"]
    repo = Repository(db_path)
    assert repo.get_profile(pid).lunchtag_opt_in is False
    repo.close()
    assert client.post("/slack/commands", data=body).status_code == 401  # unsigned


# --- channel backfill --------------------------------------------------------------


def test_backfill_credits_missed_messages_idempotently(auth_env, db_path, monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    a, b, c, sid = _round(db_path)
    monkeypatch.setattr(slack, "find_channel_id", lambda name: "C_LUNCH")
    history = [
        {"user": "U_AVA", "ts": "10.0", "text": "anyone around?"},                       # chatter
        {"user": "U_AVA", "ts": "11.0", "text": "", "files": [{"mimetype": "image/png"}]},  # photo
        {"user": "U_CARA", "ts": "12.0", "text": "had lunchtag"},                         # not in round
        {"user": "U_BOT", "ts": "13.0", "text": "had lunchtag", "bot_id": "B1"},          # bot
    ]
    monkeypatch.setattr(slack, "channel_history", lambda cid, oldest: history)
    replies = []
    monkeypatch.setattr(slack, "reply_in_thread", lambda *a: replies.append(a) or "sent")
    result = events.backfill_from_channel(db_path=db_path)
    assert result["status"] == "ok"
    assert result["scanned"] == 4 and result["qualifying"] == 2
    assert result["credited"] == 2 and result["ignored"] == 1
    assert replies == []  # backfill is quiet by default
    repo = Repository(db_path)
    assert {p.name: n for p, n in repo.leaderboard()} == {"Ava Lin": 1, "Ben Ortiz": 1, "Cara Diaz": 0}
    repo.close()
    again = events.backfill_from_channel(db_path=db_path)
    assert again["credited"] == 0 and again["duplicate"] == 1


def test_backfill_noop_without_slack_or_round(db_path, monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    assert events.backfill_from_channel(db_path=db_path) == {"status": "slack-disabled"}
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    assert events.backfill_from_channel(db_path=db_path) == {"status": "no-round"}


def test_leaderboard_sync_button_is_admin_only(auth_env, db_path, monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    app = create_app(db_path)
    member = onboarded_member(app, "m@yale.edu", "Member")
    assert "Sync from Slack" not in member.get("/leaderboard").get_data(as_text=True)
    assert member.post("/leaderboard/admin/sync").status_code == 302  # bounced by the gate
    store.set_role("boss@yale.edu", "admin", db_path=db_path)
    admin = app.test_client()
    with admin.session_transaction() as sess:
        sess["user_email"] = "boss@yale.edu"
    resp = admin.post("/leaderboard/admin/sync", follow_redirects=True)
    assert b"slack-disabled" in resp.data
