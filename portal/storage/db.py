from __future__ import annotations

import os
import sqlite3
from pathlib import Path

# One schema, two dialects. SQLite is used locally and in CI; Postgres is used
# in production whenever DATABASE_URL is set (Railway injects it).
_SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id {pk},
    name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    deleted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id {pk},
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    rng_seed {bigint} NOT NULL,
    score {float} NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS groups (
    id {pk},
    session_id INTEGER NOT NULL,
    group_index INTEGER NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS group_members (
    group_id INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    FOREIGN KEY(group_id) REFERENCES groups(id) ON DELETE CASCADE,
    FOREIGN KEY(player_id) REFERENCES players(id),
    UNIQUE(group_id, player_id)
);

CREATE TABLE IF NOT EXISTS player_profiles (
    player_id INTEGER PRIMARY KEY,
    display_name TEXT,
    class_year TEXT,
    residential_college TEXT,
    birthday TEXT,
    dining_hall TEXT,
    fun_facts TEXT,
    major TEXT,
    second_major TEXT,
    email_notifications INTEGER NOT NULL DEFAULT 1,
    slack_notifications INTEGER NOT NULL DEFAULT 1,
    onboarded INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT,
    FOREIGN KEY(player_id) REFERENCES players(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS player_preferences (
    player_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (player_id, target_id, kind),
    FOREIGN KEY(player_id) REFERENCES players(id) ON DELETE CASCADE,
    FOREIGN KEY(target_id) REFERENCES players(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_groups_session ON groups(session_id);
CREATE INDEX IF NOT EXISTS idx_group_members_player ON group_members(player_id);
"""

_SQLITE_DIALECT = {"pk": "INTEGER PRIMARY KEY AUTOINCREMENT", "bigint": "INTEGER", "float": "REAL"}
_POSTGRES_DIALECT = {"pk": "SERIAL PRIMARY KEY", "bigint": "BIGINT", "float": "DOUBLE PRECISION"}


class PostgresConnection:
    """Minimal wrapper so the repository's sqlite-style SQL runs on Postgres.

    Translates `?` placeholders to `%s`. Queries must not contain a literal `?`.
    Rows come back as dicts, matching sqlite3.Row's by-name access.
    """

    dialect = "postgres"

    def __init__(self, dsn: str) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self._conn = psycopg.connect(dsn, row_factory=dict_row)

    def execute(self, sql: str, params: tuple = ()):
        return self._conn.execute(sql.replace("?", "%s"), params)

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def connect(db_path: Path):
    """Connect to Postgres if DATABASE_URL is set, else to the local SQLite file."""
    dsn = os.environ.get("DATABASE_URL")
    if dsn:
        return PostgresConnection(dsn)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Columns added after launch. Databases created before these existed get them
# via idempotent ALTERs on connect (Postgres: IF NOT EXISTS; SQLite: the
# duplicate-column error is swallowed).
_PROFILE_COLUMN_ADDS = [
    "major TEXT",
    "second_major TEXT",
    "email_notifications INTEGER NOT NULL DEFAULT 1",
    "slack_notifications INTEGER NOT NULL DEFAULT 1",
]


def init_db(conn) -> None:
    if getattr(conn, "dialect", "sqlite") == "postgres":
        script = _SCHEMA.format(**_POSTGRES_DIALECT)
        for statement in script.split(";"):
            if statement.strip():
                conn.execute(statement)
        for column in _PROFILE_COLUMN_ADDS:
            conn.execute(f"ALTER TABLE player_profiles ADD COLUMN IF NOT EXISTS {column}")
    else:
        conn.executescript(_SCHEMA.format(**_SQLITE_DIALECT))
        for column in _PROFILE_COLUMN_ADDS:
            try:
                conn.execute(f"ALTER TABLE player_profiles ADD COLUMN {column}")
            except sqlite3.OperationalError:
                pass  # column already exists
    conn.commit()
