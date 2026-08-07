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


@dataclass(frozen=True)
class GenerationConfig:
    candidates: int = 200
    temperature: float = 1.0
    max_candidates: int = 600
    min_candidates: int = 50


DEFAULT_SCORING = ScoringConfig()
DEFAULT_GENERATION = GenerationConfig()
