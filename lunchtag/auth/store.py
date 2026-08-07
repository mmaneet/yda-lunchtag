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

from lunchtag.config import default_db_path
from lunchtag.storage.db import connect

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


def _open(db_path: Path | str | None = None):
    conn = connect(Path(db_path) if db_path is not None else default_db_path())
    conn.execute(_CREATE_ALLOWED_EMAILS)
    conn.execute(_CREATE_APP_SETTINGS)
    conn.commit()
    return conn


# --- app_settings -----------------------------------------------------------


def get_setting(key: str, default: str | None = None, db_path=None) -> str | None:
    conn = _open(db_path)
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row is not None else default
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
        return [row[0] for row in rows]
    finally:
        conn.close()
