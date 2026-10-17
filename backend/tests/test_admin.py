"""16-admin: un test por criterio de aceptación (más reglas sin AC propio)."""

import itertools
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.events import Event
from app.core.money import Money
from app.integrations.op.boot import adopt_collection_accounts
from app.integrations.op.client import OpError, OpUnavailable
from app.modules.recaudo.models import CollectionAccount
from app.modules.sales.models import Payout, Sale
from app.modules.users.models import User
from app.worker import job_submit_payouts
from tests.helpers import assert_contract, cop
from tests.test_data_model import _loan
from tests.test_sales import _account

NOW = datetime(2026, 10, 16, 15, 42, tzinfo=UTC)
DASHBOARD = "/api/v1/admin/dashboard"
RETRY = "/api/v1/admin/payouts/{}/retry"
REVOKE = "/api/v1/admin/collection-accounts/{}/revoke"
WALLETS = {
    "lender": "https://ilp.interledger-test.dev/camilo",
    "merchant": "https://ilp.interledger-test.dev/tienda-la-esquina",
    "trust": "https://ilp.interledger-test.dev/trust-revenue",
}
SALE_AMOUNT = cop(48_000)
_seq = itertools.count(1)


@pytest.fixture(autouse=True)
def _frozen(freeze):
    freeze(NOW)


# ── Ayudas ────────────────────────────────────────────────────────────────────


def _sale(db: Session, merchant: User, account: CollectionAccount, amount: Money = SALE_AMOUNT) -> Sale:
    sale = Sale(
        merchant_id=merchant.id,
        collection_account_id=account.id,
        bre_b_reference=f"BREB-ADM-{next(_seq):06d}",
        amount=amount.value,
        asset_code=amount.asset_code,
        asset_scale=amount.asset_scale,
        paid_at=NOW,
        source="live",
        status="processed",
    )
    db.add(sale)
    db.commit()
    return sale


def _payout(db: Session, sale: Sale, leg: str, amount: Money, status: str, *, sent: Money | None = None,
            receiver: str | None = "auto", minutes_ago: int = 0, **fields) -> Payout:
    sent_total = sent.value if sent is not None else (amount.value if status == "completed" else 0)
    payout = Payout(
        sale_id=sale.id,
        leg=leg,
        receiver_wallet_url=WALLETS[leg] if receiver == "auto" else receiver,
        amount=amount.value,
        asset_code=amount.asset_code,
        asset_scale=amount.asset_scale,
        description=f"Pata {leg}",
        status=status,
        current_debit_amount=amount.value - sent_total if status != "completed" else amount.value,
        sent_total=sent_total,
        created_at=NOW - timedelta(minutes=minutes_ago),
        **fields,
    )
    db.add(payout)
    db.commit()
    return payout


def _get(client, auth, user) -> dict:
    response = client.get(DASHBOARD, headers=auth(user))
    assert response.status_code == 200, response.text
    assert_contract(response, "getAdminDashboard")
    return response.json()


def _error(response, status: int, code: str, operation_id: str) -> dict:
    assert response.status_code == status, response.text
    assert_contract(response, operation_id)
    error = response.json()["error"]
    assert error["code"] == code and error["message"]
    return error


def _retry(client, auth, admin, payout: Payout):
    return client.post(RETRY.format(payout.id), headers=auth(admin))


def _revoke(client, auth, admin, account: CollectionAccount):
    return client.post(REVOKE.format(account.id), headers=auth(admin))


def _events(db: Session, entity_type: str, entity_id: uuid.UUID, type: str) -> list[Event]:
    return list(
        db.scalars(
            select(Event).where(Event.entity_type == entity_type, Event.entity_id == entity_id, Event.type == type)
        )
    )


def _zero_counts(keys: str) -> dict[str, int]:
    return dict.fromkeys(keys.split(), 0)


PAYOUT_STATUSES = "pending processing completed failed_final held skipped"
LOAN_STATUSES = "pending_acceptance pending_disbursement disbursing active paid cancelled rejected expired"


# ── AC ────────────────────────────────────────────────────────────────────────


def test_ac1_admin_only(client, db, auth, make_merchant, make_lender, make_admin):
    merchant, lender = make_merchant(), make_lender()
    account = _account(db, merchant)
    payout = _payout(db, _sale(db, merchant, account), "lender", cop(7_200), "failed_final")
    calls = (
        ("get", DASHBOARD, "getAdminDashboard"),
        ("post", RETRY.format(payout.id), "retryPayout"),
        ("post", REVOKE.format(account.id), "revokeCollectionAccount"),
        ("post", RETRY.format(uuid.uuid4()), "retryPayout"),  # el rol se revisa antes que el id
        ("post", REVOKE.format("no-es-uuid"), "revokeCollectionAccount"),
    )
    for method, url, operation_id in calls:
        for user in (merchant, lender):
            _error(getattr(client, method)(url, headers=auth(user)), 403, "forbidden", operation_id)
        _error(getattr(client, method)(url), 401, "unauthorized", operation_id)

    db.expire_all()
    assert db.get(Payout, payout.id).status == "failed_final"
    assert db.get(CollectionAccount, account.id).status == "active"
    _get(client, auth, make_admin())


def test_ac2_dashboard_money(client, db, auth, make_merchant, make_admin):
    merchant = make_merchant(business_name="Tienda La Esquina", city="Bucaramanga")
    account = _account(db, merchant)
    sale, other_sale = _sale(db, merchant, account), _sale(db, merchant, account)
    _payout(db, sale, "trust", cop(1_500), "completed", minutes_ago=4)
    _payout(db, other_sale, "trust", cop(700), "completed", minutes_ago=3)
    failed = _payout(db, sale, "lender", cop(7_200), "failed_final", sent=cop(2_000), failure_code="invalid_amount",
                     attempt=2, minutes_ago=2)
    held = _payout(db, sale, "merchant", cop(40_800), "held", receiver=None, minutes_ago=1)

    body = _get(client, auth, make_admin())

    assert body["trust_revenue"] == [cop(2_200).to_api()]
    assert body["retained_funds"] == [cop(46_000).to_api()]  # $5.200 + $40.800
    assert body["in_flight"] == [cop(0).to_api()]
    assert body["trust_pending"] == [cop(0).to_api()]  # el asset está en `payouts`: entrada "0", nunca vacía
    assert body["payouts_by_status"] == {**_zero_counts(PAYOUT_STATUSES), "completed": 2, "failed_final": 1, "held": 1}
    assert body["loans_by_status"] == _zero_counts(LOAN_STATUSES)
    assert body["collection_accounts"] == {"active": 1, "revoked": 0}
    merchant_ref = {"id": str(merchant.id), "business_name": "Tienda La Esquina", "city": "Bucaramanga",
                    "initials": "TL"}
    assert body["retained_payouts"] == [  # R4: más recientes primero
        {"id": str(held.id), "leg": "merchant", "status": "held", "failure_code": None,
         "amount": cop(40_800).to_api(), "sent_total": cop(0).to_api(), "merchant": merchant_ref},
        {"id": str(failed.id), "leg": "lender", "status": "failed_final", "failure_code": "invalid_amount",
         "amount": cop(7_200).to_api(), "sent_total": cop(2_000).to_api(), "merchant": merchant_ref},
    ]


def test_ac3_dashboard_loans(client, db, auth, make_merchant, make_lender, make_admin):
    lender = make_lender()
    _loan(db, make_merchant(), lender, "active", trust_pending=cop(200).value)
    _loan(db, make_merchant(), lender, "paid", trust_pending=cop(150).value)
    _loan(db, make_merchant(), lender, "pending_acceptance")
    db.commit()

    body = _get(client, auth, make_admin())

    assert body["trust_pending"] == [cop(350).to_api()]
    assert body["trust_pending"][0]["value"] == "35000"
    assert body["loans_by_status"] == {**_zero_counts(LOAN_STATUSES), "active": 1, "paid": 1,
                                       "pending_acceptance": 1}
    assert set(body["loans_by_status"]) == set(LOAN_STATUSES.split())
    assert body["collection_accounts"] == {"active": 3, "revoked": 0}
    for key in ("trust_revenue", "retained_funds", "in_flight"):  # sin patas: "0" en el asset de los créditos
        assert body[key] == [cop(0).to_api()], key


def test_ac4_retry_payout(client, db, auth, make_merchant, make_admin, fake_op):
    merchant, admin = make_merchant(), make_admin()
    account = _account(db, merchant)
    payout = _payout(db, _sale(db, merchant, account), "lender", cop(7_200), "failed_final", sent=cop(2_000),
                     attempt=2, failure_code="invalid_amount", last_error="Transferencia fallida",
                     op_transfer_id=uuid.uuid4(), loan_id=None)

    response = _retry(client, auth, admin, payout)

    assert response.status_code == 200, response.text
    assert_contract(response, "retryPayout")
    body = response.json()
    assert body == {
        "id": str(payout.id),
        "sale_id": str(payout.sale_id),
        "loan_id": None,
        "leg": "lender",
        "status": "pending",
        "attempt": 3,
        "amount": cop(7_200).to_api(),
        "sent_total": cop(2_000).to_api(),
        "current_debit_amount": cop(5_200).to_api(),
        "failure_code": None,
        "next_attempt_at": "2026-10-16T15:42:00Z",
    }
    assert body["current_debit_amount"]["value"] == "520000"
    db.expire_all()
    row = db.get(Payout, payout.id)
    assert (row.op_transfer_id, row.failure_code, row.last_error) == (None, None, None)
    assert row.next_attempt_at == NOW and row.receiver_wallet_url == WALLETS["lender"]
    (event,) = _events(db, "payout", payout.id, "manual_retry")
    assert event.actor_id == admin.id
    assert event.data == {"attempt": 3, "current_debit_amount": 520_000}

    # El worker (08) la envía con la clave del intento nuevo y el resto por enviar.
    adopt_collection_accounts(db, fake_op)
    job_submit_payouts(db, fake_op, NOW)
    (call,) = fake_op.calls_to("create_transfer")
    assert call.idempotency_key == f"payout:{payout.id}:3"
    assert call.body.debit_amount.to_money() == cop(5_200)
    db.expire_all()
    assert db.get(Payout, payout.id).status == "processing"


def test_ac5_retry_rejections(client, db, auth, make_merchant, make_admin):
    admin = make_admin()
    merchant = make_merchant()
    account = _account(db, merchant)
    completed = _payout(db, _sale(db, merchant, account), "trust", cop(700), "completed")
    _error(_retry(client, auth, admin, completed), 409, "invalid_state", "retryPayout")

    other = make_merchant()
    revoked = _account(db, other, "revoked")
    held_revoked = _payout(db, _sale(db, other, revoked), "lender", cop(7_200), "held")
    _error(_retry(client, auth, admin, held_revoked), 409, "collection_account_not_active", "retryPayout")

    no_wallet = make_merchant(wallet=False)
    no_wallet_account = _account(db, no_wallet)
    leg = _payout(db, _sale(db, no_wallet, no_wallet_account), "merchant", cop(40_800), "held", receiver=None)
    _error(_retry(client, auth, admin, leg), 422, "wallet_required", "retryPayout")
    db.expire_all()
    assert db.get(Payout, leg.id).status == "held" and db.get(Payout, leg.id).attempt == 1

    # Ya vinculó su wallet: 200 y la pata toma esa wallet.
    user = db.get(User, no_wallet.id)
    user.payout_wallet_url = "https://ilp.interledger-test.dev/la-nueva-de-marta"
    db.commit()
    response = _retry(client, auth, admin, leg)
    assert response.status_code == 200, response.text
    assert_contract(response, "retryPayout")
    assert (response.json()["status"], response.json()["attempt"]) == ("pending", 2)
    db.expire_all()
    assert db.get(Payout, leg.id).receiver_wallet_url == "https://ilp.interledger-test.dev/la-nueva-de-marta"
    assert all(db.get(Payout, p.id).status != "pending" for p in (completed, held_revoked))


def test_ac6_revoke_account(client, db, auth, make_merchant, make_admin, fake_op):
    admin, merchant = make_admin(), make_merchant()
    account = _account(db, merchant)
    adopt_collection_accounts(db, fake_op)
    sale = _sale(db, merchant, account)
    pending = [_payout(db, sale, "lender", cop("7.069,10"), "pending"),
               _payout(db, sale, "merchant", cop(40_800), "pending")]
    processing = _payout(db, sale, "trust", cop("130,90"), "processing", op_transfer_id=uuid.uuid4())
    neighbor = make_merchant()
    elsewhere = _payout(db, _sale(db, neighbor, _account(db, neighbor)), "merchant", cop(48_000), "pending")
    fake_op.calls.clear()

    response = _revoke(client, auth, admin, account)

    assert response.status_code == 200, response.text
    assert_contract(response, "revokeCollectionAccount")
    body = response.json()
    assert body["held_payouts"] == 2
    assert body["collection_account"]["id"] == str(account.id)
    assert body["collection_account"]["status"] == "revoked"
    assert body["collection_account"]["revoked_at"] == "2026-10-16T15:42:00Z"
    db.expire_all()
    row = db.get(CollectionAccount, account.id)
    assert row.status == "revoked" and row.revoked_at == NOW
    assert [db.get(Payout, p.id).status for p in pending] == ["held", "held"]
    assert db.get(Payout, processing.id).status == "processing"
    assert db.get(Payout, elsewhere.id).status == "pending"  # otra cuenta: no se toca
    (call,) = fake_op.calls_to("revoke_collection_account")
    assert call.args == {"collection_account_id": account.op_collection_account_id}
    assert {a.id: a.status for a in fake_op.collection_accounts}[account.op_collection_account_id] == "revoked"
    (event,) = _events(db, "collection_account", account.id, "revoked")
    assert event.actor_id == admin.id
    for payout in pending:
        (held,) = _events(db, "payout", payout.id, "held")
        assert held.actor_id == admin.id
    assert _events(db, "payout", processing.id, "held") == []


def test_ac7_revoke_commit_before_op(client, db, auth, make_merchant, make_admin, fake_op):
    admin, merchant = make_admin(), make_merchant()
    account = _account(db, merchant)
    adopt_collection_accounts(db, fake_op)
    leg = _payout(db, _sale(db, merchant, account), "merchant", cop(40_800), "pending")
    fake_op.calls.clear()
    fake_op.fail_next("revoke_collection_account", OpUnavailable("x"))

    _error(_revoke(client, auth, admin, account), 503, "op_unavailable", "revokeCollectionAccount")

    db.expire_all()
    assert db.get(CollectionAccount, account.id).status == "revoked"  # la revocación local se queda
    assert db.get(Payout, leg.id).status == "held"
    assert len(fake_op.calls_to("revoke_collection_account")) == 1

    again = _revoke(client, auth, admin, account)

    assert again.status_code == 200, again.text
    assert_contract(again, "revokeCollectionAccount")
    assert again.json()["held_payouts"] == 0
    assert again.json()["collection_account"]["status"] == "revoked"
    assert len(fake_op.calls_to("revoke_collection_account")) == 2  # Go se llamó otra vez
    assert len(_events(db, "collection_account", account.id, "revoked")) == 1


def test_ac8_admin_not_found(client, auth, make_admin):
    admin = make_admin()
    for missing in (uuid.uuid4(), "no-es-un-uuid"):
        _error(client.post(RETRY.format(missing), headers=auth(admin)), 404, "not_found", "retryPayout")
        _error(client.post(REVOKE.format(missing), headers=auth(admin)), 404, "not_found",
               "revokeCollectionAccount")


# ── Reglas sin AC propio ──────────────────────────────────────────────────────


def test_r2_empty_dashboard_and_other_assets(client, db, auth, make_merchant, make_admin):
    admin = make_admin()
    empty = _get(client, auth, admin)
    for key in ("trust_revenue", "trust_pending", "retained_funds", "in_flight"):
        assert empty[key] == [cop(0).to_api()], key  # sin filas: "0" en el asset de mercado
    assert empty["payouts_by_status"] == _zero_counts(PAYOUT_STATUSES)
    assert empty["loans_by_status"] == _zero_counts(LOAN_STATUSES)
    assert empty["collection_accounts"] == {"active": 0, "revoked": 0}
    assert empty["retained_payouts"] == []

    # Partición con patas en camino y un asset distinto (el de mercado va primero).
    merchant = make_merchant()
    account = _account(db, merchant)
    sale = _sale(db, merchant, account)
    _payout(db, sale, "lender", cop(7_200), "processing", sent=cop(1_000), op_transfer_id=uuid.uuid4())
    _payout(db, sale, "merchant", cop(40_800), "pending")
    _payout(db, sale, "trust", cop(150), "failed_final", sent=cop(50))
    usd_account = _account(db, make_merchant(), "revoked", asset_code="USD", asset_scale=2)
    usd_sale = _sale(db, merchant, usd_account, Money(1_000, "USD", 2))
    _payout(db, usd_sale, "trust", Money(300, "USD", 2), "completed")
    _payout(db, usd_sale, "merchant", Money(700, "USD", 2), "skipped")

    body = _get(client, auth, admin)

    usd = lambda value: Money(value, "USD", 2).to_api()  # noqa: E731
    assert body["trust_revenue"] == [cop(50).to_api(), usd(300)]
    assert body["retained_funds"] == [cop(100).to_api(), usd(0)]
    assert body["in_flight"] == [cop(47_000).to_api(), usd(0)]  # $6.200 + $40.800
    assert body["trust_pending"] == [cop(0).to_api(), usd(0)]
    assert body["payouts_by_status"] == {**_zero_counts(PAYOUT_STATUSES), "processing": 1, "pending": 1,
                                         "failed_final": 1, "completed": 1, "skipped": 1}
    assert body["collection_accounts"] == {"active": 1, "revoked": 1}


def test_r4_retained_payouts_limit(client, db, auth, make_merchant, make_admin):
    merchant = make_merchant()
    account = _account(db, merchant)
    newest = None
    for n in range(26):
        sale = _sale(db, merchant, account)
        _payout(db, sale, "lender", cop(7_200), "failed_final", minutes_ago=200 - 2 * n)
        newest = _payout(db, sale, "merchant", cop(40_800), "held", minutes_ago=199 - 2 * n)

    body = _get(client, auth, make_admin())

    assert len(body["retained_payouts"]) == 50
    assert body["retained_payouts"][0]["id"] == str(newest.id)
    assert body["payouts_by_status"]["failed_final"] + body["payouts_by_status"]["held"] == 52


def test_r5_retry_receivers_and_nothing_left(client, db, auth, make_merchant, make_admin, settings, monkeypatch):
    admin, merchant = make_admin(), make_merchant()
    account = _account(db, merchant)

    trust_leg = _payout(db, _sale(db, merchant, account), "trust", cop("130,90"), "held", receiver=None)
    monkeypatch.setattr(settings, "trust_revenue_wallet_address", "")
    _error(_retry(client, auth, admin, trust_leg), 409, "invalid_state", "retryPayout")
    monkeypatch.setattr(settings, "trust_revenue_wallet_address", WALLETS["trust"])
    response = _retry(client, auth, admin, trust_leg)
    assert response.status_code == 200, response.text
    assert_contract(response, "retryPayout")
    db.expire_all()
    assert db.get(Payout, trust_leg.id).receiver_wallet_url == WALLETS["trust"]

    # Ya no está failed_final/held (quedó pending): 409.
    _error(_retry(client, auth, admin, trust_leg), 409, "invalid_state", "retryPayout")

    # Nada por enviar (amount − sent_total ≤ 0): 409 invalid_state.
    spent = _payout(db, _sale(db, merchant, account), "lender", cop(7_200), "failed_final", sent=cop(7_200))
    _error(_retry(client, auth, admin, spent), 409, "invalid_state", "retryPayout")
    db.expire_all()
    assert db.get(Payout, spent.id).status == "failed_final"
    assert _events(db, "payout", spent.id, "manual_retry") == []


def test_r6_revoke_op_errors(client, db, auth, make_merchant, make_admin, fake_op):
    admin = make_admin()

    # Go ya no la tiene (404): cuenta como revocada.
    unknown = _account(db, make_merchant())  # no está en el fake: su DELETE da 404
    response = _revoke(client, auth, admin, unknown)
    assert response.status_code == 200, response.text
    assert_contract(response, "revokeCollectionAccount")
    assert response.json()["collection_account"]["status"] == "revoked"

    # Otro OpError: 502 y la revocación local se queda.
    rejected = _account(db, make_merchant())
    adopt_collection_accounts(db, fake_op)
    fake_op.fail_next("revoke_collection_account", OpError(409, "invalid_state", "no"))
    _error(_revoke(client, auth, admin, rejected), 502, "op_unavailable", "revokeCollectionAccount")
    db.expire_all()
    assert db.get(CollectionAccount, rejected.id).status == "revoked"
