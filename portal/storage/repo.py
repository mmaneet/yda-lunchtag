from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Iterable

from portal.domain.models import (
    Draft,
    Group,
    PairingStats,
    Player,
    PlayerProfile,
    Preferences,
    Session,
)
from portal.storage.db import connect, init_db


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

    # Profiles
    _PROFILE_FIELDS = (
        "display_name",
        "class_year",
        "residential_college",
        "birthday",
        "dining_hall",
        "fun_facts",
        "major",
        "second_major",
    )

    def get_profile(self, player_id: int) -> PlayerProfile | None:
        row = self.conn.execute(
            "SELECT * FROM player_profiles WHERE player_id = ?", (player_id,)
        ).fetchone()
        if row is None:
            return None
        return PlayerProfile(
            player_id=row["player_id"],
            display_name=row["display_name"],
            class_year=row["class_year"],
            residential_college=row["residential_college"],
            birthday=row["birthday"],
            dining_hall=row["dining_hall"],
            fun_facts=row["fun_facts"],
            major=row["major"],
            second_major=row["second_major"],
            email_notifications=bool(row["email_notifications"]),
            slack_notifications=bool(row["slack_notifications"]),
            lunchtag_opt_in=bool(row["lunchtag_opt_in"]),
            onboarded=bool(row["onboarded"]),
        )

    def upsert_profile(self, player_id: int, *, onboarded: bool = True, **fields) -> PlayerProfile:
        """Create or update a profile. Only keys in _PROFILE_FIELDS are
        accepted; missing keys (and the notification toggles) keep their
        current value."""
        unknown = set(fields) - set(self._PROFILE_FIELDS)
        if unknown:
            raise ValueError(f"Unknown profile fields: {sorted(unknown)}")
        current = self.get_profile(player_id)
        merged = {
            name: fields.get(name, getattr(current, name) if current else None)
            for name in self._PROFILE_FIELDS
        }
        now = _now_iso()
        self.conn.execute(
            "INSERT INTO player_profiles (player_id, display_name, class_year, "
            "residential_college, birthday, dining_hall, fun_facts, major, second_major, "
            "email_notifications, slack_notifications, lunchtag_opt_in, onboarded, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (player_id) DO UPDATE SET "
            "display_name = excluded.display_name, class_year = excluded.class_year, "
            "residential_college = excluded.residential_college, birthday = excluded.birthday, "
            "dining_hall = excluded.dining_hall, fun_facts = excluded.fun_facts, "
            "major = excluded.major, second_major = excluded.second_major, "
            "email_notifications = excluded.email_notifications, "
            "slack_notifications = excluded.slack_notifications, "
            "lunchtag_opt_in = excluded.lunchtag_opt_in, "
            "onboarded = excluded.onboarded, updated_at = excluded.updated_at",
            (
                player_id,
                merged["display_name"],
                merged["class_year"],
                merged["residential_college"],
                merged["birthday"],
                merged["dining_hall"],
                merged["fun_facts"],
                merged["major"],
                merged["second_major"],
                1 if (current is None or current.email_notifications) else 0,
                1 if (current is None or current.slack_notifications) else 0,
                1 if (current is not None and current.lunchtag_opt_in) else 0,
                1 if onboarded else 0,
                now,
            ),
        )
        self.conn.commit()
        profile = self.get_profile(player_id)
        assert profile is not None
        return profile

    def set_lunchtag_opt_in(self, player_id: int, opted_in: bool) -> None:
        """Flip a member's LunchTag participation, creating a minimal profile
        row if needed."""
        if self.get_profile(player_id) is None:
            self.conn.execute(
                "INSERT INTO player_profiles (player_id, lunchtag_opt_in, onboarded, updated_at) "
                "VALUES (?, ?, 0, ?)",
                (player_id, 1 if opted_in else 0, _now_iso()),
            )
        else:
            self.conn.execute(
                "UPDATE player_profiles SET lunchtag_opt_in = ?, updated_at = ? "
                "WHERE player_id = ?",
                (1 if opted_in else 0, _now_iso(), player_id),
            )
        self.conn.commit()

    def opt_in_by_player(self) -> dict[int, bool]:
        """player_id -> opted in? for every profile row (no row = opted out)."""
        rows = self.conn.execute("SELECT player_id, lunchtag_opt_in FROM player_profiles").fetchall()
        return {row["player_id"]: bool(row["lunchtag_opt_in"]) for row in rows}

    def list_pairing_pool(self) -> list[Player]:
        """Active, non-deleted players who explicitly opted into LunchTag.
        Opt-in is the member's own choice — no profile row means not in."""
        rows = self.conn.execute(
            "SELECT p.* FROM players p "
            "JOIN player_profiles pr ON pr.player_id = p.id "
            "WHERE p.active = 1 AND p.deleted = 0 AND pr.lunchtag_opt_in = 1 "
            "ORDER BY lower(p.name)"
        ).fetchall()
        return [_player_from_row(row) for row in rows]

    def set_notification_prefs(self, player_id: int, *, email: bool, slack: bool) -> None:
        """Set a member's notification toggles, creating a minimal profile row
        if they somehow don't have one yet."""
        if self.get_profile(player_id) is None:
            self.conn.execute(
                "INSERT INTO player_profiles (player_id, email_notifications, "
                "slack_notifications, onboarded, updated_at) VALUES (?, ?, ?, 0, ?)",
                (player_id, 1 if email else 0, 1 if slack else 0, _now_iso()),
            )
        else:
            self.conn.execute(
                "UPDATE player_profiles SET email_notifications = ?, "
                "slack_notifications = ?, updated_at = ? WHERE player_id = ?",
                (1 if email else 0, 1 if slack else 0, _now_iso(), player_id),
            )
        self.conn.commit()

    def display_names(self) -> dict[int, str]:
        """player_id -> best display name (profile display_name, else roster name)."""
        names = {p.id: p.name for p in self.list_players(include_deleted=True)}
        rows = self.conn.execute(
            "SELECT player_id, display_name FROM player_profiles "
            "WHERE display_name IS NOT NULL"
        ).fetchall()
        for row in rows:
            if row["display_name"]:
                names[row["player_id"]] = row["display_name"]
        return names

    # Preferences (strikes + boosts)
    PREFERENCE_KINDS = ("strike", "boost")

    def add_preference(self, player_id: int, target_id: int, kind: str) -> None:
        if kind not in self.PREFERENCE_KINDS:
            raise ValueError(f"Kind must be one of {self.PREFERENCE_KINDS}, got {kind!r}")
        if player_id == target_id:
            raise ValueError("Players cannot strike or boost themselves.")
        if kind == "boost":
            # One boost at a time: a new boost replaces the old one.
            self.conn.execute(
                "DELETE FROM player_preferences WHERE player_id = ? AND kind = 'boost'",
                (player_id,),
            )
        self.conn.execute(
            "INSERT INTO player_preferences (player_id, target_id, kind, created_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (player_id, target_id, kind) DO NOTHING",
            (player_id, target_id, kind, _now_iso()),
        )
        self.conn.commit()

    def remove_preference(self, player_id: int, target_id: int, kind: str) -> None:
        self.conn.execute(
            "DELETE FROM player_preferences "
            "WHERE player_id = ? AND target_id = ? AND kind = ?",
            (player_id, target_id, kind),
        )
        self.conn.commit()

    def list_preferences(self, player_id: int) -> dict[str, list[int]]:
        rows = self.conn.execute(
            "SELECT target_id, kind FROM player_preferences WHERE player_id = ? "
            "ORDER BY created_at",
            (player_id,),
        ).fetchall()
        prefs: dict[str, list[int]] = {kind: [] for kind in self.PREFERENCE_KINDS}
        for row in rows:
            prefs.setdefault(row["kind"], []).append(row["target_id"])
        return prefs

    def fetch_preferences(self) -> Preferences:
        """All preferences as normalized unordered pairs for the engine.
        Effect is symmetric: A striking B forbids the pair either way round."""
        rows = self.conn.execute(
            "SELECT player_id, target_id, kind FROM player_preferences"
        ).fetchall()
        forbidden = set()
        boosted = set()
        for row in rows:
            pair = (
                (row["player_id"], row["target_id"])
                if row["player_id"] < row["target_id"]
                else (row["target_id"], row["player_id"])
            )
            if row["kind"] == "strike":
                forbidden.add(pair)
            elif row["kind"] == "boost":
                boosted.add(pair)
        # A strike beats a boost on the same pair.
        return Preferences(
            forbidden=frozenset(forbidden),
            boosted=frozenset(boosted - forbidden),
        )

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
        # A boost is a one-shot wish: once the pair actually happens, it
        # resets automatically so the next round isn't skewed by it.
        for group in self._load_groups(session_id):
            for a, b in combinations(group.member_ids, 2):
                self.conn.execute(
                    "DELETE FROM player_preferences WHERE kind = 'boost' AND "
                    "((player_id = ? AND target_id = ?) OR (player_id = ? AND target_id = ?))",
                    (a, b, b, a),
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

    # Notification runs (background sends)
    def start_notification_run(self, session_id: int, channels: Iterable[str]) -> int:
        row = self.conn.execute(
            "INSERT INTO notification_runs (session_id, channels, status, started_at) "
            "VALUES (?, ?, 'running', ?) RETURNING id",
            (session_id, ",".join(channels), _now_iso()),
        ).fetchone()
        self.conn.commit()
        return row["id"]

    def finish_notification_run(self, run_id: int, status: str, summary: str) -> None:
        self.conn.execute(
            "UPDATE notification_runs SET status = ?, summary = ?, finished_at = ? WHERE id = ?",
            (status, summary, _now_iso(), run_id),
        )
        self.conn.commit()

    def notification_runs(self, session_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM notification_runs WHERE session_id = ? ORDER BY id DESC",
            (session_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    # Completions (leaderboard)
    def latest_confirmed_session(self) -> Session | None:
        row = self.conn.execute(
            "SELECT id FROM sessions WHERE status = 'confirmed' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return self.get_session(row["id"]) if row else None

    def record_completions(self, session_id: int, player_ids: Iterable[int], source: str) -> list[int]:
        """Credit players for completing a round; one credit per player per
        round, so repeats are ignored. Returns the ids newly credited."""
        newly = []
        now = _now_iso()
        for pid in player_ids:
            row = self.conn.execute(
                "INSERT INTO lunchtag_completions (session_id, player_id, source, created_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (session_id, player_id) DO NOTHING RETURNING player_id",
                (session_id, pid, source, now),
            ).fetchone()
            if row is not None:
                newly.append(pid)
        self.conn.commit()
        return newly

    def remove_completion(self, session_id: int, player_id: int) -> bool:
        """Un-credit a player for a round (admin correction). True if removed."""
        row = self.conn.execute(
            "DELETE FROM lunchtag_completions WHERE session_id = ? AND player_id = ? RETURNING player_id",
            (session_id, player_id),
        ).fetchone()
        self.conn.commit()
        return row is not None

    def completed_player_ids(self, session_id: int) -> set[int]:
        rows = self.conn.execute(
            "SELECT player_id FROM lunchtag_completions WHERE session_id = ?", (session_id,)
        ).fetchall()
        return {row["player_id"] for row in rows}

    def leaderboard(self) -> list[tuple[Player, int]]:
        """Every active player with their completed-lunch count, best first
        (ties broken by name)."""
        rows = self.conn.execute(
            "SELECT p.*, COUNT(c.player_id) AS done FROM players p "
            "LEFT JOIN lunchtag_completions c ON c.player_id = p.id "
            "WHERE p.deleted = 0 AND p.active = 1 "
            "GROUP BY p.id ORDER BY done DESC, lower(p.name)"
        ).fetchall()
        return [(_player_from_row(row), int(row["done"])) for row in rows]

    def completed_session_ids(self, player_id: int) -> set[int]:
        rows = self.conn.execute(
            "SELECT session_id FROM lunchtag_completions WHERE player_id = ?", (player_id,)
        ).fetchall()
        return {row["session_id"] for row in rows}

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
