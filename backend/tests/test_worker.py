"""08-worker: un test por criterio de aceptación (más reglas sin AC propio).

El worker no tiene endpoints: no hay `assert_contract` de la Trust API; los cuerpos que manda a Go se validan contra
`contracts/op-service.openapi.yaml` con `assert_op_schema`.
"""

import itertools
import threading
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import worker
from app.core import clock
from app.core.db import SessionLocal
from app.core.events import Event
from app.core.money import Money
from app.integrations.op.boot import adopt_collection_accounts
from app.integrations.op.client import OpError, OpUnavailable, request_json
from app.integrations.op.schemas import (
    Amount,
    CreatePaymentRequest,
    CreateSimQrPaymentRequest,
    PositiveAmount,
    PuntoredPaymentEvent,
    SimQrPayment,
    SimQrPaymentList,
)
from app.main import create_app
from app.modules.loans import service as loans_service
from app.modules.loans.models import Loan
from app.modules.notifications.models import Notification
from app.modules.recaudo.models import CollectionAccount
from app.modules.sales.models import Payout, Sale, WebhookEvent
from app.modules.sales.service import process_puntored_event
from app.modules.worker.models import OpCursor
from app.worker import run_once
from tests.helpers import assert_op_schema, cop
from tests.test_data_model import _assessment
from tests.test_sales import _account, _event

NOW = datetime(2026, 10, 16, 15, 42, tzinfo=UTC)  # 10:42 a. m. en Bogotá
BOGOTA = ZoneInfo("America/Bogota")
LEGS = ("lender", "merchant", "trust")
S = timedelta(seconds=1)
SALE_AMOUNT = cop(48_000)


@pytest.fixture(autouse=True)
def _env(freeze):
    freeze(NOW)


# ── Ayudas ────────────────────────────────────────────────────────────────────


def _loan(db: Session, make_merchant, make_lender, **overrides) -> SimpleNamespace:
    """Crédito tipo (CT): $2.000.000 + cargo 10 % = $2.200.000, Trust 20 % del cargo, 15 % por venta (07)."""
    merchant, lender = make_merchant(), make_lender()
    account = _account(db, merchant)
    fields = {
        "merchant_id": merchant.id,
        "lender_id": lender.id,
        "origin": "offer_taken",
        "created_by": merchant.id,
        "credit_assessment_id": _assessment(db, merchant).id,
        "collection_account_id": account.id,
        "merchant_payout_wallet_url": merchant.payout_wallet_url,
        "lender_wallet_url": lender.payout_wallet_url,
        "lender_asset_code": "COP",
        "lender_asset_scale": 2,
        "asset_code": "COP",
        "asset_scale": 2,
        "principal": cop(2_000_000).value,
        "fee_bps": 1000,
        "fee_amount": cop(200_000).value,
        "total_repayable": cop(2_200_000).value,
        "trust_share_bps": 2000,
        "term_months": 3,
        "monthly_minimum": cop("733.333,34").value,
        "pct_initial_bps": 1500,
        "pct_min_bps": 1000,
        "pct_max_bps": 2000,
        "terms": {},
        "status": "active",
        "pct_bps": 1500,
        "activated_at": clock.now(),
        **overrides,
    }
    loan = Loan(**fields)
    db.add(loan)
    db.commit()
    return SimpleNamespace(merchant=merchant, lender=lender, account=account, loan=loan)


def _sale(db: Session, account: CollectionAccount, amount: Money = SALE_AMOUNT) -> uuid.UUID:
    result = process_puntored_event(db, _event(account, amount), "puntored")
    db.commit()
    assert result.status == "processed"
    return result.sale_id


@pytest.fixture
def ct(db, make_merchant, make_lender, fake_op) -> SimpleNamespace:
    """CT `active` con su cuenta de recaudo también registrada en el fake (como tras el arranque, 02 R15)."""
    ct = _loan(db, make_merchant, make_lender)
    adopt_collection_accounts(db, fake_op)
    return ct


@pytest.fixture
def base(db, ct) -> SimpleNamespace:
    """La venta del AC-1 de 07: $48.000 → patas `pending` lender $7.069,10, merchant $40.800, trust $130,90."""
    ct.sale_id = _sale(db, ct.account)
    return ct


def _legs(db: Session, sale_id: uuid.UUID) -> dict[str, Payout]:
    db.expire_all()
    return {p.leg: p for p in db.scalars(select(Payout).where(Payout.sale_id == sale_id))}


def _refresh(db: Session, obj):
    db.expire_all()
    return db.get(type(obj), obj.id)


def _keys(fake_op, method: str = "create_transfer") -> list[str]:
    return [call.idempotency_key for call in fake_op.calls_to(method)]


def _events(db: Session, entity_id: uuid.UUID, type: str) -> list[Event]:
    return list(db.scalars(select(Event).where(Event.entity_id == entity_id, Event.type == type)))


def _notifications(db: Session, type: str) -> list[Notification]:
    return list(db.scalars(select(Notification).where(Notification.type == type).order_by(Notification.created_at)))


def _cursor(db: Session, name: str) -> int | None:
    db.expire_all()
    return db.scalar(select(OpCursor.value).where(OpCursor.name == name))


def _with_loans_fns(monkeypatch, **fns) -> None:
    """Reemplaza las funciones de 11-loans que ve el worker (las demás: `None`, el job no hace nada)."""
    monkeypatch.setattr(worker, "_loans_fn", lambda name: fns.get(name))


# ── AC ────────────────────────────────────────────────────────────────────────


def test_ac1_submits_in_leg_order(db, fake_op, base):
    run_once(db, fake_op, NOW)

    legs = _legs(db, base.sale_id)
    calls = fake_op.calls_to("create_transfer")
    assert [c.idempotency_key for c in calls] == [f"payout:{legs[leg].id}:1" for leg in LEGS]
    assert [c.body.debit_amount.value for c in calls] == ["706910", "4080000", "13090"]
    transfers = {t.external_ref: t for t in fake_op.transfers}
    for call, leg in zip(calls, LEGS, strict=True):
        payout = legs[leg]
        body = call.body
        assert body.debit_amount.to_money() == Money(payout.current_debit_amount, "COP", 2)
        assert body.description == payout.description  # congelada por 07 (hora de la venta)
        assert body.external_ref == f"payout:{payout.id}"
        assert body.receiver_wallet_address == payout.receiver_wallet_url
        assert body.collection_account_id == base.account.op_collection_account_id
        assert_op_schema(request_json(body), "CreateTransferRequest")
        assert (payout.status, payout.attempt, payout.next_attempt_at) == ("processing", 1, None)
        assert payout.op_transfer_id == transfers[f"payout:{payout.id}"].id
        assert len(_events(db, payout.id, "processing")) == 1
    assert legs["lender"].description == "Abono venta 10:42 a. m. · Tienda La Esquina"


def test_ac2_unavailable_keeps_same_key(db, fake_op, base):
    # La tabla de R4: Go caído, 401 y 409 request_in_progress se reintentan con la misma clave.
    fake_op.fail_next("create_transfer", OpUnavailable("op-service caído"))
    fake_op.fail_next("create_transfer", OpError(401, "unauthorized", "API key inválida"))
    fake_op.fail_next("create_transfer", OpError(409, "request_in_progress", "En curso", True))

    run_once(db, fake_op, NOW)

    legs = _legs(db, base.sale_id)
    for payout in legs.values():
        assert (payout.status, payout.attempt, payout.next_attempt_at) == ("pending", 1, NOW + 10 * S)
        assert payout.op_transfer_id is None
    first_keys = _keys(fake_op)
    assert first_keys == [f"payout:{legs[leg].id}:1" for leg in LEGS]

    fake_op.calls.clear()
    run_once(db, fake_op, NOW + 5 * S)
    assert fake_op.calls_to("create_transfer") == []

    run_once(db, fake_op, NOW + 11 * S)
    assert _keys(fake_op) == first_keys
    assert {leg: p.status for leg, p in _legs(db, base.sale_id).items()} == dict.fromkeys(LEGS, "processing")


def test_ac3_422_failed_final(db, fake_op, base, make_admin):
    admins = [make_admin(), make_admin()]
    fake_op.fail_next("create_transfer", OpError(422, "invalid_amount", "Monto inválido"))

    run_once(db, fake_op, NOW)

    legs = _legs(db, base.sale_id)
    lender = legs["lender"]
    assert (lender.status, lender.failure_code, lender.next_attempt_at) == ("failed_final", "invalid_amount", None)
    assert "invalid_amount" in lender.last_error
    assert (legs["merchant"].status, legs["trust"].status) == ("processing", "processing")
    assert len(_events(db, lender.id, "failed_final")) == 1

    notes = _notifications(db, "payout_failed")
    assert sorted(n.user_id for n in notes) == sorted(a.id for a in admins)  # solo admins, uno por admin
    assert notes[0].title == "Transferencia fallida"
    assert notes[0].body == "Pata prestamista de $7.069,10 · invalid_amount. La plata queda en recaudo."
    assert notes[0].data == {
        "payout_id": str(lender.id),
        "sale_id": str(base.sale_id),
        "leg": "lender",
        "failure_code": "invalid_amount",
        "loan_id": str(base.loan.id),
    }


def test_ac4_account_not_active_holds(db, fake_op, base):
    fake_op.fail_next("create_transfer", OpError(409, "collection_account_not_active", "Cuenta no activa"))

    run_once(db, fake_op, NOW)

    assert len(fake_op.calls_to("create_transfer")) == 1  # R3: no se insiste con las otras patas de esa cuenta
    account = _refresh(db, base.account)
    assert (account.status, account.revoked_at) == ("revoked", NOW)
    legs = _legs(db, base.sale_id)
    assert {leg: p.status for leg, p in legs.items()} == dict.fromkeys(LEGS, "held")
    assert len(_events(db, account.id, "revoked")) == 1
    assert all(len(_events(db, p.id, "held")) == 1 for p in legs.values())


def test_ac5_completed_updates_ledgers(db, fake_op, base):
    fake_op.set_wallet(base.lender.payout_wallet_url, asset_code="USD", asset_scale=2)
    base.loan.lender_asset_code = "USD"
    db.commit()
    run_once(db, fake_op, NOW)
    legs = _legs(db, base.sale_id)
    usd = Amount(value="177", asset_code="USD", asset_scale=2)
    transfers = {
        leg: fake_op.complete_transfer(legs[leg].op_transfer_id, receive=usd if leg == "lender" else None)
        for leg in LEGS
    }

    run_once(db, fake_op, NOW + 2 * S)

    legs = _legs(db, base.sale_id)
    for leg, payout in legs.items():
        transfer = transfers[leg]
        assert (payout.status, payout.sent_total, payout.completed_at) == ("completed", payout.amount, NOW)
        assert payout.incoming_payment_url == transfer.incoming_payment_url is not None
        assert payout.outgoing_payment_url == transfer.outgoing_payment_url is not None
        assert len(_events(db, payout.id, "completed")) == 1
    receive = {leg: (p.receive_value, p.receive_asset_code, p.receive_asset_scale) for leg, p in legs.items()}
    assert receive == {"lender": (177, "USD", 2), "merchant": (4_080_000, "COP", 2), "trust": (13_090, "COP", 2)}
    assert _refresh(db, base.account).ledger_out == cop(48_000).value
    assert _refresh(db, base.loan).delivered_total == cop(7_200).value  # lender + trust, no merchant

    notes = _notifications(db, "abono_received")
    assert [n.user_id for n in notes] == [base.lender.id]
    note = notes[0]
    assert note.title == "Abono recibido · +$7.200 · Tienda La Esquina"
    assert note.body == "Venta de $48.000 a las 10:42 a. m. Parte Trust $130,90, neto $7.069,10."
    repayment_id = note.data.pop("repayment_id")
    assert uuid.UUID(repayment_id)
    assert note.data == {
        "loan_id": str(base.loan.id),
        "sale_id": str(base.sale_id),
        "payout_id": str(legs["lender"].id),
        "gross": cop(7_200).to_api(),
        "trust_cut": cop("130,90").to_api(),
        "net": cop("7.069,10").to_api(),
        "received": {"value": "177", "asset_code": "USD", "asset_scale": 2},
    }


def test_ac6_partial_failure_retries_remainder(db, fake_op, base):
    run_once(db, fake_op, NOW)
    lender = _legs(db, base.sale_id)["lender"]
    fake_op.fail_transfer(lender.op_transfer_id, "insufficient_balance", True, sent=cop(3_000).value)

    run_once(db, fake_op, NOW)

    lender = _legs(db, base.sale_id)["lender"]
    assert (lender.status, lender.attempt, lender.sent_total) == ("pending", 2, cop(3_000).value)
    assert lender.current_debit_amount == cop("4.069,10").value
    assert (lender.next_attempt_at, lender.op_transfer_id) == (NOW + 10 * S, None)
    assert lender.failure_code == "insufficient_balance"
    assert len(_events(db, lender.id, "retry_scheduled")) == 1
    assert _refresh(db, base.account).ledger_out == cop(3_000).value
    assert _refresh(db, base.loan).delivered_total == cop(3_000).value

    fake_op.calls.clear()
    run_once(db, fake_op, NOW + 10 * S)

    calls = fake_op.calls_to("create_transfer")
    assert [c.idempotency_key for c in calls] == [f"payout:{lender.id}:2"]
    assert calls[0].body.debit_amount.to_money() == cop("4.069,10")
    assert calls[0].body.description == lender.description
    assert _legs(db, base.sale_id)["lender"].status == "processing"


def test_ac7_backoff_and_max_attempts(db, fake_op, base, make_admin):
    admin = make_admin()
    backoff = [10 * S, 60 * S, 300 * S, 300 * S]
    t = NOW
    run_once(db, fake_op, t)
    for attempt in range(1, 6):
        lender = _legs(db, base.sale_id)["lender"]
        assert (lender.status, lender.attempt) == ("processing", attempt)
        fake_op.fail_transfer(lender.op_transfer_id, "receiver_unreachable", True)
        run_once(db, fake_op, t)
        lender = _legs(db, base.sale_id)["lender"]
        if attempt < 5:
            assert (lender.status, lender.attempt) == ("pending", attempt + 1)
            assert lender.next_attempt_at == t + backoff[attempt - 1]
            t = lender.next_attempt_at
            run_once(db, fake_op, t - S)  # todavía no
            assert _legs(db, base.sale_id)["lender"].status == "pending"
            run_once(db, fake_op, t)
    assert (lender.status, lender.attempt, lender.failure_code) == ("failed_final", 5, "receiver_unreachable")
    assert [k for k in _keys(fake_op) if k.startswith(f"payout:{lender.id}")] == [
        f"payout:{lender.id}:{n}" for n in range(1, 6)
    ]
    assert [n.user_id for n in _notifications(db, "payout_failed")] == [admin.id]


def test_ac8_grant_revoked(db, fake_op, base):
    run_once(db, fake_op, NOW)
    second = _sale(db, base.account, cop(20_000))
    db.execute(update(Payout).where(Payout.sale_id == second).values(next_attempt_at=NOW + timedelta(hours=1)))
    db.commit()
    lender = _legs(db, base.sale_id)["lender"]
    fake_op.fail_transfer(lender.op_transfer_id, "grant_revoked", False)
    fake_op.calls.clear()

    run_once(db, fake_op, NOW)

    account = _refresh(db, base.account)
    assert (account.status, account.revoked_at) == ("revoked", NOW)
    first = _legs(db, base.sale_id)
    assert (first["lender"].status, first["lender"].failure_code) == ("held", "grant_revoked")
    assert (first["merchant"].status, first["trust"].status) == ("processing", "processing")  # su GET dirá
    assert {p.status for p in _legs(db, second).values()} == {"held"}
    assert fake_op.calls_to("create_transfer") == []


def test_ac9_job_failure_isolated_stub(db, fake_op, base, monkeypatch):
    # El núcleo de AC-9 sin 11: aunque `get_transfer` lance RuntimeError, J4 corre con el mismo `now`.
    run_once(db, fake_op, NOW)

    def boom(transfer_id):
        raise RuntimeError("falla inesperada")

    monkeypatch.setattr(fake_op, "get_transfer", boom)
    expired: list[datetime] = []
    _with_loans_fns(monkeypatch, expire_pending=lambda db_, now: expired.append(now) or 0)

    run_once(db, fake_op, NOW + S)

    assert expired == [NOW + S]
    assert {p.status for p in _legs(db, base.sale_id).values()} == {"processing"}


def test_ac9_job_failure_isolated(db, fake_op, base, make_merchant, make_lender, monkeypatch):
    run_once(db, fake_op, NOW)  # patas en processing: J2 va a llamar a get_transfer

    def boom(transfer_id):
        raise RuntimeError("falla inesperada")

    monkeypatch.setattr(fake_op, "get_transfer", boom)
    pending = _loan(
        db, make_merchant, make_lender, status="pending_disbursement", activated_at=None,
        expires_at=NOW - timedelta(minutes=1),
    )

    run_once(db, fake_op, NOW)

    assert _refresh(db, pending.loan).status == "expired"


def test_ac10_polls_disbursements(db, fake_op, make_merchant, make_lender, monkeypatch):
    s = _loan(db, make_merchant, make_lender, status="disbursing", activated_at=None, disbursement_attempt=1,
              disbursement_started_at=NOW - timedelta(minutes=2))
    payment = fake_op.create_payment(
        CreatePaymentRequest(
            sender_wallet_address=s.lender.payout_wallet_url,
            receiver_wallet_address=s.merchant.payout_wallet_url,
            receive_amount=PositiveAmount.from_money(cop(2_000_000)),
            description="Desembolso",
            external_ref=f"loan:{s.loan.id}:disb:1",
            return_url="https://app.trustservices.com.co/prestamista/creditos",
        ),
        f"loan:{s.loan.id}:disb:1",
    )
    fake_op.approve_payment(payment.id)
    fake_op.complete_payment(payment.id)
    s.loan.op_disbursement_payment_id = payment.id
    db.commit()
    real = loans_service.on_disbursement_update
    calls = []

    def spy(db_, loan, payment_):
        calls.append(payment_)
        return real(db_, loan, payment_)

    original = worker._loans_fn
    monkeypatch.setattr(worker, "_loans_fn", lambda name: spy if name == "on_disbursement_update" else original(name))

    run_once(db, fake_op, NOW)

    assert [(p.id, p.status) for p in calls] == [(payment.id, "completed")]
    assert _refresh(db, s.loan).status == "active"


def test_ac11_reconciliation_cursor(db, fake_op, ct, monkeypatch):
    event_a, event_b, event_c = (_event(ct.account, cop(n)) for n in (10_000, 20_000, 30_000))
    process_puntored_event(db, event_a, "puntored")  # A ya llegó por webhook
    db.commit()

    def sim(seq: int, status: str, event: dict) -> SimQrPayment:
        return SimQrPayment(
            id=uuid.uuid4(), seq=seq, collection_account_id=ct.account.op_collection_account_id,
            amount=Amount(**event["data"]["amount"]), bre_b_reference=event["data"]["bre_b_reference"],
            status=status, webhook_event=PuntoredPaymentEvent.model_validate(event), created_at=NOW,
        )

    items = [sim(3, "completed", event_a), sim(5, "completed", event_b), sim(7, "failed", event_c)]
    asked: list[int] = []

    def list_sim_qr_payments(after: int = 0, limit: int = 100) -> SimQrPaymentList:
        asked.append(after)
        page = [item for item in items if item.seq > after]
        return SimQrPaymentList(items=page, next_after=page[-1].seq if page else after)

    results: list[str] = []

    def spy(db_, event, source):
        result = process_puntored_event(db_, event, source)
        results.append(result.status)
        return result

    monkeypatch.setattr(fake_op, "list_sim_qr_payments", list_sim_qr_payments)
    monkeypatch.setattr(worker, "process_puntored_event", spy)

    run_once(db, fake_op, NOW)

    assert asked == [0]
    assert results == ["duplicate", "processed"]  # A duplicada, B nueva, C (failed) se salta
    db.expire_all()
    sale_b = db.scalar(select(Sale).where(Sale.bre_b_reference == event_b["data"]["bre_b_reference"]))
    assert sale_b is not None and sale_b.source == "live"
    assert db.scalar(select(WebhookEvent.source).where(WebhookEvent.event_id == event_b["event_id"])) == (
        "reconciliation"
    )
    assert db.scalar(select(func.count()).select_from(Sale)) == 2
    assert db.scalar(select(Sale).where(Sale.bre_b_reference == event_c["data"]["bre_b_reference"])) is None
    assert _cursor(db, "sim_qr_payments") == 7

    run_once(db, fake_op, NOW + 30 * S)
    assert asked == [0, 7]

    run_once(db, fake_op, NOW + 60 * S, reconcile=False)
    assert asked == [0, 7]


def test_ac12_daily_close_once(db, fake_op, make_merchant, make_lender, monkeypatch):
    at_0004 = datetime(2026, 10, 17, 0, 4, tzinfo=BOGOTA).astimezone(UTC)
    at_0006 = datetime(2026, 10, 17, 0, 6, tzinfo=BOGOTA).astimezone(UTC)
    due = [
        _loan(db, make_merchant, make_lender, next_review_at=at_0006 - timedelta(days=1)).loan,
        _loan(db, make_merchant, make_lender, next_review_at=at_0006 - timedelta(minutes=1)).loan,
    ]
    _loan(db, make_merchant, make_lender, next_review_at=at_0006 + timedelta(hours=1))  # todavía no
    _loan(db, make_merchant, make_lender, status="paid", next_review_at=at_0006 - timedelta(days=2))  # no active
    calls: list[tuple[uuid.UUID, datetime]] = []
    # close_period es de 11-loans: aquí se prueba solo el job (cuándo y a quién la llama).
    _with_loans_fns(monkeypatch, close_period=lambda db_, loan_id, now: calls.append((loan_id, now)))

    run_once(db, fake_op, at_0004)
    assert calls == [] and _cursor(db, "daily_close") is None

    run_once(db, fake_op, at_0006)
    assert sorted(loan_id for loan_id, _ in calls) == sorted(loan.id for loan in due)
    assert {now for _, now in calls} == {at_0006}
    assert _cursor(db, "daily_close") == 20261017

    run_once(db, fake_op, at_0006 + timedelta(hours=10))
    assert len(calls) == 2

    run_once(db, fake_op, at_0006 + timedelta(days=1))  # al día siguiente vuelve a correr
    assert len(calls) == 5 and _cursor(db, "daily_close") == 20261018  # los 2 de antes + el que ya venció


def test_ac13_no_open_tx_during_op_calls(db, fake_op, base, monkeypatch):
    seen: list[tuple[str, bool]] = []

    def watch(method: str):
        real = getattr(fake_op, method)

        def wrapper(*args, **kwargs):
            seen.append((method, db.in_transaction()))
            return real(*args, **kwargs)

        monkeypatch.setattr(fake_op, method, wrapper)

    for method in ("create_transfer", "get_transfer", "list_sim_qr_payments"):
        watch(method)

    run_once(db, fake_op, NOW)
    legs = _legs(db, base.sale_id)
    fake_op.complete_transfer(legs["lender"].op_transfer_id)
    fake_op.fail_transfer(legs["merchant"].op_transfer_id, "upstream_error", True, sent=cop(800).value)
    fake_op.fail_next("create_transfer", OpUnavailable("caído"))
    run_once(db, fake_op, NOW)
    run_once(db, fake_op, NOW + 10 * S)
    run_once(db, fake_op, NOW + 20 * S)

    methods = [method for method, _ in seen]
    assert methods.count("create_transfer") >= 5 and methods.count("get_transfer") >= 6
    assert [entry for entry in seen if entry[1]] == []


# ── Reglas sin AC propio ──────────────────────────────────────────────────────


def test_r1_loop_reconciles_on_start_and_every_30s(monkeypatch, settings):
    flags: list[bool] = []
    done = threading.Event()

    def fake_run_once(db, op, now, reconcile=True):
        flags.append(reconcile)
        if len(flags) >= 7:
            done.set()

    ticks = itertools.count(0, 10)  # cada tick "dura" 10 s en el reloj del loop
    monkeypatch.setattr(worker, "_monotonic", lambda: next(ticks))
    monkeypatch.setattr(worker, "run_once", fake_run_once)
    monkeypatch.setattr(settings, "worker_interval_seconds", 0.001)

    stop = worker.start_worker()
    try:
        assert done.wait(5)
    finally:
        stop()
    assert flags[:7] == [True, False, False, True, False, False, True]
    assert not any(t.name == "trust-worker" and t.is_alive() for t in threading.enumerate())


def test_r1_lifespan_starts_worker_only_if_enabled(monkeypatch, settings):
    started, stopped = [], []

    def fake_start():
        started.append(True)
        return lambda: stopped.append(True)

    monkeypatch.setattr("app.main.start_worker", fake_start)
    with TestClient(create_app()):
        pass
    assert (started, stopped) == ([], [])

    monkeypatch.setattr(settings, "worker_enabled", True)
    with TestClient(create_app()):
        assert started == [True] and stopped == []
    assert stopped == [True]


def test_r2_failures_isolated(db, fake_op, base, monkeypatch):
    # Una fila que falla no frena a las demás del mismo job…
    fake_op.fail_next("create_transfer", RuntimeError("falla inesperada"))
    run_once(db, fake_op, NOW)
    legs = _legs(db, base.sale_id)
    assert (legs["lender"].status, legs["lender"].attempt, legs["lender"].next_attempt_at) == ("pending", 1, None)
    assert (legs["merchant"].status, legs["trust"].status) == ("processing", "processing")

    # …y un job que falla no frena a los siguientes.
    def broken(db_, op, now):
        db_.execute(select(Payout.id))  # deja una transacción abierta antes de fallar
        raise RuntimeError("job roto")

    expired: list[datetime] = []
    monkeypatch.setattr(worker, "job_poll_payouts", broken)
    _with_loans_fns(monkeypatch, expire_pending=lambda db_, now: expired.append(now) or 0)
    run_once(db, fake_op, NOW + S)
    assert expired == [NOW + S]
    assert _legs(db, base.sale_id)["lender"].status == "processing"  # J1 corrió antes del job roto


def test_r3_changed_row_discarded_except_201(db, fake_op, base, monkeypatch, make_admin):
    make_admin()
    real_create = fake_op.create_transfer
    lender_id = _legs(db, base.sale_id)["lender"].id

    def hold_meanwhile(payout_id):
        """Otra transacción (16-admin revoca la cuenta) cambia la pata mientras el worker llama a Go."""
        with SessionLocal() as other:  # si el worker tuviera la fila bloqueada, esto se colgaría
            other.execute(update(Payout).where(Payout.id == payout_id).values(status="held"))
            other.commit()

    def rejecting(body, key):
        if key.startswith(f"payout:{lender_id}:"):
            hold_meanwhile(lender_id)
            raise OpError(422, "invalid_amount", "Monto inválido")
        return real_create(body, key)

    monkeypatch.setattr(fake_op, "create_transfer", rejecting)
    run_once(db, fake_op, NOW)
    lender = _legs(db, base.sale_id)["lender"]
    assert (lender.status, lender.failure_code) == ("held", None)  # el 422 se descarta
    assert _notifications(db, "payout_failed") == []

    # Un 201 siempre se aplica: la transferencia ya existe en Go.
    db.execute(update(Payout).where(Payout.id == lender_id).values(status="pending"))
    db.commit()

    def accepting(body, key):
        if key.startswith(f"payout:{lender_id}:"):
            hold_meanwhile(lender_id)
        return real_create(body, key)

    monkeypatch.setattr(fake_op, "create_transfer", accepting)
    run_once(db, fake_op, NOW + S)
    lender = _legs(db, base.sale_id)["lender"]
    assert lender.status == "processing" and lender.op_transfer_id is not None


@pytest.mark.parametrize(
    "error",
    [
        OpError(400, "validation_error", "Cuerpo inválido"),
        OpError(404, "not_found", "No existe"),
        OpError(409, "idempotency_conflict", "Otro cuerpo"),
    ],
)
def test_r4_other_op_errors_failed_final(db, fake_op, base, error):
    fake_op.fail_next("create_transfer", error)
    run_once(db, fake_op, NOW)
    lender = _legs(db, base.sale_id)["lender"]
    assert (lender.status, lender.failure_code) == ("failed_final", error.code)


@pytest.mark.parametrize("retryable", [True, False])
def test_r4_request_in_progress_keeps_same_key(db, fake_op, base, retryable):
    # La tabla de R4 nombra 409 request_in_progress: misma clave aunque Go no marque `retryable`.
    fake_op.fail_next("create_transfer", OpError(409, "request_in_progress", "En curso", retryable))
    run_once(db, fake_op, NOW)
    lender = _legs(db, base.sale_id)["lender"]
    assert (lender.status, lender.attempt, lender.next_attempt_at) == ("pending", 1, NOW + 10 * S)
    assert lender.failure_code is None and _notifications(db, "payout_failed") == []

    fake_op.calls.clear()
    run_once(db, fake_op, NOW + 10 * S)
    assert _keys(fake_op) == [f"payout:{lender.id}:1"]


def test_r5_get_transfer_404_failed_final(db, fake_op, base, make_admin, caplog):
    admin = make_admin()
    run_once(db, fake_op, NOW)
    fake_op.fail_next("get_transfer", OpError(404, "not_found", "No existe"))  # la primera consulta: la lender
    caplog.set_level("WARNING", logger="app.worker")

    run_once(db, fake_op, NOW + S)

    legs = _legs(db, base.sale_id)
    lender = legs["lender"]
    assert (lender.status, lender.failure_code, lender.sent_total) == ("failed_final", "not_found", 0)
    assert str(lender.op_transfer_id) in lender.last_error
    assert (legs["merchant"].status, legs["trust"].status) == ("processing", "processing")  # las demás siguen
    assert len(_events(db, lender.id, "failed_final")) == 1
    assert [n.user_id for n in _notifications(db, "payout_failed")] == [admin.id]
    assert _refresh(db, base.account).ledger_out == 0
    assert not [r for r in caplog.records if r.exc_info]  # sin traceback

    fake_op.calls.clear()
    run_once(db, fake_op, NOW + 2 * S)
    polled = [call.args["transfer_id"] for call in fake_op.calls_to("get_transfer")]
    assert lender.op_transfer_id not in polled and len(polled) == 2


@pytest.mark.parametrize(
    "error",
    [OpError(401, "unauthorized", "API key inválida"), OpError(409, "request_in_progress", "En curso", True)],
)
def test_r5_get_transfer_401_or_retryable_next_tick(db, fake_op, base, make_admin, caplog, error):
    make_admin()
    run_once(db, fake_op, NOW)
    for _ in LEGS:
        fake_op.fail_next("get_transfer", error)
    caplog.set_level("WARNING", logger="app.worker")

    run_once(db, fake_op, NOW + S)

    assert {p.status for p in _legs(db, base.sale_id).values()} == {"processing"}
    assert _notifications(db, "payout_failed") == []
    assert not [r for r in caplog.records if r.levelname == "ERROR" or r.exc_info]
    assert any(error.code in r.getMessage() for r in caplog.records if r.levelname == "WARNING")

    lender = _legs(db, base.sale_id)["lender"]
    fake_op.complete_transfer(lender.op_transfer_id)
    run_once(db, fake_op, NOW + 2 * S)  # el siguiente tick la vuelve a consultar
    assert _legs(db, base.sale_id)["lender"].status == "completed"


def test_r5_revoked_account_holds_instead_of_retrying(db, fake_op, base):
    run_once(db, fake_op, NOW)
    # 16-admin revoca la cuenta en la base mientras la pata está `processing` (16 R6 no toca las processing).
    revoke = update(CollectionAccount).where(CollectionAccount.id == base.account.id)
    db.execute(revoke.values(status="revoked", revoked_at=NOW))
    db.commit()
    lender = _legs(db, base.sale_id)["lender"]
    fake_op.fail_transfer(lender.op_transfer_id, "insufficient_balance", True, sent=cop(3_000).value)

    run_once(db, fake_op, NOW)

    lender = _legs(db, base.sale_id)["lender"]
    assert (lender.status, lender.attempt, lender.next_attempt_at) == ("held", 1, None)
    assert (lender.sent_total, lender.failure_code) == (cop(3_000).value, "insufficient_balance")
    assert _refresh(db, base.account).ledger_out == cop(3_000).value
    assert _refresh(db, base.loan).delivered_total == cop(3_000).value
    held = _events(db, lender.id, "held")
    assert [e.data["reason"] for e in held] == ["collection_account_not_active"]
    assert _events(db, lender.id, "retry_scheduled") == []

    fake_op.calls.clear()
    run_once(db, fake_op, NOW + 10 * S)
    assert fake_op.calls_to("create_transfer") == []


def test_r5_failed_without_remainder_completes(db, fake_op, base):
    run_once(db, fake_op, NOW)
    lender = _legs(db, base.sale_id)["lender"]
    fake_op.fail_transfer(lender.op_transfer_id, "upstream_error", False, sent=lender.current_debit_amount)

    run_once(db, fake_op, NOW)

    lender = _legs(db, base.sale_id)["lender"]
    assert (lender.status, lender.sent_total, lender.completed_at) == ("completed", lender.amount, NOW)
    assert _refresh(db, base.loan).delivered_total == lender.amount
    assert len(_notifications(db, "abono_received")) == 1


def test_r5_processing_and_unavailable_change_nothing(db, fake_op, base):
    run_once(db, fake_op, NOW)
    before = {leg: (p.status, p.attempt, p.sent_total) for leg, p in _legs(db, base.sale_id).items()}
    for _ in LEGS:
        fake_op.fail_next("get_transfer", OpUnavailable("caído"))
    run_once(db, fake_op, NOW + S)  # Go caído
    run_once(db, fake_op, NOW + 2 * S)  # siguen processing en Go
    assert {leg: (p.status, p.attempt, p.sent_total) for leg, p in _legs(db, base.sale_id).items()} == before
    assert _refresh(db, base.account).ledger_out == 0


def test_r6_disbursement_job_wiring(db, fake_op, make_merchant, make_lender, monkeypatch):
    s = _loan(db, make_merchant, make_lender, status="disbursing", activated_at=None)
    payment = fake_op.create_payment(
        CreatePaymentRequest(
            sender_wallet_address=s.lender.payout_wallet_url,
            receiver_wallet_address=s.merchant.payout_wallet_url,
            receive_amount=PositiveAmount.from_money(cop(2_000_000)),
            external_ref=f"loan:{s.loan.id}:disb:1",
            return_url="https://app.trustservices.com.co/",
        ),
        f"loan:{s.loan.id}:disb:1",
    )
    s.loan.op_disbursement_payment_id = payment.id
    db.commit()
    calls: list[tuple[uuid.UUID, str, uuid.UUID, bool]] = []

    def on_update(db_, loan, payment_):
        calls.append((loan.id, loan.status, payment_.id, db_.in_transaction()))

    _with_loans_fns(monkeypatch, on_disbursement_update=on_update)
    fake_op.fail_next("get_payment", OpUnavailable("caído"))
    run_once(db, fake_op, NOW)  # OpUnavailable → siguiente tick
    assert calls == []
    run_once(db, fake_op, NOW + S)
    assert calls == [(s.loan.id, "disbursing", payment.id, True)]


def test_r6_disbursement_op_error_logs_without_traceback(db, fake_op, make_merchant, make_lender, monkeypatch, caplog):
    s = _loan(db, make_merchant, make_lender, status="disbursing", activated_at=None)
    s.loan.op_disbursement_payment_id = uuid.uuid4()  # Go no lo conoce
    db.commit()
    calls: list[uuid.UUID] = []
    _with_loans_fns(monkeypatch, on_disbursement_update=lambda db_, loan, payment_: calls.append(loan.id))
    caplog.set_level("WARNING", logger="app.worker")

    run_once(db, fake_op, NOW)

    assert calls == []
    assert len(fake_op.calls_to("get_payment")) == 1
    assert not [r for r in caplog.records if r.levelname == "ERROR" or r.exc_info]
    assert any("not_found" in r.getMessage() for r in caplog.records)
    assert _refresh(db, s.loan).status == "disbursing"


def test_r10_ignores_held_failed_final_and_completed(db, fake_op, base):
    legs = _legs(db, base.sale_id)
    for leg, status in zip(LEGS, ("held", "failed_final", "completed"), strict=True):
        legs[leg].status = status
    db.commit()
    run_once(db, fake_op, NOW)
    assert fake_op.calls_to("create_transfer") == [] and fake_op.calls_to("get_transfer") == []
    assert [p.status for p in _legs(db, base.sale_id).values()] == ["held", "failed_final", "completed"]


def test_r8_reconcile_item_error_isolated(db, fake_op, ct, monkeypatch):
    first, second = (fake_op.create_sim_qr_payment(_sim_body(ct.account, cop(n)), f"sim:{n}") for n in (10_000, 20_000))
    real = process_puntored_event

    def flaky(db_, event, source):
        if event.event_id == first.webhook_event.event_id:
            raise RuntimeError("evento roto")
        return real(db_, event, source)

    monkeypatch.setattr(worker, "process_puntored_event", flaky)
    run_once(db, fake_op, NOW)
    db.expire_all()
    references = set(db.scalars(select(Sale.bre_b_reference)))
    assert references == {second.bre_b_reference}
    assert _cursor(db, "sim_qr_payments") == second.seq


def _sim_body(account: CollectionAccount, amount: Money) -> CreateSimQrPaymentRequest:
    return CreateSimQrPaymentRequest(
        collection_account_id=account.op_collection_account_id, amount=PositiveAmount.from_money(amount)
    )


def test_worker_thread_survives_tick_errors(monkeypatch, settings):
    calls: list[int] = []
    done = threading.Event()

    def failing_run_once(db, op, now, reconcile=True):
        calls.append(1)
        if len(calls) >= 3:
            done.set()
        raise RuntimeError("tick roto")

    monkeypatch.setattr(worker, "run_once", failing_run_once)
    monkeypatch.setattr(settings, "worker_interval_seconds", 0.001)
    stop = worker.start_worker()
    try:
        assert done.wait(5)
    finally:
        stop()
    assert len(calls) >= 3
