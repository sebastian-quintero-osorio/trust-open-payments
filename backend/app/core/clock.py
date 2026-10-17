"""Reloj de la app: `now()` en UTC (congelable en tests) y cálculos de día local en `TIMEZONE`."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import AfterValidator

from app.core.config import get_settings

_frozen: datetime | None = None


def now() -> datetime:
    """Hora actual en UTC; devuelve la congelada si hay una."""
    return _frozen if _frozen is not None else datetime.now(UTC)


def freeze(dt: datetime) -> None:
    if dt.tzinfo is None:
        raise ValueError("freeze() necesita un datetime con zona horaria")
    global _frozen
    _frozen = dt.astimezone(UTC)


def unfreeze() -> None:
    global _frozen
    _frozen = None


def local_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def to_local(dt: datetime) -> datetime:
    return dt.astimezone(local_tz())


def local_date(dt: datetime) -> date:
    return to_local(dt).date()


def today_local() -> date:
    return local_date(now())


def local_day_bounds(day: date) -> tuple[datetime, datetime]:
    """Inicio (inclusivo) y fin (exclusivo) del día local `day`, en UTC."""
    tz = local_tz()
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    return start.astimezone(UTC), end.astimezone(UTC)


def format_hora(dt: datetime) -> str:
    """Hora local en 12 h sin cero inicial: `10:42 a. m.`, `12:05 p. m.`."""
    local = to_local(dt)
    hour = local.hour % 12 or 12
    suffix = "a. m." if local.hour < 12 else "p. m."
    return f"{hour}:{local.minute:02d} {suffix}"


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("datetime sin zona horaria")
    return dt.astimezone(UTC)


UtcDateTime = Annotated[datetime, AfterValidator(_as_utc)]
"""Tipo para schemas de respuesta: se normaliza a UTC y Pydantic lo serializa con `Z`."""
