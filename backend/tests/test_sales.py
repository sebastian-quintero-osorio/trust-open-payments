"""07-sales: un test por criterio de aceptación (más reglas sin AC propio)."""

import hashlib
import hmac
import json
import re
import threading
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import Settings
from app.core.db import SessionLocal
from app.core.events import Event
from app.core.money import Money
from app.modules.loans.models import Loan
from app.modules.notifications.models import Notification
from app.modules.recaudo.models import CollectionAccount
from app.modules.sales import service
from app.modules.sales.models import Payout, Repayment, Sale, WebhookEvent
from app.modules.users.models import User
from tests.helpers import assert_contract, cop
from tests.test_data_model import _assessment

WEBHOOK = "/api/v1/webhooks/puntored/payments"
SECRET = "secreto-de-prueba"
NOW = datetime(2026, 10, 16, 15, 42, tzinfo=UTC)  # 10:42 a. m. en Bogotá
OP = "puntoredPaymentWebhook"

_seq = iter(range(1, 100_000))


@pytest.fixture(autouse=True)
def _env(settings, monkeypatch, freeze):
    """Un secreto de webhook vigente y el reloj congelado el 16 oct a las 10:42 a. m. (Bogotá)."""
    monkeypatch.setattr(settings, "puntored_webhook_secrets", [SECRET])
    freeze(NOW)


# ── Ayudas ────────────────────────────────────────────────────────────────────


def _account(db: Session, merchant: User, status: str = "active", asset_code="COP", asset_scale=2):
    n = next(_seq)
    account = CollectionAccount(
        merchant_id=merchant.id,
        op_collection_account_id=uuid.uuid4(),
        wallet_address=f"https://ilp.interledger-test.dev/trust-recaudo-s{n}",
        qr_payload=f"https://pay.trustservices.com.co/qr/s{n}",
        asset_code=asset_code,
        asset_scale=asset_scale,
        status=status,
        revoked_at=clock.now() if status == "revoked" else None,
    )
    db.add(account)
    db.commit()
    return account


def _ct(db: Session, make_merchant, make_lender, account_status="active", **loan_fields):
    """Crédito tipo (CT) `active`: $2.000.000 + cargo 10 % = $2.200.000, Trust 20 % del cargo, 15 % por venta."""
    merchant, lender = make_merchant(), make_lender()
    account = _account(db, merchant, account_status)
    loan = Loan(
        merchant_id=merchant.id,
        lender_id=lender.id,
        origin="offer_taken",
        created_by=merchant.id,
        credit_assessment_id=_assessment(db, merchant).id,
        collection_account_id=account.id,
        merchant_payout_wallet_url=merchant.payout_wallet_url,
        lender_wallet_url=lender.payout_wallet_url,
        lender_asset_code="COP",
        lender_asset_scale=2,
        asset_code="COP",
        asset_scale=2,
        principal=cop(2_000_000).value,
        fee_bps=1000,
        fee_amount=cop(200_000).value,
        total_repayable=cop(2_200_000).value,
        trust_share_bps=2000,
        term_months=3,
        monthly_minimum=cop("733.333,34").value,
        pct_initial_bps=1500,
        pct_min_bps=1000,
        pct_max_bps=2000,
        terms={},
        status="active",
        pct_bps=1500,
        activated_at=clock.now(),
        **loan_fields,
    )
    db.add(loan)
    db.commit()
    return SimpleNamespace(merchant=merchant, lender=lender, account=account, loan=loan)


def _event(account: CollectionAccount, amount: Money, *, event_id=None, reference=None, type="qr_payment.received",
           occurred_at: datetime | None = None, **data) -> dict:
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "type": type,
        "occurred_at": (occurred_at or clock.now()).isoformat().replace("+00:00", "Z"),
        "data": {
            "collection_account_id": str(account.op_collection_account_id),
            "collection_wallet_address": account.wallet_address,
            "amount": amount.to_api(),
            "bre_b_reference": reference or f"BREB-SIM-{next(_seq):06d}",
            "payer_bank": "Bancolombia",
            "incoming_payment_url": "https://ilp.interledger-test.dev/incoming-payments/abc",
            **data,
        },
    }


def _signature(raw: bytes, secret: str = SECRET, t: int | None = None) -> str:
    t = int(clock.now().timestamp()) if t is None else t
    digest = hmac.new(secret.encode(), f"{t}.".encode() + raw, hashlib.sha256).hexdigest()
    return f"t={t},v1={digest}"


def _post(client, body: dict | bytes, *, secret: str = SECRET, t: int | None = None, header: str | None = "auto"):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if header == "auto":
        headers["X-Puntored-Signature"] = _signature(raw, secret, t)
    elif header is not None:
        headers["X-Puntored-Signature"] = header
    return client.post(WEBHOOK, content=raw, headers=headers)


def _ok(response, status: str) -> None:
    assert response.status_code == 200, response.text
    assert_contract(response, OP)
    assert response.json() == {"status": status}


def _error(response, status: int, code: str, operation_id: str) -> dict:
    assert response.status_code == status, response.text
    assert_contract(response, operation_id)
    error = response.json()["error"]
    assert error["code"] == code
    return error


def _count(db: Session, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


def _legs(db: Session, sale_id: uuid.UUID) -> dict[str, Payout]:
    return {p.leg: p for p in db.scalars(select(Payout).where(Payout.sale_id == sale_id))}


def _only_sale(db: Session) -> Sale:
    db.expire_all()
    sales = db.scalars(select(Sale)).all()
    assert len(sales) == 1
    return sales[0]


# ── AC ────────────────────────────────────────────────────────────────────────


def test_ac1_webhook_splits_sale(client, db, settings, make_merchant, make_lender):
    ct = _ct(db, make_merchant, make_lender)
    event = _event(ct.account, cop(48_000))

    _ok(_post(client, event), "processed")

    sale = _only_sale(db)
    assert (sale.amount, sale.source, sale.status, sale.paid_at) == (cop(48_000).value, "live", "processed", NOW)
    assert (sale.puntored_event_id, sale.bre_b_reference) == (event["event_id"], event["data"]["bre_b_reference"])
    assert (sale.merchant_id, sale.collection_account_id) == (ct.merchant.id, ct.account.id)
    assert sale.payer_bank == "Bancolombia" and sale.incoming_payment_url == event["data"]["incoming_payment_url"]

    repayment = db.scalar(select(Repayment).where(Repayment.sale_id == sale.id))
    assert (repayment.abono_amount, repayment.trust_cut_amount, repayment.lender_net_amount) == (
        cop(7_200).value, cop("130,90").value, cop("7.069,10").value,
    )
    assert (repayment.pct_applied_bps, repayment.allocated_before, repayment.allocated_after) == (
        1500, 0, cop(7_200).value,
    )
    assert (repayment.loan_id, repayment.merchant_id, repayment.lender_id) == (ct.loan.id, ct.merchant.id, ct.lender.id)

    legs = _legs(db, sale.id)
    assert {leg: (p.amount, p.status) for leg, p in legs.items()} == {
        "lender": (cop("7.069,10").value, "pending"),
        "merchant": (cop(40_800).value, "pending"),
        "trust": (cop("130,90").value, "pending"),
    }
    assert legs["lender"].receiver_wallet_url == ct.lender.payout_wallet_url
    assert legs["merchant"].receiver_wallet_url == ct.merchant.payout_wallet_url
    assert legs["trust"].receiver_wallet_url == settings.trust_revenue_wallet_address
    assert {leg: p.description for leg, p in legs.items()} == {  # R6: hora de la venta, congelada
        "lender": "Abono venta 10:42 a. m. · Tienda La Esquina",
        "merchant": "Venta 10:42 a. m. · Tienda La Esquina",
        "trust": "Parte Trust venta 10:42 a. m. · Tienda La Esquina",
    }
    for p in legs.values():
        assert (p.attempt, p.current_debit_amount, p.sent_total, p.loan_id) == (1, p.amount, 0, ct.loan.id)
        assert (p.asset_code, p.asset_scale) == ("COP", 2)
    assert sum(p.amount for p in legs.values()) == cop(48_000).value  # lender + merchant + trust_cut = amount

    account, loan = db.get(CollectionAccount, ct.account.id), db.get(Loan, ct.loan.id)
    assert account.ledger_in == cop(48_000).value
    assert (loan.allocated_total, loan.trust_earned, loan.trust_pending, loan.status) == (
        cop(7_200).value, cop("130,90").value, 0, "active",
    )

    webhook = db.scalar(select(WebhookEvent))
    assert (webhook.status, webhook.sale_id, webhook.source, webhook.error) == ("processed", sale.id, "puntored", None)
    assert webhook.processed_at == NOW and webhook.payload == event
    payout_events = db.scalars(select(Event).where(Event.entity_type == "payout")).all()
    assert len(payout_events) == 3  # un record_event por pata


def test_ac2_rejects_bad_signature(client, db, make_merchant, make_lender):
    ct = _ct(db, make_merchant, make_lender)
    raw = json.dumps(_event(ct.account, cop(48_000))).encode()

    _error(_post(client, raw, header=None), 401, "invalid_signature", OP)
    _error(_post(client, raw, secret="secreto-ajeno"), 401, "invalid_signature", OP)
    for header in ("basura", "t=abc,v1=00", f"v1={hashlib.sha256(raw).hexdigest()}", "t=1792209720"):
        _error(_post(client, raw, header=header), 401, "invalid_signature", OP)
    expired = int(clock.now().timestamp()) - 301
    _error(_post(client, raw, t=expired), 401, "signature_expired", OP)
    _error(_post(client, raw, t=int(clock.now().timestamp()) + 301), 401, "signature_expired", OP)
    for huge in ("1" + "0" * 400, "1" + "0" * 5000):  # `t` enorme (también sobre el tope de int de 4300 dígitos)
        digest = hmac.new(SECRET.encode(), f"{huge}.".encode() + raw, hashlib.sha256).hexdigest()
        _error(_post(client, raw, header=f"t={huge},v1={digest}"), 401, "signature_expired", OP)

    assert _count(db, WebhookEvent) == 0 and _count(db, Sale) == 0
    _ok(_post(client, raw, t=int(clock.now().timestamp()) - 300), "processed")  # dentro de la tolerancia


def test_ac3_secret_rotation_raw_body(client, db, settings, monkeypatch, make_merchant):
    monkeypatch.setenv("PUNTORED_WEBHOOK_SECRETS", "viejo,nuevo")
    secrets = Settings().puntored_webhook_secrets
    assert secrets == ["viejo", "nuevo"]
    monkeypatch.setattr(settings, "puntored_webhook_secrets", secrets)
    account = _account(db, make_merchant())
    event = _event(account, cop(48_000))
    data = event["data"]
    template = (  # espacios y claves en otro orden: se firma y se verifica sobre estos bytes exactos
        '{ "data" : { "bre_b_reference" : "REF",\t"amount" : {"asset_scale": 2, "value": "4800000", '
        '"asset_code": "COP"},'
        '  "collection_wallet_address": "WALLET",   "collection_account_id": "ACCOUNT" },\n'
        '  "occurred_at": "OCCURRED", "type": "qr_payment.received", "event_id": "EVENT" }'
    )
    for placeholder, value in (("REF", data["bre_b_reference"]), ("WALLET", data["collection_wallet_address"]),
                               ("ACCOUNT", data["collection_account_id"]), ("OCCURRED", event["occurred_at"]),
                               ("EVENT", event["event_id"])):
        template = template.replace(placeholder, value)
    raw = template.encode()

    # Firmar el JSON re-serializado (compacto) no sirve: la firma es sobre el body crudo.
    compact = json.dumps(json.loads(raw), separators=(",", ":")).encode()
    _error(_post(client, raw, header=_signature(compact, "nuevo")), 401, "invalid_signature", OP)

    _ok(_post(client, raw, secret="nuevo"), "processed")
    assert _only_sale(db).bre_b_reference == data["bre_b_reference"]


def test_ac4_dedup(client, db, make_merchant, make_lender):
    ct = _ct(db, make_merchant, make_lender)
    first = _event(ct.account, cop(48_000), reference="BREB-SIM-000777")

    _ok(_post(client, first), "processed")
    _ok(_post(client, first), "duplicate")  # mismo event_id (reintento de Go)
    second = _event(ct.account, cop(48_000), reference="BREB-SIM-000777")
    _ok(_post(client, second), "duplicate")  # otro event_id, misma referencia Bre-B

    sale = _only_sale(db)
    assert len(_legs(db, sale.id)) == 3
    assert _count(db, Repayment) == 1
    webhooks = {w.event_id: w for w in db.scalars(select(WebhookEvent))}
    assert len(webhooks) == 2
    assert (webhooks[first["event_id"]].status, webhooks[first["event_id"]].sale_id) == ("processed", sale.id)
    assert (webhooks[second["event_id"]].status, webhooks[second["event_id"]].error) == (
        "ignored", "duplicate_reference",
    )
    loan = db.get(Loan, ct.loan.id)
    assert loan.allocated_total == cop(7_200).value  # el duplicado no abona dos veces
    assert db.get(CollectionAccount, ct.account.id).ledger_in == cop(48_000).value


def test_ac5_unmatched(client, db, make_merchant):
    account = _account(db, make_merchant())
    unknown = _event(account, cop(48_000))
    unknown["data"]["collection_account_id"] = str(uuid.uuid4())
    cases = {
        "unknown_account": unknown,
        "wallet_mismatch": _event(account, cop(48_000), collection_wallet_address="https://ilp.interledger-test.dev/otra"),
        "asset_mismatch": _event(account, Money(4_800, "USD", 2)),
        "invalid_payload": _event(account, Money(0, "COP", 2)),  # monto no positivo
    }
    for event in cases.values():
        _ok(_post(client, event), "unmatched")
    missing = _event(account, cop(48_000))
    del missing["data"]["bre_b_reference"]
    _ok(_post(client, missing), "unmatched")
    blank = _event(account, cop(48_000), bre_b_reference="  ")
    _ok(_post(client, blank), "unmatched")
    huge = _event(account, cop(48_000))
    huge["data"]["amount"]["value"] = "99999999999999999999999"  # cabe en el patrón pero no en bigint
    _ok(_post(client, huge), "unmatched")

    db.expire_all()
    assert _count(db, Sale) == 0
    errors = {w.event_id: (w.status, w.error) for w in db.scalars(select(WebhookEvent))}
    for error, event in cases.items():
        assert errors[event["event_id"]] == ("unmatched", error)
    assert errors[missing["event_id"]] == errors[blank["event_id"]] == errors[huge["event_id"]] == (
        "unmatched", "invalid_payload",
    )
    events = db.scalars(select(Event).where(Event.entity_type == "webhook_event", Event.type == "unmatched")).all()
    assert len(events) == 7
    assert db.get(CollectionAccount, account.id).ledger_in == 0


def test_ac6_ignores_other_types(client, db, make_merchant):
    account = _account(db, make_merchant())
    event = _event(account, cop(48_000), type="qr_payment.reversed")

    _ok(_post(client, event), "ignored")

    db.expire_all()
    assert _count(db, Sale) == 0
    webhook = db.scalar(select(WebhookEvent))
    assert (webhook.status, webhook.type, webhook.sale_id) == ("ignored", "qr_payment.reversed", None)


def test_ac7_no_loan_full_to_merchant(client, db, make_merchant):
    merchant = make_merchant()
    account = _account(db, merchant)

    _ok(_post(client, _event(account, cop(48_000))), "processed")

    sale = _only_sale(db)
    legs = _legs(db, sale.id)
    assert list(legs) == ["merchant"]
    leg = legs["merchant"]
    assert (leg.amount, leg.status, leg.loan_id, leg.receiver_wallet_url) == (
        cop(48_000).value, "pending", None, merchant.payout_wallet_url,
    )
    assert _count(db, Repayment) == 0
    note = db.scalar(select(Notification).where(Notification.user_id == merchant.id))
    assert (note.type, note.title, note.body) == ("sale_split", "Venta de $48.000 · $48.000 para ti",
                                                  "El 100 % va a tu wallet.")


def test_ac8_trust_pending_below_min(client, db, make_merchant, make_lender):
    ct = _ct(db, make_merchant, make_lender)

    _ok(_post(client, _event(ct.account, cop(10_000))), "processed")

    sale = _only_sale(db)
    repayment = db.scalar(select(Repayment))
    assert (repayment.abono_amount, repayment.trust_cut_amount, repayment.lender_net_amount) == (
        cop(1_500).value, cop("27,27").value, cop("1.472,73").value,
    )
    legs = _legs(db, sale.id)
    assert {leg: p.amount for leg, p in legs.items()} == {
        "lender": cop("1.472,73").value,
        "merchant": cop(8_500).value,
    }  # sin pata trust: $27,27 está bajo TRUST_LEG_MIN ($100)
    loan = db.get(Loan, ct.loan.id)
    assert (loan.trust_pending, loan.trust_earned) == (cop("27,27").value, cop("27,27").value)


def test_ac9_last_sale_pays_off(client, db, make_merchant, make_lender):
    ct = _ct(
        db, make_merchant, make_lender,
        allocated_total=cop(2_198_000).value, trust_earned=cop("39.963,63").value, trust_pending=cop("27,27").value,
    )

    _ok(_post(client, _event(ct.account, cop(48_000))), "processed")

    sale = _only_sale(db)
    repayment = db.scalar(select(Repayment))
    assert (repayment.abono_amount, repayment.trust_cut_amount, repayment.lender_net_amount) == (
        cop(2_000).value, cop("36,37").value, cop("1.963,63").value,
    )
    assert (repayment.allocated_before, repayment.allocated_after) == (cop(2_198_000).value, cop(2_200_000).value)
    legs = _legs(db, sale.id)
    assert {leg: p.amount for leg, p in legs.items()} == {
        "lender": cop("1.963,63").value,
        "merchant": cop(46_000).value,
        "trust": cop("63,64").value,  # $27,27 pendientes + $36,37: bajo el mínimo, pero sale porque se pagó
    }
    loan = db.get(Loan, ct.loan.id)
    assert (loan.status, loan.paid_at, loan.trust_pending) == ("paid", NOW, 0)
    assert (loan.allocated_total, loan.trust_earned) == (cop(2_200_000).value, cop(40_000).value)
    assert db.scalar(select(Event).where(Event.entity_id == loan.id, Event.type == "paid")) is not None

    paid = {n.user_id: n for n in db.scalars(select(Notification).where(Notification.type == "loan_paid"))}
    assert set(paid) == {ct.merchant.id, ct.lender.id}
    assert (paid[ct.merchant.id].title, paid[ct.merchant.id].body) == (
        "¡Pagaste tu crédito!", "Completaste $2.200.000 con tus ventas.",
    )
    assert (paid[ct.lender.id].title, paid[ct.lender.id].body) == (
        "Crédito pagado", "Tienda La Esquina completó el pago de $2.200.000.",
    )
    assert paid[ct.lender.id].data == {"loan_id": str(loan.id), "total_repayable": cop(2_200_000).to_api()}

    # Con el crédito pagado, la venta siguiente va completa al comercio.
    _ok(_post(client, _event(ct.account, cop(10_000))), "processed")
    db.expire_all()
    later = db.scalar(select(Sale).where(Sale.id != sale.id))
    assert {leg: (p.amount, p.loan_id) for leg, p in _legs(db, later.id).items()} == {
        "merchant": (cop(10_000).value, None),
    }


def test_ac10_revoked_account_holds(client, db, make_merchant, make_lender):
    ct = _ct(db, make_merchant, make_lender, account_status="revoked")

    _ok(_post(client, _event(ct.account, cop(48_000))), "processed")

    sale = _only_sale(db)
    assert sale.status == "held"
    legs = _legs(db, sale.id)
    assert set(legs) == {"lender", "merchant", "trust"}
    assert {p.status for p in legs.values()} == {"held"}
    assert legs["merchant"].receiver_wallet_url == ct.merchant.payout_wallet_url  # receptor congelado igual
    loan = db.get(Loan, ct.loan.id)
    assert (loan.allocated_total, loan.trust_earned) == (cop(7_200).value, cop("130,90").value)
    assert db.get(CollectionAccount, ct.account.id).ledger_in == cop(48_000).value
    # Venta held: sin aviso de división (14: solo ventas `processed`), la pata del negocio está retenida.
    assert db.scalar(select(func.count()).select_from(Notification).where(Notification.type == "sale_split")) == 0


def test_ac11_no_wallet_holds_merchant_leg(client, db, make_merchant):
    merchant = make_merchant(wallet=False)
    account = _account(db, merchant)

    _ok(_post(client, _event(account, cop(48_000))), "processed")

    sale = _only_sale(db)
    assert sale.status == "processed"
    legs = _legs(db, sale.id)
    assert list(legs) == ["merchant"]
    assert (legs["merchant"].status, legs["merchant"].receiver_wallet_url, legs["merchant"].amount) == (
        "held", None, cop(48_000).value,
    )


def test_ac12_sale_split_notification(client, db, settings, monkeypatch, make_merchant, make_lender):
    ct = _ct(db, make_merchant, make_lender)

    _ok(_post(client, _event(ct.account, cop(48_000))), "processed")
    sale = _only_sale(db)
    notes = db.scalars(select(Notification)).all()
    assert len(notes) == 1
    note = notes[0]
    assert (note.user_id, note.type) == (ct.merchant.id, "sale_split")
    assert (note.title, note.body) == ("Venta de $48.000 · $40.800 para ti", "$7.200 abonado a tu crédito.")
    assert note.data == {
        "sale_id": str(sale.id),
        "loan_id": str(ct.loan.id),
        "amount": cop(48_000).to_api(),
        "merchant_amount": cop(40_800).to_api(),
        "abono": cop(7_200).to_api(),
    }

    monkeypatch.setattr(settings, "notify_sale_split", False)
    _ok(_post(client, _event(ct.account, cop(26_000))), "processed")
    db.expire_all()
    assert _count(db, Sale) == 2
    assert _count(db, Notification) == 1


def test_ac13_sales_today(client, db, auth, freeze, make_merchant, make_lender):
    ct = _ct(db, make_merchant, make_lender)
    yesterday = datetime(2026, 10, 15, 20, 0, tzinfo=UTC)  # 3:00 p. m. del 15 en Bogotá
    freeze(datetime(2026, 10, 16, 12, 0, 0, 1, tzinfo=UTC))
    _ok(_post(client, _event(ct.account, cop(48_000), occurred_at=yesterday)), "processed")
    today = [(13, 48_000), (14, 26_000), (15, 12_500), (16, 85_000)]  # 8, 9, 10 y 11 a. m. en Bogotá
    for i, (hour, amount) in enumerate(today):
        freeze(datetime(2026, 10, 16, hour, 0, 5, 204_312 + i, tzinfo=UTC))
        _ok(_post(client, _event(ct.account, cop(amount))), "processed")

    freeze(datetime(2026, 10, 16, 16, 30, tzinfo=UTC))
    response = client.get("/api/v1/sales/today", headers=auth(ct.merchant))

    assert response.status_code == 200, response.text
    assert_contract(response, "getSalesToday")
    body = response.json()
    assert (body["date"], body["live"]) == ("2026-10-16", True)
    assert body["summary"] == {
        "sales_total": cop(171_500).to_api(),
        "sales_count": 4,
        "merchant_total": cop(145_775).to_api(),
        "abonado_total": cop(25_725).to_api(),
    }
    assert body["rule"] == {"merchant_bps": 8500, "lender_bps": 1500}
    assert body["wallet_today"] == cop(145_775).to_api()
    assert [row["amount"] for row in body["rows"]] == [cop(a).to_api() for a in (85_000, 12_500, 26_000, 48_000)]
    assert [row["abono_amount"]["value"] for row in body["rows"]] == [
        str(cop(a).value) for a in (12_750, 1_875, 3_900, 7_200)
    ]
    assert body["rows"][0]["merchant_amount"] == cop(72_250).to_api()
    assert {row["payout_status"] for row in body["rows"]} == {"in_progress"}
    assert body["next_since"] == "2026-10-16T16:00:05.204315Z"  # máximo created_at, con microsegundos

    freeze(datetime(2026, 10, 16, 16, 35, tzinfo=UTC))
    _ok(_post(client, _event(ct.account, cop(10_000))), "processed")
    response = client.get("/api/v1/sales/today", params={"since": body["next_since"]}, headers=auth(ct.merchant))
    assert response.status_code == 200, response.text
    assert_contract(response, "getSalesToday")
    polled = response.json()
    assert [row["amount"] for row in polled["rows"]] == [cop(10_000).to_api()]
    assert polled["summary"]["sales_count"] == 5  # el summary es siempre del día completo
    assert polled["next_since"] == "2026-10-16T16:35:00.000000Z"

    # Sin ventas nuevas, el cursor no se mueve; un `since` inválido es 422 y solo el comercio entra.
    again = client.get("/api/v1/sales/today", params={"since": polled["next_since"]}, headers=auth(ct.merchant))
    assert again.json()["rows"] == [] and again.json()["next_since"] == polled["next_since"]
    _error(client.get("/api/v1/sales/today?since=ayer", headers=auth(ct.merchant)), 422, "validation_error",
           "getSalesToday")
    _error(client.get("/api/v1/sales/today", headers=auth(ct.lender)), 403, "forbidden", "getSalesToday")
    _error(client.get("/api/v1/sales/today"), 401, "unauthorized", "getSalesToday")

    # Comercio sin ventas ni crédito: regla 10000/0 y cursor null.
    empty = client.get("/api/v1/sales/today", headers=auth(make_merchant()))
    assert_contract(empty, "getSalesToday")
    assert empty.json()["rule"] == {"merchant_bps": 10000, "lender_bps": 0}
    assert (empty.json()["rows"], empty.json()["next_since"]) == ([], None)


def test_ac14_sale_detail_owner(client, db, auth, make_merchant, make_lender, make_admin):
    ct = _ct(db, make_merchant, make_lender)
    _ok(_post(client, _event(ct.account, cop(48_000))), "processed")
    sale = _only_sale(db)
    lender_leg = _legs(db, sale.id)["lender"]  # lo que dejará el worker (08) al completar la pata
    lender_leg.status, lender_leg.sent_total, lender_leg.completed_at = "completed", lender_leg.amount, NOW
    lender_leg.receive_value, lender_leg.receive_asset_code, lender_leg.receive_asset_scale = 177, "USD", 2
    lender_leg.incoming_payment_url = "https://ilp.interledger-test.dev/camilo/incoming-payments/1"
    lender_leg.outgoing_payment_url = "https://ilp.interledger-test.dev/recaudo/outgoing-payments/1"
    db.commit()
    url = f"/api/v1/sales/{sale.id}"

    _error(client.get(url, headers=auth(make_merchant())), 403, "forbidden", "getSale")
    _error(client.get(url, headers=auth(ct.lender)), 403, "forbidden", "getSale")

    response = client.get(url, headers=auth(ct.merchant))
    assert response.status_code == 200, response.text
    assert_contract(response, "getSale")
    body = response.json()
    assert body["sale"]["id"] == str(sale.id)
    assert (body["sale"]["amount"], body["sale"]["source"], body["sale"]["status"]) == (
        cop(48_000).to_api(), "live", "processed",
    )
    assert body["repayment"]["abono"] == cop(7_200).to_api()
    assert body["repayment"]["trust_cut"] == cop("130,90").to_api()
    assert (body["repayment"]["loan_id"], body["repayment"]["pct_applied_bps"]) == (str(ct.loan.id), 1500)
    payouts = body["payouts"]
    assert [p["leg"] for p in payouts] == ["lender", "merchant", "trust"]
    assert [(p["status"], p["attempt"]) for p in payouts] == [("completed", 1), ("pending", 1), ("pending", 1)]
    assert payouts[0]["receive"] == {"value": "177", "asset_code": "USD", "asset_scale": 2}
    assert payouts[0]["sent_total"] == cop("7.069,10").to_api()
    assert payouts[0]["incoming_payment_url"] == lender_leg.incoming_payment_url
    assert payouts[0]["outgoing_payment_url"] == lender_leg.outgoing_payment_url
    assert payouts[0]["completed_at"] == "2026-10-16T15:42:00Z"
    assert (payouts[1]["receive"], payouts[1]["incoming_payment_url"], payouts[1]["completed_at"]) == (None, None, None)

    admin = client.get(url, headers=auth(make_admin()))
    assert admin.status_code == 200
    assert_contract(admin, "getSale")
    _error(client.get("/api/v1/sales/no-es-uuid", headers=auth(ct.merchant)), 404, "not_found", "getSale")
    _error(client.get(f"/api/v1/sales/{uuid.uuid4()}", headers=auth(ct.merchant)), 404, "not_found", "getSale")


# ── Reglas sin AC propio ──────────────────────────────────────────────────────


def test_webhook_bad_request(client, db):
    """R2: con firma válida, JSON ilegible o sin `event_id`/`type` → 400 `bad_request`, sin guardar nada."""
    for raw in (b"{no es json", b"[]", b'{"type": "qr_payment.received"}', b'{"event_id": "", "type": "x"}',
                b'{"event_id": "e1"}', b'{"event_id": "e1", "type": "qr_payment.received", "x": NaN}',
                b'{"event_id": "e1", "type": "x", "n": 1e400}', b'{"event_id": "e1", "type": "x", "p": "a\\u0000b"}'):
        _error(_post(client, raw), 400, "bad_request", OP)
    assert _count(db, WebhookEvent) == 0


def test_reconciliation_strips_nul(db, make_merchant):
    """Sin `parse_event` (reconciliación de 08), un NUL en un texto se quita en vez de romper el insert en bucle."""
    account = _account(db, make_merchant())
    event = _event(account, cop(48_000), payer_bank="Banco\x00X")

    result = service.process_puntored_event(db, event, "reconciliation")
    db.commit()

    assert result.status == "processed"
    sale = db.get(Sale, result.sale_id)
    assert sale.payer_bank == "BancoX"
    assert db.scalar(select(WebhookEvent)).payload["data"]["payer_bank"] == "BancoX"


def test_webhook_unexpected_error_rolls_back(client, db, monkeypatch, make_merchant):
    """R2: una excepción inesperada → rollback y 500; el reintento de Go (mismo event_id) se procesa una vez."""
    account = _account(db, make_merchant())
    event = _event(account, cop(48_000))

    def boom(*args, **kwargs):
        raise RuntimeError("falla inesperada")

    original = service.split_sale
    monkeypatch.setattr(service, "split_sale", boom)
    response = _post(client, event)
    assert response.status_code == 500 and response.json()["error"]["code"] == "internal_error"
    assert _count(db, WebhookEvent) == 0 and _count(db, Sale) == 0

    monkeypatch.setattr(service, "split_sale", original)
    _ok(_post(client, event), "processed")
    _ok(_post(client, event), "duplicate")
    assert _count(db, Sale) == 1


def test_list_sales_history(client, db, auth, make_merchant, make_lender):
    """R11: historial del comercio (todas las fuentes), orden `paid_at` desc, filtro por días locales, paginación."""
    ct = _ct(db, make_merchant, make_lender)
    seed = Sale(
        merchant_id=ct.merchant.id, collection_account_id=ct.account.id, bre_b_reference="SEED-1",
        amount=cop(30_000).value, asset_code="COP", asset_scale=2, source="seed", status="processed",
        paid_at=datetime(2026, 10, 10, 15, 0, tzinfo=UTC),
    )
    db.add(seed)
    db.commit()
    late_night = datetime(2026, 10, 17, 3, 0, tzinfo=UTC)  # 10:00 p. m. del 16 en Bogotá
    _ok(_post(client, _event(ct.account, cop(48_000), occurred_at=late_night)), "processed")
    _ok(_post(client, _event(ct.account, cop(26_000), occurred_at=datetime(2026, 10, 15, 15, 0, tzinfo=UTC))),
        "processed")
    other = make_merchant()
    _ok(_post(client, _event(_account(db, other), cop(99_000))), "processed")

    response = client.get("/api/v1/sales", headers=auth(ct.merchant))
    assert response.status_code == 200, response.text
    assert_contract(response, "listSales")
    items = response.json()["items"]
    assert [i["amount"]["value"] for i in items] == [str(cop(a).value) for a in (48_000, 26_000, 30_000)]
    assert items[2]["merchant_amount"] == cop(30_000).to_api() and items[2]["payout_status"] == "completed"
    assert items[0]["abono_amount"] == cop(7_200).to_api()
    assert response.json()["next_offset"] is None

    page = client.get("/api/v1/sales", params={"limit": 2}, headers=auth(ct.merchant))
    assert_contract(page, "listSales")
    assert (len(page.json()["items"]), page.json()["next_offset"]) == (2, 2)

    day = client.get("/api/v1/sales", params={"from": "2026-10-16", "to": "2026-10-16"}, headers=auth(ct.merchant))
    assert_contract(day, "listSales")
    assert [i["amount"] for i in day.json()["items"]] == [cop(48_000).to_api()]

    _error(client.get("/api/v1/sales", params={"from": "2026-10-17", "to": "2026-10-16"}, headers=auth(ct.merchant)),
           422, "validation_error", "listSales")
    _error(client.get("/api/v1/sales", params={"from": "16-10-2026"}, headers=auth(ct.merchant)),
           422, "validation_error", "listSales")
    _error(client.get("/api/v1/sales", headers=auth(ct.lender)), 403, "forbidden", "listSales")


def _in_parallel(first: dict, second: dict) -> tuple[str, str]:
    """`first` se procesa sin commit; `second` corre en otra sesión y debe esperar hasta el commit de `first`."""
    result: dict[str, str] = {}

    def run_second() -> None:
        with SessionLocal() as other:
            result["second"] = service.process_puntored_event(other, second, "reconciliation").status
            other.commit()

    with SessionLocal() as session:
        result["first"] = service.process_puntored_event(session, first, "puntored").status
        thread = threading.Thread(target=run_second)
        thread.start()
        thread.join(0.5)
        assert thread.is_alive(), "la segunda transacción no esperó el bloqueo"
        session.commit()
    thread.join(10)
    assert not thread.is_alive()
    return result["first"], result["second"]


def test_concurrent_duplicates_process_once(db, make_merchant):
    """R3.1 y R3.5 con transacciones simultáneas: el mismo event_id espera el índice único y la misma referencia
    espera el bloqueo de la cuenta; en los dos casos queda una sola venta."""
    account = _account(db, make_merchant())
    same_event = _event(account, cop(48_000))
    assert _in_parallel(same_event, same_event) == ("processed", "duplicate")
    first, second = (_event(account, cop(26_000), reference="BREB-SIM-424242") for _ in range(2))
    assert _in_parallel(first, second) == ("processed", "duplicate")

    db.expire_all()
    assert _count(db, Sale) == 2
    assert db.get(CollectionAccount, account.id).ledger_in == cop(74_000).value


def test_trust_leg_held_without_revenue_wallet(client, db, settings, monkeypatch, make_merchant, make_lender):
    """R4: sin TRUST_REVENUE_WALLET_ADDRESS la pata trust queda `held` sin receptor; las otras salen normal."""
    ct = _ct(db, make_merchant, make_lender)
    monkeypatch.setattr(settings, "trust_revenue_wallet_address", "")

    _ok(_post(client, _event(ct.account, cop(48_000))), "processed")

    legs = _legs(db, _only_sale(db).id)
    assert {leg: (p.status, p.receiver_wallet_url is None) for leg, p in legs.items()} == {
        "lender": ("pending", False), "merchant": ("pending", False), "trust": ("held", True),
    }
    assert legs["trust"].amount == cop("130,90").value


def test_payout_status_aggregate():
    """R7: `failed` > `held` > `in_progress` > `completed`; `skipped` no cuenta."""
    assert service.payout_status([]) == "completed"
    assert service.payout_status(["completed", "skipped"]) == "completed"
    assert service.payout_status(["completed", "processing"]) == "in_progress"
    assert service.payout_status(["pending", "held"]) == "held"
    assert service.payout_status(["held", "failed_final", "pending"]) == "failed"


def test_today_summary_counts_seed_sales(db, make_merchant):
    """R8/R10: las ventas `seed` (sin abono) cuentan completas como "para ti"."""
    merchant = make_merchant()
    account = _account(db, merchant)
    db.add(Sale(
        merchant_id=merchant.id, collection_account_id=account.id, bre_b_reference="SEED-HOY",
        amount=cop(12_500).value, asset_code="COP", asset_scale=2, source="seed", status="processed", paid_at=NOW,
    ))
    db.commit()
    summary = service.today_summary(db, merchant.id, clock.today_local())
    assert summary == service.TodaySummary(cop(12_500), 1, cop(12_500), cop(0))
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z", service.format_cursor(NOW))
