"""`HttpOpClient`: op-service real por HTTP, según `contracts/op-service.openapi.yaml` (02 R4–R8)."""

import logging
import time
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ValidationError

from app.integrations.op.client import IdLike, OpError, OpUnavailable, request_json
from app.integrations.op.schemas import (
    CollectionAccount,
    CollectionAccountList,
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

logger = logging.getLogger(__name__)

API_KEY_HEADER = "X-Internal-Api-Key"
IDEMPOTENCY_HEADER = "Idempotency-Key"
HEALTH_TIMEOUT_SECONDS = 2.0  # R7: `GET /health` no se cuelga con Go caído

def _segment(value: IdLike) -> str:
    """Id como segmento de ruta, escapado (un id raro nunca cambia la ruta)."""
    return quote(str(value), safe="")


class HttpOpClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = 10,
        transport: httpx.BaseTransport | None = None,
    ):
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers={API_KEY_HEADER: api_key},
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    # ── Transporte y mapeo de respuestas (R5, R6, R8) ─────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: BaseModel | None = None,
        idempotency_key: str | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """Exactamente una petición (nunca reintenta). 5xx, timeout o error de conexión → `OpUnavailable`."""
        headers = {IDEMPOTENCY_HEADER: idempotency_key} if idempotency_key is not None else None
        extra: dict[str, Any] = {"timeout": timeout} if timeout is not None else {}
        started = time.perf_counter()
        try:
            response = self._client.request(
                method,
                path,
                params=params,
                json=request_json(body) if body is not None else None,
                headers=headers,
                **extra,
            )
        except httpx.HTTPError as exc:  # transporte, timeouts, DecodingError, TooManyRedirects
            self._log(method, path, type(exc).__name__, started, idempotency_key, logging.WARNING)
            raise OpUnavailable(f"op-service no responde ({type(exc).__name__})") from exc

        level = logging.INFO if response.status_code < 500 else logging.WARNING
        self._log(method, path, response.status_code, started, idempotency_key, level)
        if response.status_code >= 500:
            raise OpUnavailable(f"op-service respondió {response.status_code}")
        if 400 <= response.status_code < 500:
            raise _op_error(response)
        if not 200 <= response.status_code < 300:
            raise OpUnavailable(f"op-service respondió {response.status_code} inesperado")
        return response

    @staticmethod
    def _log(method: str, path: str, status: object, started: float, key: str | None, level: int) -> None:
        # Nunca la API key (R8).
        ms = round((time.perf_counter() - started) * 1000)
        logger.log(level, "op-service %s %s → %s (%d ms) Idempotency-Key=%s", method, path, status, ms, key or "-")

    @staticmethod
    def _parse[M: BaseModel](response: httpx.Response, model: type[M]) -> M:
        """2xx → schema. Un 2xx que no es JSON o no cumple el schema → `OpUnavailable` (R5)."""
        try:
            return model.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise OpUnavailable(f"op-service devolvió un {model.__name__} inválido") from exc

    # ── Endpoints (R1) ────────────────────────────────────────────────────────

    def resolve_wallet_address(self, url: str) -> WalletAddress:
        response = self._request("GET", "/v1/wallet-addresses", params={"url": url})
        return self._parse(response, WalletAddress)

    def create_collection_account(
        self, body: CreateCollectionAccountRequest, idempotency_key: str
    ) -> CollectionAccount:
        response = self._request("POST", "/v1/collection-accounts", body=body, idempotency_key=idempotency_key)
        return self._parse(response, CollectionAccount)

    def get_collection_account(self, collection_account_id: IdLike) -> CollectionAccount:
        response = self._request("GET", f"/v1/collection-accounts/{_segment(collection_account_id)}")
        return self._parse(response, CollectionAccount)

    def find_collection_accounts(self, external_ref: str) -> list[CollectionAccount]:
        response = self._request("GET", "/v1/collection-accounts", params={"external_ref": external_ref})
        return self._parse(response, CollectionAccountList).items

    def revoke_collection_account(self, collection_account_id: IdLike) -> None:
        # DELETE sin Idempotency-Key: es idempotente en Go (204 aunque ya estuviera revocada).
        self._request("DELETE", f"/v1/collection-accounts/{_segment(collection_account_id)}")

    def create_transfer(self, body: CreateTransferRequest, idempotency_key: str) -> Transfer:
        response = self._request("POST", "/v1/transfers", body=body, idempotency_key=idempotency_key)
        return self._parse(response, Transfer)

    def get_transfer(self, transfer_id: IdLike) -> Transfer:
        response = self._request("GET", f"/v1/transfers/{_segment(transfer_id)}")
        return self._parse(response, Transfer)

    def create_payment(self, body: CreatePaymentRequest, idempotency_key: str) -> Payment:
        response = self._request("POST", "/v1/payments", body=body, idempotency_key=idempotency_key)
        return self._parse(response, Payment)

    def get_payment(self, payment_id: IdLike) -> Payment:
        response = self._request("GET", f"/v1/payments/{_segment(payment_id)}")
        return self._parse(response, Payment)

    def create_sim_qr_payment(self, body: CreateSimQrPaymentRequest, idempotency_key: str) -> SimQrPayment:
        response = self._request("POST", "/v1/sim/puntored/qr-payments", body=body, idempotency_key=idempotency_key)
        return self._parse(response, SimQrPayment)

    def list_sim_qr_payments(self, after: int = 0, limit: int = 100) -> SimQrPaymentList:
        response = self._request("GET", "/v1/sim/puntored/qr-payments", params={"after": after, "limit": limit})
        return self._parse(response, SimQrPaymentList)

    def health(self) -> Health:
        response = self._request("GET", "/v1/health", timeout=HEALTH_TIMEOUT_SECONDS)
        return self._parse(response, Health)


def _op_error(response: httpx.Response) -> OpError:
    """4xx → `OpError` con el `{"error": {code, message, retryable}}` de Go; sin cuerpo válido, `http_<status>`."""
    try:
        error = response.json()["error"]
        code, message, retryable = error["code"], error.get("message", ""), error.get("retryable", False)
        if isinstance(code, str) and code and isinstance(message, str) and isinstance(retryable, bool):
            return OpError(response.status_code, code, message, retryable)
    except (ValueError, KeyError, TypeError, AttributeError):
        pass
    return OpError(response.status_code, f"http_{response.status_code}", response.text[:200], False)
