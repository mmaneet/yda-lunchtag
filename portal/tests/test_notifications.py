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
    assert "Ava" not in slack_posts[0]  # channel post names nobody
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
    # Toggle on: dry-run counts are reported (no SMTP configured).
    notifications.set_notifications_enabled(True, db_path=db_path)
    client.post("/lunchtag/admin/draft/generate")
    repo = Repository(db_path)
    draft = repo.get_draft()
    repo.close()
    resp = client.post(f"/lunchtag/admin/draft/{draft.session_id}/confirm", follow_redirects=True)
    assert b"dry-run" in resp.data


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


def test_admin_template_page_roundtrip(auth_env, db_path):
    app = create_app(db_path)
    store.set_role("boss@yale.edu", "admin", db_path=db_path)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_email"] = "boss@yale.edu"
    resp = client.get("/lunchtag/admin/email-template")
    assert resp.status_code == 200
    assert b"Handsome Dan" in resp.data  # preview rendered with sample data
    assert b"Hey Ava!" in resp.data
    client.post("/lunchtag/admin/email-template",
                data={"subject": "S", "template": "hi {{ first_name }}"})
    resp = client.get("/lunchtag/admin/email-template")
    assert b"hi Ava" in resp.data  # preview uses saved template
    client.post("/lunchtag/admin/email-template", data={"action": "reset"})
    resp = client.get("/lunchtag/admin/email-template")
    assert b"Hey Ava!" in resp.data


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
    def fake_dm(email, text):
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
