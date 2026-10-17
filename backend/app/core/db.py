"""SQLAlchemy 2.0 síncrono: engine, sesiones, `Base` y columnas comunes (01-data-model, "Convenciones")."""

import uuid
from collections.abc import Iterable, Iterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import BigInteger, DateTime, MetaData, SmallInteger, Text, Uuid, create_engine, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.core import clock
from app.core.config import get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


# Tipos de columna: `int` es INTEGER; los montos usan `bigint` y las escalas `smallint`.
bigint = Annotated[int, mapped_column(BigInteger)]
smallint = Annotated[int, mapped_column(SmallInteger)]
JsonDict = dict[str, Any]
JsonList = list[Any]


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        uuid.UUID: Uuid(),
        datetime: DateTime(timezone=True),
        str: Text(),
        JsonDict: JSONB(),
        JsonList: JSONB(),
    }


# Las sesiones de Postgres van en UTC: los timestamptz vuelven con tzinfo UTC.
engine = create_engine(
    get_settings().database_url,
    pool_pre_ping=True,
    connect_args={"options": "-c timezone=UTC"},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DbSession = Annotated[Session, Depends(get_db)]


# ── Columnas comunes ──────────────────────────────────────────────────────────


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, sort_order=-1)


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(default=clock.now, server_default=func.now())


class TimestampsMixin(CreatedAtMixin):
    updated_at: Mapped[datetime] = mapped_column(default=clock.now, onupdate=clock.now, server_default=func.now())


def sql_in(column: str, values: Iterable[str]) -> str:
    """SQL `column IN ('a', 'b')` para CHECKs de enums guardados como `text` e índices parciales."""
    return f"{column} IN ({', '.join(f"'{v}'" for v in values)})"
