"""One-time migration of the local SQLite database into Postgres.

Usage:
    DATABASE_URL=postgres://... python scripts/migrate_sqlite_to_postgres.py [sqlite_path]

Copies all rows preserving primary keys, then bumps the Postgres sequences so
new inserts don't collide. Refuses to run if the target already has players
(pass --force to wipe the target first).
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lunchtag.storage.db import PostgresConnection, init_db

TABLES = {
    "players": ["id", "name", "active", "deleted", "created_at", "updated_at"],
    "sessions": ["id", "created_at", "status", "rng_seed", "score", "notes"],
    "groups": ["id", "session_id", "group_index"],
    "group_members": ["group_id", "player_id"],
}
SEQUENCED = ["players", "sessions", "groups"]


def main() -> None:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("DATABASE_URL must be set to the target Postgres connection string.")
    args = [a for a in sys.argv[1:] if a != "--force"]
    force = "--force" in sys.argv[1:]
    sqlite_path = Path(args[0]) if args else Path("data/lunchtag.sqlite")
    if not sqlite_path.exists():
        sys.exit(f"SQLite file not found: {sqlite_path}")

    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row
    dst = PostgresConnection(dsn)
    init_db(dst)

    existing = dst.execute("SELECT COUNT(*) AS n FROM players").fetchone()["n"]
    if existing and not force:
        sys.exit(f"Target already has {existing} players; pass --force to wipe and re-migrate.")
    if force:
        for table in ["group_members", "groups", "sessions", "players"]:
            dst.execute(f"DELETE FROM {table}")

    for table, cols in TABLES.items():
        rows = src.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
        placeholders = ", ".join(["?"] * len(cols))
        for row in rows:
            dst.execute(
                f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
                tuple(row[c] for c in cols),
            )

    for table in SEQUENCED:
        dst.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"(SELECT COALESCE(MAX(id), 1) FROM {table}))"
        )
    dst.commit()

    print(f"{'table':<15}{'sqlite':>8}{'postgres':>10}")
    ok = True
    for table in TABLES:
        n_src = src.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        n_dst = dst.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        ok = ok and n_src == n_dst
        print(f"{table:<15}{n_src:>8}{n_dst:>10}")
    dst.close()
    src.close()
    sys.exit(0 if ok else "Row count mismatch — investigate before trusting the target.")


if __name__ == "__main__":
    main()
