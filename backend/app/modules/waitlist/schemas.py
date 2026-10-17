"""Schemas de la lista de espera (15-waitlist; `WaitlistRequest` del YAML)."""

from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, EmailStr, StringConstraints

CORREO_MAX = 254


def _strip(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


def _lower_and_bounded(value: str) -> str:
    if len(value) > CORREO_MAX:
        raise ValueError(f"El correo no puede tener más de {CORREO_MAX} caracteres.")
    return value.lower()


def _no_nul(value: str) -> str:
    """Postgres no guarda `\\x00` en text: sin esto el insert falla y el envío daría 500 en vez de 422."""
    if "\x00" in value:
        raise ValueError("El texto tiene caracteres no permitidos.")
    return value


Correo = Annotated[EmailStr, BeforeValidator(_strip), AfterValidator(_lower_and_bounded)]
Nombre = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120), AfterValidator(_no_nul)]
Ciudad = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80), AfterValidator(_no_nul)]


class WaitlistRequest(BaseModel):
    """R1: los campos del formulario de la landing; JSON y urlencoded pasan por este mismo modelo (R2)."""

    model_config = ConfigDict(extra="ignore")

    tipo: Literal["comercio", "prestamista"]
    nombre: Nombre
    correo: Correo  # guardado en minúsculas
    ciudad: Ciudad
    website: str | None = None  # honeypot (R4): nunca se guarda


class WaitlistResult(BaseModel):
    status: Literal["created", "exists"]
