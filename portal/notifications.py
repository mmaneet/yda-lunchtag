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


def _build_message(to: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = os.environ.get("MAIL_FROM", os.environ.get("SMTP_USERNAME", ""))
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


def _send_via_gmail(msg: EmailMessage) -> str:
    """Send through the Gmail API as the account that granted the refresh
    token (gmail.send scope) — this is how mail goes out from a personal
    Yale address. See scripts/get_gmail_token.py for the one-time consent."""
    import base64

    import requests

    token_resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": os.environ["GMAIL_CLIENT_ID"],
            "client_secret": os.environ["GMAIL_CLIENT_SECRET"],
            "refresh_token": os.environ["GMAIL_REFRESH_TOKEN"],
            "grant_type": "refresh_token",
        },
        timeout=15,
    )
    token_resp.raise_for_status()
    access_token = token_resp.json()["access_token"]
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    send_resp = requests.post(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"raw": raw},
        timeout=20,
    )
    send_resp.raise_for_status()
    return "sent"


def send_email(to: str, subject: str, body: str) -> str:
    """Send one email; returns 'sent' or 'dry-run' (nothing configured).
    Transport preference: Gmail API (personal-account sending) > SMTP."""
    if all(os.environ.get(k) for k in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN")):
        return _send_via_gmail(_build_message(to, subject, body))
    host = os.environ.get("SMTP_HOST")
    if not host:
        log.info("[dry-run] email to %s: %s", to, subject)
        return "dry-run"
    msg = _build_message(to, subject, body)
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


def _partner_info_sentences(profile) -> str:
    """The 'quick info' sentences for one partner, per the board's template.
    Sentences whose facts are missing (or major is Undeclared) are dropped."""
    if profile is None:
        return ""
    sentences = []
    if profile.class_year and profile.residential_college:
        sentences.append(
            f"They're in the class of {profile.class_year}, "
            f"in {profile.residential_college} College."
        )
    elif profile.class_year:
        sentences.append(f"They're in the class of {profile.class_year}.")
    elif profile.residential_college:
        sentences.append(f"They're in {profile.residential_college} College.")
    major = (profile.major or "").strip()
    if major and major.lower() != "undeclared":
        studying = f"They're studying {major}"
        second = (profile.second_major or "").strip()
        if second and second.lower() != "undeclared":
            studying += f" and {second}"
        sentences.append(studying + ".")
    if profile.fun_facts:
        sentences.append(f"Some fun facts about them include: {profile.fun_facts}.")
    if profile.dining_hall:
        sentences.append(f"Their favorite dining hall is: {profile.dining_hall}.")
    return " ".join(sentences)


def _pairing_email_body(first_name: str, partners: list[tuple[str, object]]) -> str:
    """partners: [(display name, profile-or-None), ...] — usually one, but a
    trio round gives two."""
    partner_names = " and ".join(name for name, _ in partners)
    plural = "pairings" if len(partners) > 1 else "pairing"
    verb = "are" if len(partners) > 1 else "is"
    intro = f"Your Lunchtag {plural} for this week {verb}: {partner_names}."
    for name, profile in partners:
        info = _partner_info_sentences(profile)
        if info and len(partners) > 1:
            intro += f" About {name}: {info}"
        elif info:
            intro += f" Some quick info about them: {info}"
    return "\n".join(
        [
            f"Hey {first_name}!",
            "",
            intro + " Have fun! Reach out to board if you have any questions.",
            "",
            f"As usual, this email was sent automatically from {base_url()}. "
            "Check the lunchtag page on the website to opt-out!",
            "",
            "Have a great lunchtag,",
            "Maneet Mehta",
            "YDA Social Chair",
        ]
    )


def send_pairing_notifications(repo, session_id: int, db_path=None) -> dict:
    """Email every opted-in, linked member their partner(s) + profile info,
    and post a summary to Slack. Returns counts for the admin flash."""
    from portal.auth import store

    pairing_session = repo.get_session(session_id)
    names = repo.display_names()
    emails = store.emails_by_player(db_path=db_path)
    sent = dry = skipped = opted_out = 0
    pair_lines = []
    for group in pairing_session.groups:
        pair_lines.append(" & ".join(names.get(pid, f"#{pid}") for pid in group.member_ids))
        for pid in group.member_ids:
            to = emails.get(pid)
            if not to:
                skipped += 1
                continue
            profile = repo.get_profile(pid)
            if profile is not None and not profile.email_notifications:
                opted_out += 1
                continue
            partners = [
                (names.get(q, f"#{q}"), repo.get_profile(q))
                for q in group.member_ids
                if q != pid
            ]
            first_name = names.get(pid, "there").split()[0]
            body = _pairing_email_body(first_name, partners)
            result = send_email(to, "Your LunchTag pairing this week", body)
            if result == "sent":
                sent += 1
            else:
                dry += 1
    slack_text = (
        ":sandwich: *This week's LunchTag pairings are out!*\n"
        + "\n".join(f"• {line}" for line in pair_lines)
        + f"\nDetails are in your email — profiles and history at {base_url()}/lunchtag"
        + "\n_Notifications can be disabled on the website's LunchTag page._"
    )
    slack = post_slack(slack_text)
    return {
        "emails_sent": sent,
        "emails_dry_run": dry,
        "members_without_email": skipped,
        "opted_out": opted_out,
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
