"""Outbound notifications: pairing emails to members, a Slack announcement,
and the weekly admin reminder.

Dry-run by default: with no SMTP_* / SLACK_WEBHOOK_URL env vars set, sends are
logged and counted but nothing leaves the machine. On top of that, the master
switch is the app_settings key "notifications_enabled" (config-as-data,
default off) — flip it with `python -m portal.cli notifications on`.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

log = logging.getLogger(__name__)

NOTIFICATIONS_ENABLED_KEY = "notifications_enabled"


def base_url() -> str:
    return os.environ.get("PORTAL_BASE_URL", "https://web-production-f7a671.up.railway.app").rstrip("/")


def notifications_enabled(db_path=None) -> bool:
    from portal.auth import store

    return store.get_setting(NOTIFICATIONS_ENABLED_KEY, db_path=db_path) == "1"


def set_notifications_enabled(enabled: bool, db_path=None) -> None:
    from portal.auth import store

    store.set_setting(NOTIFICATIONS_ENABLED_KEY, "1" if enabled else "0", db_path=db_path)


# --- transports -------------------------------------------------------------


def send_email(to: str, subject: str, body: str) -> str:
    """Send one email; returns 'sent' or 'dry-run' (no SMTP configured)."""
    host = os.environ.get("SMTP_HOST")
    if not host:
        log.info("[dry-run] email to %s: %s", to, subject)
        return "dry-run"
    msg = EmailMessage()
    msg["From"] = os.environ.get("MAIL_FROM", os.environ.get("SMTP_USERNAME", ""))
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    port = int(os.environ.get("SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=20) as smtp:
        smtp.starttls()
        username = os.environ.get("SMTP_USERNAME")
        if username:
            smtp.login(username, os.environ.get("SMTP_PASSWORD", ""))
        smtp.send_message(msg)
    return "sent"


def post_slack(text: str) -> str:
    """Post to the configured Slack incoming webhook; 'sent' or 'dry-run'."""
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        log.info("[dry-run] slack post: %s", text[:120])
        return "dry-run"
    import requests

    resp = requests.post(url, json={"text": text}, timeout=10)
    resp.raise_for_status()
    return "sent"


# --- message builders + senders ---------------------------------------------


def _profile_lines(profile) -> list[str]:
    if profile is None:
        return []
    lines = []
    if profile.class_year:
        lines.append(f"  Class of {profile.class_year}")
    if profile.residential_college:
        lines.append(f"  {profile.residential_college}")
    if profile.dining_hall:
        lines.append(f"  Favorite dining hall: {profile.dining_hall}")
    if profile.fun_facts:
        lines.append(f"  Fun facts: {profile.fun_facts}")
    return lines


def send_pairing_notifications(repo, session_id: int, db_path=None) -> dict:
    """Email every linked member their partner(s) + profile info, and post a
    summary to Slack. Returns counts for the admin-facing flash message."""
    from portal.auth import store

    pairing_session = repo.get_session(session_id)
    names = repo.display_names()
    emails = store.emails_by_player(db_path=db_path)
    sent = dry = skipped = 0
    pair_lines = []
    for group in pairing_session.groups:
        pair_lines.append(" & ".join(names.get(pid, f"#{pid}") for pid in group.member_ids))
        for pid in group.member_ids:
            to = emails.get(pid)
            if not to:
                skipped += 1
                continue
            partners = [q for q in group.member_ids if q != pid]
            body = [f"Hi {names.get(pid, 'there')},", "", "Your LunchTag pairing for this week:"]
            for q in partners:
                body.append("")
                body.append(f"{names.get(q, f'#{q}')}")
                body.extend(_profile_lines(repo.get_profile(q)))
            body += [
                "",
                "Reach out and set up a lunch together this week!",
                f"See your pairing history: {base_url()}/lunchtag",
                "",
                "— YDA LunchTag",
            ]
            result = send_email(to, "Your LunchTag pairing this week", "\n".join(body))
            if result == "sent":
                sent += 1
            else:
                dry += 1
    slack_text = (
        ":sandwich: *This week's LunchTag pairings are out!*\n"
        + "\n".join(f"• {line}" for line in pair_lines)
        + f"\nDetails are in your email — profiles and history at {base_url()}/lunchtag"
    )
    slack = post_slack(slack_text)
    return {
        "emails_sent": sent,
        "emails_dry_run": dry,
        "members_without_email": skipped,
        "slack": slack,
    }


def send_admin_reminder(db_path=None) -> dict:
    """Sunday-evening nudge to every admin to generate and push pairings."""
    from portal.auth import store

    admins = [email for email, role in store.list_roles(db_path=db_path) if role == "admin"]
    body = "\n".join(
        [
            "It's Sunday — time to generate and push this week's LunchTag pairings.",
            "",
            f"Generate: {base_url()}/lunchtag/admin/generate",
            "",
            "— YDA LunchTag",
        ]
    )
    results = [send_email(a, "LunchTag: push this week's pairings", body) for a in admins]
    return {
        "admins": len(admins),
        "sent": results.count("sent"),
        "dry_run": results.count("dry-run"),
    }
