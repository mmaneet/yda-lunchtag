from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import combinations
from math import exp
from typing import Iterable

from lunchtag.domain.models import PairingStats, ScoreBreakdown
from lunchtag.engine.config import ScoringConfig


@dataclass(frozen=True)
class PairPenalty:
    key: tuple[int, int]
    value: float


def _pair_key(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def _days_since(now: datetime, then: datetime) -> float:
    delta = now - then
    return delta.total_seconds() / 86400.0


def pair_penalty(
    a: int,
    b: int,
    stats: PairingStats,
    config: ScoringConfig,
    now: datetime | None = None,
) -> float:
    if now is None:
        now = datetime.now(timezone.utc)
    key = _pair_key(a, b)
    met = stats.met_count.get(key, 0)
    count_penalty = config.count_weight * (met ** config.count_exponent) if met > 0 else 0.0

    recency_penalty = 0.0
    last = stats.last_met.get(key)
    if last is not None and config.recency_window_days > 0:
        days = _days_since(now, last)
        if days <= config.recency_window_days:
            recency_penalty = config.recency_weight * (1.0 - (days / config.recency_window_days))

    fairness_penalty = 0.0
    if config.fairness_weight > 0.0 and stats.degree:
        max_degree = max(stats.degree.values()) if stats.degree else 0
        if max_degree > 0:
            degree_a = stats.degree.get(a, 0)
            degree_b = stats.degree.get(b, 0)
            fairness_penalty = config.fairness_weight * ((degree_a + degree_b) / (2 * max_degree))

    return count_penalty + recency_penalty + fairness_penalty


def score_groups(
    groups: Iterable[Iterable[int]],
    stats: PairingStats,
    config: ScoringConfig,
    now: datetime | None = None,
) -> ScoreBreakdown:
    if now is None:
        now = datetime.now(timezone.utc)
    pair_penalties: dict[tuple[int, int], float] = {}
    total = 0.0
    for group in groups:
        members = list(group)
        for a, b in combinations(members, 2):
            key = _pair_key(a, b)
            penalty = pair_penalty(a, b, stats, config, now=now)
            pair_penalties[key] = penalty
            total += penalty
        if len(members) == 3:
            total += config.trio_penalty
    return ScoreBreakdown(total=total, pair_penalties=pair_penalties)


def repeat_edges(pair_penalties: dict[tuple[int, int], float], stats: PairingStats) -> list[tuple[int, int]]:
    repeats = []
    for key in pair_penalties:
        if stats.met_count.get(key, 0) > 0:
            repeats.append(key)
    return repeats
