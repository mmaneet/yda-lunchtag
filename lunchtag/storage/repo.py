from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from lunchtag.domain.models import Draft, Group, PairingStats, Player, Session
from lunchtag.storage.db import connect, init_db


@dataclass(frozen=True)
class SessionSummary:
    id: int
    created_at: datetime
    status: str
    score: float


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _player_from_row(row) -> Player:
    return Player(
        id=row["id"],
        name=row["name"],
        active=bool(row["active"]),
        deleted=bool(row["deleted"]),
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


class Repository:
    def __init__(self, db_path: Path) -> None:
        self.conn = connect(db_path)
        init_db(self.conn)

    def close(self) -> None:
        self.conn.close()

    # Players
    def add_player(self, name: str) -> Player:
        now = _now_iso()
        row = self.conn.execute(
            "INSERT INTO players (name, active, deleted, created_at, updated_at) "
            "VALUES (?, 1, 0, ?, ?) RETURNING id",
            (name.strip(), now, now),
        ).fetchone()
        self.conn.commit()
        return self.get_player(row["id"])

    def get_player(self, player_id: int) -> Player:
        row = self.conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
        if row is None:
            raise ValueError(f"Player {player_id} not found")
        return _player_from_row(row)

    def list_players(self, include_deleted: bool = False) -> list[Player]:
        if include_deleted:
            rows = self.conn.execute("SELECT * FROM players ORDER BY lower(name)").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM players WHERE deleted = 0 ORDER BY lower(name)"
            ).fetchall()
        return [_player_from_row(row) for row in rows]

    def list_active_players(self) -> list[Player]:
        rows = self.conn.execute(
            "SELECT * FROM players WHERE active = 1 AND deleted = 0 ORDER BY lower(name)"
        ).fetchall()
        return [_player_from_row(row) for row in rows]

    def rename_player(self, player_id: int, new_name: str) -> Player:
        now = _now_iso()
        self.conn.execute(
            "UPDATE players SET name = ?, updated_at = ? WHERE id = ?",
            (new_name.strip(), now, player_id),
        )
        self.conn.commit()
        return self.get_player(player_id)

    def set_player_active(self, player_id: int, active: bool) -> Player:
        now = _now_iso()
        self.conn.execute(
            "UPDATE players SET active = ?, updated_at = ? WHERE id = ?",
            (1 if active else 0, now, player_id),
        )
        self.conn.commit()
        return self.get_player(player_id)

    def soft_delete_player(self, player_id: int) -> None:
        now = _now_iso()
        self.conn.execute(
            "UPDATE players SET deleted = 1, active = 0, updated_at = ? WHERE id = ?",
            (now, player_id),
        )
        self.conn.commit()

    # Sessions + groups
    def clear_drafts(self) -> None:
        self.conn.execute("DELETE FROM sessions WHERE status = 'draft'")
        self.conn.commit()

    def create_draft(self, rng_seed: int, score: float, groups: Iterable[Iterable[int]]) -> int:
        now = _now_iso()
        row = self.conn.execute(
            "INSERT INTO sessions (created_at, status, rng_seed, score) "
            "VALUES (?, 'draft', ?, ?) RETURNING id",
            (now, rng_seed, score),
        ).fetchone()
        session_id = row["id"]
        self._insert_groups(session_id, groups)
        self.conn.commit()
        return session_id

    def update_draft(self, session_id: int, score: float, groups: Iterable[Iterable[int]]) -> None:
        self.conn.execute(
            "DELETE FROM groups WHERE session_id = ?",
            (session_id,),
        )
        self._insert_groups(session_id, groups)
        self.conn.execute(
            "UPDATE sessions SET score = ? WHERE id = ?",
            (score, session_id),
        )
        self.conn.commit()

    def delete_draft(self, session_id: int) -> None:
        self.conn.execute("DELETE FROM sessions WHERE id = ? AND status = 'draft'", (session_id,))
        self.conn.commit()

    def confirm_draft(self, session_id: int, score: float) -> None:
        now = _now_iso()
        self.conn.execute(
            "UPDATE sessions SET status = 'confirmed', score = ?, created_at = ? WHERE id = ?",
            (score, now, session_id),
        )
        self.conn.commit()

    def get_draft(self) -> Draft | None:
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE status = 'draft' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        groups = self._load_groups(row["id"])
        return Draft(
            session_id=row["id"],
            rng_seed=row["rng_seed"],
            score=row["score"],
            groups=groups,
        )

    def list_confirmed_sessions(self) -> list[SessionSummary]:
        rows = self.conn.execute(
            "SELECT id, created_at, status, score FROM sessions WHERE status = 'confirmed' "
            "ORDER BY created_at DESC"
        ).fetchall()
        return [
            SessionSummary(
                id=row["id"],
                created_at=_parse_dt(row["created_at"]),
                status=row["status"],
                score=row["score"],
            )
            for row in rows
        ]

    def get_session(self, session_id: int) -> Session:
        row = self.conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise ValueError(f"Session {session_id} not found")
        groups = self._load_groups(row["id"])
        return Session(
            id=row["id"],
            created_at=_parse_dt(row["created_at"]),
            status=row["status"],
            rng_seed=row["rng_seed"],
            score=row["score"],
            notes=row["notes"],
            groups=groups,
        )

    def player_history(self, player_id: int) -> list[tuple[SessionSummary, tuple[int, ...]]]:
        """Confirmed sessions containing this player, newest first, each with
        the member ids of the group the player was in."""
        rows = self.conn.execute(
            """
            SELECT s.id AS session_id, s.created_at, s.status, s.score, g.id AS group_id
            FROM sessions s
            JOIN groups g ON g.session_id = s.id
            JOIN group_members gm ON gm.group_id = g.id
            WHERE gm.player_id = ? AND s.status = 'confirmed'
            ORDER BY s.created_at DESC
            """,
            (player_id,),
        ).fetchall()
        history: list[tuple[SessionSummary, tuple[int, ...]]] = []
        for row in rows:
            members = self.conn.execute(
                "SELECT player_id FROM group_members WHERE group_id = ? ORDER BY player_id",
                (row["group_id"],),
            ).fetchall()
            summary = SessionSummary(
                id=row["session_id"],
                created_at=_parse_dt(row["created_at"]),
                status=row["status"],
                score=row["score"],
            )
            history.append((summary, tuple(m["player_id"] for m in members)))
        return history

    def reset_history(self) -> None:
        self.conn.execute("DELETE FROM sessions")
        self.conn.commit()

    def full_reset(self) -> None:
        self.conn.execute("DELETE FROM sessions")
        self.conn.execute("DELETE FROM players")
        self.conn.commit()

    def fetch_pairing_stats(self) -> PairingStats:
        rows = self.conn.execute(
            """
            SELECT gm1.player_id AS a, gm2.player_id AS b,
                   COUNT(*) AS cnt, MAX(s.created_at) AS last_met
            FROM group_members gm1
            JOIN group_members gm2
              ON gm1.group_id = gm2.group_id
             AND gm1.player_id < gm2.player_id
            JOIN groups g ON gm1.group_id = g.id
            JOIN sessions s ON g.session_id = s.id
            WHERE s.status = 'confirmed'
            GROUP BY gm1.player_id, gm2.player_id
            """
        ).fetchall()
        met_count: dict[tuple[int, int], int] = {}
        last_met: dict[tuple[int, int], datetime] = {}
        for row in rows:
            key = (row["a"], row["b"])
            met_count[key] = int(row["cnt"])
            last_met[key] = _parse_dt(row["last_met"])

        degree: dict[int, int] = {}
        for (a, b), cnt in met_count.items():
            degree[a] = degree.get(a, 0) + cnt
            degree[b] = degree.get(b, 0) + cnt
        return PairingStats(met_count=met_count, last_met=last_met, degree=degree)

    def _insert_groups(self, session_id: int, groups: Iterable[Iterable[int]]) -> None:
        for idx, members in enumerate(groups):
            row = self.conn.execute(
                "INSERT INTO groups (session_id, group_index) VALUES (?, ?) RETURNING id",
                (session_id, idx),
            ).fetchone()
            group_id = row["id"]
            for player_id in members:
                self.conn.execute(
                    "INSERT INTO group_members (group_id, player_id) VALUES (?, ?)",
                    (group_id, player_id),
                )

    def _load_groups(self, session_id: int) -> tuple[Group, ...]:
        rows = self.conn.execute(
            """
            SELECT g.id AS group_id, g.group_index, gm.player_id
            FROM groups g
            JOIN group_members gm ON g.id = gm.group_id
            WHERE g.session_id = ?
            ORDER BY g.group_index, gm.player_id
            """,
            (session_id,),
        ).fetchall()
        groups_map: dict[int, list[int]] = {}
        group_index: dict[int, int] = {}
        for row in rows:
            group_id = row["group_id"]
            groups_map.setdefault(group_id, []).append(row["player_id"])
            group_index[group_id] = row["group_index"]
        groups: list[Group] = []
        for group_id, members in sorted(groups_map.items(), key=lambda item: group_index[item[0]]):
            groups.append(
                Group(
                    id=group_id,
                    session_id=session_id,
                    group_index=group_index[group_id],
                    member_ids=tuple(members),
                )
            )
        return tuple(groups)
