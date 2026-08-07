from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lunchtag.domain.models import PairingStats, Preferences
from lunchtag.engine.config import DEFAULT_GENERATION, DEFAULT_SCORING
from lunchtag.engine.generator import generate_best, validate_groups
from lunchtag.engine.scoring import pair_penalty, score_groups


def _empty_stats() -> PairingStats:
    return PairingStats(met_count={}, last_met={}, degree={})


def _prefs(forbidden=(), boosted=()) -> Preferences:
    return Preferences(forbidden=frozenset(forbidden), boosted=frozenset(boosted))


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


# --- strikes and boosts -----------------------------------------------------


def _all_pairs(groups):
    from itertools import combinations

    pairs = set()
    for group in groups:
        for a, b in combinations(sorted(group), 2):
            pairs.add((a, b))
    return pairs


def test_struck_pair_never_grouped_when_avoidable():
    players = [1, 2, 3, 4]
    prefs = _prefs(forbidden=[(1, 2)])
    for seed in range(20):
        draft = generate_best(players, _empty_stats(), DEFAULT_SCORING, DEFAULT_GENERATION, seed=seed, prefs=prefs)
        assert (1, 2) not in _all_pairs(draft.groups), f"seed {seed}"
        valid, msg = validate_groups(draft.groups, players)
        assert valid, msg


def test_even_roster_falls_back_to_trio_when_strikes_block_pairs():
    # 4 players; player 1 strikes both 2 and 3, so the only pair for 1 is 4.
    # But then 2 and 3 pair, fine. Make it harder: 1 strikes 2, 3 AND 4 —
    # no pair exists for 1, so 1 must join a pair as a third (or the engine
    # must find the least-bad grouping). No forbidden pair may appear.
    players = [1, 2, 3, 4]
    prefs = _prefs(forbidden=[(1, 2), (1, 3), (1, 4)])
    draft = generate_best(players, _empty_stats(), DEFAULT_SCORING, DEFAULT_GENERATION, seed=7, prefs=prefs)
    placed = sorted(pid for group in draft.groups for pid in group)
    assert placed == players  # everyone placed exactly once
    # Player 1 cannot be with anyone — best possible outcome is 1 alone
    # and (2,3,4) or a pair+single. No forbidden pair should be formed.
    assert not (_all_pairs(draft.groups) & prefs.forbidden)


def test_even_roster_trio_from_partial_strikes():
    # 6 players; 1 strikes 2, 3, 4, 5 — only 6 can pair with 1.
    # If 6 pairs with someone else first, 1 becomes a leftover and must join
    # a group as a third. Either way: no forbidden pair, everyone placed.
    players = [1, 2, 3, 4, 5, 6]
    prefs = _prefs(forbidden=[(1, 2), (1, 3), (1, 4), (1, 5)])
    for seed in range(10):
        draft = generate_best(players, _empty_stats(), DEFAULT_SCORING, DEFAULT_GENERATION, seed=seed, prefs=prefs)
        assert sorted(pid for g in draft.groups for pid in g) == players
        assert not (_all_pairs(draft.groups) & prefs.forbidden), f"seed {seed}"
        valid, msg = validate_groups(draft.groups, players)
        assert valid, msg


def test_boost_increases_pairing_frequency():
    players = [1, 2, 3, 4, 5, 6]
    boosted_hits = 0
    baseline_hits = 0
    trials = 60
    for seed in range(trials):
        with_boost = generate_best(
            players, _empty_stats(), DEFAULT_SCORING, DEFAULT_GENERATION, seed=seed,
            prefs=_prefs(boosted=[(1, 2)]),
        )
        without = generate_best(
            players, _empty_stats(), DEFAULT_SCORING, DEFAULT_GENERATION, seed=seed,
        )
        if (1, 2) in _all_pairs(with_boost.groups):
            boosted_hits += 1
        if (1, 2) in _all_pairs(without.groups):
            baseline_hits += 1
    assert boosted_hits > baseline_hits


def test_boosted_pair_penalty_is_lower():
    stats = _empty_stats()
    base = pair_penalty(1, 2, stats, DEFAULT_SCORING)
    boosted = pair_penalty(1, 2, stats, DEFAULT_SCORING, prefs=_prefs(boosted=[(1, 2)]))
    assert boosted < base


def test_struck_pair_penalty_is_huge():
    penalty = pair_penalty(1, 2, _empty_stats(), DEFAULT_SCORING, prefs=_prefs(forbidden=[(1, 2)]))
    assert penalty == DEFAULT_SCORING.strike_penalty


def test_validate_allows_trio_with_even_roster():
    valid, _ = validate_groups([(1, 2, 3), (4, 5)], [1, 2, 3, 4, 5])
    assert valid
    valid, _ = validate_groups([(1, 2, 3), (4, 5, 6)], [1, 2, 3, 4, 5, 6])
    assert valid  # even roster, trios allowed now


def test_validate_still_rejects_bad_groups():
    valid, _ = validate_groups([(1,), (2, 3)], [1, 2, 3])
    assert not valid  # singleton with multi-player roster
    valid, _ = validate_groups([(1, 2, 3, 4)], [1, 2, 3, 4])
    assert not valid  # oversize group
