"""Schemas de scoring (09-scoring; `CreditProfile` y `MerchantCreditView` del YAML)."""

import uuid
from typing import Literal

from pydantic import BaseModel

from app.core.clock import UtcDateTime
from app.core.money import MoneySchema
from app.modules.users.schemas import MerchantRef

GradeOut = Literal["A", "B", "C", "D"]


class CreditProfileOut(BaseModel):
    assessment_id: uuid.UUID
    policy_version: str
    computed_at: UtcDateTime
    score: int
    grade: GradeOut
    eligible: bool
    unlocks_at: UtcDateTime | None
    active_months: int
    cupo_total: MoneySchema
    cupo_usado: MoneySchema
    cupo_disponible: MoneySchema
    reasons: list[str]


class SalesBand(BaseModel):
    """Franja de ventas mensuales promedio (R6); `max` null en la franja más alta."""

    min: MoneySchema
    max: MoneySchema | None


class MerchantCreditView(BaseModel):
    """Lo que ve el prestamista: sin cifras exactas de ventas ni `features`."""

    merchant: MerchantRef
    score: int
    grade: GradeOut
    eligible: bool
    active_months: int
    sales_band: SalesBand
    cupo_total: MoneySchema
    cupo_disponible: MoneySchema
    computed_at: UtcDateTime
