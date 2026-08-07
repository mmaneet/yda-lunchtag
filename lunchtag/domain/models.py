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
