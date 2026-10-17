"""Cuerpos de los POST de `contracts/op-service.openapi.yaml` (independientes de `app`).

Como un decoder JSON de Go: los campos de más se ignoran; tipos equivocados, faltantes o longitudes fuera de rango
son `400 validation_error`. Los montos son estrictos (`asset_scale` entero de verdad, `value` entero en texto).
"""

import uuid

from pydantic import BaseModel, ConfigDict, Field

VALUE_PATTERN = r"^(0|[1-9][0-9]*)$"
POSITIVE_VALUE_PATTERN = r"^[1-9][0-9]*$"


class _Body(BaseModel):
    model_config = ConfigDict(extra="ignore")


class PositiveAmount(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    value: str = Field(pattern=POSITIVE_VALUE_PATTERN)
    asset_code: str
    asset_scale: int = Field(ge=0, le=255)


# `description: str = None`: sin el campo queda en None, pero un `null` explícito no es un string → 400.


class CreateCollectionAccountRequest(_Body):
    external_ref: str = Field(max_length=200)
    display_name: str = Field(max_length=100)
    asset_code: str


class CreateTransferRequest(_Body):
    collection_account_id: uuid.UUID
    receiver_wallet_address: str
    debit_amount: PositiveAmount
    description: str = Field(default=None, max_length=200)
    external_ref: str = Field(max_length=200)


class CreatePaymentRequest(_Body):
    sender_wallet_address: str
    receiver_wallet_address: str
    receive_amount: PositiveAmount
    description: str = Field(default=None, max_length=200)
    external_ref: str = Field(max_length=200)
    return_url: str


class CreateSimQrPaymentRequest(_Body):
    collection_account_id: uuid.UUID
    amount: PositiveAmount
    payer_bank: str = Field(default=None, max_length=60)


# ── Controles del mock (/mock/*, no son parte del contrato) ───────────────────


class WalletOverride(_Body):
    url: str
    asset_code: str | None = None
    asset_scale: int | None = Field(default=None, ge=0, le=255)
    public_name: str | None = None
    exists: bool = True


class FaultRequest(_Body):
    method: str
    path: str = Field(description="Ruta exacta (`/v1/transfers`) o prefijo terminado en `*`")
    status: int = Field(ge=400, le=599)
    code: str | None = None
    times: int = Field(default=1, ge=1)


class FailTransferRequest(_Body):
    failure_code: str
    retryable: bool | None = None
    sent_value: str = Field(default="0", pattern=VALUE_PATTERN)


class FailPaymentRequest(_Body):
    failure_code: str = "upstream_error"
