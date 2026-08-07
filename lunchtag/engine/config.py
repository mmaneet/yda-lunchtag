from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScoringConfig:
    count_weight: float = 1.0
    count_exponent: float = 1.4
    recency_weight: float = 1.5
    recency_window_days: int = 30
    fairness_weight: float = 0.6
    trio_penalty: float = 0.3
    # Strikes: a forbidden pair grouped together costs this much — dominates
    # every other signal, so it only happens when no valid partition exists.
    strike_penalty: float = 1000.0
    # Boosts: subtracted from a boosted pair's penalty. At temperature 1.0,
    # exp(0.7) ~= 2, so a boost roughly doubles the pair's selection odds.
    boost_bonus: float = 0.7
    # A group of one — worse than any trio, better than a forbidden pair.
    singleton_penalty: float = 500.0


@dataclass(frozen=True)
class GenerationConfig:
    candidates: int = 200
    temperature: float = 1.0
    max_candidates: int = 600
    min_candidates: int = 50


DEFAULT_SCORING = ScoringConfig()
DEFAULT_GENERATION = GenerationConfig()
