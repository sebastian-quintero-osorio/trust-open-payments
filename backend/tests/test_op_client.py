"""02-op-client: un test por criterio de aceptación, más pruebas de las reglas R1–R21.

La spec no tiene endpoints de la Trust API (los del fake están en 03), así que no hay `assert_contract`: en su lugar
las peticiones de `HttpOpClient` y las salidas del fake se validan contra `contracts/op-service.openapi.yaml`.
"""

import json
import logging
import re
import threading
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.core import clock
from app.integrations.op.client import OpClientDep, OpError, OpUnavailable, get_op_client, set_op_client
from app.integrations.op.fake import FAKE_NAMESPACE, FakeOpClient
from app.integrations.op.http import HttpOpClient
from app.integrations.op.schemas import (
    Amount,
    CollectionAccount,
    CreateCollectionAccountRequest,
    CreatePaymentRequest,
    CreateSimQrPaymentRequest,
    CreateTransferRequest,
    PositiveAmount,
    Transfer,
)
from app.main import create_app
from app.modules.recaudo.models import CollectionAccount as CollectionAccountRow
from app.modules.sales.models import Sale
from app.modules.worker.models import OpCursor
from tests.helpers import assert_op_schema, cop, op_contract

CAMILO_WALLET = "https://ilp.interledger-test.dev/camilo"
MARTA_WALLET = "https://ilp.interledger-test.dev/tienda-la-esquina"
ACCOUNT_ID = "2c4e6a80-1b3d-4f5a-8c7e-9d0f1a2b3c4d"
TRANSFER_ID = "7d1e0f3a-6b2c-4d8e-9f10-2a3b4c5d6e7f"
PAYMENT_ID = "5a9e1d2c-7b34-4e8f-a0c6-91d2e3f4a5b6"
API_KEY = "clave-interna-de-prueba"


# ── Ayudas del fake ───────────────────────────────────────────────────────────


def _new_fake(**kwargs) -> FakeOpClient:
    return FakeOpClient(salt="test", auto_complete=False, **kwargs)


def _account_body(ref: str = "merchant:A", name: str = "Tienda La Esquina", asset_code: str = "COP"):
    return CreateCollectionAccountRequest(external_ref=ref, display_name=name, asset_code=asset_code)


def _account(fake: FakeOpClient, ref: str = "merchant:A", key: str | None = None) -> CollectionAccount:
    return fake.create_collection_account(_account_body(ref), key or f"{ref}:recaudo")


def _transfer_body(account_id, amount=None, receiver: str = CAMILO_WALLET, ref: str = "payout:X", description=None):
    return CreateTransferRequest(
        collection_account_id=account_id,
        receiver_wallet_address=receiver,
        debit_amount=Amount.from_money(amount or cop(7_200)),
        external_ref=ref,
        description=description,
    )


def _payment_body(amount=None, return_url: str = "https://app.trustservices.com.co/op/return?loan_id=L&client=web"):
    return CreatePaymentRequest(
        sender_wallet_address=CAMILO_WALLET,
        receiver_wallet_address=MARTA_WALLET,
        receive_amount=Amount.from_money(amount or cop(2_000_000)),
        description="Desembolso crédito Trust · Tienda La Esquina",
        external_ref="loan:L:disb",
        return_url=return_url,
    )


def _sim_body(account_id, amount=None, payer_bank: str | None = "Bancolombia"):
    return CreateSimQrPaymentRequest(
        collection_account_id=account_id, amount=Amount.from_money(amount or cop(48_000)), payer_bank=payer_bank
    )


def _op_error(excinfo) -> tuple[int, str, bool]:
    return excinfo.value.status, excinfo.value.code, excinfo.value.retryable


# ── Ayudas de HttpOpClient ────────────────────────────────────────────────────


def _http(handler, timeout: float = 10) -> tuple[HttpOpClient, list[httpx.Request]]:
    """`HttpOpClient` con `MockTransport`; devuelve también la lista de peticiones que recibió el transport."""
    requests: list[httpx.Request] = []

    def recorder(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = HttpOpClient(
        base_url="http://op-service:8080", api_key=API_KEY, timeout=timeout, transport=httpx.MockTransport(recorder)
    )
    return client, requests


def _err(status: int, code: str, retryable: bool = False, message: str = "Mensaje de Go") -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": message, "retryable": retryable}})


COP_7200 = {"value": "720000", "asset_code": "COP", "asset_scale": 2}
COP_48000 = {"value": "4800000", "asset_code": "COP", "asset_scale": 2}
WALLET_JSON = {"url": MARTA_WALLET, "public_name": "Tienda La Esquina", "asset_code": "COP", "asset_scale": 2}
ACCOUNT_JSON = {
    "id": ACCOUNT_ID,
    "external_ref": "merchant:8b1f0c3e-5d2a-4f7b-9c61-0e4d2a9b7c15",
    "display_name": "Tienda La Esquina",
    "wallet_address": "https://ilp.interledger-test.dev/trust-recaudo-01",
    "qr_payload": f"https://pay.trustservices.com.co/qr/{ACCOUNT_ID}",
    "asset_code": "COP",
    "asset_scale": 2,
    "status": "active",
    "ledger_balance": {"value": "0", "asset_code": "COP", "asset_scale": 2},
    "created_at": "2026-10-16T14:00:00Z",
}
TRANSFER_JSON = {
    "id": TRANSFER_ID,
    "external_ref": "payout:X",
    "collection_account_id": ACCOUNT_ID,
    "receiver_wallet_address": CAMILO_WALLET,
    "description": None,
    "debit_amount": COP_7200,
    "sent_amount": None,
    "receive_amount": None,
    "status": "processing",
    "failure_code": None,
    "retryable": False,
    "incoming_payment_url": None,
    "outgoing_payment_url": None,
    "created_at": "2026-10-17T15:42:00Z",
    "completed_at": None,
}
PAYMENT_JSON = {
    "id": PAYMENT_ID,
    "external_ref": "loan:L:disb",
    "sender_wallet_address": CAMILO_WALLET,
    "receiver_wallet_address": MARTA_WALLET,
    "receive_amount": {"value": "200000000", "asset_code": "COP", "asset_scale": 2},
    "debit_amount": {"value": "200000000", "asset_code": "COP", "asset_scale": 2},
    "sent_amount": None,
    "status": "awaiting_authorization",
    "authorization_url": "https://auth.interledger-test.dev/interact/abc",
    "authorization_expires_at": "2026-10-17T15:52:00Z",
    "failure_code": None,
    "incoming_payment_url": None,
    "outgoing_payment_url": None,
    "created_at": "2026-10-17T15:42:00Z",
    "updated_at": "2026-10-17T15:42:00Z",
}
EVENT_JSON = {
    "event_id": "0f8e7d6c-5b4a-4392-8170-6e5d4c3b2a19",
    "type": "qr_payment.received",
    "occurred_at": "2026-10-17T15:42:00Z",
    "data": {
        "collection_account_id": ACCOUNT_ID,
        "collection_wallet_address": "https://ilp.interledger-test.dev/trust-recaudo-01",
        "amount": COP_48000,
        "bre_b_reference": "BREB-SIM-000123",
        "payer_bank": "Bancolombia",
        "incoming_payment_url": "https://ilp.interledger-test.dev/x/incoming-payments/1",
    },
}
SIM_JSON = {
    "id": "9b8a7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d",
    "seq": 123,
    "collection_account_id": ACCOUNT_ID,
    "amount": COP_48000,
    "payer_bank": "Bancolombia",
    "bre_b_reference": "BREB-SIM-000123",
    "status": "completed",
    "incoming_payment_url": "https://ilp.interledger-test.dev/x/incoming-payments/1",
    "webhook_event": EVENT_JSON,
    "webhook_delivered_at": "2026-10-17T15:42:01Z",
    "created_at": "2026-10-17T15:42:00Z",
}
HEALTH_JSON = {"status": "ok", "client_wallet_address": "https://ilp.interledger-test.dev/trust", "version": "0.1.0"}


def _yaml_operation(method: str, path: str) -> tuple[str, dict, list[dict]]:
    """(plantilla, operación, parámetros resueltos) del YAML de op-service para un método y una ruta concretos."""
    document = op_contract()

    def resolve(param: dict) -> dict:
        if "$ref" not in param:
            return param
        node = document
        for part in param["$ref"].removeprefix("#/").split("/"):
            node = node[part]
        return node

    for template, item in document["paths"].items():
        if re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", template), path) and method.lower() in item:
            operation = item[method.lower()]
            params = [resolve(p) for p in item.get("parameters", []) + operation.get("parameters", [])]
            return template, operation, params
    raise AssertionError(f"{method} {path} no existe en op-service.openapi.yaml")


# ── Criterios de aceptación ───────────────────────────────────────────────────


def test_ac1_fake_idempotent_replay(fake_op):
    account = _account(fake_op)
    body = _transfer_body(account.id)

    first = fake_op.create_transfer(body, "payout:X:1")
    second = fake_op.create_transfer(_transfer_body(account.id), "payout:X:1")  # mismo cuerpo, otro objeto

    assert second.id == first.id
    assert second == first
    assert [t.id for t in fake_op.transfers] == [first.id]
    assert [c.idempotency_key for c in fake_op.calls_to("create_transfer")] == ["payout:X:1", "payout:X:1"]
    # La repetición devuelve la respuesta guardada (como Go), aunque la transferencia ya haya cambiado.
    fake_op.complete_transfer(first.id)
    assert fake_op.create_transfer(body, "payout:X:1").status == "processing"
    assert len(fake_op.transfers) == 1


def test_ac2_fake_idempotency_conflict(fake_op):
    account = _account(fake_op)
    fake_op.create_transfer(_transfer_body(account.id, amount=cop(7_200)), "payout:X:1")

    with pytest.raises(OpError) as excinfo:
        fake_op.create_transfer(_transfer_body(account.id, amount=cop(7_000)), "payout:X:1")

    assert _op_error(excinfo) == (409, "idempotency_conflict", False)
    assert len(fake_op.transfers) == 1
    # La clave es por método: la misma clave en otro método no choca.
    assert fake_op.create_sim_qr_payment(_sim_body(account.id), "payout:X:1").status == "completed"


def test_ac3_http_headers_and_body():
    client, requests = _http(lambda request: httpx.Response(201, json=TRANSFER_JSON))
    body = _transfer_body(ACCOUNT_ID)  # sin description

    transfer = client.create_transfer(body, "payout:X:1")

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "http://op-service:8080/v1/transfers"
    assert request.headers["X-Internal-Api-Key"] == API_KEY
    assert request.headers["Idempotency-Key"] == "payout:X:1"
    assert request.headers["content-type"] == "application/json"
    sent = json.loads(request.content)
    assert sent == {
        "collection_account_id": ACCOUNT_ID,
        "receiver_wallet_address": CAMILO_WALLET,
        "debit_amount": COP_7200,
        "external_ref": "payout:X",
    }
    assert "description" not in sent and None not in sent.values()
    assert_op_schema(sent, "CreateTransferRequest")
    assert str(transfer.id) == TRANSFER_ID and transfer.status == "processing"
    assert transfer.debit_amount.to_money() == cop(7_200)

    # Mismo modelo → mismo JSON (Go compara JSON canónico); el replay de Go (201 + Idempotent-Replayed) es igual.
    client2, requests2 = _http(
        lambda request: httpx.Response(201, json=TRANSFER_JSON, headers={"Idempotent-Replayed": "true"})
    )
    assert client2.create_transfer(body, "payout:X:1") == transfer
    assert requests2[0].content == request.content


def test_ac4_http_maps_4xx():
    responses = iter(
        [
            _err(422, "wallet_not_found", message="La wallet no existe"),
            _err(409, "request_in_progress", retryable=True),
            httpx.Response(404, text="<html>no</html>"),
            httpx.Response(401, json={"detail": "sin formato de Go"}),
            httpx.Response(400, json={"error": {"code": 7, "message": "x", "retryable": False}}),
        ]
    )
    client, requests = _http(lambda request: next(responses))

    with pytest.raises(OpError) as excinfo:
        client.resolve_wallet_address("$ilp.interledger-test.dev/no-existe")
    assert (excinfo.value.status, excinfo.value.code, excinfo.value.message, excinfo.value.retryable) == (
        422, "wallet_not_found", "La wallet no existe", False,
    )

    with pytest.raises(OpError) as excinfo:
        client.create_transfer(_transfer_body(ACCOUNT_ID), "payout:X:1")
    assert _op_error(excinfo) == (409, "request_in_progress", True)

    # 4xx sin el cuerpo de error de Go → `http_<status>` con el texto (máx. 200) y no reintentable.
    with pytest.raises(OpError) as excinfo:
        client.get_transfer(TRANSFER_ID)
    assert (excinfo.value.status, excinfo.value.code, excinfo.value.message, excinfo.value.retryable) == (
        404, "http_404", "<html>no</html>", False,
    )
    with pytest.raises(OpError) as excinfo:
        client.health()
    assert _op_error(excinfo) == (401, "http_401", False)
    with pytest.raises(OpError) as excinfo:
        client.get_payment(PAYMENT_ID)
    assert _op_error(excinfo) == (400, "http_400", False)
    assert len(requests) == 5  # una petición por llamada


@pytest.mark.parametrize(
    "outcome",
    [
        _err(502, "upstream_error", retryable=True),
        httpx.Response(503, text="Service Unavailable"),
        httpx.Response(500),
        httpx.ReadTimeout("lento"),
        httpx.ConnectTimeout("lento"),
        httpx.ConnectError("conexión rechazada"),
        httpx.RemoteProtocolError("cortada"),
        httpx.DecodingError("gzip roto"),
    ],
    ids=["502", "503", "500", "read-timeout", "connect-timeout", "connect-error", "protocol-error", "decoding-error"],
)
def test_ac5_http_5xx_no_retry(outcome):
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    client, requests = _http(handler)

    with pytest.raises(OpUnavailable):
        client.create_transfer(_transfer_body(ACCOUNT_ID), "payout:X:1")
    assert len(requests) == 1

    with pytest.raises(OpUnavailable):
        client.get_transfer(TRANSFER_ID)
    assert len(requests) == 2


def test_ac6_fake_collection_pool():
    fake = _new_fake(pool_size=1)

    account = fake.create_collection_account(_account_body("merchant:A"), "merchant:A:recaudo")
    assert account.status == "active"
    assert account.wallet_address == "https://ilp.interledger-test.dev/trust-recaudo-01"
    assert account.qr_payload == f"https://pay.trustservices.com.co/qr/{account.id}"
    assert (account.external_ref, account.display_name, account.asset_code, account.asset_scale) == (
        "merchant:A", "Tienda La Esquina", "COP", 2,
    )

    with pytest.raises(OpError) as excinfo:
        fake.create_collection_account(_account_body("merchant:A"), "merchant:A:recaudo:otra")
    assert _op_error(excinfo) == (409, "already_assigned", False)

    with pytest.raises(OpError) as excinfo:
        fake.create_collection_account(_account_body("merchant:B", "Panadería El Trigal"), "merchant:B:recaudo")
    assert _op_error(excinfo) == (409, "pool_exhausted", False)

    # La misma clave repite el 201 aunque el pool ya esté vacío (respuesta guardada).
    assert fake.create_collection_account(_account_body("merchant:A"), "merchant:A:recaudo").id == account.id

    # `reset(pool_size)` acepta el tamaño posicional (R12) y rellena el pool.
    fake.reset(2)
    assert fake.pool_size == 2 and fake.calls == []
    fake.create_collection_account(_account_body("merchant:A"), "merchant:A:recaudo")
    fake.create_collection_account(_account_body("merchant:B", "Panadería El Trigal"), "merchant:B:recaudo")


def test_ac7_fake_transfer_lifecycle(fake_op):
    account = _account(fake_op)
    transfer = fake_op.create_transfer(_transfer_body(account.id, amount=cop(7_200)), "payout:X:1")
    assert transfer.debit_amount.value == "720000"
    assert transfer.status == "processing"
    assert (transfer.incoming_payment_url, transfer.outgoing_payment_url, transfer.sent_amount) == (None, None, None)

    fake_op.fail_transfer(transfer.id, "insufficient_balance", True, sent=200_000)

    failed = fake_op.get_transfer(transfer.id)
    assert failed.status == "failed"
    assert failed.failure_code == "insufficient_balance"
    assert failed.sent_amount.value == "200000"
    assert failed.sent_amount.to_money() == cop(2_000)
    assert failed.retryable is True
    with pytest.raises(ValueError):
        fake_op.complete_transfer(transfer.id)  # los terminales no cambian

    other = fake_op.create_transfer(_transfer_body(account.id, ref="payout:Y"), "payout:Y:1")
    fake_op.complete_transfer(other.id)
    completed = fake_op.get_transfer(other.id)
    assert completed.status == "completed"
    assert completed.sent_amount == completed.debit_amount
    assert completed.receive_amount == completed.debit_amount
    assert completed.incoming_payment_url and completed.outgoing_payment_url
    assert completed.completed_at is not None

    # receive en otro asset (wallet USD del prestamista, 08 AC-5).
    usd = fake_op.create_transfer(_transfer_body(account.id, ref="payout:Z"), "payout:Z:1")
    receive = Amount(value="177", asset_code="USD", asset_scale=2)
    assert fake_op.complete_transfer(usd.id, receive=receive).receive_amount == receive


def test_ac8_fake_payment_lifecycle(fake_op):
    payment = fake_op.create_payment(_payment_body(), "loan:L:disb:1")
    assert payment.status == "awaiting_authorization"

    assert fake_op.approve_payment(payment.id).status == "processing"
    processing = fake_op.get_payment(payment.id)
    assert processing.status == "processing" and processing.authorization_url is None

    assert fake_op.complete_payment(payment.id).status == "completed"
    completed = fake_op.get_payment(payment.id)
    assert completed.status == "completed"
    assert completed.sent_amount == completed.debit_amount == completed.receive_amount

    with pytest.raises(ValueError):
        fake_op.reject_payment(payment.id)
    for transition in (fake_op.approve_payment, fake_op.complete_payment, fake_op.expire_payment):
        with pytest.raises(ValueError):
            transition(payment.id)
    assert fake_op.get_payment(payment.id).status == "completed"


def test_ac9_fake_sim_qr_payment(fake_op):
    account = _account(fake_op)

    sim = fake_op.create_sim_qr_payment(_sim_body(account.id, amount=cop(48_000)), "sim:1")

    assert sim.status == "completed"
    assert sim.webhook_event.data.amount.value == "4800000"
    assert sim.webhook_event.type == "qr_payment.received"
    assert sim.webhook_event.data.collection_account_id == account.id
    assert sim.webhook_event.data.collection_wallet_address == account.wallet_address
    assert sim.webhook_event.data.bre_b_reference == sim.bre_b_reference == f"BREB-SIM-{sim.seq:06d}"
    assert sim.webhook_event.occurred_at == sim.created_at

    listed = fake_op.list_sim_qr_payments(after=0)
    assert [item.id for item in listed.items] == [sim.id]
    assert listed.next_after == sim.seq
    assert listed.items[0].webhook_event == sim.webhook_event


def test_ac10_fake_wallets_and_fail_next(fake_op):
    wallet = fake_op.resolve_wallet_address("$ilp.interledger-test.dev/tienda/")
    assert wallet.url == "https://ilp.interledger-test.dev/tienda"
    assert (wallet.public_name, wallet.asset_code, wallet.asset_scale) == ("tienda", "COP", 2)

    fake_op.set_wallet("https://ilp.interledger-test.dev/tienda", exists=False)
    with pytest.raises(OpError) as excinfo:
        fake_op.resolve_wallet_address("$ilp.interledger-test.dev/tienda/")
    assert _op_error(excinfo) == (422, "wallet_not_found", False)

    fake_op.fail_next("health", OpUnavailable("x"))
    with pytest.raises(OpUnavailable):
        fake_op.health()
    assert fake_op.health().status == "ok"
    assert len(fake_op.calls_to("health")) == 2


def test_ac11_get_op_client(monkeypatch, settings, fake_op):
    # En los tests el singleton es el fake de conftest (salt="test", sin auto-completar), siempre el mismo.
    assert get_op_client() is fake_op
    assert get_op_client() is get_op_client()
    assert (fake_op.salt, fake_op.auto_complete) == ("test", False)

    try:
        set_op_client(None)
        monkeypatch.setattr(settings, "op_client", "fake")
        fake = get_op_client()
        assert isinstance(fake, FakeOpClient)
        assert get_op_client() is fake
        assert fake.salt != "test"  # salt aleatorio por proceso (R11)
        assert fake.auto_complete is settings.op_fake_auto_complete

        set_op_client(None)
        monkeypatch.setattr(settings, "op_client", "http")
        monkeypatch.setattr(settings, "op_service_base_url", "http://op-service:8080")
        http = get_op_client()
        assert isinstance(http, HttpOpClient)
        assert get_op_client() is http
        http.close()

        # Dependencia FastAPI: sin override, las rutas reciben el singleton.
        set_op_client(fake)
        app = FastAPI()
        router = APIRouter()

        @router.get("/op")
        def which(op: OpClientDep) -> dict:
            return {"same": op is fake, "type": type(op).__name__}

        app.include_router(router)
        with TestClient(app) as test_client:
            assert test_client.get("/op").json() == {"same": True, "type": "FakeOpClient"}
    finally:
        set_op_client(fake_op)
    assert get_op_client() is fake_op


def test_ac12_fake_adopts_accounts_on_boot(db, make_merchant, fake_op):
    merchant = make_merchant(business_name="Tienda La Esquina")
    op_id = uuid.uuid4()
    db.add(
        CollectionAccountRow(
            merchant_id=merchant.id,
            op_collection_account_id=op_id,
            wallet_address="https://ilp.interledger-test.dev/trust-recaudo-01",
            qr_payload=f"https://pay.trustservices.com.co/qr/{op_id}",
            asset_code="COP",
            asset_scale=2,
            status="active",
        )
    )
    db.commit()
    assert fake_op.collection_accounts == []  # fake recién creado (reiniciado): no conoce la cuenta

    with TestClient(create_app()):
        pass  # el lifespan corre al arrancar

    sim = fake_op.create_sim_qr_payment(_sim_body(op_id, amount=cop(48_000)), "sim:boot")
    assert sim.status == "completed"
    assert sim.webhook_event.data.collection_account_id == op_id
    assert sim.webhook_event.data.collection_wallet_address == "https://ilp.interledger-test.dev/trust-recaudo-01"

    adopted = fake_op.get_collection_account(op_id)
    assert (adopted.external_ref, adopted.display_name, adopted.status) == (
        f"merchant:{merchant.id}", "Tienda La Esquina", "active",
    )
    # La wallet adoptada sale del pool y el negocio ya tiene su cuenta activa en el fake.
    with pytest.raises(OpError) as excinfo:
        _account(fake_op, f"merchant:{merchant.id}")
    assert excinfo.value.code == "already_assigned"
    assert _account(fake_op, "merchant:otro").wallet_address == "https://ilp.interledger-test.dev/trust-recaudo-02"


# ── Reglas sin AC propio ──────────────────────────────────────────────────────


def test_fake_idempotency_stores_4xx_but_not_injected_failures(fake_op):
    missing = uuid.uuid4()
    body = _transfer_body(missing)
    with pytest.raises(OpError) as excinfo:
        fake_op.create_transfer(body, "payout:X:1")
    assert _op_error(excinfo) == (404, "not_found", False)

    fake_op.adopt_collection_account(
        missing, "merchant:A", "https://ilp.interledger-test.dev/externa", "https://pay.trustservices.com.co/qr/x",
        "COP", 2, "active",
    )
    with pytest.raises(OpError) as excinfo:  # la 4xx definitiva quedó guardada con esa clave
        fake_op.create_transfer(body, "payout:X:1")
    assert excinfo.value.code == "not_found"
    assert fake_op.create_transfer(body, "payout:X:2").status == "processing"

    # Lo inyectado con fail_next no se guarda: el reintento con la misma clave crea la transferencia.
    fake_op.fail_next("create_transfer", OpUnavailable("caído"))
    fake_op.fail_next("create_transfer", OpError(409, "request_in_progress", "en curso", True))
    with pytest.raises(OpUnavailable):
        fake_op.create_transfer(body, "payout:X:3")
    with pytest.raises(OpError) as excinfo:
        fake_op.create_transfer(body, "payout:X:3")
    assert excinfo.value.retryable is True
    created = fake_op.create_transfer(body, "payout:X:3")
    assert fake_op.create_transfer(body, "payout:X:3").id == created.id
    assert len(fake_op.transfers) == 2
    assert [c.idempotency_key for c in fake_op.calls_to("create_transfer")] == [
        "payout:X:1", "payout:X:1", "payout:X:2", "payout:X:3", "payout:X:3", "payout:X:3", "payout:X:3",
    ]
    assert fake_op.calls_to("create_transfer")[0].body == body

    with pytest.raises(ValueError):
        fake_op.fail_next("no_existe", OpUnavailable("x"))
    with pytest.raises(OpError) as excinfo:
        fake_op.create_transfer(body, "")
    assert excinfo.value.status == 400


def test_fake_validates_like_go(fake_op, settings):
    account = _account(fake_op)

    for call in (
        lambda: fake_op.get_collection_account(uuid.uuid4()),
        lambda: fake_op.get_collection_account("no-es-un-uuid"),
        lambda: fake_op.revoke_collection_account(uuid.uuid4()),
        lambda: fake_op.get_transfer(uuid.uuid4()),
        lambda: fake_op.get_payment("x"),
        lambda: fake_op.create_sim_qr_payment(_sim_body(uuid.uuid4()), "sim:404"),
    ):
        with pytest.raises(OpError) as excinfo:
            call()
        assert _op_error(excinfo) == (404, "not_found", False)

    usd = Amount(value="4800", asset_code="USD", asset_scale=2)
    cop0 = Amount(value="48000", asset_code="COP", asset_scale=0)
    for amount in (usd, cop0):
        with pytest.raises(OpError) as excinfo:
            fake_op.create_transfer(_transfer_body(account.id, amount=amount.to_money()), f"payout:{amount}")
        assert _op_error(excinfo) == (422, "asset_mismatch", False)
        with pytest.raises(OpError) as excinfo:
            fake_op.create_sim_qr_payment(_sim_body(account.id, amount=amount.to_money()), f"sim:{amount}")
        assert _op_error(excinfo) == (422, "asset_mismatch", False)
        with pytest.raises(OpError) as excinfo:
            fake_op.create_payment(_payment_body(amount=amount.to_money()), f"loan:{amount}")
        assert _op_error(excinfo) == (422, "asset_mismatch", False)

    fake_op.set_wallet(CAMILO_WALLET, exists=False)
    with pytest.raises(OpError) as excinfo:  # receptor que no resuelve
        fake_op.create_transfer(_transfer_body(account.id), "payout:sin-wallet")
    assert excinfo.value.code == "wallet_not_found"
    with pytest.raises(OpError) as excinfo:  # emisor que no resuelve
        fake_op.create_payment(_payment_body(), "loan:sin-wallet")
    assert excinfo.value.code == "wallet_not_found"

    with pytest.raises(OpError) as excinfo:  # el pool solo tiene wallets del asset de mercado
        fake_op.create_collection_account(_account_body("merchant:B", asset_code="USD"), "merchant:B:recaudo")
    assert excinfo.value.code == "pool_exhausted"

    fake_op.revoke_collection_account(account.id)
    fake_op.revoke_collection_account(account.id)  # idempotente
    with pytest.raises(OpError) as excinfo:
        fake_op.create_transfer(_transfer_body(account.id, receiver=MARTA_WALLET), "payout:revocada")
    assert _op_error(excinfo) == (409, "collection_account_not_active", False)
    with pytest.raises(OpError) as excinfo:
        fake_op.create_sim_qr_payment(_sim_body(account.id), "sim:revocada")
    assert _op_error(excinfo) == (409, "collection_account_not_active", False)

    with pytest.raises(OpError) as excinfo:
        fake_op.list_sim_qr_payments(after=0, limit=201)
    assert excinfo.value.status == 400


def test_fake_wallet_rules(fake_op):
    assert fake_op.resolve_wallet_address("https://ilp.interledger-test.dev/camilo/").url == CAMILO_WALLET
    for bad in ("http://ilp.interledger-test.dev/x", "ftp://x/y", "ilp.interledger-test.dev/x", "https://", "$", ""):
        with pytest.raises(OpError) as excinfo:
            fake_op.resolve_wallet_address(bad)
        assert _op_error(excinfo) == (422, "invalid_url", False)

    fake_op.set_wallet("$ilp.interledger-test.dev/camilo", "USD", 2, public_name="Camilo Pardo")
    wallet = fake_op.resolve_wallet_address(CAMILO_WALLET)
    assert (wallet.url, wallet.public_name, wallet.asset_code, wallet.asset_scale) == (
        CAMILO_WALLET, "Camilo Pardo", "USD", 2,
    )
    fake_op.set_wallet("https://ilp.interledger-test.dev/trust-revenue", "COP", 0)
    assert fake_op.resolve_wallet_address("https://ilp.interledger-test.dev/trust-revenue").asset_scale == 0
    assert [c.args for c in fake_op.calls_to("resolve_wallet_address")][-1] == {
        "url": "https://ilp.interledger-test.dev/trust-revenue"
    }
    fake_op.reset()
    assert fake_op.resolve_wallet_address(CAMILO_WALLET).asset_code == "COP"


def test_fake_accounts_revoke_find_and_grant_revoked(fake_op, freeze):
    freeze(datetime(2026, 10, 17, 15, 0, tzinfo=UTC))
    first = _account(fake_op, "merchant:A")
    fake_op.revoke_collection_account(first.id)
    assert fake_op.get_collection_account(first.id).status == "revoked"
    second = _account(fake_op, "merchant:A", key="merchant:A:recaudo:2")  # la revocada libera el external_ref
    assert second.wallet_address == "https://ilp.interledger-test.dev/trust-recaudo-02"  # la wallet no vuelve

    found = fake_op.find_collection_accounts("merchant:A")
    assert [a.id for a in found] == [second.id, first.id]  # incluye revocadas, la más reciente primero
    assert fake_op.find_collection_accounts("merchant:nadie") == []

    transfer = fake_op.create_transfer(_transfer_body(second.id), "payout:X:1")
    fake_op.fail_transfer(transfer.id, "grant_revoked", False)
    assert fake_op.get_collection_account(second.id).status == "revoked"
    assert fake_op.get_transfer(transfer.id).sent_amount.value == "0"
    for bad in (("insufficient_funds", True, 0), ("internal", True, 720_001), ("internal", True, -1)):
        other = fake_op.create_transfer(_transfer_body(_account(fake_op, f"merchant:{bad}").id), f"payout:{bad}")
        with pytest.raises(ValueError):
            fake_op.fail_transfer(other.id, *bad[:2], sent=bad[2])

    # adopt es idempotente y deja la wallet fuera del pool.
    again = fake_op.adopt_collection_account(
        first.id, "merchant:A", "x", "y", "COP", 2, "active",
    )
    assert again.status == "revoked" and again.wallet_address == first.wallet_address


def test_fake_auto_complete(fake_op):
    fake_op.auto_complete = True
    account = _account(fake_op)
    transfer = fake_op.create_transfer(_transfer_body(account.id), "payout:X:1")
    completed = fake_op.get_transfer(transfer.id)
    assert completed.status == "completed" and completed.sent_amount == completed.debit_amount

    payment = fake_op.create_payment(_payment_body(), "loan:L:disb:1")
    assert fake_op.get_payment(payment.id).status == "awaiting_authorization"  # nunca sale solo de aquí
    fake_op.approve_payment(payment.id)
    assert fake_op.get_payment(payment.id).status == "completed"

    fake_op.reset()
    assert fake_op.auto_complete is False  # reset vuelve a lo de conftest


def test_fake_payment_details(fake_op, freeze, settings, monkeypatch):
    now = datetime(2026, 10, 17, 15, 42, tzinfo=UTC)
    freeze(now)
    monkeypatch.setattr(settings, "public_api_url", "http://localhost:8000/")
    payment = fake_op.create_payment(_payment_body(), "loan:L:disb:1")

    assert payment.authorization_url == f"http://localhost:8000/api/v1/dev/fake-wallet/authorize/{payment.id}"
    assert payment.authorization_expires_at == now + timedelta(minutes=10)
    assert payment.debit_amount == payment.receive_amount and payment.sent_amount is None
    assert payment.created_at == payment.updated_at == now
    request = fake_op.get_payment_request(payment.id)
    assert request.return_url == "https://app.trustservices.com.co/op/return?loan_id=L&client=web"
    with pytest.raises(OpError):
        fake_op.get_payment_request(uuid.uuid4())

    freeze(now + timedelta(minutes=1))
    rejected = fake_op.reject_payment(payment.id)
    assert (rejected.status, rejected.authorization_url, rejected.updated_at) == (
        "rejected", None, now + timedelta(minutes=1),
    )
    expired = fake_op.expire_payment(fake_op.create_payment(_payment_body(), "loan:L:disb:2").id)
    assert expired.status == "expired"
    failed = fake_op.fail_payment(fake_op.create_payment(_payment_body(), "loan:L:disb:3").id, "quote_expired")
    assert (failed.status, failed.failure_code) == ("failed", "quote_expired")
    with pytest.raises(ValueError):
        fake_op.complete_payment(fake_op.create_payment(_payment_body(), "loan:L:disb:4").id)  # sin aprobar
    assert fake_op.calls_to("create_payment")[0].body.return_url == request.return_url


def test_fake_sim_list_and_seq(fake_op):
    account = _account(fake_op)
    sims = [fake_op.create_sim_qr_payment(_sim_body(account.id, amount=cop(1_000 * n)), f"sim:{n}") for n in (1, 2, 3)]
    assert [s.seq for s in sims] == [1, 2, 3]
    assert [s.bre_b_reference for s in sims] == ["BREB-SIM-000001", "BREB-SIM-000002", "BREB-SIM-000003"]
    assert len({s.webhook_event.event_id for s in sims}) == 3
    assert fake_op.create_sim_qr_payment(_sim_body(account.id, amount=cop(1_000)), "sim:1").seq == 1  # replay

    page = fake_op.list_sim_qr_payments(after=1, limit=1)
    assert ([s.seq for s in page.items], page.next_after) == ([2], 2)
    empty = fake_op.list_sim_qr_payments(after=3)
    assert (empty.items, empty.next_after) == ([], 3)

    fake_op.resume_seq_after(41)
    assert fake_op.create_sim_qr_payment(_sim_body(account.id), "sim:4").bre_b_reference == "BREB-SIM-000042"
    no_bank = fake_op.create_sim_qr_payment(_sim_body(account.id, payer_bank=None), "sim:5")
    assert no_bank.payer_bank is None and no_bank.webhook_event.data.payer_bank is None


def test_fake_ids_deterministic():
    a, b = _new_fake(), _new_fake()
    first = _account(a)
    assert _account(b).id == first.id == uuid.uuid5(FAKE_NAMESPACE, "test:collection_account:1")
    assert _account(FakeOpClient(salt="otro")).id != first.id
    a.reset()
    assert _account(a).id == first.id  # reset reinicia los contadores


def test_fake_outputs_follow_op_service_contract(fake_op):
    """Todo lo que devuelve el fake cumple los schemas de `op-service.openapi.yaml` (como Go)."""

    def check(model, schema: str) -> None:
        assert_op_schema(model.model_dump(mode="json", exclude_none=True), schema)

    account = _account(fake_op)
    check(account, "CollectionAccount")
    check(fake_op.resolve_wallet_address(CAMILO_WALLET), "WalletAddress")
    transfer = fake_op.create_transfer(_transfer_body(account.id, description="Abono venta 10:42 a. m."), "p:1")
    check(transfer, "Transfer")
    check(fake_op.complete_transfer(transfer.id), "Transfer")
    failed = fake_op.create_transfer(_transfer_body(account.id), "p:2")
    check(fake_op.fail_transfer(failed.id, "upstream_error", True, sent=100), "Transfer")
    payment = fake_op.create_payment(_payment_body(), "loan:L:disb:1")
    check(payment, "Payment")
    fake_op.approve_payment(payment.id)
    check(fake_op.complete_payment(payment.id), "Payment")
    sim = fake_op.create_sim_qr_payment(_sim_body(account.id), "sim:1")
    check(sim, "SimQrPayment")
    check(sim.webhook_event, "PuntoredPaymentEvent")
    check(fake_op.list_sim_qr_payments(), "SimQrPaymentList")
    check(fake_op.health(), "Health")
    assert fake_op.health().client_wallet_address == "https://ilp.interledger-test.dev/trust"


def test_fake_is_thread_safe(fake_op):
    account = _account(fake_op)

    def worker(n: int) -> None:
        for i in range(20):
            fake_op.create_transfer(_transfer_body(account.id, ref=f"payout:{n}-{i}"), f"payout:{n}-{i}:1")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len({t.id for t in fake_op.transfers}) == 100


def test_http_follows_op_service_contract():
    """Cada método de `HttpOpClient` pega a la ruta, método, headers, query y cuerpo del YAML de op-service."""
    responses = {
        ("GET", "/v1/wallet-addresses"): (200, WALLET_JSON),
        ("POST", "/v1/collection-accounts"): (201, ACCOUNT_JSON),
        ("GET", f"/v1/collection-accounts/{ACCOUNT_ID}"): (200, ACCOUNT_JSON),
        ("GET", "/v1/collection-accounts"): (200, {"items": [ACCOUNT_JSON]}),
        ("DELETE", f"/v1/collection-accounts/{ACCOUNT_ID}"): (204, None),
        ("POST", "/v1/transfers"): (201, TRANSFER_JSON),
        ("GET", f"/v1/transfers/{TRANSFER_ID}"): (200, TRANSFER_JSON),
        ("POST", "/v1/payments"): (201, PAYMENT_JSON),
        ("GET", f"/v1/payments/{PAYMENT_ID}"): (200, PAYMENT_JSON),
        ("POST", "/v1/sim/puntored/qr-payments"): (201, SIM_JSON),
        ("GET", "/v1/sim/puntored/qr-payments"): (200, {"items": [SIM_JSON], "next_after": 123}),
        ("GET", "/v1/health"): (200, HEALTH_JSON),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        template, operation, params = _yaml_operation(method, path)
        assert request.headers.get("X-Internal-Api-Key") == API_KEY
        headers = {p["name"] for p in params if p["in"] == "header"}
        assert ("Idempotency-Key" in headers) == (method == "POST") == ("Idempotency-Key" in request.headers)
        declared_query = {p["name"] for p in params if p["in"] == "query"}
        assert set(request.url.params) <= declared_query, f"{method} {template}: query {dict(request.url.params)}"
        required_query = {p["name"] for p in params if p["in"] == "query" and p.get("required")}
        assert required_query <= set(request.url.params)
        if "requestBody" in operation:
            schema = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]
            assert_op_schema(json.loads(request.content), schema)
        else:
            assert request.content == b""
        status, body = responses[(method, path)]
        assert str(status) in operation["responses"], f"{method} {template} no declara {status}"
        if body is not None:
            pointer = "/" + "/".join(
                ["paths", template.replace("/", "~1"), method.lower(), "responses", str(status), "content",
                 "application~1json", "schema"]
            )
            assert_op_schema(body, pointer)
            return httpx.Response(status, json=body)
        return httpx.Response(status)

    client, requests = _http(handler)

    assert client.resolve_wallet_address("$ilp.interledger-test.dev/tienda-la-esquina").url == MARTA_WALLET
    assert requests[-1].url.params["url"] == "$ilp.interledger-test.dev/tienda-la-esquina"
    assert str(client.create_collection_account(_account_body(), "merchant:A:recaudo").id) == ACCOUNT_ID
    assert client.get_collection_account(uuid.UUID(ACCOUNT_ID)).status == "active"
    assert [str(a.id) for a in client.find_collection_accounts("merchant:A")] == [ACCOUNT_ID]
    assert requests[-1].url.params["external_ref"] == "merchant:A"
    assert client.revoke_collection_account(ACCOUNT_ID) is None
    assert client.create_transfer(_transfer_body(ACCOUNT_ID, description="Abono"), "payout:X:1").status == "processing"
    assert client.get_transfer(TRANSFER_ID).debit_amount.value == "720000"
    payment = client.create_payment(_payment_body(), "loan:L:disb:1")
    assert payment.authorization_url == PAYMENT_JSON["authorization_url"]
    assert client.get_payment(PAYMENT_ID).status == "awaiting_authorization"
    sim = client.create_sim_qr_payment(_sim_body(ACCOUNT_ID), "sim:1")
    assert sim.webhook_event.data.amount.to_money() == cop(48_000)
    listed = client.list_sim_qr_payments(after=7, limit=50)
    assert (listed.next_after, dict(requests[-1].url.params)) == (123, {"after": "7", "limit": "50"})
    assert client.health().status == "ok"
    assert len(requests) == 12  # una petición por llamada, nunca reintenta


def test_http_invalid_2xx_is_unavailable_and_health_timeout():
    bodies = iter([
        httpx.Response(200, json={"status": "fine"}),  # no cumple Health
        httpx.Response(201, text="no es json"),
        httpx.Response(200, json={**TRANSFER_JSON, "debit_amount": {"value": 720000, "asset_code": "COP",
                                                                    "asset_scale": 2}}),
    ])
    client, requests = _http(lambda request: next(bodies), timeout=10)
    with pytest.raises(OpUnavailable):
        client.health()
    with pytest.raises(OpUnavailable):
        client.create_transfer(_transfer_body(ACCOUNT_ID), "payout:X:1")
    with pytest.raises(OpUnavailable):
        client.get_transfer(TRANSFER_ID)

    # R7: health con timeout de 2 s; el resto con OP_HTTP_TIMEOUT_SECONDS.
    assert requests[0].extensions["timeout"] == {"connect": 2.0, "read": 2.0, "write": 2.0, "pool": 2.0}
    assert requests[1].extensions["timeout"]["read"] == 10


def test_http_logs_calls_without_api_key(caplog):
    client, _ = _http(lambda request: httpx.Response(201, json=TRANSFER_JSON))
    with caplog.at_level(logging.INFO, logger="app.integrations.op.http"):
        client.create_transfer(_transfer_body(ACCOUNT_ID), "payout:X:1")
    assert "POST /v1/transfers" in caplog.text
    assert "201" in caplog.text and "ms" in caplog.text
    assert "payout:X:1" in caplog.text
    assert API_KEY not in caplog.text


def test_schemas_mirror_contract():
    """R2: montos con `from_money`/`to_money`; requests estrictos (`extra="forbid"`) y responses tolerantes."""
    assert Amount.from_money(cop(48_000)).model_dump() == COP_48000
    assert Amount.model_validate(COP_48000).to_money() == cop(48_000)
    with pytest.raises(ValueError):
        PositiveAmount.from_money(cop(0))
    with pytest.raises(ValueError):
        Amount.model_validate({"value": 4800000, "asset_code": "COP", "asset_scale": 2})  # nunca números
    with pytest.raises(ValueError):
        CreateTransferRequest(**_transfer_body(ACCOUNT_ID).model_dump(), sorpresa=1)
    with pytest.raises(ValueError):
        _transfer_body(ACCOUNT_ID, amount=cop(0))  # debit_amount es PositiveAmount
    tolerant = Transfer.model_validate({**TRANSFER_JSON, "campo_nuevo_de_go": True})
    assert str(tolerant.id) == TRANSFER_ID
    assert set(op_contract()["components"]["schemas"]) >= {
        "Amount", "PositiveAmount", "WalletAddress", "CreateCollectionAccountRequest", "CollectionAccount",
        "CreateTransferRequest", "Transfer", "CreatePaymentRequest", "Payment", "CreateSimQrPaymentRequest",
        "SimQrPayment", "SimQrPaymentList", "PuntoredPaymentEvent", "Health",
    }


def test_boot_resumes_sim_seq_and_skips_http(db, make_merchant, fake_op, settings, monkeypatch):
    merchant = make_merchant()
    row = CollectionAccountRow(
        merchant_id=merchant.id,
        op_collection_account_id=uuid.uuid4(),
        wallet_address="https://ilp.interledger-test.dev/trust-recaudo-03",
        qr_payload="https://pay.trustservices.com.co/qr/x",
        asset_code="COP",
        asset_scale=2,
        status="revoked",
    )
    db.add(row)
    db.flush()
    db.add(Sale(
        merchant_id=merchant.id, collection_account_id=row.id, bre_b_reference="BREB-SIM-000007",
        amount=4_800_000, asset_code="COP", asset_scale=2, paid_at=clock.now(), source="live", status="processed",
    ))
    db.add(OpCursor(name="sim_qr_payments", value=5))
    db.commit()

    with TestClient(create_app()):
        pass
    assert fake_op.get_collection_account(row.op_collection_account_id).status == "revoked"
    other = _account(fake_op, "merchant:B")
    assert other.wallet_address == "https://ilp.interledger-test.dev/trust-recaudo-01"
    assert fake_op.create_sim_qr_payment(_sim_body(other.id), "sim:1").bre_b_reference == "BREB-SIM-000008"

    # Con un cliente que no es el fake (OP_CLIENT=http) el lifespan no adopta nada.
    http = HttpOpClient(base_url="http://op-service:8080", api_key=API_KEY)
    app = create_app()
    app.dependency_overrides[get_op_client] = lambda: http
    fake_op.reset()
    with TestClient(app):
        pass
    assert fake_op.collection_accounts == []
    http.close()
