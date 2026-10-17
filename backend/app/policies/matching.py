"""Cruce de una solicitud con una oferta: monto propuesto dentro del rango común y del cupo (05 R8–R9)."""

from dataclasses import dataclass
from typing import Protocol


class AmountRange(Protocol):
    """Cualquier objeto con `min_amount`, `max_amount` y `pct_bps` (filas ORM, schemas o uno virtual)."""

    @property
    def min_amount(self) -> int: ...

    @property
    def max_amount(self) -> int: ...

    @property
    def pct_bps(self) -> int: ...


@dataclass(frozen=True)
class Proposal:
    amount: int
    overlap_min: int
    overlap_max: int
    pct_bps: int


def fits(request: AmountRange, offer: AmountRange, cupo_disponible: int, step: int) -> Proposal | None:
    """`None` si los rangos no se cruzan, si el mínimo común pasa el cupo o si la oferta cobra más % que la
    solicitud (R8). Si encaja, el punto medio redondeado hacia abajo al `step`, dentro del cruce y del cupo (R9)."""
    if step <= 0:
        raise ValueError("El paso de montos debe ser mayor que 0")
    overlap_min = max(request.min_amount, offer.min_amount)
    overlap_max = min(request.max_amount, offer.max_amount)
    if overlap_min > overlap_max or overlap_min > cupo_disponible or offer.pct_bps > request.pct_bps:
        return None
    mid = (overlap_min + overlap_max) // 2
    amount = min(max(mid - mid % step, overlap_min), overlap_max, cupo_disponible)
    return Proposal(amount=amount, overlap_min=overlap_min, overlap_max=overlap_max, pct_bps=offer.pct_bps)
