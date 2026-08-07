from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lunchtag.domain.models import PairingStats
from lunchtag.engine.config import DEFAULT_GENERATION, DEFAULT_SCORING
from lunchtag.engine.generator import generate_best, validate_groups
from lunchtag.engine.scoring import pair_penalty, score_groups


def test_scoring_penalizes_repeats():
    stats = PairingStats(met_count={(1, 2): 3}, last_met={}, degree={1: 3, 2: 3})
    base = pair_penalty(1, 2, stats, DEFAULT_SCORING)
    assert base > 0


def test_scoring_recency_penalty():
    now = datetime.now(timezone.utc)
    recent = now - timedelta(days=2)
    stats = PairingStats(met_count={(1, 2): 1}, last_met={(1, 2): recent}, degree={1: 1, 2: 1})
    penalty = pair_penalty(1, 2, stats, DEFAULT_SCORING, now=now)
    assert penalty > 0


def test_generator_validity_even():
    players = list(range(1, 9))
    stats = PairingStats(met_count={}, last_met={}, degree={})
    draft = generate_best(players, stats, DEFAULT_SCORING, DEFAULT_GENERATION, seed=123)
    valid, _ = validate_groups(draft.groups, players)
    assert valid


def test_generator_validity_odd():
    players = list(range(1, 7))
    stats = PairingStats(met_count={}, last_met={}, degree={})
    draft = generate_best(players, stats, DEFAULT_SCORING, DEFAULT_GENERATION, seed=321)
    valid, _ = validate_groups(draft.groups, players)
    assert valid


def test_generator_handles_dense_history():
    players = list(range(1, 9))
    met_count = {}
    degree = {pid: 0 for pid in players}
    now = datetime.now(timezone.utc) - timedelta(days=1)
    for i in range(len(players)):
        for j in range(i + 1, len(players)):
            met_count[(players[i], players[j])] = 5
            degree[players[i]] += 5
            degree[players[j]] += 5
    stats = PairingStats(met_count=met_count, last_met={}, degree=degree)
    draft = generate_best(players, stats, DEFAULT_SCORING, DEFAULT_GENERATION, seed=456)
    valid, _ = validate_groups(draft.groups, players)
    assert valid
    breakdown = score_groups(draft.groups, stats, DEFAULT_SCORING)
    assert breakdown.total > 0
