from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Player:
    id: int
    name: str
    active: bool
    deleted: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class PlayerProfile:
    """Self-service member profile. Required fields come from onboarding;
    the rest are optional extras the member may add later."""

    player_id: int
    display_name: str | None = None
    class_year: str | None = None
    residential_college: str | None = None
    birthday: str | None = None
    dining_hall: str | None = None
    fun_facts: str | None = None
    onboarded: bool = False


@dataclass(frozen=True)
class Group:
    id: int | None
    session_id: int | None
    group_index: int
    member_ids: tuple[int, ...]

    @property
    def size(self) -> int:
        return len(self.member_ids)


@dataclass(frozen=True)
class Session:
    id: int
    created_at: datetime
    status: str  # draft or confirmed
    rng_seed: int
    score: float
    notes: str | None
    groups: tuple[Group, ...]


@dataclass(frozen=True)
class Draft:
    session_id: int
    rng_seed: int
    score: float
    groups: tuple[Group, ...]


@dataclass(frozen=True)
class PairingStats:
    met_count: dict[tuple[int, int], int]
    last_met: dict[tuple[int, int], datetime]
    degree: dict[int, int]


@dataclass(frozen=True)
class Preferences:
    """Member pairing preferences, normalized to unordered (min, max) pairs.

    forbidden: strikes — these pairs must never be grouped together.
    boosted: boosts — these pairs get a scoring bonus (roughly double odds).
    """

    forbidden: frozenset[tuple[int, int]]
    boosted: frozenset[tuple[int, int]]


EMPTY_PREFERENCES = Preferences(forbidden=frozenset(), boosted=frozenset())


@dataclass(frozen=True)
class ScoreBreakdown:
    total: float
    pair_penalties: dict[tuple[int, int], float]


@dataclass(frozen=True)
class Candidate:
    groups: tuple[tuple[int, ...], ...]
    score: float
    breakdown: ScoreBreakdown

    @property
    def all_members(self) -> Sequence[int]:
        return [pid for group in self.groups for pid in group]
