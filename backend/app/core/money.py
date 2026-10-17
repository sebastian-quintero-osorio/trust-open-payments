"""Dinero en enteros (unidades mínimas) con su asset, como el `Money` de Open Payments (00-core, "Dinero")."""

import re
from dataclasses import dataclass
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import get_settings
from app.core.errors import AppError

VALUE_PATTERN = r"^(0|[1-9][0-9]*)$"
BIGINT_MAX = 2**63 - 1  # tope de las columnas de dinero (`bigint`): un `value` mayor es 422, nunca 500
_VALUE_RE = re.compile(VALUE_PATTERN)


@dataclass(frozen=True)
class Money:
    value: int
    asset_code: str
    asset_scale: int

    def __post_init__(self) -> None:
        # Nunca float, Decimal ni bool (`True` es un int en Python).
        if type(self.value) is not int or type(self.asset_scale) is not int:
            raise TypeError("Money.value y asset_scale deben ser int")

    def to_api(self) -> dict[str, Any]:
        return {"value": str(self.value), "asset_code": self.asset_code, "asset_scale": self.asset_scale}

    @classmethod
    def from_api(cls, data: Any, field: str = "amount") -> Self:
        """Lee un `Money` de la API. Rechaza números (int o float), negativos, decimales y texto no numérico.

        Solo valida la forma (patrón de `value`, `asset_code` y `asset_scale`); exigir el asset de mercado
        (`422 asset_mismatch`) es de cada service, con `require_market_asset`.
        """
        if not isinstance(data, dict):
            raise _invalid(field, "Debe ser un objeto Money.")
        value, code, scale = data.get("value"), data.get("asset_code"), data.get("asset_scale")
        if not isinstance(value, str) or not _VALUE_RE.fullmatch(value):
            raise _invalid(f"{field}.value", "Debe ser un entero no negativo en texto, en unidades mínimas.")
        if not isinstance(code, str) or not code.strip():
            raise _invalid(f"{field}.asset_code", "Falta el código del asset.")
        if isinstance(scale, bool) or not isinstance(scale, int) or not 0 <= scale <= 255:
            raise _invalid(f"{field}.asset_scale", "La escala debe ser un entero entre 0 y 255.")
        return cls(int(value), code, scale)

    def same_asset(self, other: "Money") -> bool:
        return (self.asset_code, self.asset_scale) == (other.asset_code, other.asset_scale)


def _invalid(field: str, message: str) -> AppError:
    return AppError(422, "validation_error", details={"fields": [{"field": field, "message": message}]})


def market_money(value: int) -> Money:
    settings = get_settings()
    return Money(value, settings.market_asset_code, settings.market_asset_scale)


def require_market_asset(money: Money) -> Money:
    """422 `asset_mismatch` si el monto no está en el asset de mercado (código y escala)."""
    settings = get_settings()
    if (money.asset_code, money.asset_scale) != (settings.market_asset_code, settings.market_asset_scale):
        raise AppError(
            422,
            "asset_mismatch",
            f"El monto debe estar en {settings.market_asset_code} con escala {settings.market_asset_scale}.",
            {"expected": {"asset_code": settings.market_asset_code, "asset_scale": settings.market_asset_scale}},
        )
    return money


def bps_of(amount: int, bps: int) -> int:
    """Porcentaje en puntos básicos, redondeado hacia abajo: `bps_of(4_800_000, 1500) == 720_000`."""
    return amount * bps // 10_000


def format_es(m: Money) -> str:
    """Solo para avisos: `$48.000`, `$130,90`, `$1.590.000` (decimales solo si no son cero)."""
    sign = "-" if m.value < 0 else ""
    units, cents = divmod(abs(m.value), 10**m.asset_scale)
    text = f"{units:,}".replace(",", ".")
    if cents:
        text += "," + str(cents).zfill(m.asset_scale)
    return f"{sign}${text}"


class MoneySchema(BaseModel):
    """Objeto `Money` de la API (entrada y salida). Estricto: sin coerciones (`"2"`, `true` o `2.0` no son escala)."""

    model_config = ConfigDict(strict=True)

    value: str = Field(pattern=VALUE_PATTERN, examples=["4800000"])
    asset_code: str = Field(min_length=1, examples=["COP"])
    asset_scale: int = Field(ge=0, le=255, examples=[2])

    @classmethod
    def of(cls, m: Money) -> Self:
        return cls(**m.to_api())

    def to_money(self) -> Money:
        return Money(int(self.value), self.asset_code, self.asset_scale)
