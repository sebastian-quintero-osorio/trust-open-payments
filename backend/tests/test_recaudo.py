"""06-recaudo: un test por criterio de aceptación (más reglas sin AC propio)."""

import logging
import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.db import SessionLocal
from app.core.events import Event
from app.integrations.op.client import OpError, OpUnavailable
from app.integrations.op.fake import POOL_WALLET_PREFIX
from app.integrations.op.schemas import CreateCollectionAccountRequest
from app.modules.recaudo import service
from app.modules.recaudo.models import CollectionAccount
from tests.helpers import assert_contract, cop

URL = "/api/v1/me/collection-account"


def _error(response, status: int, code: str, operation_id: str) -> dict:
    assert response.status_code == status, response.text
    assert_contract(response, operation_id)
    error = response.json()["error"]
    assert error["code"] == code
    return error


def _create(client, auth, merchant, status: int = 201) -> dict:
    response = client.post(URL, headers=auth(merchant))
    assert response.status_code == status, response.text
    assert_contract(response, "createCollectionAccount")
    return response.json()


def _rows(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(CollectionAccount))


def _revoke(db: Session, fake_op, account_id: str) -> CollectionAccount:
    """Lo que hará 16-admin: la cuenta queda `revoked` en Trust y en Go."""
    account = db.get(CollectionAccount, uuid.UUID(account_id))
    account.status, account.revoked_at = "revoked", clock.now()
    db.commit()
    fake_op.revoke_collection_account(account.op_collection_account_id)
    return account


# ── AC ────────────────────────────────────────────────────────────────────────


def test_ac1_get_without_account(client, auth, make_merchant, make_lender, fake_op):
    _error(client.get(URL, headers=auth(make_merchant())), 404, "not_found", "getCollectionAccount")
    _error(client.get(URL, headers=auth(make_lender())), 403, "forbidden", "getCollectionAccount")
    assert fake_op.calls == []  # solo lee la base


def test_ac2_wallet_required(client, db, auth, make_merchant, fake_op):
    merchant = make_merchant(wallet=False)

    error = _error(client.post(URL, headers=auth(merchant)), 422, "wallet_required", "createCollectionAccount")

    assert error["message"]
    assert fake_op.calls_to("create_collection_account") == []
    assert _rows(db) == 0


def test_ac3_create_account(client, db, auth, make_merchant, fake_op):
    merchant = make_merchant(business_name="Tienda La Esquina")

    body = _create(client, auth, merchant, 201)

    assert body["status"] == "active" and body["revoked_at"] is None
    (remote,) = fake_op.collection_accounts
    assert body["qr_payload"] == remote.qr_payload
    assert body["wallet_address"] == remote.wallet_address
    assert body["asset"] == {"asset_code": "COP", "asset_scale": 2}
    assert body["balance"] == cop(0).to_api()
    account = db.get(CollectionAccount, uuid.UUID(body["id"]))  # id de Trust, no el de Go
    assert account.op_collection_account_id == remote.id and account.id != remote.id
    assert account.merchant_id == merchant.id

    (call,) = fake_op.calls_to("create_collection_account")
    assert call.idempotency_key == f"merchant:{merchant.id}:recaudo"
    assert call.body.model_dump() == {
        "external_ref": f"merchant:{merchant.id}",
        "display_name": "Tienda La Esquina",
        "asset_code": "COP",
    }
    events = db.scalars(
        select(Event).where(Event.entity_type == "collection_account", Event.entity_id == account.id)
    ).all()
    assert [(e.type, e.actor_id) for e in events] == [("assigned", merchant.id)]
    assert events[0].data == {"op_id": str(remote.id), "wallet_address": remote.wallet_address}


def test_ac4_post_idempotent(client, db, auth, make_merchant, fake_op):
    merchant = make_merchant()
    first = _create(client, auth, merchant, 201)
    fake_op.calls.clear()

    second = _create(client, auth, merchant, 200)

    assert second["id"] == first["id"]
    assert second == first
    assert fake_op.calls == []  # sin otra llamada a Go
    assert _rows(db) == 1


def test_ac5_already_assigned_recovers(client, db, auth, make_merchant, fake_op):
    merchant = make_merchant()
    remote = fake_op.create_collection_account(
        CreateCollectionAccountRequest(
            external_ref=f"merchant:{merchant.id}", display_name="Tienda La Esquina", asset_code="COP"
        ),
        idempotency_key="otra-clave",
    )
    fake_op.calls.clear()

    body = _create(client, auth, merchant, 201)

    account = db.get(CollectionAccount, uuid.UUID(body["id"]))
    assert account.op_collection_account_id == remote.id
    assert body["qr_payload"] == remote.qr_payload
    assert [c.args["external_ref"] for c in fake_op.calls_to("find_collection_accounts")] == [
        f"merchant:{merchant.id}"
    ]
    assert len(fake_op.collection_accounts) == 1  # no se asignó otra wallet del pool


def test_ac6_op_errors(client, db, auth, make_merchant, fake_op):
    merchant = make_merchant()

    fake_op.reset(pool_size=0)
    _error(client.post(URL, headers=auth(merchant)), 409, "pool_exhausted", "createCollectionAccount")
    assert _rows(db) == 0

    fake_op.fail_next("create_collection_account", OpUnavailable("x"))
    _error(client.post(URL, headers=auth(make_merchant())), 503, "op_unavailable", "createCollectionAccount")
    assert _rows(db) == 0
    assert db.scalar(select(func.count()).select_from(Event).where(Event.entity_type == "collection_account")) == 0


def test_ac7_balance(client, db, auth, make_merchant):
    merchant = make_merchant()
    created = _create(client, auth, merchant)
    account = db.get(CollectionAccount, uuid.UUID(created["id"]))
    account.ledger_in, account.ledger_out = cop(48_000).value, cop(7_200).value
    db.commit()

    response = client.get(URL, headers=auth(merchant))

    assert response.status_code == 200, response.text
    assert_contract(response, "getCollectionAccount")
    assert response.json()["balance"] == {"value": "4080000", "asset_code": "COP", "asset_scale": 2}  # $40.800


def test_ac8_reassign_after_revoke(client, db, auth, make_merchant, fake_op):
    merchant = make_merchant()
    old = _create(client, auth, merchant)
    _revoke(db, fake_op, old["id"])
    fake_op.calls.clear()

    new = _create(client, auth, merchant, 201)

    assert new["id"] != old["id"] and new["status"] == "active"
    (call,) = fake_op.calls_to("create_collection_account")
    assert call.idempotency_key == f"merchant:{merchant.id}:recaudo:2"
    response = client.get(URL, headers=auth(merchant))
    assert_contract(response, "getCollectionAccount")
    assert response.json()["id"] == new["id"]  # la activa gana sobre la revocada


def test_ac9_concurrent_insert_returns_existing(db, make_merchant, fake_op, monkeypatch):
    merchant = make_merchant()
    original = fake_op.create_collection_account
    parallel: dict[str, uuid.UUID] = {}

    def create_and_race(body, idempotency_key):
        remote = original(body, idempotency_key)
        with SessionLocal() as other:  # otro POST guardó la misma cuenta (mismo replay de Go) antes que este
            row = CollectionAccount(
                merchant_id=merchant.id,
                op_collection_account_id=remote.id,
                wallet_address=remote.wallet_address,
                qr_payload=remote.qr_payload,
                asset_code=remote.asset_code,
                asset_scale=remote.asset_scale,
                status="active",
            )
            other.add(row)
            other.commit()
            parallel["id"] = row.id
        return remote

    monkeypatch.setattr(fake_op, "create_collection_account", create_and_race)

    account = service.ensure_collection_account(db, fake_op, merchant)

    assert account.id == parallel["id"]
    assert account.status == "active"
    assert _rows(db) == 1


# ── Reglas sin AC propio ──────────────────────────────────────────────────────


def test_r_auth_required(client, fake_op):
    _error(client.get(URL), 401, "unauthorized", "getCollectionAccount")
    _error(client.post(URL), 401, "unauthorized", "createCollectionAccount")
    assert fake_op.calls == []


def test_r1_get_returns_latest_revoked(client, db, auth, make_merchant, make_admin, fake_op):
    merchant = make_merchant()
    created = _create(client, auth, merchant)
    revoked = _revoke(db, fake_op, created["id"])
    fake_op.calls.clear()

    response = client.get(URL, headers=auth(merchant))

    assert response.status_code == 200, response.text
    assert_contract(response, "getCollectionAccount")
    body = response.json()
    assert (body["id"], body["status"]) == (created["id"], "revoked")
    assert body["revoked_at"].endswith("Z")
    assert datetime.fromisoformat(body["revoked_at"]) == revoked.revoked_at
    assert fake_op.calls == []
    _error(client.post(URL, headers=auth(make_admin())), 403, "forbidden", "createCollectionAccount")


def test_r4_pool_exhausted_then_refilled(client, db, auth, make_merchant, fake_op):
    """R4.3 + R4.5: Go guarda 72 h la 409 `pool_exhausted` por clave; el siguiente intento usa otra clave y, si el
    pool ya tiene wallets, obtiene su cuenta."""
    merchant = make_merchant()
    fake_op.reset(pool_size=0)

    _error(client.post(URL, headers=auth(merchant)), 409, "pool_exhausted", "createCollectionAccount")
    assert _rows(db) == 0
    (event,) = db.scalars(
        select(Event).where(Event.entity_type == "user", Event.type == "collection_account_pool_exhausted")
    ).all()
    assert (event.entity_id, event.actor_id) == (merchant.id, merchant.id)
    assert event.data == {"idempotency_key": f"merchant:{merchant.id}:recaudo"}

    fake_op._pool.append(f"{POOL_WALLET_PREFIX}99")  # el pool vuelve a tener una wallet libre

    body = _create(client, auth, merchant, 201)

    assert body["status"] == "active"
    assert body["wallet_address"] == f"{POOL_WALLET_PREFIX}99"
    assert [c.idempotency_key for c in fake_op.calls_to("create_collection_account")] == [
        f"merchant:{merchant.id}:recaudo",
        f"merchant:{merchant.id}:recaudo:2",
    ]


def test_r4_other_op_errors(client, db, auth, make_merchant, fake_op):
    merchant = make_merchant()

    # already_assigned pero Go no devuelve ninguna activa → 502.
    fake_op.fail_next("create_collection_account", OpError(409, "already_assigned", "ya tiene"))
    _error(client.post(URL, headers=auth(merchant)), 502, "op_unavailable", "createCollectionAccount")
    # already_assigned y la búsqueda cae → 503.
    fake_op.fail_next("create_collection_account", OpError(409, "already_assigned", "ya tiene"))
    fake_op.fail_next("find_collection_accounts", OpUnavailable("x"))
    _error(client.post(URL, headers=auth(merchant)), 503, "op_unavailable", "createCollectionAccount")
    # Cualquier otro OpError (también uno reintentable) → 502.
    for failure in (OpError(400, "validation_error", "mal"), OpError(409, "request_in_progress", "", True)):
        fake_op.fail_next("create_collection_account", failure)
        _error(client.post(URL, headers=auth(merchant)), 502, "op_unavailable", "createCollectionAccount")
    assert _rows(db) == 0

    # Sin cambios guardados: el negocio puede reintentar y obtiene su cuenta con la clave base.
    _create(client, auth, merchant, 201)
    assert fake_op.calls_to("create_collection_account")[-1].idempotency_key == f"merchant:{merchant.id}:recaudo"


def test_r6_foreign_asset_saved_and_health_degraded(client, db, auth, make_merchant, fake_op, caplog):
    merchant = make_merchant()
    op_id = uuid.uuid4()
    fake_op.adopt_collection_account(
        op_id, f"merchant:{merchant.id}", "https://ilp.interledger-test.dev/trust-recaudo-usd",
        f"https://pay.trustservices.com.co/qr/{op_id}", "COP", 0, "active",
    )

    with caplog.at_level(logging.ERROR, logger="app.modules.recaudo.service"):
        body = _create(client, auth, merchant, 201)

    assert body["asset"] == {"asset_code": "COP", "asset_scale": 0}  # se guarda con el asset que dio Go
    assert body["balance"] == {"value": "0", "asset_code": "COP", "asset_scale": 0}
    assert any("market_asset_mismatch" in r.getMessage() for r in caplog.records if r.levelno == logging.ERROR)
    health = client.get("/api/v1/health")
    assert_contract(health, "getHealth")
    assert health.json()["status"] == "degraded"
    assert "market_asset_mismatch: expected COP/2, collection_account COP/0" in health.json()["warnings"]


def test_r4_display_name_truncated(client, auth, make_merchant, fake_op):
    merchant = make_merchant(business_name="Ñ" * 150)

    _create(client, auth, merchant, 201)

    (call,) = fake_op.calls_to("create_collection_account")
    assert call.body.display_name == "Ñ" * 100
