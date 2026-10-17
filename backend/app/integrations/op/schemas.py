"""Espejo 1:1 de `components/schemas` de `contracts/op-service.openapi.yaml` (02 R2).

Requests con `extra="forbid"` (un campo de más es un error de programación) y responses con `extra="ignore"`
(Go puede agregar campos sin romper a Trust). Los ids son `uuid.UUID` (`format: uuid`) y las URLs `str` sin
normalizar: el `url` de una wallet es el id exacto que devuelve Go y se guarda tal cual.
"""

import uuid
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.clock import UtcDateTime
from app.core.money import VALUE_PATTERN, Money

POSITIVE_VALUE_PATTERN = r"^[1-9][0-9]*$"


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Response(BaseModel):
    model_config = ConfigDict(extra="ignore")


# ── Montos ────────────────────────────────────────────────────────────────────


class Amount(BaseModel):
    """`{value, asset_code, asset_scale}` con `value` entero en texto. Estricto: sin coerciones de tipos.

    Se usa en requests y responses, así que ignora campos de más (como las responses).
    """

    model_config = ConfigDict(extra="ignore", strict=True)

    value: str = Field(pattern=VALUE_PATTERN)
    asset_code: str
    asset_scale: int = Field(ge=0, le=255)

    @classmethod
    def from_money(cls, m: Money) -> Self:
        return cls(**m.to_api())

    def to_money(self) -> Money:
        return Money(int(self.value), self.asset_code, self.asset_scale)


class PositiveAmount(Amount):
    """`Amount` con `value > 0`. Acepta un `Amount` ya construido (`receive_amount=Amount.from_money(m)`, 11 R10)."""

    value: str = Field(pattern=POSITIVE_VALUE_PATTERN)

    @model_validator(mode="before")
    @classmethod
    def _from_amount(cls, data: Any) -> Any:
        if isinstance(data, Amount) and not isinstance(data, cls):
            return data.model_dump()
        return data


# ── Wallets ───────────────────────────────────────────────────────────────────


class WalletAddress(_Response):
    url: str
    public_name: str | None = None
    asset_code: str
    asset_scale: int


# ── Cuentas de recaudo ────────────────────────────────────────────────────────

CollectionAccountStatus = Literal["active", "revoked"]


class CreateCollectionAccountRequest(_Request):
    external_ref: str = Field(max_length=200)
    display_name: str = Field(max_length=100)
    asset_code: str


class CollectionAccount(_Response):
    id: uuid.UUID
    external_ref: str
    display_name: str
    wallet_address: str
    qr_payload: str
    asset_code: str
    asset_scale: int
    status: CollectionAccountStatus
    ledger_balance: Amount | None = None
    created_at: UtcDateTime


class CollectionAccountList(_Response):
    """Respuesta de `GET /v1/collection-accounts?external_ref=` (schema inline en el YAML)."""

    items: list[CollectionAccount]


# ── Transferencias ────────────────────────────────────────────────────────────

TransferStatus = Literal["processing", "completed", "failed"]
TransferFailureCode = Literal[
    "insufficient_balance",
    "receiver_unreachable",
    "invalid_amount",
    "amount_too_small",
    "grant_revoked",
    "upstream_error",
    "internal",
]


class CreateTransferRequest(_Request):
    collection_account_id: uuid.UUID
    receiver_wallet_address: str
    debit_amount: PositiveAmount
    description: str | None = Field(default=None, max_length=200)
    external_ref: str = Field(max_length=200)


class Transfer(_Response):
    id: uuid.UUID
    external_ref: str
    collection_account_id: uuid.UUID
    receiver_wallet_address: str
    description: str | None = None
    debit_amount: Amount
    sent_amount: Amount | None = None
    receive_amount: Amount | None = None
    status: TransferStatus
    failure_code: TransferFailureCode | None = None
    retryable: bool
    incoming_payment_url: str | None = None
    outgoing_payment_url: str | None = None
    created_at: UtcDateTime
    completed_at: UtcDateTime | None = None


# ── Pagos (desembolso) ────────────────────────────────────────────────────────

PaymentStatus = Literal["awaiting_authorization", "processing", "completed", "failed", "rejected", "expired"]
PaymentFailureCode = Literal[
    "insufficient_balance", "quote_expired", "receiver_unreachable", "upstream_error", "internal"
]


class CreatePaymentRequest(_Request):
    sender_wallet_address: str
    receiver_wallet_address: str
    receive_amount: PositiveAmount
    description: str | None = Field(default=None, max_length=200)
    external_ref: str = Field(max_length=200)
    return_url: str


class Payment(_Response):
    id: uuid.UUID
    external_ref: str
    sender_wallet_address: str
    receiver_wallet_address: str
    receive_amount: Amount
    debit_amount: Amount | None = None
    sent_amount: Amount | None = None
    status: PaymentStatus
    authorization_url: str | None = None
    authorization_expires_at: UtcDateTime | None = None
    failure_code: PaymentFailureCode | None = None
    incoming_payment_url: str | None = None
    outgoing_payment_url: str | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime


# ── Simulador de Puntored ─────────────────────────────────────────────────────


class PuntoredPaymentData(_Response):
    collection_account_id: uuid.UUID
    collection_wallet_address: str
    amount: PositiveAmount
    bre_b_reference: str
    payer_bank: str | None = None
    incoming_payment_url: str | None = None


class PuntoredPaymentEvent(_Response):
    """Body del webhook `puntoredPayment` (Puntored → Trust)."""

    event_id: uuid.UUID
    type: Literal["qr_payment.received", "qr_payment.reversed"]
    occurred_at: UtcDateTime
    data: PuntoredPaymentData


class CreateSimQrPaymentRequest(_Request):
    collection_account_id: uuid.UUID
    amount: PositiveAmount
    payer_bank: str | None = Field(default=None, max_length=60)


class SimQrPayment(_Response):
    id: uuid.UUID
    seq: int | None
    collection_account_id: uuid.UUID
    amount: Amount
    payer_bank: str | None = None
    bre_b_reference: str
    status: Literal["processing", "completed", "failed"]
    incoming_payment_url: str | None = None
    webhook_event: PuntoredPaymentEvent | None = None
    webhook_delivered_at: UtcDateTime | None = None
    created_at: UtcDateTime


class SimQrPaymentList(_Response):
    items: list[SimQrPayment]
    next_after: int


# ── Plataforma ────────────────────────────────────────────────────────────────


class Health(_Response):
    status: Literal["ok", "degraded"]
    client_wallet_address: str | None = None
    version: str | None = None
