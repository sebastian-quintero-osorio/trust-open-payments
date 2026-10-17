"""Tabla `credit_assessments` (01-data-model; dueño 09-scoring). Inmutable: sin `updated_at`."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, CreatedAtMixin, IdMixin, JsonDict, JsonList, bigint, smallint, sql_in


class Grade(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class CreditAssessment(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "credit_assessments"
    __table_args__ = (
        CheckConstraint(sql_in("grade", Grade), name="grade_enum"),
        CheckConstraint("score BETWEEN 0 AND 1000", name="score_range"),
    )

    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    policy_version: Mapped[str]
    score: Mapped[int]
    grade: Mapped[str]
    eligible: Mapped[bool]
    unlocks_at: Mapped[datetime | None]
    cupo_total: Mapped[bigint]
    asset_code: Mapped[str]
    asset_scale: Mapped[smallint]
    features: Mapped[JsonDict]
    reasons: Mapped[JsonList]
    computed_at: Mapped[datetime]


Index(
    "ix_credit_assessments_merchant_id_computed_at",
    CreditAssessment.merchant_id,
    CreditAssessment.computed_at.desc(),
)
