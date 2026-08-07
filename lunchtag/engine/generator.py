from __future__ import annotations

import random
from dataclasses import dataclass
from itertools import combinations
from math import exp
from typing import Iterable, Sequence

from lunchtag.domain.models import Candidate, EMPTY_PREFERENCES, PairingStats, Preferences
from lunchtag.engine.config import GenerationConfig, ScoringConfig
from lunchtag.engine.scoring import score_groups, pair_penalty


def _forbidden(a: int, b: int, prefs: Preferences) -> bool:
    return ((a, b) if a < b else (b, a)) in prefs.forbidden


@dataclass(frozen=True)
class GeneratedDraft:
    groups: tuple[tuple[int, ...], ...]
    score: float
    seed: int


def _softmax_sample(rng: random.Random, items: list[int], scores: list[float], temperature: float) -> int:
    if len(items) == 1:
        return items[0]
    if temperature <= 0:
        best_idx = min(range(len(items)), key=lambda i: scores[i])
        return items[best_idx]
    weights = [exp(-score / temperature) for score in scores]
    total = sum(weights)
    if total <= 0:
        return rng.choice(items)
    r = rng.random() * total
    cumulative = 0.0
    for item, weight in zip(items, weights):
        cumulative += weight
        if r <= cumulative:
            return item
    return items[-1]


def _risk_score(player: int, others: Sequence[int], stats: PairingStats, config: ScoringConfig) -> float:
    if not others:
        return 0.0
    penalties = [pair_penalty(player, other, stats, config) for other in others]
    return sum(penalties) / len(penalties)


def _pick_next_player(rng: random.Random, unassigned: list[int], stats: PairingStats, config: ScoringConfig) -> int:
    if len(unassigned) == 1:
        return unassigned[0]
    risks = []
    for player in unassigned:
        others = [p for p in unassigned if p != player]
        risks.append(_risk_score(player, others, stats, config))
    max_risk = max(risks)
    candidates = [player for player, risk in zip(unassigned, risks) if risk == max_risk]
    return rng.choice(candidates)


def _build_candidate(
    players: Sequence[int],
    stats: PairingStats,
    scoring: ScoringConfig,
    generation: GenerationConfig,
    rng: random.Random,
    prefs: Preferences = EMPTY_PREFERENCES,
) -> tuple[tuple[int, ...], ...]:
    unassigned = list(players)
    groups: list[list[int]] = []
    leftovers: list[int] = []

    while len(unassigned) >= 2:
        a = _pick_next_player(rng, unassigned, stats, scoring)
        unassigned.remove(a)
        partner_candidates = [b for b in unassigned if not _forbidden(a, b, prefs)]
        if not partner_candidates:
            # Every remaining player is struck — defer; placed into a trio below.
            leftovers.append(a)
            continue
        penalties = [pair_penalty(a, b, stats, scoring, prefs=prefs) for b in partner_candidates]
        b = _softmax_sample(rng, partner_candidates, penalties, generation.temperature)
        unassigned.remove(b)
        groups.append([a, b])

    leftovers.extend(unassigned)

    # First, pair leftovers with each other where allowed.
    while len(leftovers) >= 2:
        a = leftovers[0]
        allowed = [b for b in leftovers[1:] if not _forbidden(a, b, prefs)]
        if not allowed:
            break
        penalties = [pair_penalty(a, b, stats, scoring, prefs=prefs) for b in allowed]
        b = _softmax_sample(rng, allowed, penalties, generation.temperature)
        leftovers.remove(a)
        leftovers.remove(b)
        groups.append([a, b])

    # Remaining singles join an existing pair as a third — the trio fallback
    # for odd rosters and for strike graphs with no perfect matching.
    for leftover in leftovers:
        open_groups = [
            i
            for i, group in enumerate(groups)
            if len(group) < 3 and not any(_forbidden(leftover, m, prefs) for m in group)
        ]
        if not open_groups:
            # Pathological strike graph: a singleton group, which scoring
            # penalizes so a better candidate wins whenever one exists.
            groups.append([leftover])
            continue
        incremental = [
            sum(pair_penalty(leftover, m, stats, scoring, prefs=prefs) for m in groups[i])
            for i in open_groups
        ]
        idx = _softmax_sample(rng, open_groups, incremental, generation.temperature)
        groups[idx].append(leftover)

    return tuple(tuple(group) for group in groups)


def generate_best(
    players: Sequence[int],
    stats: PairingStats,
    scoring: ScoringConfig,
    generation: GenerationConfig,
    seed: int,
    prefs: Preferences = EMPTY_PREFERENCES,
) -> GeneratedDraft:
    if not players:
        return GeneratedDraft(groups=tuple(), score=0.0, seed=seed)

    rng = random.Random(seed)
    target_candidates = generation.candidates + max(0, len(players) - 6) * 10
    k = max(generation.min_candidates, min(generation.max_candidates, target_candidates))
    candidates: list[Candidate] = []
    for i in range(k):
        local_rng = random.Random(rng.randint(0, 2**31 - 1))
        groups = _build_candidate(players, stats, scoring, generation, local_rng, prefs=prefs)
        breakdown = score_groups(groups, stats, scoring, prefs=prefs)
        candidates.append(Candidate(groups=groups, score=breakdown.total, breakdown=breakdown))

    best_score = min(c.score for c in candidates)
    best_candidates = [c for c in candidates if c.score == best_score]
    winner = rng.choice(best_candidates)
    return GeneratedDraft(groups=winner.groups, score=winner.score, seed=seed)


def validate_groups(groups: Iterable[Iterable[int]], active_ids: Sequence[int]) -> tuple[bool, str]:
    # Trios are allowed regardless of roster parity: strikes can make a
    # perfect 2-person matching impossible even with an even player count.
    assigned = [pid for group in groups for pid in group]
    if sorted(assigned) != sorted(active_ids):
        return False, "Each active player must appear exactly once."
    group_sizes = [len(tuple(group)) for group in groups]
    if any(size < 2 for size in group_sizes) and len(active_ids) > 1:
        return False, "Groups must have size 2 or 3."
    if any(size > 3 for size in group_sizes):
        return False, "Groups cannot exceed 3 members."
    return True, "OK"
