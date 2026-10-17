"""Scoring `v1`: score, grado, elegibilidad, cupo, fecha de desbloqueo y razones (05 R13–R19).

Función pura: los insumos (`MerchantFeatures`) los calcula 09-scoring desde `sales`; aquí no hay base de datos, red
ni `clock.now()`. La zona horaria (para `unlocks_at`) sale de `cfg.timezone`.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.core.config import Settings

POLICY_VERSION = "v1"
Grade = Literal["A", "B", "C", "D"]

GRADE_THRESHOLDS: tuple[tuple[int, Grade], ...] = ((750, "A"), (600, "B"), (450, "C"))
GRADE_FACTOR_BPS: dict[str, int] = {"A": 10_000, "B": 7_500, "C": 5_000, "D": 0}
WEIGHTS = {"vol": 3_500, "cons": 2_500, "days": 1_500, "ant": 1_000, "pay": 1_500}  # suman 10000
MAX_REASONS = 4


@dataclass(frozen=True)
class MerchantFeatures:
    """Insumos de R13 (montos en el asset de mercado; meses, semanas y días en `TIMEZONE`)."""

    as_of: datetime
    first_sale_at: datetime | None
    active_months: int
    avg_monthly_sales_3m: int
    consistency_bps: int
    active_days_bps: int
    months_on_platform: int
    loans_paid: int
    pct_increases: int

    def to_dict(self) -> dict[str, Any]:
        """JSON serializable (para `credit_assessments.features`): fechas en ISO 8601 UTC con `Z`."""
        return {
            "as_of": _iso(self.as_of),
            "first_sale_at": _iso(self.first_sale_at) if self.first_sale_at is not None else None,
            "active_months": self.active_months,
            "avg_monthly_sales_3m": self.avg_monthly_sales_3m,
            "consistency_bps": self.consistency_bps,
            "active_days_bps": self.active_days_bps,
            "months_on_platform": self.months_on_platform,
            "loans_paid": self.loans_paid,
            "pct_increases": self.pct_increases,
        }


@dataclass(frozen=True)
class Assessment:
    policy_version: str
    score: int
    grade: Grade
    eligible: bool
    unlocks_at: datetime | None
    cupo_total: int
    reasons: list[str]
    features: dict[str, Any]


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("datetime sin zona horaria")
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def grade_for(score: int) -> Grade:
    """R15: A ≥ 750, B ≥ 600, C ≥ 450, D el resto."""
    for threshold, grade in GRADE_THRESHOLDS:
        if score >= threshold:
            return grade
    return "D"


def components(features: MerchantFeatures, cfg: Settings) -> dict[str, int]:
    """R14: los cinco componentes en bps (0–10000)."""
    return {
        "vol": min(10_000, features.avg_monthly_sales_3m * 10_000 // cfg.score_volume_ref),
        "cons": features.consistency_bps,
        "days": features.active_days_bps,
        "ant": min(10_000, features.months_on_platform * 10_000 // 12),
        "pay": _clamp(5_000 + 2_500 * features.loans_paid - 1_000 * features.pct_increases, 0, 10_000),
    }


def score_of(parts: dict[str, int]) -> int:
    """R14: `(3500·vol + 2500·cons + 1500·days + 1000·ant + 1500·pay) // 100000` (0–1000)."""
    return sum(WEIGHTS[name] * parts[name] for name in WEIGHTS) // 100_000


def cupo_for(avg_monthly_sales: int, grade: Grade, eligible: bool, cfg: Settings) -> int:
    """R16: `avg × 1500 × 25 × factor // (10000 × 10 × 10000)` (un solo floor), al paso de montos y con tope."""
    if not eligible:
        return 0
    raw = avg_monthly_sales * 1_500 * 25 * GRADE_FACTOR_BPS[grade] // (10_000 * 10 * 10_000)
    return min(cfg.cupo_cap, raw - raw % cfg.amount_step)


def unlocks_at(features: MerchantFeatures, eligible: bool, cfg: Settings) -> datetime | None:
    """R17: 00:00 local del día 1 del mes `mes(as_of) + (MIN_MONTHS − active_months)`, en UTC."""
    if eligible or features.first_sale_at is None:
        return None
    tz = ZoneInfo(cfg.timezone)
    local = features.as_of.astimezone(tz)
    index = local.year * 12 + local.month - 1 + (cfg.min_months - features.active_months)
    return datetime(index // 12, index % 12 + 1, 1, tzinfo=tz).astimezone(UTC)


def _reasons(features: MerchantFeatures, parts: dict[str, int], eligible: bool, cfg: Settings) -> list[str]:
    """R18: máximo 4, en orden fijo."""
    reasons: list[str] = []
    if not eligible:
        if features.first_sale_at is None:
            reasons.append("Empieza a cobrar con tu QR de Trust para construir historial.")
        else:
            missing = cfg.min_months - features.active_months
            if missing == 1:
                reasons.append("Te falta 1 mes de ventas con el QR de Trust.")
            else:
                reasons.append(f"Te faltan {missing} meses de ventas con el QR de Trust.")
    reasons.append(
        "Buen volumen de ventas en los últimos 3 meses."
        if parts["vol"] >= 7_000
        else "Volumen de ventas bajo en los últimos 3 meses."
    )
    reasons.append(
        "Ventas constantes semana a semana." if parts["cons"] >= 7_500 else "Ventas irregulares entre semanas."
    )
    if features.loans_paid > 0:
        paid = features.loans_paid
        reasons.append("Has pagado 1 crédito con Trust." if paid == 1 else f"Has pagado {paid} créditos con Trust.")
    elif features.pct_increases > 0:
        reasons.append("Hubo meses por debajo del abono mínimo.")
    else:
        reasons.append("Sin historial de créditos: cuenta como neutro.")
    return reasons[:MAX_REASONS]


def assess(features: MerchantFeatures, cfg: Settings) -> Assessment:
    """R19. `cfg` es `core.config.get_settings()`; `cfg.scoring_policy` distinto de `v1` → `ValueError`."""
    if cfg.scoring_policy != POLICY_VERSION:
        raise ValueError(f"Política de scoring no soportada: {cfg.scoring_policy!r}")
    parts = components(features, cfg)
    score = score_of(parts)
    grade = grade_for(score)
    eligible = features.active_months >= cfg.min_months  # R16
    return Assessment(
        policy_version=POLICY_VERSION,
        score=score,
        grade=grade,
        eligible=eligible,
        unlocks_at=unlocks_at(features, eligible, cfg),
        cupo_total=cupo_for(features.avg_monthly_sales_3m, grade, eligible, cfg),
        reasons=_reasons(features, parts, eligible, cfg),
        features=features.to_dict(),
    )
