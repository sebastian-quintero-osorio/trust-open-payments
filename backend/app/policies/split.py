"""División de una venta en abono, parte de Trust, neto del prestamista y monto del comercio (05 R1–R5).

Función pura: sin base de datos, sin red y sin reloj. Todo en enteros (unidades mínimas y bps).
"""

from dataclasses import dataclass
from typing import Literal

from app.core.money import bps_of

SplitPolicy = Literal["prorated", "literal"]
POLICIES: tuple[SplitPolicy, ...] = ("prorated", "literal")


@dataclass(frozen=True)
class SplitLoanState:
    """Lo que la división necesita del crédito `active` (positional, como en 00-core)."""

    pct_bps: int
    total_repayable: int
    allocated_total: int
    fee_amount: int
    trust_share_bps: int


@dataclass(frozen=True)
class SaleSplit:
    abono: int
    trust_cut: int
    lender_net: int
    merchant_amount: int
    allocated_after: int
    paid_off: bool


def _prorated(repaid: int, loan: SplitLoanState) -> int:
    """R3: `T(r) = r × fee_amount × trust_share_bps // (total_repayable × 10000)` (un solo floor)."""
    return repaid * loan.fee_amount * loan.trust_share_bps // (loan.total_repayable * 10_000)


def _literal(repaid: int, loan: SplitLoanState) -> int:
    """R4: `min(bps_of(r, trust_share_bps), fee_amount)`: % de cada abono, con tope total en el cargo."""
    return min(bps_of(repaid, loan.trust_share_bps), loan.fee_amount)


def split_sale(amount: int, loan: SplitLoanState | None, policy: SplitPolicy = "prorated") -> SaleSplit:
    """Divide una venta de `amount` (unidades mínimas). `loan=None` si el negocio no tiene crédito `active` (R1)."""
    if policy not in POLICIES:
        raise ValueError(f"Política de parte de Trust desconocida: {policy!r}")
    if amount < 0:
        raise ValueError("El monto de la venta no puede ser negativo")
    if loan is None:
        return SaleSplit(abono=0, trust_cut=0, lender_net=0, merchant_amount=amount, allocated_after=0, paid_off=False)
    if loan.total_repayable <= 0 or not 0 <= loan.allocated_total <= loan.total_repayable:
        raise ValueError("Estado del crédito inválido para dividir la venta")

    # R2: el abono nunca pasa lo que falta por pagar.
    abono = min(bps_of(amount, loan.pct_bps), loan.total_repayable - loan.allocated_total)
    allocated_after = loan.allocated_total + abono
    cumulative = _prorated if policy == "prorated" else _literal
    trust_cut = cumulative(allocated_after, loan) - cumulative(loan.allocated_total, loan)  # R3 / R4
    return SaleSplit(
        abono=abono,
        trust_cut=trust_cut,
        lender_net=abono - trust_cut,  # R5
        merchant_amount=amount - abono,
        allocated_after=allocated_after,
        paid_off=allocated_after == loan.total_repayable,
    )
