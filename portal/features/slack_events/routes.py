"""Inbound Slack events: POST /slack/events.

Slack pushes every message the bot can see (the #lunchtag channel and DMs to
the bot) here. Two behaviors:

1. Completion tally: a photo — or "had lunchtag" — posted in the LunchTag
   channel credits the sender and their partner(s) from the latest confirmed
   round on the leaderboard (one credit per player per round), and the bot
   replies in-thread.
2. Onboarding usher: a DM to the bot mentioning opting in / joining gets a
   reply with the website link and instructions. The bot never creates
   accounts — the website's Google login remains the source of identity.

This endpoint is exempt from the login gate (Slack's servers can't sign in);
every request is authenticated with Slack's HMAC signature instead.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import time

from flask import Blueprint, abort, current_app, jsonify, request

from portal import notifications, slack
from portal.auth import store as auth_store
from portal.shared import get_repo

log = logging.getLogger(__name__)

bp = Blueprint("slack", __name__)

COMPLETION_PHRASE = re.compile(r"had\s+lunch\s*tag", re.IGNORECASE)
OPT_IN_PHRASE = re.compile(r"opt[\s-]*in|\bjoin\b|sign[\s-]*up|onboard", re.IGNORECASE)
OPT_OUT_PHRASE = re.compile(r"^\s*/?quit\b|opt[\s-]*out|\bleave\b|unsubscribe|\bstop\b", re.IGNORECASE)


def verify_signature(headers, body: bytes) -> bool:
    secret = os.environ.get("SLACK_SIGNING_SECRET", "")
    timestamp = headers.get("X-Slack-Request-Timestamp", "")
    signature = headers.get("X-Slack-Signature", "")
    if not (secret and timestamp and signature):
        return False
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False  # replay protection
    except ValueError:
        return False
    basestring = f"v0:{timestamp}:".encode() + body
    expected = "v0=" + hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@bp.route("/slack/events", methods=["POST"])
def events():
    body = request.get_data()
    if not verify_signature(request.headers, body):
        abort(401)
    payload = request.get_json(silent=True) or {}
    if payload.get("type") == "url_verification":
        return jsonify({"challenge": payload.get("challenge", "")})
    # Slack retries if we don't answer within 3s; our handlers are idempotent,
    # so just skip retried deliveries outright.
    if request.headers.get("X-Slack-Retry-Num"):
        return "", 200
    if payload.get("type") == "event_callback":
        event = payload.get("event", {})
        try:
            result = handle_event(event, db_path=current_app.config["DB_PATH"])
        except Exception:
            log.exception("Slack event handling failed")
            result = "error"
        log.info(
            "slack event type=%s subtype=%s channel_type=%s user=%s files=%d -> %s",
            event.get("type"), event.get("subtype"), event.get("channel_type"),
            event.get("user"), len(event.get("files", []) or []), result,
        )
    return "", 200


def _is_image_message(event: dict) -> bool:
    return any((f.get("mimetype") or "").startswith("image/") for f in event.get("files", []))


def handle_event(event: dict, db_path=None) -> str:
    """Route one Slack event. Returns a short label of what happened (for
    logs and tests)."""
    if event.get("type") != "message":
        return "ignored:type"
    if event.get("bot_id") or event.get("subtype") in {"bot_message", "message_changed", "message_deleted"}:
        return "ignored:bot"
    user_id = event.get("user")
    if not user_id:
        return "ignored:no-user"
    text = event.get("text") or ""
    if event.get("channel_type") == "im":
        return _handle_dm(event, user_id, text, db_path=db_path)
    return _handle_channel_message(event, user_id, text, db_path=db_path)


def _handle_dm(event: dict, user_id: str, text: str, db_path=None) -> str:
    if OPT_OUT_PHRASE.search(text):
        return opt_out(user_id, db_path=db_path)
    if not OPT_IN_PHRASE.search(text):
        return "ignored:dm"
    slack.send_dm_to_user(
        user_id,
        f":sandwich: Welcome! LunchTag lives on the website — sign in with your Yale "
        f"Google account and flip the *Join LunchTag* switch:\n{notifications.base_url()}\n"
        "Once you're in, your weekly pairing lands here, in your email, and on the site.",
    )
    return "usher"


def opt_out(user_id: str, db_path=None) -> str:
    """Opt the member behind this Slack account out of LunchTag."""
    player_id = auth_store.player_by_slack_user(user_id, db_path=db_path)
    if player_id is None:
        slack.send_dm_to_user(
            user_id,
            "I couldn't find a LunchTag account linked to your Slack. You can opt out "
            f"from your profile on the website: {notifications.base_url()}/profile",
        )
        return "quit:unknown-member"
    get_repo().set_lunchtag_opt_in(player_id, False)
    slack.send_dm_to_user(
        user_id,
        ":wave: You're opted out of LunchTag — no more pairings for you. Changed your mind? "
        f"Flip the switch on your profile anytime: {notifications.base_url()}/profile",
    )
    return "quit"


@bp.route("/slack/commands", methods=["POST"])
def commands():
    """Slash commands (form-encoded). Register /quit at api.slack.com with the
    request URL <site>/slack/commands."""
    if not verify_signature(request.headers, request.get_data()):
        abort(401)
    command = request.form.get("command", "")
    user_id = request.form.get("user_id", "")
    if command == "/quit" and user_id:
        result = opt_out(user_id, db_path=current_app.config["DB_PATH"])
        if result == "quit":
            return jsonify({"response_type": "ephemeral", "text": "You're opted out of LunchTag. Rejoin anytime from your profile."})
        return jsonify({"response_type": "ephemeral", "text": "Couldn't find your LunchTag account — opt out from your profile on the website."})
    return jsonify({"response_type": "ephemeral", "text": "Unknown command."})


def _handle_channel_message(event: dict, user_id: str, text: str, db_path=None) -> str:
    if not (_is_image_message(event) or COMPLETION_PHRASE.search(text)):
        return "ignored:not-a-completion"
    wanted = os.environ.get("SLACK_CHANNEL", "lunchtag").lstrip("#")
    channel_id = event.get("channel", "")
    if slack.enabled() and slack.channel_name(channel_id) != wanted:
        return "ignored:other-channel"
    player_id = auth_store.player_by_slack_user(user_id, db_path=db_path)
    if player_id is None:
        return "ignored:unknown-member"
    repo = get_repo()
    session = repo.latest_confirmed_session()
    if session is None:
        return "ignored:no-round"
    group = next((g for g in session.groups if player_id in g.member_ids), None)
    if group is None:
        return "ignored:not-in-round"
    newly = repo.record_completions(session.id, group.member_ids, source="slack")
    if not newly:
        return "duplicate"
    names = repo.display_names()
    who = " & ".join(names.get(pid, f"#{pid}") for pid in group.member_ids)
    slack.reply_in_thread(
        channel_id, event.get("ts", ""),
        f":white_check_mark: LunchTag logged for *{who}*! "
        f"Leaderboard: {notifications.base_url()}/leaderboard",
    )
    return f"credited:{len(newly)}"
