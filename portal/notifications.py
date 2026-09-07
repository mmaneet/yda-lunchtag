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
EMAIL_SUBJECT_KEY = "pairing_email_subject"
EMAIL_TEMPLATE_KEY = "pairing_email_template"

DEFAULT_EMAIL_SUBJECT = "Your LunchTag pairing this week"

# The board's template, as sandboxed Jinja. Admins can edit the stored copy
# on the admin Email-template page; an empty stored value means this default.
DEFAULT_EMAIL_TEMPLATE = """\
Hey {{ first_name }}!

Your Lunchtag {{ "pairings" if partners|length > 1 else "pairing" }} for this week \
{{ "are" if partners|length > 1 else "is" }}: {{ partner_names }}.\
{% for p in partners %}{% if p.info %}\
{% if partners|length > 1 %} About {{ p.name }}: {% else %} Some quick info about them: {% endif %}\
{{ p.info }}{% endif %}{% endfor %} Have fun! Reach out to board if you have any questions.

As usual, this email was sent automatically from {{ website_url }}. Check the lunchtag page on the website to opt-out!

Have a great lunchtag,
Maneet Mehta
YDA Social Chair"""


def base_url() -> str:
    return os.environ.get("PORTAL_BASE_URL", "https://lunchtag.yaledebate.org").rstrip("/")


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
    """Post the channel announcement. Prefers the bot (SLACK_BOT_TOKEN);
    falls back to an incoming webhook; 'dry-run' when neither is set."""
    from portal import slack

    if slack.enabled():
        return slack.post_channel(text)
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        log.info("[dry-run] slack post: %s", text[:120])
        return "dry-run"
    import requests

    resp = requests.post(url, json={"text": text}, timeout=10)
    resp.raise_for_status()
    return "sent"


def send_slack_dm(email: str, text: str) -> str:
    from portal import slack

    return slack.send_dm(email, text)


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


def get_email_subject(db_path=None) -> str:
    from portal.auth import store

    return store.get_setting(EMAIL_SUBJECT_KEY, db_path=db_path) or DEFAULT_EMAIL_SUBJECT


def get_email_template(db_path=None) -> str:
    from portal.auth import store

    return store.get_setting(EMAIL_TEMPLATE_KEY, db_path=db_path) or DEFAULT_EMAIL_TEMPLATE


def set_email_template(subject: str, template: str, db_path=None) -> None:
    """Store the admin-edited subject/template. Empty values mean 'use the
    built-in default'."""
    from portal.auth import store

    store.set_setting(EMAIL_SUBJECT_KEY, subject or "", db_path=db_path)
    store.set_setting(EMAIL_TEMPLATE_KEY, template or "", db_path=db_path)


def _jinja_env():
    # Sandboxed: admin-supplied templates cannot reach Python internals.
    from jinja2.sandbox import SandboxedEnvironment

    return SandboxedEnvironment(autoescape=False)


def _email_context(first_name: str, partners: list[dict]) -> dict:
    return {
        "first_name": first_name,
        "partners": partners,
        "partner_names": " and ".join(p["name"] for p in partners),
        "website_url": base_url(),
    }


def render_pairing_email(first_name: str, partners: list[dict], db_path=None) -> str:
    """Render the stored template; if it's broken, fall back to the default so
    a bad edit can never stop pairing emails."""
    context = _email_context(first_name, partners)
    try:
        return _jinja_env().from_string(get_email_template(db_path=db_path)).render(**context)
    except Exception:
        log.exception("Custom email template failed to render; using default")
        return _jinja_env().from_string(DEFAULT_EMAIL_TEMPLATE).render(**context)


SAMPLE_PARTNER = {
    "name": "Handsome Dan",
    "class_year": "2027",
    "residential_college": "Branford",
    "major": "Philosophy",
    "second_major": "Economics",
    "fun_facts": "Has attended every YDA meeting since 1889",
    "dining_hall": "Commons",
}


def preview_pairing_email(db_path=None) -> tuple[str, str | None]:
    """(rendered preview with sample data, error message or None) — for the
    admin edit page, where errors should be visible rather than swallowed."""
    from portal.domain.models import PlayerProfile

    sample = dict(SAMPLE_PARTNER)
    sample["info"] = _partner_info_sentences(
        PlayerProfile(player_id=0, **{k: v for k, v in SAMPLE_PARTNER.items() if k != "name"})
    )
    context = _email_context("Ava", [sample])
    try:
        return _jinja_env().from_string(get_email_template(db_path=db_path)).render(**context), None
    except Exception as exc:
        return "", f"{type(exc).__name__}: {exc}"


def _partner_dicts(repo, names, member_ids, pid) -> list[dict]:
    partners = []
    for q in member_ids:
        if q == pid:
            continue
        p = repo.get_profile(q)
        partners.append(
            {
                "name": names.get(q, f"#{q}"),
                "class_year": p.class_year if p else None,
                "residential_college": p.residential_college if p else None,
                "major": p.major if p else None,
                "second_major": p.second_major if p else None,
                "fun_facts": p.fun_facts if p else None,
                "dining_hall": p.dining_hall if p else None,
                "info": _partner_info_sentences(p),
            }
        )
    return partners


def render_pairing_dm(first_name: str, partners: list[dict]) -> str:
    """Short Slack DM version of the pairing notice."""
    names_text = " and ".join(p["name"] for p in partners)
    lines = [f":sandwich: Hey {first_name}! Your LunchTag pairing this week: *{names_text}*."]
    for p in partners:
        if p["info"]:
            lines.append(f"About {p['name']}: {p['info']}" if len(partners) > 1 else p["info"])
    lines.append(f"Reach out and grab lunch this week! Details + history: {base_url()}/lunchtag")
    lines.append("_You can turn off Slack notifications on the website's LunchTag page._")
    return "\n".join(lines)


def send_pairing_notifications(repo, session_id: int, db_path=None) -> dict:
    """Notify every linked member of their partner(s): email and/or Slack DM
    per their toggles, plus a channel announcement. Returns counts for the
    admin flash."""
    from portal.auth import store

    pairing_session = repo.get_session(session_id)
    names = repo.display_names()
    emails = store.emails_by_player(db_path=db_path)
    counts = {
        "emails_sent": 0, "emails_dry_run": 0, "members_without_email": 0, "opted_out": 0,
        "dms_sent": 0, "dms_dry_run": 0, "dms_no_slack_user": 0, "dms_opted_out": 0,
    }
    pair_lines = []
    for group in pairing_session.groups:
        pair_lines.append(" & ".join(names.get(pid, f"#{pid}") for pid in group.member_ids))
        for pid in group.member_ids:
            to = emails.get(pid)
            if not to:
                counts["members_without_email"] += 1
                continue
            profile = repo.get_profile(pid)
            partners = _partner_dicts(repo, names, group.member_ids, pid)
            first_name = names.get(pid, "there").split()[0]
            if profile is None or profile.email_notifications:
                body = render_pairing_email(first_name, partners, db_path=db_path)
                result = send_email(to, get_email_subject(db_path=db_path), body)
                counts["emails_sent" if result == "sent" else "emails_dry_run"] += 1
            else:
                counts["opted_out"] += 1
            if profile is None or profile.slack_notifications:
                try:
                    result = send_slack_dm(to, render_pairing_dm(first_name, partners))
                except Exception:
                    log.exception("Slack DM to %s failed", to)
                    result = "no-slack-user"
                key = {"sent": "dms_sent", "dry-run": "dms_dry_run"}.get(result, "dms_no_slack_user")
                counts[key] += 1
            else:
                counts["dms_opted_out"] += 1
    slack_text = (
        ":sandwich: *Hey everyone! This week's LunchTag pairings are out!*\n"
        "Check your Slack DMs, email, or the website for your partner.\n"
        f"{base_url()}/lunchtag\n"
        "_Notifications can be disabled on the website's LunchTag page._"
    )
    counts["slack"] = post_slack(slack_text)
    return counts


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
