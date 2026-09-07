"""Storage for auth data: the email whitelist and app settings.

Deliberately separate from lunchtag/storage/db.py and repo.py (which hold the
pairing data): this module owns exactly two tables and creates them on first
use with CREATE TABLE IF NOT EXISTS, so no migration step is needed.

SQL rules for this module (do not break them):
- `?` placeholders only, one statement per conn.execute() call (no
  executescript), and only syntax valid in BOTH SQLite and Postgres —
  `connect` is becoming dual-backend on main.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from portal.config import default_db_path
from portal.storage.db import connect

# app_settings key controlling whitelist enforcement. Value "1" = enforced,
# anything else (or missing) = not enforced. Config-as-data: flip it with
# set_whitelist_enforced(), never with a code change.
WHITELIST_ENFORCED_KEY = "auth_whitelist_enforced"

_CREATE_ALLOWED_EMAILS = """
CREATE TABLE IF NOT EXISTS allowed_emails (
    email TEXT PRIMARY KEY,
    added_at TEXT NOT NULL
)
"""

_CREATE_APP_SETTINGS = """
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

# Roles: 'admin' sees the full dashboard; everyone else is 'member' (the
# default — absence of a row means member) and sees only their own pairings.
_CREATE_USER_ROLES = """
CREATE TABLE IF NOT EXISTS user_roles (
    email TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

# Links a signed-in Google account to a player row so members can see their
# own pairings. Multiple emails may point at one player; one email, one player.
_CREATE_PLAYER_EMAILS = """
CREATE TABLE IF NOT EXISTS player_emails (
    email TEXT PRIMARY KEY,
    player_id INTEGER NOT NULL
)
"""

# A player's Slack identity, so DMs work even when their Slack account uses a
# different email than their portal login (e.g. a personal Gmail).
_CREATE_PLAYER_SLACK = """
CREATE TABLE IF NOT EXISTS player_slack (
    player_id INTEGER PRIMARY KEY,
    slack_user_id TEXT NOT NULL,
    slack_email TEXT,
    updated_at TEXT NOT NULL
)
"""

ROLES = ("member", "admin")


def _open(db_path: Path | str | None = None):
    conn = connect(Path(db_path) if db_path is not None else default_db_path())
    conn.execute(_CREATE_ALLOWED_EMAILS)
    conn.execute(_CREATE_APP_SETTINGS)
    conn.execute(_CREATE_USER_ROLES)
    conn.execute(_CREATE_PLAYER_EMAILS)
    conn.execute(_CREATE_PLAYER_SLACK)
    conn.commit()
    return conn


# --- app_settings -----------------------------------------------------------


def get_setting(key: str, default: str | None = None, db_path=None) -> str | None:
    conn = _open(db_path)
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row is not None else default
    finally:
        conn.close()


def set_setting(key: str, value: str, db_path=None) -> None:
    conn = _open(db_path)
    try:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def get_whitelist_enforced(db_path=None) -> bool:
    return get_setting(WHITELIST_ENFORCED_KEY, db_path=db_path) == "1"


def set_whitelist_enforced(enforced: bool, db_path=None) -> None:
    set_setting(WHITELIST_ENFORCED_KEY, "1" if enforced else "0", db_path=db_path)


# --- allowed_emails (whitelist) --------------------------------------------


def add_allowed_email(email: str, db_path=None) -> None:
    email = email.strip().lower()
    conn = _open(db_path)
    try:
        conn.execute(
            "INSERT INTO allowed_emails (email, added_at) VALUES (?, ?) "
            "ON CONFLICT (email) DO NOTHING",
            (email, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def remove_allowed_email(email: str, db_path=None) -> None:
    conn = _open(db_path)
    try:
        conn.execute(
            "DELETE FROM allowed_emails WHERE email = ?",
            (email.strip().lower(),),
        )
        conn.commit()
    finally:
        conn.close()


def list_allowed_emails(db_path=None) -> list[str]:
    conn = _open(db_path)
    try:
        rows = conn.execute(
            "SELECT email FROM allowed_emails ORDER BY email"
        ).fetchall()
        return [row["email"] for row in rows]
    finally:
        conn.close()


# --- user_roles -------------------------------------------------------------


def get_role(email: str, db_path=None) -> str:
    """Role for an email; 'member' when no row exists."""
    conn = _open(db_path)
    try:
        row = conn.execute(
            "SELECT role FROM user_roles WHERE email = ?",
            (email.strip().lower(),),
        ).fetchone()
        return row["role"] if row is not None else "member"
    finally:
        conn.close()


def set_role(email: str, role: str, db_path=None) -> None:
    if role not in ROLES:
        raise ValueError(f"Role must be one of {ROLES}, got {role!r}")
    conn = _open(db_path)
    try:
        conn.execute(
            "INSERT INTO user_roles (email, role, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT (email) DO UPDATE SET role = excluded.role, "
            "updated_at = excluded.updated_at",
            (email.strip().lower(), role, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def list_roles(db_path=None) -> list[tuple[str, str]]:
    conn = _open(db_path)
    try:
        rows = conn.execute(
            "SELECT email, role FROM user_roles ORDER BY email"
        ).fetchall()
        return [(row["email"], row["role"]) for row in rows]
    finally:
        conn.close()


# --- player_emails (account → player link) ---------------------------------


def link_player_email(email: str, player_id: int, db_path=None) -> None:
    conn = _open(db_path)
    try:
        conn.execute(
            "INSERT INTO player_emails (email, player_id) VALUES (?, ?) "
            "ON CONFLICT (email) DO UPDATE SET player_id = excluded.player_id",
            (email.strip().lower(), player_id),
        )
        conn.commit()
    finally:
        conn.close()


def unlink_player(player_id: int, db_path=None) -> None:
    conn = _open(db_path)
    try:
        conn.execute("DELETE FROM player_emails WHERE player_id = ?", (player_id,))
        conn.commit()
    finally:
        conn.close()


def get_player_id_for_email(email: str, db_path=None) -> int | None:
    conn = _open(db_path)
    try:
        row = conn.execute(
            "SELECT player_id FROM player_emails WHERE email = ?",
            (email.strip().lower(),),
        ).fetchone()
        return row["player_id"] if row is not None else None
    finally:
        conn.close()


def get_email_for_player(player_id: int, db_path=None) -> str | None:
    conn = _open(db_path)
    try:
        row = conn.execute(
            "SELECT email FROM player_emails WHERE player_id = ?",
            (player_id,),
        ).fetchone()
        return row["email"] if row is not None else None
    finally:
        conn.close()


def set_slack_user(player_id: int, slack_user_id: str, slack_email: str | None, db_path=None) -> None:
    conn = _open(db_path)
    try:
        conn.execute(
            "INSERT INTO player_slack (player_id, slack_user_id, slack_email, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (player_id) DO UPDATE SET "
            "slack_user_id = excluded.slack_user_id, slack_email = excluded.slack_email, "
            "updated_at = excluded.updated_at",
            (player_id, slack_user_id, slack_email, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def slack_users_by_player(db_path=None) -> dict[int, str]:
    conn = _open(db_path)
    try:
        rows = conn.execute("SELECT player_id, slack_user_id FROM player_slack").fetchall()
        return {row["player_id"]: row["slack_user_id"] for row in rows}
    finally:
        conn.close()


def emails_by_player(db_path=None) -> dict[int, str]:
    conn = _open(db_path)
    try:
        rows = conn.execute("SELECT email, player_id FROM player_emails").fetchall()
        return {row["player_id"]: row["email"] for row in rows}
    finally:
        conn.close()
