"""Único punto de contacto con op-service (Go): `OpClient`, sus errores y `get_op_client()` (spec 02).

Contrato: `contracts/op-service.openapi.yaml`. El cliente es síncrono (SQLAlchemy sync + hilo del worker) y nunca
reintenta por su cuenta: reintentar con la misma `Idempotency-Key` es de cada llamador (R3, R6).
"""

import json
import threading
import uuid
from typing import Annotated, Protocol

from fastapi import Depends
from pydantic import BaseModel

from app.core.config import get_settings
from app.integrations.op.schemas import (
    CollectionAccount,
    CreateCollectionAccountRequest,
    CreatePaymentRequest,
    CreateSimQrPaymentRequest,
    CreateTransferRequest,
    Health,
    Payment,
    SimQrPayment,
    SimQrPaymentList,
    Transfer,
    WalletAddress,
)


class OpError(Exception):
    """Respuesta 4xx de op-service: `{"error": {code, message, retryable}}` (R3).

    `retryable=True` (por ejemplo `409 request_in_progress`) se trata igual que `OpUnavailable`: se reintenta con
    la **misma** clave.
    """

    def __init__(self, status: int, code: str, message: str = "", retryable: bool = False):
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(f"{status} {code}: {message}" if message else f"{status} {code}")

    def __repr__(self) -> str:
        return f"OpError({self.status!r}, {self.code!r}, {self.message!r}, {self.retryable!r})"


class OpUnavailable(Exception):
    """op-service no respondió bien: 5xx, timeout, error de conexión o un 2xx que no cumple el schema (R3, R5)."""

    def __init__(self, message: str = "op-service no disponible"):
        self.message = message
        super().__init__(message)


IdLike = str | uuid.UUID


class OpClient(Protocol):
    """Un método por endpoint de `contracts/op-service.openapi.yaml` que usa Trust (R1)."""

    def resolve_wallet_address(self, url: str) -> WalletAddress: ...  # GET /v1/wallet-addresses?url=

    def create_collection_account(  # POST /v1/collection-accounts
        self, body: CreateCollectionAccountRequest, idempotency_key: str
    ) -> CollectionAccount: ...

    def get_collection_account(self, collection_account_id: IdLike) -> CollectionAccount: ...  # GET …/{id}

    def find_collection_accounts(self, external_ref: str) -> list[CollectionAccount]: ...  # GET ?external_ref=

    def revoke_collection_account(self, collection_account_id: IdLike) -> None: ...  # DELETE …/{id} (204)

    def create_transfer(self, body: CreateTransferRequest, idempotency_key: str) -> Transfer: ...  # POST /v1/transfers

    def get_transfer(self, transfer_id: IdLike) -> Transfer: ...  # GET /v1/transfers/{id}

    def create_payment(self, body: CreatePaymentRequest, idempotency_key: str) -> Payment: ...  # POST /v1/payments

    def get_payment(self, payment_id: IdLike) -> Payment: ...  # GET /v1/payments/{id}

    def create_sim_qr_payment(  # POST /v1/sim/puntored/qr-payments
        self, body: CreateSimQrPaymentRequest, idempotency_key: str
    ) -> SimQrPayment: ...

    def list_sim_qr_payments(self, after: int = 0, limit: int = 100) -> SimQrPaymentList: ...  # GET …/qr-payments

    def health(self) -> Health: ...  # GET /v1/health


def request_json(body: BaseModel) -> dict:
    """Cuerpo de un POST: `model_dump(mode="json", exclude_none=True)`. Mismo modelo → mismo JSON (R4)."""
    return body.model_dump(mode="json", exclude_none=True)


def canonical_json(body: BaseModel) -> str:
    """JSON canónico del cuerpo (claves ordenadas, sin espacios), como compara op-service la idempotencia."""
    return json.dumps(request_json(body), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# ── Singleton por proceso (R21) ───────────────────────────────────────────────

_instance: OpClient | None = None
_instance_lock = threading.Lock()


def _build_op_client() -> OpClient:
    # Imports perezosos: fake.py y http.py importan `OpError` de este módulo.
    settings = get_settings()
    if settings.op_client == "http":
        from app.integrations.op.http import HttpOpClient

        return HttpOpClient(
            base_url=settings.op_service_base_url,
            api_key=settings.op_service_api_key,
            timeout=settings.op_http_timeout_seconds,
        )
    from app.integrations.op.fake import FakeOpClient

    # Salt aleatorio por proceso: los ids nuevos no chocan con los ya guardados en Postgres (R11).
    return FakeOpClient(auto_complete=settings.op_fake_auto_complete)


def get_op_client() -> OpClient:
    """El `OpClient` del proceso según `OP_CLIENT` (`fake` | `http`), siempre la misma instancia.

    Es dependencia FastAPI (`OpClientDep`) y la usa el worker. Los tests instalan su fake con `set_op_client`.
    """
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = _build_op_client()
        return _instance


def set_op_client(client: OpClient | None) -> None:
    """Fija el singleton (tests: el `FakeOpClient` con `salt="test"`). `None` hace que el próximo uso lo cree."""
    global _instance
    with _instance_lock:
        _instance = client


OpClientDep = Annotated[OpClient, Depends(get_op_client)]
