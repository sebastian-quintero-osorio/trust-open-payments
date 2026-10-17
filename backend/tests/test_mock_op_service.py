"""Mock HTTP de op-service (`tools/mock_op_service`): cada respuesta se valida contra `op-service.openapi.yaml`.

También se apunta el `HttpOpClient` real de Trust al mock y el webhook firmado se verifica con `verify_signature`.
"""

import json
import uuid
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.errors import AppError
from app.integrations.op.client import OpError, OpUnavailable
from app.integrations.op.http import HttpOpClient
from app.integrations.op.schemas import (
    Amount,
    CreateCollectionAccountRequest,
    CreatePaymentRequest,
    CreateSimQrPaymentRequest,
    CreateTransferRequest,
    PositiveAmount,
)
from app.modules.sales.service import verify_signature
from tests.helpers import assert_op_schema
from tools.mock_op_service.app import create_app
from tools.mock_op_service.config import MockConfig
from tools.mock_op_service.webhooks import SIGNATURE_HEADER

API_KEY = "test-internal-key"
SECRET = "test-webhook-secret"
BASE = "http://mock-op.test"
WEBHOOK_URL = "http://trust.test/api/v1/webhooks/puntored/payments"
POOL = [f"https://ilp.interledger-test.dev/trust-recaudo-{n:02d}" for n in (1, 2, 3)]
RECEIVER = "https://ilp.interledger-test.dev/camilo"
SENDER = "https://ilp.interledger-test.dev/lender-camilo"
MERCHANT_WALLET = "https://ilp.interledger-test.dev/tienda-la-esquina"
RETURN_URL = "https://app.trustservices.com.co/op/return?loan_id=5a9e1d2c&client=web"
START = datetime(2026, 10, 17, 15, 0, tzinfo=UTC)
ACCOUNT_LIST = "/paths/~1v1~1collection-accounts/get/responses/200/content/application~1json/schema"


class FakeClock:
    def __init__(self, start: datetime = START):
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


@dataclass
class Mock:
    client: TestClient
    clock: FakeClock
    config: MockConfig
    app: Any
    webhooks: list[httpx.Request] = field(default_factory=list)
    webhook_statuses: deque[int | Exception] = field(default_factory=deque)

    @property
    def store(self):
        return self.app.state.store

    @property
    def dispatcher(self):
        return self.app.state.dispatcher

    def post(self, path: str, body: Any, key: str | None = None, **kwargs: Any) -> httpx.Response:
        headers = {"Idempotency-Key": key or f"test:{uuid.uuid4()}"}
        return self.client.post(path, json=body, headers=headers, **kwargs)

    def account(self, ref: str | None = None) -> dict[str, Any]:
        body = {"external_ref": ref or f"merchant:{uuid.uuid4()}", "display_name": "Tienda La Esquina",
                "asset_code": "COP"}
        response = self.post("/v1/collection-accounts", body)
        assert response.status_code == 201, response.text
        return response.json()


def _config(**changes: Any) -> MockConfig:
    values = {
        "api_key": API_KEY,
        "public_base_url": BASE,
        "pool_wallet_addresses": list(POOL),
        "webhook_url": WEBHOOK_URL,
        "webhook_secret": SECRET,
        "webhook_retry_seconds": (0.01, 0.01, 0.01, 0.01),
        **changes,
    }
    return MockConfig(**values)


def _start(config: MockConfig) -> Iterator[Mock]:
    clock = FakeClock()
    holder: dict[str, Mock] = {}

    def receive(request: httpx.Request) -> httpx.Response:
        mock = holder["mock"]
        request.read()
        mock.webhooks.append(request)
        outcome = mock.webhook_statuses.popleft() if mock.webhook_statuses else 200
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(outcome)

    app = create_app(config, clock=clock, webhook_client=httpx.Client(transport=httpx.MockTransport(receive)))
    with TestClient(app, base_url=BASE, headers={"X-Internal-Api-Key": API_KEY}) as client:
        holder["mock"] = Mock(client=client, clock=clock, config=config, app=app)
        yield holder["mock"]


@pytest.fixture
def mock() -> Iterator[Mock]:
    yield from _start(_config())


@pytest.fixture
def slow_mock() -> Iterator[Mock]:
    """Transferencias y pagos aprobados tardan 5 s en quedar `completed`."""
    yield from _start(_config(settle_seconds=5))


def _check(response: httpx.Response, status: int, schema: str, code: str | None = None) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()
    assert_op_schema(body, schema)
    if code is not None:
        assert body["error"]["code"] == code, body
    return body


def _cop(value: int | str) -> dict[str, Any]:
    return {"value": str(value), "asset_code": "COP", "asset_scale": 2}


def _transfer_body(account_id: str, value: int = 1_200_000, **changes: Any) -> dict[str, Any]:
    return {
        "collection_account_id": account_id,
        "receiver_wallet_address": RECEIVER,
        "debit_amount": _cop(value),
        "description": "Abono venta 10:42 a. m. · Tienda La Esquina",
        "external_ref": f"payout:{uuid.uuid4()}",
        **changes,
    }


def _payment_body(**changes: Any) -> dict[str, Any]:
    return {
        "sender_wallet_address": SENDER,
        "receiver_wallet_address": MERCHANT_WALLET,
        "receive_amount": _cop(400_000_000),
        "description": "Desembolso crédito Trust · Tienda La Esquina",
        "external_ref": "loan:5a9e1d2c:disb",
        "return_url": RETURN_URL,
        **changes,
    }


def _sim_body(account_id: str, value: int = 4_800_000, **changes: Any) -> dict[str, Any]:
    return {"collection_account_id": account_id, "amount": _cop(value), "payer_bank": "Bancolombia", **changes}


def _redirect_query(response: httpx.Response) -> tuple[str, dict[str, list[str]]]:
    assert response.status_code == 302, response.text
    location = urlsplit(response.headers["location"])
    return f"{location.scheme}://{location.netloc}{location.path}", parse_qs(location.query)


# ── Plataforma, autenticación y wallets ───────────────────────────────────────


def test_health_and_api_key(mock):
    anonymous = TestClient(mock.app, base_url=BASE)
    body = _check(anonymous.get("/v1/health"), 200, "Health")
    assert body["status"] == "ok"

    unknown = f"/v1/transfers/{uuid.uuid4()}"
    _check(anonymous.get(unknown), 401, "Error", "unauthorized")
    _check(anonymous.get(unknown, headers={"X-Internal-Api-Key": "otra"}), 401, "Error", "unauthorized")
    _check(mock.client.get(unknown), 404, "Error", "not_found")

    # Un 401 nunca se guarda: el reintento con la misma clave y la API key correcta se ejecuta.
    key = "merchant:sin-key:recaudo"
    body = {"external_ref": "merchant:sin-key", "display_name": "Tienda", "asset_code": "COP"}
    response = anonymous.post("/v1/collection-accounts", json=body, headers={"Idempotency-Key": key})
    _check(response, 401, "Error", "unauthorized")
    response = mock.post("/v1/collection-accounts", body, key)
    _check(response, 201, "CollectionAccount")
    assert "Idempotent-Replayed" not in response.headers


def test_wallet_addresses(mock):
    body = _check(mock.client.get("/v1/wallet-addresses", params={"url": "$ilp.interledger-test.dev/tienda/"}), 200,
                  "WalletAddress")
    assert body == {"url": "https://ilp.interledger-test.dev/tienda", "public_name": "tienda", "asset_code": "COP",
                    "asset_scale": 2}
    for bad in ("http://ilp.interledger-test.dev/x", "no es url", "https://"):
        _check(mock.client.get("/v1/wallet-addresses", params={"url": bad}), 422, "Error", "invalid_url")
    _check(mock.client.get("/v1/wallet-addresses"), 400, "Error", "validation_error")

    mock.client.post("/mock/wallets", json={"url": "https://ilp.interledger-test.dev/borrada", "exists": False})
    mock.client.post("/mock/wallets", json={"url": "https://ilp.interledger-test.dev/usd", "asset_code": "USD",
                                            "public_name": "Wallet USD"})
    response = mock.client.get("/v1/wallet-addresses", params={"url": "https://ilp.interledger-test.dev/borrada"})
    _check(response, 422, "Error", "wallet_not_found")
    body = _check(mock.client.get("/v1/wallet-addresses", params={"url": "https://ilp.interledger-test.dev/usd"}),
                  200, "WalletAddress")
    assert (body["asset_code"], body["asset_scale"], body["public_name"]) == ("USD", 2, "Wallet USD")


# ── Cuentas de recaudo ────────────────────────────────────────────────────────


def test_collection_accounts_pool_find_and_revoke(mock):
    ref = "merchant:8b1f0c3e-5d2a-4f7b-9c61-0e4d2a9b7c15"
    first = mock.account(ref)
    assert first["wallet_address"] == POOL[0] and first["status"] == "active"
    assert first["qr_payload"] == f"{BASE}/qr/{first['id']}"
    assert first["ledger_balance"] == _cop(0) and first["created_at"] == "2026-10-17T15:00:00Z"
    assert _check(mock.client.get(f"/v1/collection-accounts/{first['id']}"), 200, "CollectionAccount") == first

    body = {"external_ref": ref, "display_name": "Tienda", "asset_code": "COP"}
    _check(mock.post("/v1/collection-accounts", body), 409, "Error", "already_assigned")

    assert mock.client.delete(f"/v1/collection-accounts/{first['id']}").status_code == 204
    assert mock.client.delete(f"/v1/collection-accounts/{first['id']}").status_code == 204  # idempotente
    mock.clock.advance(60)
    second = mock.account(ref)
    assert second["wallet_address"] == POOL[1]  # la wallet revocada no vuelve al pool

    listed = _check(mock.client.get("/v1/collection-accounts", params={"external_ref": ref}), 200, ACCOUNT_LIST)
    assert [(a["id"], a["status"]) for a in listed["items"]] == [(second["id"], "active"), (first["id"], "revoked")]
    assert _check(mock.client.get("/v1/collection-accounts", params={"external_ref": "otro"}), 200,
                  ACCOUNT_LIST) == {"items": []}
    _check(mock.client.get("/v1/collection-accounts"), 400, "Error", "validation_error")

    for missing in (uuid.uuid4(), "no-es-uuid"):
        _check(mock.client.get(f"/v1/collection-accounts/{missing}"), 404, "Error", "not_found")
        _check(mock.client.delete(f"/v1/collection-accounts/{missing}"), 404, "Error", "not_found")

    usd = {"external_ref": "merchant:usd", "display_name": "Tienda USD", "asset_code": "USD"}
    _check(mock.post("/v1/collection-accounts", usd), 409, "Error", "pool_exhausted")
    mock.account()
    _check(mock.post("/v1/collection-accounts", {**usd, "asset_code": "COP"}), 409, "Error", "pool_exhausted")


# ── Idempotencia ──────────────────────────────────────────────────────────────


def test_idempotency_replays_original_status_and_conflicts(mock):
    account = mock.account()
    key = f"payout:{uuid.uuid4()}:1"
    body = _transfer_body(account["id"])
    first = mock.post("/v1/transfers", body, key)
    created = _check(first, 201, "Transfer")
    assert "Idempotent-Replayed" not in first.headers

    # Mismo cuerpo con otro orden de claves y espacios: JSON canónico → respuesta guardada, status 201 original.
    reordered = json.dumps(dict(reversed(list(body.items()))), indent=2)
    replay = mock.client.post("/v1/transfers", content=reordered,
                              headers={"Idempotency-Key": key, "Content-Type": "application/json"})
    assert _check(replay, 201, "Transfer") == created
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert len(mock.store._transfers) == 1

    conflict = mock.post("/v1/transfers", {**body, "debit_amount": _cop(1_200_001)}, key)
    _check(conflict, 409, "Error", "idempotency_conflict")
    assert conflict.json()["error"]["retryable"] is False
    assert mock.post("/v1/transfers", body, key).headers["Idempotent-Replayed"] == "true"  # el conflicto no se guardó

    # El alcance es (método, ruta): la misma clave en otra ruta es otra operación.
    _check(mock.post("/v1/payments", _payment_body(), key), 201, "Payment")

    # Sin clave, vacía o de más de 255 caracteres → 400 (nada se guarda).
    for headers in ({}, {"Idempotency-Key": ""}, {"Idempotency-Key": "k" * 256}):
        response = mock.client.post("/v1/transfers", json=body, headers=headers)
        _check(response, 400, "Error", "validation_error")
        assert "Idempotent-Replayed" not in response.headers


def test_idempotency_stores_definitive_4xx(mock):
    ref = f"merchant:{uuid.uuid4()}"
    account = mock.account(ref)
    key = f"{ref}:recaudo:2"
    body = {"external_ref": ref, "display_name": "Tienda", "asset_code": "COP"}
    _check(mock.post("/v1/collection-accounts", body, key), 409, "Error", "already_assigned")
    mock.client.delete(f"/v1/collection-accounts/{account['id']}")
    replay = mock.post("/v1/collection-accounts", body, key)  # ya podría asignarse, pero la 409 quedó guardada
    _check(replay, 409, "Error", "already_assigned")
    assert replay.headers["Idempotent-Replayed"] == "true"
    _check(mock.post("/v1/collection-accounts", body, f"{ref}:recaudo:3"), 201, "CollectionAccount")

    # 400 de validación y 422 también se guardan.
    invalid = _transfer_body(str(uuid.uuid4()), debit_amount={"value": "0", "asset_code": "COP", "asset_scale": 2})
    _check(mock.post("/v1/transfers", invalid, "k-400"), 400, "Error", "validation_error")
    assert mock.post("/v1/transfers", invalid, "k-400").headers["Idempotent-Replayed"] == "true"
    other = mock.account()
    usd = _transfer_body(other["id"], debit_amount={"value": "100", "asset_code": "USD", "asset_scale": 2})
    _check(mock.post("/v1/transfers", usd, "k-422"), 422, "Error", "asset_mismatch")
    assert mock.post("/v1/transfers", usd, "k-422").headers["Idempotent-Replayed"] == "true"


def test_idempotency_never_stores_5xx(mock):
    account = mock.account()
    fault = {"method": "POST", "path": "/v1/transfers", "status": 503}
    assert mock.client.post("/mock/faults", json=fault).status_code == 201
    key, body = "payout:5xx:1", _transfer_body(account["id"])
    failed = _check(mock.post("/v1/transfers", body, key), 503, "Error", "upstream_error")
    assert failed["error"]["retryable"] is True and not mock.store._transfers

    retried = mock.post("/v1/transfers", body, key)  # misma clave: se vuelve a ejecutar
    created = _check(retried, 201, "Transfer")
    assert "Idempotent-Replayed" not in retried.headers
    replay = mock.post("/v1/transfers", body, key)
    assert replay.status_code == 201 and replay.json() == created and replay.headers["Idempotent-Replayed"] == "true"

    # Las fallas inyectadas también sirven para los GET (por ejemplo, Go caído durante el polling).
    mock.client.post("/mock/faults", json={"method": "GET", "path": "/v1/transfers/*", "status": 502})
    _check(mock.client.get(f"/v1/transfers/{created['id']}"), 502, "Error", "upstream_error")
    _check(mock.client.get(f"/v1/transfers/{created['id']}"), 200, "Transfer")


# ── Transferencias ────────────────────────────────────────────────────────────


def test_transfer_lifecycle(mock):
    account = mock.account()
    mock.post("/v1/sim/puntored/qr-payments", _sim_body(account["id"], 4_800_000))
    created = _check(mock.post("/v1/transfers", _transfer_body(account["id"], 1_200_000)), 201, "Transfer")
    assert created["status"] == "processing" and created["retryable"] is False
    assert [created[k] for k in ("sent_amount", "receive_amount", "failure_code", "incoming_payment_url",
                                 "outgoing_payment_url", "completed_at")] == [None] * 6

    mock.clock.advance(3)
    done = _check(mock.client.get(f"/v1/transfers/{created['id']}"), 200, "Transfer")
    assert done["status"] == "completed" and done["failure_code"] is None
    assert done["sent_amount"] == done["receive_amount"] == done["debit_amount"] == _cop(1_200_000)
    assert done["incoming_payment_url"].startswith("https://") and done["outgoing_payment_url"].startswith("https://")
    assert done["completed_at"] == created["created_at"]
    mock.clock.advance(60)
    assert mock.client.get(f"/v1/transfers/{created['id']}").json() == done  # terminal: no cambia

    balance = mock.client.get(f"/v1/collection-accounts/{account['id']}").json()["ledger_balance"]
    assert balance == _cop(3_600_000)  # entradas del simulador − transferencias completadas


def test_transfer_validation(mock):
    account = mock.account()
    _check(mock.post("/v1/transfers", _transfer_body(str(uuid.uuid4()))), 404, "Error", "not_found")
    usd = {"value": "100", "asset_code": "COP", "asset_scale": 0}
    _check(mock.post("/v1/transfers", _transfer_body(account["id"], debit_amount=usd)), 422, "Error",
           "asset_mismatch")
    bad_receiver = _transfer_body(account["id"], receiver_wallet_address="ftp://wallet")
    _check(mock.post("/v1/transfers", bad_receiver), 422, "Error", "invalid_url")
    mock.client.post("/mock/wallets", json={"url": RECEIVER, "exists": False})
    _check(mock.post("/v1/transfers", _transfer_body(account["id"])), 422, "Error", "wallet_not_found")

    for invalid in (
        {"debit_amount": _cop(0)},
        {"debit_amount": {"value": 100, "asset_code": "COP", "asset_scale": 2}},
        {"debit_amount": {"value": "100", "asset_code": "COP", "asset_scale": "2"}},
        {"description": None},
        {"description": "x" * 201},
        {"collection_account_id": "no-es-uuid"},
        {"external_ref": None},
    ):
        _check(mock.post("/v1/transfers", _transfer_body(account["id"], **invalid)), 400, "Error",
               "validation_error")
    response = mock.client.post("/v1/transfers", content=b"{no json", headers={"Idempotency-Key": "k"})
    _check(response, 400, "Error", "validation_error")

    mock.client.delete(f"/v1/collection-accounts/{account['id']}")
    mock.client.post("/mock/wallets", json={"url": RECEIVER})
    _check(mock.post("/v1/transfers", _transfer_body(account["id"])), 409, "Error",
           "collection_account_not_active")


def test_transfer_settles_after_delay_and_failures(slow_mock):
    mock = slow_mock
    account = mock.account()
    created = mock.post("/v1/transfers", _transfer_body(account["id"])).json()
    mock.clock.advance(4)
    assert _check(mock.client.get(f"/v1/transfers/{created['id']}"), 200, "Transfer")["status"] == "processing"
    mock.clock.advance(1)
    assert _check(mock.client.get(f"/v1/transfers/{created['id']}"), 200, "Transfer")["status"] == "completed"

    # Revocada antes de ejecutarse → failed con grant_revoked (terminal).
    queued = mock.post("/v1/transfers", _transfer_body(account["id"])).json()
    mock.client.delete(f"/v1/collection-accounts/{account['id']}")
    mock.clock.advance(5)
    failed = _check(mock.client.get(f"/v1/transfers/{queued['id']}"), 200, "Transfer")
    assert (failed["status"], failed["failure_code"], failed["retryable"]) == ("failed", "grant_revoked", False)
    assert failed["sent_amount"] == _cop(0) and failed["receive_amount"] is None

    # Control del mock: falla con envío parcial; grant_revoked revoca la cuenta.
    other = mock.account()
    partial = mock.post("/v1/transfers", _transfer_body(other["id"], 1_000)).json()
    response = mock.client.post(f"/mock/transfers/{partial['id']}/fail",
                                json={"failure_code": "upstream_error", "sent_value": "400"})
    assert response.status_code == 200
    body = _check(mock.client.get(f"/v1/transfers/{partial['id']}"), 200, "Transfer")
    assert (body["status"], body["failure_code"], body["retryable"]) == ("failed", "upstream_error", True)
    assert body["sent_amount"] == body["receive_amount"] == _cop(400)
    revoked = mock.post("/v1/transfers", _transfer_body(other["id"])).json()
    mock.client.post(f"/mock/transfers/{revoked['id']}/fail", json={"failure_code": "grant_revoked"})
    assert mock.client.get(f"/v1/collection-accounts/{other['id']}").json()["status"] == "revoked"
    assert mock.client.post(f"/mock/transfers/{revoked['id']}/fail",
                            json={"failure_code": "internal"}).status_code == 409


# ── Pagos y consentimiento ────────────────────────────────────────────────────


def test_payment_consent_flow(mock):
    created = _check(mock.post("/v1/payments", _payment_body()), 201, "Payment")
    assert created["status"] == "awaiting_authorization"
    assert created["authorization_url"] == f"{BASE}/mock/consent/{created['id']}"
    assert created["authorization_expires_at"] == "2026-10-17T15:10:00Z"
    assert created["debit_amount"] == created["receive_amount"] == _cop(400_000_000)
    assert created["sent_amount"] is None and created["outgoing_payment_url"] is None

    page = mock.client.get(urlsplit(created["authorization_url"]).path)
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert "Aprobar" in page.text and "4.000.000,00 COP" in page.text

    mock.clock.advance(30)
    approve = mock.client.get(f"/mock/consent/{created['id']}", params={"decision": "approve"},
                              follow_redirects=False)
    target, query = _redirect_query(approve)
    assert target == "https://app.trustservices.com.co/op/return"
    assert query == {"loan_id": ["5a9e1d2c"], "client": ["web"], "op_resource": ["payment"],
                     "op_id": [created["id"]], "op_status": ["processing"]}

    done = _check(mock.client.get(f"/v1/payments/{created['id']}"), 200, "Payment")
    assert done["status"] == "completed" and done["authorization_url"] is None
    assert done["sent_amount"] == done["debit_amount"]
    assert done["outgoing_payment_url"].startswith("https://") and done["updated_at"] == "2026-10-17T15:00:30Z"

    # Refresh del navegador: no cambia nada y redirige con el estado actual.
    again = mock.client.get(f"/mock/consent/{created['id']}", params={"decision": "reject"}, follow_redirects=False)
    assert _redirect_query(again)[1]["op_status"] == ["completed"]


def test_payment_processing_then_completed(slow_mock):
    mock = slow_mock
    created = mock.post("/v1/payments", _payment_body()).json()
    mock.client.get(f"/mock/consent/{created['id']}", params={"decision": "approve"}, follow_redirects=False)
    processing = _check(mock.client.get(f"/v1/payments/{created['id']}"), 200, "Payment")
    assert processing["status"] == "processing" and processing["authorization_url"] is None
    assert processing["sent_amount"] is None and processing["outgoing_payment_url"]
    mock.clock.advance(5)
    assert _check(mock.client.get(f"/v1/payments/{created['id']}"), 200, "Payment")["status"] == "completed"


def test_payment_reject_expire_and_interact_finish(mock):
    rejected = mock.post("/v1/payments", _payment_body()).json()
    response = mock.client.get(f"/mock/consent/{rejected['id']}", params={"decision": "reject"},
                               follow_redirects=False)
    assert _redirect_query(response)[1]["op_status"] == ["rejected"]
    body = _check(mock.client.get(f"/v1/payments/{rejected['id']}"), 200, "Payment")
    assert body["status"] == "rejected" and body["authorization_url"] is None

    expired = mock.post("/v1/payments", _payment_body()).json()
    mock.clock.advance(600 + 29)
    assert mock.client.get(f"/v1/payments/{expired['id']}").json()["status"] == "awaiting_authorization"
    mock.clock.advance(1)
    body = _check(mock.client.get(f"/v1/payments/{expired['id']}"), 200, "Payment")
    assert body["status"] == "expired" and body["updated_at"] == "2026-10-17T15:10:30Z"
    late = mock.client.get(f"/mock/consent/{expired['id']}", params={"decision": "approve"}, follow_redirects=False)
    assert _redirect_query(late)[1]["op_status"] == ["expired"]

    # Aprobar después de authorization_expires_at (dentro de la gracia) también vence.
    slow = mock.post("/v1/payments", _payment_body()).json()
    mock.clock.advance(601)
    response = mock.client.get(f"/mock/consent/{slow['id']}", params={"decision": "approve"}, follow_redirects=False)
    assert _redirect_query(response)[1]["op_status"] == ["expired"]

    # /v1/interact/finish/{attempt_id}: result → rejected/expired/failed; interact_ref + hash → processing.
    cases = [({"result": "request_denied"}, "rejected"), ({"result": "invalid_interaction"}, "expired"),
             ({"result": "grant_invalid"}, "failed"), ({"interact_ref": "r", "hash": "h"}, "processing")]
    for params, status in cases:
        payment = mock.post("/v1/payments", _payment_body()).json()
        attempt = mock.store.peek_payment(payment["id"]).attempt_id
        anonymous = TestClient(mock.app, base_url=BASE)
        assert anonymous.get(f"/v1/interact/finish/{attempt}").status_code == 400  # sin result ni interact_ref
        response = anonymous.get(f"/v1/interact/finish/{attempt}", params=params, follow_redirects=False)
        assert _redirect_query(response)[1]["op_status"] == [status]
        body = _check(mock.client.get(f"/v1/payments/{payment['id']}"), 200, "Payment")
        assert body["status"] in (status, "completed")
        if status == "failed":
            assert body["failure_code"] == "upstream_error"
    assert mock.client.get("/v1/interact/finish/no-existe", params={"result": "x"}).status_code == 400
    assert mock.client.get(f"/mock/consent/{uuid.uuid4()}").status_code == 404
    assert mock.client.get(f"/mock/consent/{rejected['id']}", params={"decision": "talvez"}).status_code == 400


def test_payment_validation(mock):
    _check(mock.post("/v1/payments", _payment_body(receive_amount={"value": "5", "asset_code": "USD",
                                                                   "asset_scale": 2})), 422, "Error",
           "asset_mismatch")
    _check(mock.post("/v1/payments", _payment_body(sender_wallet_address="nada")), 422, "Error", "invalid_url")
    _check(mock.post("/v1/payments", _payment_body(return_url="/op/return")), 422, "Error", "invalid_url")
    _check(mock.post("/v1/payments", _payment_body(return_url=None)), 400, "Error", "validation_error")
    mock.config.allowed_return_url_prefixes = ["https://app.trustservices.com.co/"]
    _check(mock.post("/v1/payments", _payment_body(return_url="https://evil.test/op/return")), 422, "Error",
           "return_url_not_allowed")
    _check(mock.post("/v1/payments", _payment_body()), 201, "Payment")
    for missing in (uuid.uuid4(), "x"):
        _check(mock.client.get(f"/v1/payments/{missing}"), 404, "Error", "not_found")


# ── Simulador de Puntored y webhook ───────────────────────────────────────────


def test_sim_qr_payment_sends_signed_webhook(mock):
    account = mock.account()
    sim = _check(mock.post("/v1/sim/puntored/qr-payments", _sim_body(account["id"])), 201, "SimQrPayment")
    assert (sim["status"], sim["seq"], sim["bre_b_reference"]) == ("completed", 1, "BREB-SIM-000001")
    event = sim["webhook_event"]
    assert_op_schema(event, "PuntoredPaymentEvent")
    assert event["data"] == {
        "collection_account_id": account["id"],
        "collection_wallet_address": account["wallet_address"],
        "amount": _cop(4_800_000),
        "bre_b_reference": "BREB-SIM-000001",
        "payer_bank": "Bancolombia",
        "incoming_payment_url": sim["incoming_payment_url"],
    }

    assert mock.dispatcher.wait_idle()
    [request] = mock.webhooks
    assert str(request.url) == WEBHOOK_URL and request.headers["content-type"] == "application/json"
    header = request.headers[SIGNATURE_HEADER]
    verify_signature(request.content, header, [SECRET], datetime.now(UTC))  # la verificación de Trust: no lanza
    with pytest.raises(AppError):
        verify_signature(request.content, header, ["otro-secreto"], datetime.now(UTC))
    assert json.loads(request.content) == event

    listed = _check(mock.client.get("/v1/sim/puntored/qr-payments"), 200, "SimQrPaymentList")
    assert listed["next_after"] == 1 and [item["id"] for item in listed["items"]] == [sim["id"]]
    assert listed["items"][0]["webhook_delivered_at"] == "2026-10-17T15:00:00Z"
    assert listed["items"][0]["webhook_event"] == event
    assert mock.client.get(f"/v1/collection-accounts/{account['id']}").json()["ledger_balance"] == _cop(4_800_000)


def test_sim_webhook_retries_with_same_bytes(mock):
    account = mock.account()
    mock.webhook_statuses.extend([500, httpx.ConnectError("Trust caído"), 200])
    sim = mock.post("/v1/sim/puntored/qr-payments", _sim_body(account["id"])).json()
    assert mock.dispatcher.wait_idle()
    assert len(mock.webhooks) == 3
    assert {request.content for request in mock.webhooks} == {json.dumps(sim["webhook_event"],
                                                                         separators=(",", ":")).encode()}
    for request in mock.webhooks:
        verify_signature(request.content, request.headers[SIGNATURE_HEADER], [SECRET], datetime.now(UTC))
    assert mock.client.get("/v1/sim/puntored/qr-payments").json()["items"][0]["webhook_delivered_at"] is not None

    # Sin 2xx tras 1 + 4 reintentos: queda sin entregar (Trust lo recupera con el listado).
    mock.webhook_statuses.extend([500] * 5)
    lost = mock.post("/v1/sim/puntored/qr-payments", _sim_body(account["id"])).json()
    assert mock.dispatcher.wait_idle()
    assert [r for r in mock.webhooks if json.loads(r.content)["event_id"] == lost["webhook_event"]["event_id"]]
    assert len(mock.webhooks) == 3 + 5
    items = mock.client.get("/v1/sim/puntored/qr-payments", params={"after": 1}).json()["items"]
    assert [(i["seq"], i["webhook_delivered_at"]) for i in items] == [(2, None)]


def test_sim_qr_payment_rules_and_listing(mock):
    account = mock.account()
    key = "sim:replay"
    first = mock.post("/v1/sim/puntored/qr-payments", _sim_body(account["id"]), key).json()
    replay = mock.post("/v1/sim/puntored/qr-payments", _sim_body(account["id"]), key)
    assert replay.json() == first and replay.headers["Idempotent-Replayed"] == "true"
    assert mock.dispatcher.wait_idle() and len(mock.webhooks) == 1  # el replay no paga ni notifica otra vez

    _check(mock.post("/v1/sim/puntored/qr-payments", _sim_body(str(uuid.uuid4()))), 404, "Error", "not_found")
    _check(mock.post("/v1/sim/puntored/qr-payments", _sim_body(account["id"], amount={"value": "1",
           "asset_code": "USD", "asset_scale": 2})), 422, "Error", "asset_mismatch")
    over = mock.config.sim_max_amount + 1
    _check(mock.post("/v1/sim/puntored/qr-payments", _sim_body(account["id"], over)), 422, "Error",
           "invalid_amount")
    _check(mock.post("/v1/sim/puntored/qr-payments", _sim_body(account["id"], payer_bank="b" * 61)), 400, "Error",
           "validation_error")

    for n in range(2, 5):
        assert mock.post("/v1/sim/puntored/qr-payments", _sim_body(account["id"])).json()["seq"] == n
    page = _check(mock.client.get("/v1/sim/puntored/qr-payments", params={"after": 1, "limit": 2}), 200,
                  "SimQrPaymentList")
    assert [i["seq"] for i in page["items"]] == [2, 3] and page["next_after"] == 3
    empty = _check(mock.client.get("/v1/sim/puntored/qr-payments", params={"after": 4}), 200, "SimQrPaymentList")
    assert empty == {"items": [], "next_after": 4}
    for params in ({"after": -1}, {"limit": 0}, {"limit": 201}, {"after": "x"}):
        _check(mock.client.get("/v1/sim/puntored/qr-payments", params=params), 400, "Error", "validation_error")

    mock.client.delete(f"/v1/collection-accounts/{account['id']}")
    _check(mock.post("/v1/sim/puntored/qr-payments", _sim_body(account["id"])), 409, "Error",
           "collection_account_not_active")
    mock.config.sim_enabled = False
    _check(mock.post("/v1/sim/puntored/qr-payments", _sim_body(account["id"])), 409, "Error", "sim_disabled")


def test_webhook_without_url_is_skipped():
    for mock in _start(_config(webhook_url="")):
        account = mock.account()
        sim = mock.post("/v1/sim/puntored/qr-payments", _sim_body(account["id"])).json()
        assert sim["status"] == "completed" and mock.dispatcher.wait_idle() and mock.webhooks == []


# ── El HttpOpClient real de Trust contra el mock ──────────────────────────────


class _ViaTestClient(httpx.BaseTransport):
    """Transport de `httpx` que entrega cada petición al TestClient del mock (que usa `httpx2`)."""

    def __init__(self, client: TestClient):
        self._client = client

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
        response = self._client.request(request.method, request.url.raw_path.decode(), headers=headers,
                                        content=request.read(), follow_redirects=False)
        return httpx.Response(response.status_code, headers=list(response.headers.items()),
                              content=response.content)


def test_trust_http_op_client_against_mock(mock):
    anonymous = TestClient(mock.app, base_url=BASE)
    op = HttpOpClient(base_url=BASE, api_key=API_KEY, transport=_ViaTestClient(anonymous))
    assert op.health().status == "ok"
    assert op.resolve_wallet_address(f"{MERCHANT_WALLET}/").url == MERCHANT_WALLET

    ref = f"merchant:{uuid.uuid4()}"
    request = CreateCollectionAccountRequest(external_ref=ref, display_name="Tienda La Esquina", asset_code="COP")
    account = op.create_collection_account(request, idempotency_key=f"{ref}:recaudo")
    assert op.create_collection_account(request, idempotency_key=f"{ref}:recaudo") == account
    assert op.get_collection_account(account.id) == account
    assert [a.id for a in op.find_collection_accounts(ref)] == [account.id]

    sim = op.create_sim_qr_payment(
        CreateSimQrPaymentRequest(collection_account_id=account.id, amount=PositiveAmount(**_cop(4_800_000))),
        idempotency_key=f"sim:{uuid.uuid4()}",
    )
    assert sim.status == "completed" and sim.webhook_event is not None
    page = op.list_sim_qr_payments(after=0)
    assert [item.id for item in page.items] == [sim.id] and page.next_after == sim.seq

    transfer_request = CreateTransferRequest(
        collection_account_id=account.id,
        receiver_wallet_address=RECEIVER,
        debit_amount=PositiveAmount(**_cop(960_000)),
        description="Abono venta",
        external_ref=f"payout:{uuid.uuid4()}",
    )
    transfer = op.create_transfer(transfer_request, idempotency_key="payout:x:1")
    assert transfer.status == "processing"
    assert op.get_transfer(transfer.id).status == "completed"

    payment = op.create_payment(
        CreatePaymentRequest(
            sender_wallet_address=SENDER,
            receiver_wallet_address=MERCHANT_WALLET,
            receive_amount=Amount(**_cop(400_000_000)),
            external_ref="loan:x:disb",
            return_url=RETURN_URL,
        ),
        idempotency_key="loan:x:disb:1",
    )
    assert payment.status == "awaiting_authorization" and payment.authorization_url
    anonymous.get(urlsplit(payment.authorization_url).path, params={"decision": "approve"}, follow_redirects=False)
    assert op.get_payment(payment.id).status == "completed"

    op.revoke_collection_account(account.id)
    with pytest.raises(OpError) as exc:
        op.create_transfer(transfer_request, idempotency_key="payout:x:2")
    assert (exc.value.status, exc.value.code, exc.value.retryable) == (409, "collection_account_not_active", False)
    with pytest.raises(OpError) as exc:
        op.create_transfer(transfer_request.model_copy(update={"external_ref": "otro"}), idempotency_key="payout:x:1")
    assert (exc.value.status, exc.value.code) == (409, "idempotency_conflict")
    with pytest.raises(OpError) as exc:
        op.get_transfer(uuid.uuid4())
    assert (exc.value.status, exc.value.code) == (404, "not_found")
    mock.client.post("/mock/faults", json={"method": "GET", "path": "/v1/payments/*", "status": 503})
    with pytest.raises(OpUnavailable):
        op.get_payment(payment.id)
    with pytest.raises(OpError) as exc:
        HttpOpClient(base_url=BASE, api_key="mala", transport=_ViaTestClient(anonymous)).get_transfer(transfer.id)
    assert (exc.value.status, exc.value.code) == (401, "unauthorized")
    op.close()
