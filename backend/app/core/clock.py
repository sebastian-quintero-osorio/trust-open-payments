"""Reloj de la app: `now()` en UTC (congelable en tests) y cálculos de día local en `TIMEZONE`."""

import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from pydantic import AfterValidator, BeforeValidator

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
    """Inicio (inclusivo) y fin (exclusivo) del día local `day`, en UTC.

    Para `date.max` (`9999-12-31`, fecha válida en un filtro `to=`) el día siguiente no existe: el fin es
    `datetime.max` en UTC en vez de desbordar con un 500.
    """
    tz = local_tz()
    start = datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)
    if day == date.max:
        return start, datetime.max.replace(tzinfo=UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
    return start, end


def format_hora(dt: datetime) -> str:
    """Hora local en 12 h sin cero inicial: `10:42 a. m.`, `12:05 p. m.`."""
    local = to_local(dt)
    hour = local.hour % 12 or 12
    suffix = "a. m." if local.hour < 12 else "p. m."
    return f"{hour}:{local.minute:02d} {suffix}"


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("datetime sin zona horaria")
    try:
        return dt.astimezone(UTC)
    except OverflowError:  # `0001-01-01T00:00:00+14:00` o `9999-12-31T23:59:59-14:00`: en UTC cae fuera de 1..9999
        raise ValueError("La fecha está fuera del rango válido.") from None


UtcDateTime = Annotated[datetime, AfterValidator(_as_utc)]
"""Se normaliza a UTC y Pydantic lo serializa con `Z`. En la entrada (query `since`, `occurred_at` del webhook) una
fecha sin zona o que en UTC desborda es un `ValueError` → 422 / `invalid_payload`, nunca un 500."""


_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")  # `\d` aceptaría dígitos no ASCII


def _iso_date_only(value: Any) -> Any:
    if isinstance(value, str) and not _ISO_DATE.fullmatch(value):
        raise ValueError("La fecha debe tener el formato AAAA-MM-DD.")
    return value


LocalDate = Annotated[date, BeforeValidator(_iso_date_only)]
"""Fecha local `YYYY-MM-DD` de los filtros `from`/`to`: sin timestamps ni fechas con hora (las acepta el modo laxo)."""
