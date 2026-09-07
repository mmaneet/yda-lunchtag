from __future__ import annotations

from portal import notifications
from portal.app import create_app
from portal.auth import store
from portal.storage.repo import Repository


def _clean_env(monkeypatch):
    for var in ("SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD",
                "MAIL_FROM", "SLACK_WEBHOOK_URL", "SLACK_BOT_TOKEN",
                "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"):
        monkeypatch.delenv(var, raising=False)


def test_email_and_slack_dry_run_without_config(monkeypatch):
    _clean_env(monkeypatch)
    assert notifications.send_email("x@example.com", "s", "b") == "dry-run"
    assert notifications.post_slack("hello") == "dry-run"


def test_toggle_defaults_off(db_path):
    assert notifications.notifications_enabled(db_path=db_path) is False
    notifications.set_notifications_enabled(True, db_path=db_path)
    assert notifications.notifications_enabled(db_path=db_path) is True


def test_pairing_notifications_content_and_counts(db_path, monkeypatch):
    _clean_env(monkeypatch)
    repo = Repository(db_path)
    a = repo.add_player("Ava Lin")
    b = repo.add_player("Ben Ortiz")
    c = repo.add_player("Cara")  # no linked email -> skipped
    repo.upsert_profile(a.id, display_name="Ava Lin")
    repo.upsert_profile(
        b.id, display_name="Ben Ortiz", class_year="2028", residential_college="Morse",
        major="History", second_major="Economics",
        dining_hall="Commons", fun_facts="Collects stamps",
    )
    store.link_player_email("ava@yale.edu", a.id, db_path=db_path)
    store.link_player_email("ben@yale.edu", b.id, db_path=db_path)
    sid = repo.create_draft(1, 0.0, [[a.id, b.id], [c.id]])
    repo.confirm_draft(sid, 0.0)

    sent_emails = []
    slack_posts = []
    monkeypatch.setattr(notifications, "send_email",
                        lambda to, subject, body: (sent_emails.append((to, subject, body)), "sent")[1])
    monkeypatch.setattr(notifications, "post_slack",
                        lambda text: (slack_posts.append(text), "sent")[1])

    summary = notifications.send_pairing_notifications(repo, sid, db_path=db_path)
    assert summary["emails_sent"] == 2
    assert summary["members_without_email"] == 1
    assert summary["opted_out"] == 0
    assert summary["slack"] == "sent"
    ava_body = next(body for to, _, body in sent_emails if to == "ava@yale.edu")
    # The board's template, with every conditional sentence present.
    assert ava_body.startswith("Hey Ava!")
    assert "Your Lunchtag pairing for this week is: Ben Ortiz." in ava_body
    assert "They're in the class of 2028, in Morse College." in ava_body
    assert "They're studying History and Economics." in ava_body
    assert "Some fun facts about them include: Collects stamps." in ava_body
    assert "Their favorite dining hall is: Commons." in ava_body
    assert "Check the lunchtag page on the website to opt-out!" in ava_body
    assert "YDA Social Chair" in ava_body
    # Ben's email about Ava: she has no facts, so no info sentences at all.
    ben_body = next(body for to, _, body in sent_emails if to == "ben@yale.edu")
    assert "Your Lunchtag pairing for this week is: Ava Lin." in ben_body
    assert "Some quick info" not in ben_body
    assert "pairings are out" in slack_posts[0]
    assert "Check your Slack DMs, email, or the website" in slack_posts[0]
    assert "• Ava Lin & Ben Ortiz" in slack_posts[0]  # every group listed
    assert "• Cara" in slack_posts[0]
    assert "disabled on the website" in slack_posts[0]  # opt-out tag
    assert summary["dms_dry_run"] == 2  # no bot token -> DMs dry-run
    repo.close()


def test_undeclared_major_is_not_mentioned(db_path):
    repo = Repository(db_path)
    a = repo.add_player("Ava")
    profile = repo.upsert_profile(a.id, display_name="Ava", major="Undeclared")
    assert "studying" not in notifications._partner_info_sentences(profile).lower()
    profile = repo.upsert_profile(a.id, major="Physics", second_major="undeclared")
    assert "They're studying Physics." in notifications._partner_info_sentences(profile)
    repo.close()


def test_opted_out_member_gets_no_email(db_path, monkeypatch):
    _clean_env(monkeypatch)
    repo = Repository(db_path)
    a = repo.add_player("Ava")
    b = repo.add_player("Ben")
    repo.upsert_profile(a.id, display_name="Ava")
    repo.upsert_profile(b.id, display_name="Ben")
    repo.set_notification_prefs(b.id, email=False, slack=True)
    store.link_player_email("ava@yale.edu", a.id, db_path=db_path)
    store.link_player_email("ben@yale.edu", b.id, db_path=db_path)
    sid = repo.create_draft(1, 0.0, [[a.id, b.id]])
    repo.confirm_draft(sid, 0.0)
    sent = []
    monkeypatch.setattr(notifications, "send_email",
                        lambda to, subject, body: (sent.append(to), "sent")[1])
    monkeypatch.setattr(notifications, "post_slack", lambda text: "sent")
    summary = notifications.send_pairing_notifications(repo, sid, db_path=db_path)
    assert sent == ["ava@yale.edu"]
    assert summary["opted_out"] == 1
    repo.close()


def test_admin_reminder_targets_admins_only(db_path, monkeypatch):
    _clean_env(monkeypatch)
    store.set_role("boss@yale.edu", "admin", db_path=db_path)
    store.set_role("member@yale.edu", "member", db_path=db_path)
    sent = []
    monkeypatch.setattr(notifications, "send_email",
                        lambda to, subject, body: (sent.append(to), "sent")[1])
    result = notifications.send_admin_reminder(db_path=db_path)
    assert result["admins"] == 1
    assert sent == ["boss@yale.edu"]


def test_confirm_flow_respects_toggle(auth_env, db_path, monkeypatch):
    _clean_env(monkeypatch)
    app = create_app(db_path)
    store.set_role("boss@yale.edu", "admin", db_path=db_path)
    repo = Repository(db_path)
    a = repo.add_player("Ava")
    b = repo.add_player("Ben")
    repo.set_lunchtag_opt_in(a.id, True)
    repo.set_lunchtag_opt_in(b.id, True)
    repo.close()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_email"] = "boss@yale.edu"
    client.post("/lunchtag/admin/draft/generate")
    repo = Repository(db_path)
    draft = repo.get_draft()
    repo.close()
    # Toggle off: confirming mentions notifications are off.
    resp = client.post(f"/lunchtag/admin/draft/{draft.session_id}/confirm", follow_redirects=True)
    assert b"Notifications are off" in resp.data
    # Toggle on: a run is recorded with dry-run counts (nothing configured).
    notifications.set_notifications_enabled(True, db_path=db_path)
    client.post("/lunchtag/admin/draft/generate")
    repo = Repository(db_path)
    draft = repo.get_draft()
    repo.close()
    sid = draft.session_id
    resp = client.post(f"/lunchtag/admin/draft/{sid}/confirm", follow_redirects=True)
    assert b"sending in the background" in resp.data
    repo = Repository(db_path)
    runs = repo.notification_runs(sid)
    repo.close()
    assert len(runs) == 1 and runs[0]["status"] == "done"
    assert "dry-run" in runs[0]["summary"]
    # The round's page shows the run and offers a per-channel resend.
    html = client.get(f"/lunchtag/admin/history/{sid}").get_data(as_text=True)
    assert "dry-run" in html and 'name="dm"' in html
    client.post(f"/lunchtag/admin/history/{sid}/notify", data={"dm": "1"})
    repo = Repository(db_path)
    runs = repo.notification_runs(sid)
    repo.close()
    assert len(runs) == 2 and runs[0]["channels"] == "dm"
    assert "Emails: 0 sent" in runs[0]["summary"]  # email channel skipped
    assert "Channel post: skipped" in runs[0]["summary"]


# --- editable email template -------------------------------------------------


def test_custom_template_overrides_default(db_path, monkeypatch):
    _clean_env(monkeypatch)
    repo = Repository(db_path)
    a = repo.add_player("Ava")
    b = repo.add_player("Ben")
    repo.upsert_profile(a.id, display_name="Ava")
    repo.upsert_profile(b.id, display_name="Ben")
    store.link_player_email("ava@yale.edu", a.id, db_path=db_path)
    notifications.set_email_template(
        "Lunch time!", "Yo {{ first_name }}, you got {{ partner_names }}.", db_path=db_path
    )
    sid = repo.create_draft(1, 0.0, [[a.id, b.id]])
    repo.confirm_draft(sid, 0.0)
    sent = []
    monkeypatch.setattr(notifications, "send_email",
                        lambda to, subject, body: (sent.append((subject, body)), "sent")[1])
    monkeypatch.setattr(notifications, "post_slack", lambda text: "sent")
    notifications.send_pairing_notifications(repo, sid, db_path=db_path)
    assert sent[0] == ("Lunch time!", "Yo Ava, you got Ben.")
    repo.close()


def test_broken_template_falls_back_to_default(db_path):
    notifications.set_email_template("", "{{ undefined_thing() }}", db_path=db_path)
    body = notifications.render_pairing_email(
        "Ava", [dict(notifications.SAMPLE_PARTNER, info="")], db_path=db_path
    )
    assert body.startswith("Hey Ava!")  # default template used
    preview, error = notifications.preview_pairing_email(db_path=db_path)
    assert error is not None  # but the admin page surfaces the problem


def test_reset_restores_default(db_path):
    notifications.set_email_template("Custom", "custom body", db_path=db_path)
    assert notifications.get_email_template(db_path=db_path) == "custom body"
    notifications.set_email_template("", "", db_path=db_path)
    assert notifications.get_email_template(db_path=db_path) == notifications.DEFAULT_EMAIL_TEMPLATE
    assert notifications.get_email_subject(db_path=db_path) == notifications.DEFAULT_EMAIL_SUBJECT


def test_admin_templates_page_covers_every_message(auth_env, db_path):
    app = create_app(db_path)
    store.set_role("boss@yale.edu", "admin", db_path=db_path)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_email"] = "boss@yale.edu"
    resp = client.get("/lunchtag/admin/templates")
    assert resp.status_code == 200
    for key in notifications.TEMPLATES:
        assert f'id="{key}"'.encode() in resp.data
    assert b"Handsome Dan" in resp.data  # pairing previews use sample data
    assert b"Hey Ava!" in resp.data
    # Edit the Slack DM template; preview reflects it and sends use it.
    client.post("/lunchtag/admin/templates",
                data={"key": "pairing_dm", "value": "yo {{ first_name }} -> {{ partner_names }}"})
    resp = client.get("/lunchtag/admin/templates")
    assert b"yo Ava -&gt; Handsome Dan" in resp.data or b"yo Ava -> Handsome Dan" in resp.data
    assert notifications.render_pairing_dm("Zed", [dict(notifications.SAMPLE_PARTNER, info="")],
                                           db_path=db_path) == "yo Zed -> Handsome Dan"
    # Reset restores the default.
    client.post("/lunchtag/admin/templates", data={"key": "pairing_dm", "action": "reset"})
    assert notifications.get_template("pairing_dm", db_path=db_path) == notifications.DEFAULT_DM_TEMPLATE
    # Unknown keys are rejected; the old URL redirects here.
    client.post("/lunchtag/admin/templates", data={"key": "nope", "value": "x"})
    assert client.get("/lunchtag/admin/email-template").headers["Location"].endswith("/admin/templates")


def test_admin_reminder_uses_editable_template(db_path, monkeypatch):
    _clean_env(monkeypatch)
    store.set_role("boss@yale.edu", "admin", db_path=db_path)
    notifications.set_template("admin_reminder_subject", "Go go go", db_path=db_path)
    sent = []
    monkeypatch.setattr(notifications, "send_email",
                        lambda to, subject, body: (sent.append(subject), "sent")[1])
    notifications.send_admin_reminder(db_path=db_path)
    assert sent == ["Go go go"]



# --- Slack DMs ----------------------------------------------------------------


def test_slack_dm_respects_toggle_and_missing_users(db_path, monkeypatch):
    _clean_env(monkeypatch)
    repo = Repository(db_path)
    a = repo.add_player("Ava Lin")
    b = repo.add_player("Ben Ortiz")
    c = repo.add_player("Cara Diaz")
    for p in (a, b, c):
        repo.upsert_profile(p.id, display_name=p.name)
    repo.set_notification_prefs(b.id, email=True, slack=False)  # Ben: no DMs
    for p, e in ((a, "ava@yale.edu"), (b, "ben@yale.edu"), (c, "cara@yale.edu")):
        store.link_player_email(e, p.id, db_path=db_path)
    sid = repo.create_draft(1, 0.0, [[a.id, b.id, c.id]])
    repo.confirm_draft(sid, 0.0)

    dms = []
    def fake_dm(email, text, user_id=None):
        dms.append((email, text))
        return "no-slack-user" if email == "cara@yale.edu" else "sent"
    monkeypatch.setattr(notifications, "send_slack_dm", fake_dm)
    monkeypatch.setattr(notifications, "send_email", lambda *a: "sent")
    monkeypatch.setattr(notifications, "post_slack", lambda text: "sent")

    summary = notifications.send_pairing_notifications(repo, sid, db_path=db_path)
    assert summary["dms_sent"] == 1          # Ava
    assert summary["dms_opted_out"] == 1     # Ben
    assert summary["dms_no_slack_user"] == 1 # Cara
    ava_dm = next(t for e, t in dms if e == "ava@yale.edu")
    assert "Hey Ava!" in ava_dm
    assert "Ben Ortiz and Cara Diaz" in ava_dm  # trio: both partners named
    assert "turn off Slack notifications" in ava_dm
    repo.close()


def test_slack_client_dry_run_without_token(monkeypatch):
    _clean_env(monkeypatch)
    from portal import slack

    assert slack.enabled() is False
    assert slack.send_dm("x@yale.edu", "hi") == "dry-run"
    assert slack.post_channel("hi") == "dry-run"


def test_normalize_name():
    from portal.slack import normalize_name

    assert normalize_name("Zoë  O'Brien-Smith") == "zoe obriensmith"
    assert normalize_name("  Justin KIM ") == "justin kim"


def test_dm_uses_stored_slack_id_over_email_lookup(db_path, monkeypatch):
    _clean_env(monkeypatch)
    repo = Repository(db_path)
    a = repo.add_player("Ava")
    b = repo.add_player("Ben")
    repo.upsert_profile(a.id, display_name="Ava")
    repo.upsert_profile(b.id, display_name="Ben")
    store.link_player_email("ava@yale.edu", a.id, db_path=db_path)
    store.link_player_email("ben@yale.edu", b.id, db_path=db_path)
    store.set_slack_user(b.id, "U_BEN", "ben@gmail.com", db_path=db_path)  # Gmail on Slack
    sid = repo.create_draft(1, 0.0, [[a.id, b.id]])
    repo.confirm_draft(sid, 0.0)
    calls = []
    monkeypatch.setattr(notifications, "send_slack_dm",
                        lambda email, text, user_id=None: (calls.append((email, user_id)), "sent")[1])
    monkeypatch.setattr(notifications, "send_email", lambda *a: "sent")
    monkeypatch.setattr(notifications, "post_slack", lambda text: "sent")
    notifications.send_pairing_notifications(repo, sid, db_path=db_path)
    assert ("ava@yale.edu", None) in calls      # no stored ID -> email lookup
    assert ("ben@yale.edu", "U_BEN") in calls   # stored ID wins
    repo.close()


def test_slack_send_dm_prefers_user_id(monkeypatch):
    from portal import slack

    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    sent = []
    monkeypatch.setattr(slack, "send_dm_to_user", lambda uid, text: (sent.append(uid), "sent")[1])
    monkeypatch.setattr(slack, "lookup_user_id", lambda email: None)
    assert slack.send_dm("x@yale.edu", "hi", user_id="U42") == "sent"
    assert sent == ["U42"]
    assert slack.send_dm("x@yale.edu", "hi") == "no-slack-user"


def test_channel_selection_limits_sends(db_path, monkeypatch):
    _clean_env(monkeypatch)
    repo = Repository(db_path)
    a = repo.add_player("Ava")
    b = repo.add_player("Ben")
    for p in (a, b):
        repo.upsert_profile(p.id, display_name=p.name)
    store.link_player_email("ava@yale.edu", a.id, db_path=db_path)
    store.link_player_email("ben@yale.edu", b.id, db_path=db_path)
    sid = repo.create_draft(1, 0.0, [[a.id, b.id]])
    repo.confirm_draft(sid, 0.0)
    calls = {"email": 0, "dm": 0, "channel": 0}
    monkeypatch.setattr(notifications, "send_email", lambda *a: (calls.__setitem__("email", calls["email"] + 1), "sent")[1])
    monkeypatch.setattr(notifications, "send_slack_dm", lambda *a, **k: (calls.__setitem__("dm", calls["dm"] + 1), "sent")[1])
    monkeypatch.setattr(notifications, "post_slack", lambda t: (calls.__setitem__("channel", calls["channel"] + 1), "sent")[1])
    notifications.send_pairing_notifications(repo, sid, db_path=db_path, channels=("dm",))
    assert calls == {"email": 0, "dm": 2, "channel": 0}
    notifications.send_pairing_notifications(repo, sid, db_path=db_path, channels=("email", "channel"))
    assert calls == {"email": 2, "dm": 2, "channel": 1}
    repo.close()


def test_failed_email_does_not_abort_run(db_path, monkeypatch):
    _clean_env(monkeypatch)
    repo = Repository(db_path)
    a = repo.add_player("Ava")
    b = repo.add_player("Ben")
    store.link_player_email("ava@yale.edu", a.id, db_path=db_path)
    store.link_player_email("ben@yale.edu", b.id, db_path=db_path)
    sid = repo.create_draft(1, 0.0, [[a.id, b.id]])
    repo.confirm_draft(sid, 0.0)
    def flaky(to, subject, body):
        if to == "ava@yale.edu":
            raise RuntimeError("smtp hiccup")
        return "sent"
    monkeypatch.setattr(notifications, "send_email", flaky)
    monkeypatch.setattr(notifications, "post_slack", lambda t: "sent")
    counts = notifications.send_pairing_notifications(repo, sid, db_path=db_path, channels=("email",))
    assert counts["emails_sent"] == 1 and counts["emails_dry_run"] == 1
    repo.close()
