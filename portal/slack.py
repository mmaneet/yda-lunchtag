"""Slack bot client — outbound calls only (DMs, channel posts, member list).

Dry-run when SLACK_BOT_TOKEN is unset: every send returns "dry-run" and
nothing leaves the machine. The Slack app itself (scopes, token) is
registered at api.slack.com; this module is the code behind it.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata

log = logging.getLogger(__name__)

API = "https://slack.com/api"


class SlackError(RuntimeError):
    pass


def enabled() -> bool:
    return bool(os.environ.get("SLACK_BOT_TOKEN"))


def _call(method: str, **payload) -> dict:
    import requests

    resp = requests.post(
        f"{API}/{method}",
        headers={
            "Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise SlackError(data.get("error", "unknown_error"))
    return data


# --- members ----------------------------------------------------------------


def list_members() -> list[dict]:
    """Every human, non-deactivated workspace member: {id, name, email}."""
    members: list[dict] = []
    cursor = None
    while True:
        data = _call("users.list", limit=200, **({"cursor": cursor} if cursor else {}))
        for u in data.get("members", []):
            if u.get("is_bot") or u.get("deleted") or u.get("id") == "USLACKBOT":
                continue
            profile = u.get("profile", {})
            members.append(
                {
                    "id": u["id"],
                    "name": profile.get("real_name") or u.get("real_name") or u.get("name", ""),
                    "display_name": profile.get("display_name") or "",
                    "email": (profile.get("email") or "").strip().lower(),
                }
            )
        cursor = data.get("response_metadata", {}).get("next_cursor") or None
        if not cursor:
            return members


def lookup_user_id(email: str) -> str | None:
    try:
        return _call("users.lookupByEmail", email=email)["user"]["id"]
    except SlackError as exc:
        if str(exc) == "users_not_found":
            return None
        raise


def normalize_name(name: str) -> str:
    """Accent-insensitive, punctuation-free, lowercase — for roster matching."""
    flat = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", "", flat.lower())).strip()


def match_member(members: list[dict], full_name: str, email: str | None = None) -> dict | None:
    """Find the one workspace member for a portal user, loosest-last:
    email → full name → unique first name → unique last name. Members whose
    Slack name is just "Faith" still match "Faith Duncan". None if ambiguous."""
    email = (email or "").strip().lower()
    if email:
        hits = [m for m in members if m.get("email") == email]
        if len(hits) == 1:
            return hits[0]
    target = normalize_name(full_name)
    if not target:
        return None
    parts = target.split()

    def names_of(m):
        return {normalize_name(m.get("name", "")), normalize_name(m.get("display_name", ""))} - {""}

    for predicate in (
        lambda names: target in names,
        lambda names: any(n.split()[0] == parts[0] for n in names) if parts else False,
        lambda names: any(n.split()[-1] == parts[-1] for n in names) if len(parts) > 1 else False,
    ):
        hits = [m for m in members if predicate(names_of(m))]
        unique = {m["id"]: m for m in hits}
        if len(unique) == 1:
            return next(iter(unique.values()))
    return None


def identify_member(full_name: str, email: str | None = None) -> dict | None:
    """match_member() against the live workspace; None when Slack is off."""
    if not enabled():
        return None
    return match_member(list_members(), full_name, email)


# --- sending ----------------------------------------------------------------


def send_dm_to_user(user_id: str, text: str) -> str:
    """DM a workspace member by Slack user ID."""
    if not enabled():
        log.info("[dry-run] slack DM to %s: %s", user_id, text[:80])
        return "dry-run"
    channel = _call("conversations.open", users=user_id)["channel"]["id"]
    _call("chat.postMessage", channel=channel, text=text)
    return "sent"


def send_dm(email: str, text: str, user_id: str | None = None) -> str:
    """DM a member: by stored Slack user ID when known, else by looking up
    their email in the workspace. Returns 'sent', 'dry-run', or
    'no-slack-user' (no ID and the email isn't in the workspace)."""
    if not enabled():
        log.info("[dry-run] slack DM to %s: %s", user_id or email, text[:80])
        return "dry-run"
    if not user_id:
        user_id = lookup_user_id(email)
        if user_id is None:
            return "no-slack-user"
    return send_dm_to_user(user_id, text)


def reply_in_thread(channel_id: str, thread_ts: str, text: str) -> str:
    if not enabled():
        log.info("[dry-run] slack thread reply in %s: %s", channel_id, text[:80])
        return "dry-run"
    _call("chat.postMessage", channel=channel_id, thread_ts=thread_ts, text=text)
    return "sent"


def channel_name(channel_id: str) -> str | None:
    """Name of a channel by ID (None if the bot can't see it)."""
    try:
        return _call("conversations.info", channel=channel_id)["channel"].get("name")
    except SlackError:
        return None


def find_channel_id(name: str) -> str | None:
    """ID of a public channel by name (the bot must be able to see it)."""
    cursor = None
    while True:
        data = _call("conversations.list", types="public_channel", limit=200,
                     **({"cursor": cursor} if cursor else {}))
        for ch in data.get("channels", []):
            if ch.get("name") == name:
                return ch["id"]
        cursor = data.get("response_metadata", {}).get("next_cursor") or None
        if not cursor:
            return None


def channel_history(channel_id: str, oldest: str) -> list[dict]:
    """All top-level messages in a channel since `oldest` (a Slack ts string),
    oldest first."""
    messages: list[dict] = []
    cursor = None
    while True:
        data = _call("conversations.history", channel=channel_id, oldest=oldest, limit=200,
                     **({"cursor": cursor} if cursor else {}))
        messages.extend(data.get("messages", []))
        cursor = data.get("response_metadata", {}).get("next_cursor") or None
        if not cursor:
            break
    messages.sort(key=lambda m: float(m.get("ts", "0")))
    return messages


def post_channel(text: str) -> str:
    """Post to the configured channel (SLACK_CHANNEL, default 'lunchtag')."""
    if not enabled():
        log.info("[dry-run] slack channel post: %s", text[:80])
        return "dry-run"
    name = os.environ.get("SLACK_CHANNEL", "lunchtag").lstrip("#")
    _call("chat.postMessage", channel=f"#{name}", text=text)
    return "sent"
