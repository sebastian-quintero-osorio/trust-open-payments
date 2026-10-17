"""Términos congelados de un crédito: cargo, total, mínimo mensual y rango del porcentaje (05 R6–R7)."""

from dataclasses import asdict, dataclass
from typing import Any

from app.core.config import Settings
from app.core.money import bps_of

PCT_FLOOR_BPS = 500  # piso absoluto de `pct_min_bps`
PCT_MIN_DELTA_BPS = 500  # cuánto puede bajar el porcentaje desde el inicial


@dataclass(frozen=True)
class Terms:
    principal: int
    fee_bps: int
    fee_amount: int
    total_repayable: int
    trust_share_bps: int
    term_months: int
    monthly_minimum: int
    pct_initial_bps: int
    pct_min_bps: int
    pct_max_bps: int

    def to_dict(self) -> dict[str, Any]:
        """Bloque `terms` del snapshot de `loans.terms` (JSON): montos como enteros en unidades mínimas del asset del
        crédito; envolverlos en Money para la API es de 11-loans."""
        return asdict(self)


def build_terms(
    principal: int,
    pct_bps: int,
    fee_bps: int,
    term_months: int,
    request_pct_bps: int | None,
    cfg: Settings,
) -> Terms:
    """R6. `cfg` es `core.config.get_settings()` (usa `trust_share_bps` y `reprice_max_delta_bps`)."""
    if principal <= 0:
        raise ValueError("El monto del crédito debe ser mayor que 0")
    if fee_bps < 0:
        raise ValueError("El cargo no puede ser negativo")
    if term_months < 1:
        raise ValueError("El plazo debe ser de al menos 1 mes")
    fee_amount = bps_of(principal, fee_bps)
    total_repayable = principal + fee_amount
    pct_max_bps = max(pct_bps, request_pct_bps) if request_pct_bps is not None else pct_bps + cfg.reprice_max_delta_bps
    return Terms(
        principal=principal,
        fee_bps=fee_bps,
        fee_amount=fee_amount,
        total_repayable=total_repayable,
        trust_share_bps=cfg.trust_share_bps,
        term_months=term_months,
        monthly_minimum=-(-total_repayable // term_months),  # techo entero
        pct_initial_bps=pct_bps,
        pct_min_bps=max(PCT_FLOOR_BPS, pct_bps - PCT_MIN_DELTA_BPS),
        pct_max_bps=pct_max_bps,
    )
