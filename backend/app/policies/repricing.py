"""Revisión de periodo: sube, baja o mantiene el porcentaje según lo abonado frente al mínimo (05 R10–R12)."""

from dataclasses import dataclass
from typing import Literal

ReviewReason = Literal["missed_minimum", "ahead_of_schedule", "unchanged"]


@dataclass(frozen=True)
class ReviewDecision:
    new_pct_bps: int
    reason: ReviewReason
    target_amount: int
    missed: bool


def review_period(
    pct_bps: int,
    pct_min_bps: int,
    pct_max_bps: int,
    monthly_minimum: int,
    balance_at_period_start: int,
    allocated_in_period: int,
    step_bps: int,
) -> ReviewDecision:
    if balance_at_period_start == 0:  # R10: ya no debía nada al empezar el periodo
        return ReviewDecision(pct_bps, "unchanged", 0, False)
    target = min(monthly_minimum, balance_at_period_start)
    if allocated_in_period < target:  # R11: por debajo del mínimo
        new = min(pct_bps + step_bps, pct_max_bps)
        return ReviewDecision(new, "missed_minimum" if new != pct_bps else "unchanged", target, True)
    if allocated_in_period * 4 >= target * 5:  # R12: ≥ 125 % del objetivo
        new = max(pct_bps - step_bps, pct_min_bps)
        return ReviewDecision(new, "ahead_of_schedule" if new != pct_bps else "unchanged", target, False)
    return ReviewDecision(pct_bps, "unchanged", target, False)
