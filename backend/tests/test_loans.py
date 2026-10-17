"""11-loans: un test por criterio de aceptación (más los endpoints y reglas sin AC propio)."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.events import Event
from app.core.money import Money
from app.integrations.op.client import OpError, OpUnavailable, request_json
from app.modules.loans import service
from app.modules.loans.models import Loan, LoanPctChange
from app.modules.marketplace.models import LoanOffer, LoanRequest
from app.modules.notifications.models import Notification
from app.modules.recaudo.models import CollectionAccount
from app.modules.sales.models import Repayment, Sale
from app.modules.scoring.models import CreditAssessment
from app.modules.users.models import User
from app.worker import run_once
from tests.helpers import assert_contract, assert_op_schema, cop
from tests.test_marketplace import _offer
from tests.test_sales import _account
from tests.test_scoring import NOW, _assessment

LOANS = "/api/v1/loans"
PREVIEW = "/api/v1/loans/preview"
H = timedelta(hours=1)
DAY = timedelta(days=1)
WEB_RETURN = "https://app.trustservices.com.co/op/return?loan_id={id}&client={client}"

_refs = iter(range(1, 1_000_000))


@pytest.fixture(autouse=True)
def _env(freeze):
    freeze(NOW)


# ── Ayudas ────────────────────────────────────────────────────────────────────


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _request(db: Session, merchant: User, assessment: CreditAssessment) -> LoanRequest:
    """Solicitud `open` de la base: $1.500.000–$3.000.000 @1500."""
    low, high, pct_bps = cop(1_500_000), cop(3_000_000), 1500
    request = LoanRequest(
        merchant_id=merchant.id, min_amount=low.value, max_amount=high.value, asset_code="COP", asset_scale=2,
        pct_bps=pct_bps, status="open", credit_assessment_id=assessment.id,
    )
    db.add(request)
    db.commit()
    return request


def _base(db: Session, make_merchant, make_lender) -> SimpleNamespace:
    """Base de los AC: negocio elegible (cupo $6.000.000, wallet propia, recaudo `active`) con solicitud
    $1.500.000–$3.000.000 @1500, y Camilo P. con wallet y oferta A $1.000.000–$2.500.000 @1500, cargo 500, 3 meses."""
    merchant = make_merchant()
    assessment = _assessment(db, merchant)
    account = _account(db, merchant)
    request = _request(db, merchant, assessment)
    lender = make_lender()
    offer = _offer(db, lender, cop(1_000_000), cop(2_500_000), 1500, fee_bps=500, term_months=3)
    return SimpleNamespace(
        merchant=merchant, assessment=assessment, account=account, request=request, lender=lender, offer=offer
    )


@pytest.fixture
def base(db, make_merchant, make_lender) -> SimpleNamespace:
    return _base(db, make_merchant, make_lender)


def _error(response, status: int, code: str, operation_id: str) -> dict:
    assert response.status_code == status, response.text
    assert_contract(response, operation_id)
    error = response.json()["error"]
    assert error["code"] == code, error
    return error


def _create(client, auth, user: User, body: dict) -> dict:
    response = client.post(LOANS, json=body, headers=auth(user))
    assert response.status_code == 201, response.text
    assert_contract(response, "createLoan")
    return response.json()


def _action(client, auth, user: User, loan_id: str, action: str, operation_id: str) -> dict:
    response = client.post(f"{LOANS}/{loan_id}/{action}", headers=auth(user))
    assert response.status_code == 200, response.text
    assert_contract(response, operation_id)
    return response.json()


def _disburse(client, auth, user: User, loan_id: str, client_kind: str = "web"):
    return client.post(f"{LOANS}/{loan_id}/disburse", json={"client": client_kind}, headers=auth(user))


def _detail(client, auth, user: User, loan_id) -> dict:
    response = client.get(f"{LOANS}/{loan_id}", headers=auth(user))
    assert response.status_code == 200, response.text
    assert_contract(response, "getLoan")
    return response.json()


def _get(db: Session, model, row_id):
    db.expire_all()
    return db.get(model, uuid.UUID(str(row_id)))


def _locked(db: Session, loan_id) -> Loan:
    db.expire_all()
    return db.scalar(select(Loan).where(Loan.id == uuid.UUID(str(loan_id))).with_for_update())


def _notes(db: Session, user: User, type: str | None = None) -> list[Notification]:
    db.expire_all()
    query = select(Notification).where(Notification.user_id == user.id)
    if type is not None:
        query = query.where(Notification.type == type)
    return list(db.scalars(query.order_by(Notification.created_at, Notification.id)))


def _events(db: Session, loan_id) -> list[str]:
    """Tipos de evento del crédito, ordenados alfabéticamente (con el reloj congelado comparten `created_at`)."""
    db.expire_all()
    return sorted(
        db.scalars(select(Event.type).where(Event.entity_type == "loan", Event.entity_id == uuid.UUID(str(loan_id))))
    )


def _loan_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Loan))


def _keys(fake_op) -> list[str]:
    return [call.idempotency_key for call in fake_op.calls_to("create_payment")]


def _match_body(base: SimpleNamespace) -> dict:
    return {"request_id": str(base.request.id), "offer_id": str(base.offer.id)}


def _active_loan(db: Session, merchant: User, lender: User, **fields) -> Loan:
    """Crédito `active` insertado directo: $2.000.000 + cargo 500 = $2.100.000, mínimo $700.000, pct 1500
    (min 1000, max 2000)."""
    account = db.scalar(select(CollectionAccount).where(CollectionAccount.merchant_id == merchant.id))
    assessment = db.scalar(select(CreditAssessment).where(CreditAssessment.merchant_id == merchant.id))
    values = {
        "merchant_id": merchant.id,
        "lender_id": lender.id,
        "origin": "offer_taken",
        "created_by": merchant.id,
        "credit_assessment_id": (assessment or _assessment(db, merchant)).id,
        "collection_account_id": (account or _account(db, merchant)).id,
        "merchant_payout_wallet_url": merchant.payout_wallet_url,
        "lender_wallet_url": lender.payout_wallet_url,
        "lender_asset_code": "COP",
        "lender_asset_scale": 2,
        "asset_code": "COP",
        "asset_scale": 2,
        "principal": cop(2_000_000).value,
        "fee_bps": 500,
        "fee_amount": cop(100_000).value,
        "total_repayable": cop(2_100_000).value,
        "trust_share_bps": 2000,
        "term_months": 3,
        "monthly_minimum": cop(700_000).value,
        "pct_initial_bps": 1500,
        "pct_min_bps": 1000,
        "pct_max_bps": 2000,
        "terms": {},
        "status": "active",
        "pct_bps": 1500,
        "disbursement_attempt": 1,
        "accepted_at": NOW - 31 * DAY,
        "activated_at": NOW - 30 * DAY,
        "period_start": NOW - 30 * DAY,
        "next_review_at": NOW,
        **fields,
    }
    loan = Loan(**values)
    db.add(loan)
    db.commit()
    return loan


def _repay(db: Session, loan: Loan, amount: Money, at: datetime) -> Repayment:
    """Abono de `amount` con `created_at = at` (y su venta), como los deja 07."""
    sale = Sale(
        merchant_id=loan.merchant_id, collection_account_id=loan.collection_account_id,
        bre_b_reference=f"BREB-LOANS-{next(_refs)}", amount=amount.value * 10_000 // loan.pct_bps,
        asset_code="COP", asset_scale=2, paid_at=at, source="live", status="processed", created_at=at,
    )
    db.add(sale)
    db.flush()
    repayment = Repayment(
        sale_id=sale.id, loan_id=loan.id, merchant_id=loan.merchant_id, lender_id=loan.lender_id,
        abono_amount=amount.value, trust_cut_amount=0, lender_net_amount=amount.value, asset_code="COP",
        asset_scale=2, pct_applied_bps=loan.pct_bps, allocated_before=loan.allocated_total,
        allocated_after=loan.allocated_total + amount.value, created_at=at,
    )
    db.add(repayment)
    loan.allocated_total += amount.value
    db.commit()
    return repayment


def _disbursing(client, auth, base: SimpleNamespace, fake_op) -> tuple[str, uuid.UUID]:
    """El negocio toma la oferta A y el prestamista inicia el desembolso: (loan_id, payment_id)."""
    loan_id = _create(client, auth, base.merchant, {"offer_id": str(base.offer.id)})["id"]
    response = _disburse(client, auth, base.lender, loan_id)
    assert response.status_code == 200, response.text
    assert_contract(response, "disburseLoan")
    return loan_id, fake_op.payments[-1].id


# ── Criterios de aceptación ───────────────────────────────────────────────────


def test_ac1_preview_no_writes(client, db, auth, base):
    response = client.post(PREVIEW, json={"offer_id": str(base.offer.id)}, headers=auth(base.merchant))

    assert response.status_code == 200, response.text
    assert_contract(response, "previewLoan")
    body = response.json()
    assert (body["origin"], body["request_id"], body["offer_id"]) == (
        "match", str(base.request.id), str(base.offer.id)
    )
    assert body["merchant"] == {
        "id": str(base.merchant.id), "business_name": "Tienda La Esquina", "city": "Bucaramanga", "initials": "TL",
    }
    assert body["lender"] == {"id": str(base.lender.id), "public_name": "Camilo P."}
    assert body["terms"] == {
        "principal": cop(2_000_000).to_api(),
        "fee_bps": 500,
        "fee_amount": cop(100_000).to_api(),
        "total_repayable": cop(2_100_000).to_api(),
        "trust_share_bps": 2000,
        "term_months": 3,
        "monthly_minimum": cop(700_000).to_api(),
        "pct_initial_bps": 1500,
        "pct_min_bps": 1000,
        "pct_max_bps": 1500,
    }
    assert body["example"] == {
        "sale": cop(48_000).to_api(),
        "abono": cop(7_200).to_api(),
        "merchant_amount": cop(40_800).to_api(),
        "trust_cut": cop("68,57").to_api(),
        "lender_net": cop("7.131,43").to_api(),
    }
    assert body["expires_in_hours"] == 48
    assert _loan_count(db) == 0
    assert (_get(db, LoanRequest, base.request.id).status, _get(db, LoanOffer, base.offer.id).status) == (
        "open", "open"
    )
    assert db.scalar(select(func.count()).select_from(Event).where(Event.entity_type == "loan")) == 0


def test_ac2_merchant_takes_offer(client, db, auth, base):
    preview = client.post(PREVIEW, json={"offer_id": str(base.offer.id)}, headers=auth(base.merchant)).json()

    body = _create(client, auth, base.merchant, {"offer_id": str(base.offer.id)})

    assert (body["status"], body["origin"]) == ("pending_disbursement", "match")
    assert (body["request_id"], body["offer_id"]) == (str(base.request.id), str(base.offer.id))
    assert body["expires_at"] == _iso(NOW + 48 * H)
    assert (body["created_at"], body["accepted_at"], body["activated_at"], body["paid_at"]) == (
        _iso(NOW), _iso(NOW), None, None
    )
    assert body["terms"] == preview["terms"]
    assert body["pct_bps"] == 1500 and body["pct_paid_bps"] == 0
    assert body["outstanding"] == cop(2_100_000).to_api() and body["allocated"] == cop(0).to_api()
    assert body["next_action"] == {"type": "disburse", "actor": "lender"}
    assert body["disbursement"] == {
        "status": "not_started", "authorization_url": None, "authorization_expires_at": None, "attempt": 0
    }
    assert body["period"] is None

    loan = _get(db, Loan, body["id"])
    assert loan.credit_assessment_id == base.assessment.id
    assert (loan.created_by, loan.collection_account_id) == (base.merchant.id, base.account.id)
    assert (loan.merchant_payout_wallet_url, loan.lender_wallet_url) == (
        base.merchant.payout_wallet_url, base.lender.payout_wallet_url
    )
    assert (loan.lender_asset_code, loan.lender_asset_scale, loan.asset_code, loan.asset_scale) == ("COP", 2, "COP", 2)
    assert loan.terms == preview  # snapshot del Preview completo, montos en Money
    assert (_get(db, LoanRequest, base.request.id).status, _get(db, LoanOffer, base.offer.id).status) == (
        "matched", "taken"
    )
    [note] = _notes(db, base.lender, "loan_accepted")
    assert (note.title, note.body) == (
        "Tienda La Esquina aceptó tu oferta", "Autoriza en tu wallet el desembolso de $2.000.000."
    )
    assert note.data == {"loan_id": body["id"], "principal": cop(2_000_000).to_api()}
    assert _events(db, body["id"]) == sorted(["created"])

    profile = client.get("/api/v1/me/credit-profile", headers=auth(base.merchant))
    assert profile.status_code == 200, profile.text
    assert_contract(profile, "getMyCreditProfile")
    assert profile.json()["cupo_disponible"] == cop(4_000_000).to_api()


def test_ac3_lender_funds_request(client, db, auth, base):
    funding = {"request_id": str(base.request.id), "amount": cop(2_000_000).to_api()}

    error = _error(client.post(LOANS, json=funding, headers=auth(base.lender)), 422, "validation_error", "createLoan")
    assert [f["field"] for f in error["details"]["fields"]] == ["fee_bps"]
    assert _loan_count(db) == 0

    body = _create(client, auth, base.lender, {**funding, "fee_bps": 500})

    assert (body["status"], body["origin"], body["pct_bps"]) == ("pending_acceptance", "request_funded", 1500)
    assert (body["request_id"], body["offer_id"], body["accepted_at"]) == (str(base.request.id), None, None)
    terms = body["terms"]
    assert (terms["term_months"], terms["fee_bps"], terms["pct_min_bps"], terms["pct_max_bps"]) == (3, 500, 1000, 1500)
    assert (terms["principal"], terms["total_repayable"]) == (cop(2_000_000).to_api(), cop(2_100_000).to_api())
    assert body["next_action"] == {"type": "accept", "actor": "merchant"}
    assert body["expires_at"] == _iso(NOW + 48 * H)
    assert _get(db, LoanRequest, base.request.id).status == "matched"
    assert _get(db, LoanOffer, base.offer.id).status == "open"
    [note] = _notes(db, base.merchant, "offer_received")
    assert (note.title, note.body) == (
        "Recibiste una oferta",
        "Camilo P. te ofrece $2.000.000 con el 15 % de cada venta. Revísala antes de aceptar.",
    )
    assert note.data == {"loan_id": body["id"], "principal": cop(2_000_000).to_api(), "pct_bps": 1500}
    assert _get(db, Loan, body["id"]).created_by == base.lender.id


def test_ac4_create_validations(client, db, auth, base, make_merchant, make_lender):
    take_a = {"offer_id": str(base.offer.id), "amount": cop(2_000_000).to_api()}

    no_account = make_merchant()
    _assessment(db, no_account)
    _error(client.post(LOANS, json=take_a, headers=auth(no_account)), 422, "collection_account_required",
           "createLoan")

    no_wallet = make_merchant(wallet=False)
    _assessment(db, no_wallet)
    _account(db, no_wallet)
    error = _error(client.post(LOANS, json=take_a, headers=auth(no_wallet)), 422, "wallet_required", "createLoan")
    assert error["details"] == {"party": "merchant"}

    too_much = {"offer_id": str(base.offer.id), "amount": cop(2_600_000).to_api()}
    _error(client.post(LOANS, json=too_much, headers=auth(base.merchant)), 422, "no_overlap", "createLoan")

    base.assessment.cupo_total = cop(1_000_000).value
    db.commit()
    error = _error(client.post(LOANS, json={"offer_id": str(base.offer.id)}, headers=auth(base.merchant)), 422,
                   "exceeds_cupo", "createLoan")
    assert error["details"]["cupo_disponible"] == cop(1_000_000).to_api()
    base.assessment.cupo_total = cop(6_000_000).value
    db.commit()

    _create(client, auth, base.merchant, {"offer_id": str(base.offer.id)})
    other_offer = _offer(db, make_lender(first_name="Ana"), cop(1_000_000), cop(2_000_000), 1500)
    again = {"offer_id": str(other_offer.id), "amount": cop(1_000_000).to_api()}
    _error(client.post(LOANS, json=again, headers=auth(base.merchant)), 409, "active_loan_exists", "createLoan")

    other = make_merchant()
    _assessment(db, other)
    _account(db, other)
    _error(client.post(LOANS, json=take_a, headers=auth(other)), 409, "invalid_state", "createLoan")
    _error(client.post(PREVIEW, json=take_a, headers=auth(other)), 409, "invalid_state", "previewLoan")
    assert _loan_count(db) == 1


def test_ac5_accept(client, db, auth, base, freeze):
    funding = {"request_id": str(base.request.id), "amount": cop(2_000_000).to_api(), "fee_bps": 500}
    loan_id = _create(client, auth, base.lender, funding)["id"]

    _error(client.post(f"{LOANS}/{loan_id}/accept", headers=auth(base.lender)), 403, "forbidden", "acceptLoan")

    freeze(NOW + H)
    body = _action(client, auth, base.merchant, loan_id, "accept", "acceptLoan")

    assert body["status"] == "pending_disbursement"
    assert (body["accepted_at"], body["expires_at"]) == (_iso(NOW + H), _iso(NOW + 49 * H))
    assert body["next_action"] == {"type": "disburse", "actor": "lender"}
    [note] = _notes(db, base.lender, "loan_accepted")
    assert note.title == "Tienda La Esquina aceptó tu oferta" and note.data["loan_id"] == loan_id
    assert _events(db, loan_id) == sorted(["created", "accepted"])

    _error(client.post(f"{LOANS}/{loan_id}/accept", headers=auth(base.merchant)), 409, "invalid_state", "acceptLoan")


def test_accept_after_expiry(client, db, auth, base, freeze):
    """R7: vencido el plazo (`expires_at ≤ now`) no se acepta ni se reabre; lo vence el worker (R12)."""
    funding = {"request_id": str(base.request.id), "amount": cop(2_000_000).to_api(), "fee_bps": 500}
    loan_id = _create(client, auth, base.lender, funding)["id"]  # pending_acceptance, expires_at = NOW + 48 h

    for moment in (NOW + 48 * H, NOW + 49 * H):
        freeze(moment)
        response = client.post(f"{LOANS}/{loan_id}/accept", headers=auth(base.merchant))
        error = _error(response, 409, "invalid_state", "acceptLoan")
        assert (error["message"], error["details"]) == ("El crédito ya venció.", {"status": "pending_acceptance"})

    loan = _get(db, Loan, loan_id)
    assert (loan.status, loan.accepted_at, loan.expires_at) == ("pending_acceptance", None, NOW + 48 * H)
    assert _notes(db, base.lender, "loan_accepted") == []
    assert _events(db, loan_id) == ["created"]


def test_ac6_reject_cancel_expire_reopen(client, db, auth, base):
    def statuses() -> tuple[str, str]:
        return _get(db, LoanRequest, base.request.id).status, _get(db, LoanOffer, base.offer.id).status

    rejected = _create(client, auth, base.lender, _match_body(base))
    assert (rejected["status"], rejected["origin"]) == ("pending_acceptance", "match")
    body = _action(client, auth, base.merchant, rejected["id"], "reject", "rejectLoan")
    assert body["status"] == "rejected" and body["expires_at"] is None
    assert _get(db, Loan, rejected["id"]).rejected_at == NOW
    assert statuses() == ("open", "open")
    [note] = _notes(db, base.lender, "loan_rejected")
    assert note.body == "Tienda La Esquina rechazó el crédito de $2.000.000."

    cancelled = _create(client, auth, base.merchant, {"offer_id": str(base.offer.id)})
    assert cancelled["status"] == "pending_disbursement"
    assert _action(client, auth, base.lender, cancelled["id"], "cancel", "cancelLoan")["status"] == "cancelled"
    assert _get(db, Loan, cancelled["id"]).cancelled_at == NOW
    assert statuses() == ("open", "open")
    [note] = _notes(db, base.merchant, "loan_cancelled")
    assert note.body == "Camilo P. canceló el crédito de $2.000.000. Tu solicitud u oferta vuelve a estar abierta."

    expiring = _create(client, auth, base.merchant, {"offer_id": str(base.offer.id)})
    assert service.expire_pending(db, NOW + 47 * H) == 0
    assert service.expire_pending(db, NOW + 49 * H) == 1
    db.commit()

    loan = _get(db, Loan, expiring["id"])
    assert (loan.status, loan.expired_at) == ("expired", NOW + 49 * H)
    assert statuses() == ("open", "open")
    for user in (base.merchant, base.lender):
        [note] = _notes(db, user, "loan_expired")
        assert (note.title, note.body) == (
            "El crédito venció", "Pasaron 48 h sin desembolso. Tu solicitud u oferta vuelve a estar abierta."
        )
        assert note.data == {"loan_id": expiring["id"]}
    assert _events(db, rejected["id"]) == sorted(["created", "rejected"])
    assert _events(db, cancelled["id"]) == sorted(["created", "cancelled"])
    assert _events(db, expiring["id"]) == sorted(["created", "expired"])
    detail = _detail(client, auth, base.merchant, expiring["id"])
    assert (detail["status"], detail["expires_at"]) == ("expired", None)


def test_ac7_disburse_and_reuse_url(client, db, auth, base, fake_op, make_admin):
    loan_id = _create(client, auth, base.merchant, {"offer_id": str(base.offer.id)})["id"]

    response = _disburse(client, auth, base.lender, loan_id)

    assert response.status_code == 200, response.text
    assert_contract(response, "disburseLoan")
    body = response.json()
    [payment] = fake_op.payments
    assert body == {
        "status": "disbursing",
        "authorization_url": payment.authorization_url,
        "authorization_expires_at": _iso(payment.authorization_expires_at),
    }
    [call] = fake_op.calls_to("create_payment")
    assert call.idempotency_key == f"loan:{loan_id}:disb:1"
    assert call.body.receive_amount.to_money() == cop(2_000_000)
    assert call.body.external_ref == f"loan:{loan_id}:disb"
    assert call.body.return_url == WEB_RETURN.format(id=loan_id, client="web")
    assert call.body.description == "Desembolso crédito Trust · Tienda La Esquina"
    assert (call.body.sender_wallet_address, call.body.receiver_wallet_address) == (
        base.lender.payout_wallet_url, base.merchant.payout_wallet_url
    )
    assert_op_schema(request_json(call.body), "CreatePaymentRequest")
    loan = _get(db, Loan, loan_id)
    assert (loan.status, loan.op_disbursement_payment_id, loan.disbursement_attempt) == (
        "disbursing", payment.id, 1
    )
    assert (loan.disbursement_started_at, loan.disbursement_authorization_url) == (NOW, payment.authorization_url)

    second = _disburse(client, auth, base.lender, loan_id)
    assert second.status_code == 200
    assert_contract(second, "disburseLoan")
    assert second.json() == body
    assert len(fake_op.calls_to("create_payment")) == 1

    as_lender = _detail(client, auth, base.lender, loan_id)
    assert as_lender["next_action"] == {"type": "authorize_in_wallet", "actor": "lender"}
    assert as_lender["disbursement"] == {
        "status": "in_progress", "authorization_url": payment.authorization_url,
        "authorization_expires_at": _iso(payment.authorization_expires_at), "attempt": 1,
    }
    as_merchant = _detail(client, auth, base.merchant, loan_id)
    assert as_merchant["disbursement"]["authorization_url"] is None
    assert as_merchant["disbursement"]["authorization_expires_at"] is None
    assert as_merchant["expires_at"] is None  # `disbursing`: ya no es un estado pendiente
    as_admin = _detail(client, auth, make_admin(), loan_id)
    assert (as_admin["disbursement"]["authorization_url"], as_admin["disbursement"]["authorization_expires_at"]) == (
        None, None
    )
    assert _events(db, loan_id) == sorted(["created", "disbursement_requested", "disbursing"])


def test_ac8_disburse_op_unavailable_same_key(client, db, auth, base, fake_op):
    loan_id = _create(client, auth, base.merchant, {"offer_id": str(base.offer.id)})["id"]
    fake_op.fail_next("create_payment", OpUnavailable("caído"))

    _error(_disburse(client, auth, base.lender, loan_id), 503, "op_unavailable", "disburseLoan")

    loan = _get(db, Loan, loan_id)
    assert (loan.status, loan.disbursement_attempt) == ("pending_disbursement", 1)
    response = _disburse(client, auth, base.lender, loan_id)
    assert response.status_code == 200, response.text
    assert_contract(response, "disburseLoan")
    assert _keys(fake_op) == [f"loan:{loan_id}:disb:1", f"loan:{loan_id}:disb:1"]
    assert _get(db, Loan, loan_id).status == "disbursing"


def test_ac9_disbursement_update(client, db, auth, base, fake_op, freeze, make_merchant, make_lender):
    loan_id, payment_id = _disbursing(client, auth, base, fake_op)
    fake_op.approve_payment(payment_id)
    fake_op.complete_payment(payment_id)
    freeze(NOW + H)

    service.on_disbursement_update(db, _locked(db, loan_id), fake_op.get_payment(payment_id))
    db.commit()

    loan = _get(db, Loan, loan_id)
    assert loan.status == "active"
    assert loan.period_start == loan.activated_at == NOW + H
    assert loan.next_review_at == NOW + H + 30 * DAY
    assert loan.disbursement_authorization_url is None and loan.disbursement_authorization_expires_at is None
    [to_merchant] = _notes(db, base.merchant, "loan_active")
    assert (to_merchant.title, to_merchant.body) == (
        "Tu crédito está activo", "Recibiste $2.000.000. Desde hoy, el 15 % de cada venta abona a tu crédito."
    )
    [to_lender] = _notes(db, base.lender, "loan_active")
    assert (to_lender.title, to_lender.body) == (
        "Desembolso completado", "Tienda La Esquina recibió $2.000.000. Los abonos llegan con cada venta."
    )
    detail = _detail(client, auth, base.merchant, loan_id)
    assert detail["status"] == "active" and detail["activated_at"] == _iso(NOW + H)
    assert detail["disbursement"]["status"] == "completed" and detail["next_action"] == {"type": "none", "actor": None}
    assert detail["period"] == {
        "start": _iso(NOW + H), "next_review_at": _iso(NOW + H + 30 * DAY),
        "allocated_in_period": cop(0).to_api(), "target": cop(700_000).to_api(),
    }
    assert "activated" in _events(db, loan_id)

    other = _base(db, make_merchant, make_lender)
    failed_id, failed_payment = _disbursing(client, auth, other, fake_op)
    fake_op.fail_payment(failed_payment, "insufficient_balance")

    service.on_disbursement_update(db, _locked(db, failed_id), fake_op.get_payment(failed_payment))
    db.commit()

    loan = _get(db, Loan, failed_id)
    assert (loan.status, loan.disbursement_attempt, loan.disbursement_authorization_url) == (
        "pending_disbursement", 2, None
    )
    [note] = _notes(db, other.lender, "action_required")
    assert (note.title, note.body) == (
        "El desembolso no se completó", "Vuelve a autorizar el desembolso de $2.000.000 a Tienda La Esquina."
    )
    assert note.data == {"loan_id": failed_id, "failure_code": "insufficient_balance"}
    assert _detail(client, auth, other.lender, failed_id)["next_action"] == {"type": "disburse", "actor": "lender"}

    response = _disburse(client, auth, other.lender, failed_id)
    assert response.status_code == 200, response.text
    assert_contract(response, "disburseLoan")
    assert _keys(fake_op)[-1] == f"loan:{failed_id}:disb:2"
    assert _get(db, Loan, failed_id).op_disbursement_payment_id == fake_op.payments[-1].id


def test_ac10_loan_detail_and_list(client, db, auth, make_merchant, make_lender, make_admin):
    merchant, lender = make_merchant(), make_lender()
    active = _active_loan(
        db, merchant, lender, fee_bps=0, fee_amount=0, total_repayable=cop(2_000_000).value,
        monthly_minimum=cop("666.666,67").value, allocated_total=cop(640_000).value,
        delivered_total=cop(632_800).value, next_review_at=NOW + 16 * DAY, period_start=NOW - 14 * DAY,
    )

    body = _detail(client, auth, merchant, active.id)

    assert (body["pct_paid_bps"], body["outstanding"], body["allocated"], body["delivered"]) == (
        3200, cop(1_360_000).to_api(), cop(640_000).to_api(), cop(632_800).to_api()
    )
    assert body["next_action"] == {"type": "none", "actor": None}
    assert (body["terms"]["total_repayable"], body["terms"]["monthly_minimum"]) == (
        Money(200_000_000, "COP", 2).to_api(), Money(66_666_667, "COP", 2).to_api()
    )
    assert body["period"] == {
        "start": _iso(NOW - 14 * DAY), "next_review_at": _iso(NOW + 16 * DAY),
        "allocated_in_period": cop(0).to_api(), "target": Money(66_666_667, "COP", 2).to_api(),
    }
    assert body["disbursement"]["status"] == "completed" and body["expires_at"] is None

    other_merchant = make_merchant(business_name="Panadería El Trigal")
    pending = _active_loan(db, other_merchant, lender, status="pending_disbursement", activated_at=None,
                           period_start=None, next_review_at=None, expires_at=NOW + 48 * H,
                           created_at=NOW + H)
    detail = _detail(client, auth, lender, pending.id)
    assert detail["next_action"] == {"type": "disburse", "actor": "lender"}
    assert (detail["expires_at"], detail["period"]) == (_iso(NOW + 48 * H), None)

    stranger = make_merchant()
    _error(client.get(f"{LOANS}/{active.id}", headers=auth(stranger)), 403, "forbidden", "getLoan")
    _error(client.get(f"{LOANS}/{uuid.uuid4()}", headers=auth(lender)), 404, "not_found", "getLoan")
    _error(client.get(f"{LOANS}/no-es-uuid", headers=auth(lender)), 404, "not_found", "getLoan")
    assert _detail(client, auth, make_admin(), active.id)["id"] == str(active.id)

    other_lender = make_lender(first_name="Ana", last_name="Díaz")
    _active_loan(db, make_merchant(business_name="Papelería Nova"), other_lender)

    response = client.get(f"{LOANS}?status=active", headers=auth(lender))
    assert response.status_code == 200, response.text
    assert_contract(response, "listLoans")
    page = response.json()
    assert [item["id"] for item in page["items"]] == [str(active.id)] and page["next_offset"] is None
    assert page["items"][0] == {
        "id": str(active.id), "status": "active", "origin": "offer_taken",
        "merchant": {"id": str(merchant.id), "business_name": "Tienda La Esquina", "city": "Bucaramanga",
                     "initials": "TL"},
        "lender": {"id": str(lender.id), "public_name": "Camilo P."},
        "principal": cop(2_000_000).to_api(), "pct_bps": 1500, "allocated": cop(640_000).to_api(),
        "pct_paid_bps": 3200, "is_late": False, "next_action": {"type": "none", "actor": None},
        "created_at": _iso(NOW), "activated_at": _iso(NOW - 30 * DAY),
    }

    mine = client.get(LOANS, headers=auth(lender)).json()
    assert [item["id"] for item in mine["items"]] == [str(pending.id), str(active.id)]  # más recientes primero
    both = client.get(f"{LOANS}?status=active,pending_disbursement&limit=1", headers=auth(lender))
    assert_contract(both, "listLoans")
    assert [i["id"] for i in both.json()["items"]] == [str(pending.id)] and both.json()["next_offset"] == 1
    assert len(client.get(LOANS, headers=auth(make_admin())).json()["items"]) == 3
    assert [i["id"] for i in client.get(LOANS, headers=auth(merchant)).json()["items"]] == [str(active.id)]
    _error(client.get(f"{LOANS}?status=active,bogus", headers=auth(lender)), 422, "validation_error", "listLoans")


def test_ac11_close_period(client, db, auth, make_merchant, make_lender):
    merchant, lender = make_merchant(), make_lender()
    loan = _active_loan(db, merchant, lender)
    _repay(db, loan, cop(500_000), NOW - 10 * DAY)

    first = service.close_period(db, loan.id, NOW)
    db.commit()

    assert (first.old_pct_bps, first.new_pct_bps, first.reason) == (1500, 1750, "missed_minimum")
    assert (first.period_start, first.period_end) == (NOW - 30 * DAY, NOW)
    assert (first.allocated_in_period, first.target_amount) == (cop(500_000).value, cop(700_000).value)
    loan = _get(db, Loan, loan.id)
    assert (loan.pct_bps, loan.missed_periods, loan.is_late) == (1750, 1, False)
    assert (loan.period_start, loan.next_review_at) == (NOW, NOW + 30 * DAY)
    [to_merchant] = _notes(db, merchant, "pct_changed")
    assert (to_merchant.title, to_merchant.body) == (
        "Cambió tu porcentaje", "Desde ahora abonas el 17,5 % de cada venta (antes 15 %)."
    )
    [to_lender] = _notes(db, lender, "pct_changed")
    assert to_lender.title == "Tienda La Esquina cambió de porcentaje"
    assert to_lender.data == {"loan_id": str(loan.id), "old_pct_bps": 1500, "new_pct_bps": 1750,
                              "reason": "missed_minimum"}

    second = service.close_period(db, loan.id, NOW + 30 * DAY)
    db.commit()

    assert (second.new_pct_bps, second.reason, second.allocated_in_period) == (2000, "missed_minimum", 0)
    loan = _get(db, Loan, loan.id)
    assert (loan.pct_bps, loan.missed_periods, loan.is_late) == (2000, 2, True)
    for user, body in ((merchant, "No alcanzaste el mínimo en 2 periodos."),
                       (lender, "Tienda La Esquina no alcanzó el mínimo en 2 periodos.")):
        [note] = _notes(db, user, "loan_late")
        assert note.body == body and note.data == {"loan_id": str(loan.id), "missed_periods": 2}

    _repay(db, loan, cop(700_000), NOW + 40 * DAY)
    third = service.close_period(db, loan.id, NOW + 61 * DAY)
    db.commit()

    assert (third.old_pct_bps, third.new_pct_bps, third.reason) == (2000, 2000, "unchanged")
    assert (third.period_start, third.period_end) == (NOW + 30 * DAY, NOW + 60 * DAY)  # cierra en next_review_at
    assert (third.allocated_in_period, third.target_amount) == (cop(700_000).value, cop(700_000).value)
    loan = _get(db, Loan, loan.id)
    assert (loan.missed_periods, loan.is_late, loan.next_review_at) == (2, True, NOW + 90 * DAY)
    assert len(_notes(db, merchant, "pct_changed")) == 2 and len(_notes(db, merchant, "loan_late")) == 1
    assert db.scalar(select(func.count()).select_from(LoanPctChange).where(LoanPctChange.loan_id == loan.id)) == 3
    assert _events(db, loan.id) == sorted(["period_closed"] * 3)

    response = client.get(f"{LOANS}/{loan.id}/pct-history", headers=auth(merchant))
    assert response.status_code == 200, response.text
    assert_contract(response, "getLoanPctHistory")
    items = response.json()["items"]
    assert [(i["reason"], i["old_pct_bps"], i["new_pct_bps"]) for i in items] == [
        ("missed_minimum", 1500, 1750), ("missed_minimum", 1750, 2000), ("unchanged", 2000, 2000)
    ]
    assert items[0]["allocated_in_period"] == cop(500_000).to_api()
    assert items[0]["period_start"] == _iso(NOW - 30 * DAY)
    _error(client.get(f"{LOANS}/{loan.id}/pct-history", headers=auth(make_merchant())), 403, "forbidden",
           "getLoanPctHistory")
    _error(client.get(f"{LOANS}/{uuid.uuid4()}/pct-history", headers=auth(merchant)), 404, "not_found",
           "getLoanPctHistory")

    pending = _active_loan(db, make_merchant(), lender, status="pending_disbursement")
    with pytest.raises(ValueError):
        service.close_period(db, pending.id, NOW)
    db.rollback()
    with pytest.raises(ValueError):
        service.close_period(db, uuid.uuid4(), NOW)


# ── Reglas sin AC propio ──────────────────────────────────────────────────────


def test_draft_validation_order(client, db, auth, base, make_merchant, make_lender, make_admin, settings):
    """R1/R2/R4: 400 forma → 404 → 403 → 409 → … → 422, para preview y para crear."""
    merchant, lender = auth(base.merchant), auth(base.lender)
    oid, rid = str(base.offer.id), str(base.request.id)

    _error(client.post(PREVIEW, json={"request_id": rid}, headers=merchant), 400, "bad_request", "previewLoan")
    _error(client.post(LOANS, json={"offer_id": oid}, headers=lender), 400, "bad_request", "createLoan")
    _error(client.post(LOANS, json={"offer_id": oid}, headers=auth(make_admin())), 403, "forbidden", "createLoan")
    _error(client.post(LOANS, json={"offer_id": "no-es-uuid"}, headers=merchant), 422, "validation_error",
           "createLoan")
    _error(client.post(LOANS, json={"offer_id": str(uuid.uuid4())}, headers=merchant), 404, "not_found",
           "createLoan")
    _error(client.post(LOANS, json={"request_id": str(uuid.uuid4()), "offer_id": oid}, headers=lender), 404,
           "not_found", "createLoan")

    other = make_merchant()
    other_request = _request(db, other, _assessment(db, other))
    foreign = {"request_id": str(other_request.id), "offer_id": oid}
    _error(client.post(PREVIEW, json=foreign, headers=merchant), 403, "forbidden", "previewLoan")
    other_offer = _offer(db, make_lender(first_name="Ana"), cop(1_000_000), cop(2_500_000), 1500)
    _error(client.post(PREVIEW, json={"request_id": rid, "offer_id": str(other_offer.id)}, headers=lender), 403,
           "forbidden", "previewLoan")

    # Lender: request_funded con cargo o plazo fuera de rango, o sin monto.
    funding = {"request_id": rid, "amount": cop(2_000_000).to_api()}
    for extra, field in (({"fee_bps": 10_001}, "fee_bps"), ({"fee_bps": -1}, "fee_bps"),
                         ({"fee_bps": 500, "term_months": 25}, "term_months"),
                         ({"fee_bps": 500, "term_months": 0}, "term_months")):
        error = _error(client.post(PREVIEW, json={**funding, **extra}, headers=lender), 422, "validation_error",
                       "previewLoan")
        assert error["details"]["fields"][0]["field"] == field
    error = _error(client.post(PREVIEW, json={"request_id": rid, "fee_bps": 500}, headers=lender), 422,
                   "validation_error", "previewLoan")
    assert error["details"]["fields"][0]["field"] == "amount"
    preview = client.post(PREVIEW, json={**funding, "fee_bps": 0, "term_months": 6}, headers=lender)
    assert preview.status_code == 200, preview.text
    assert_contract(preview, "previewLoan")
    assert (preview.json()["origin"], preview.json()["terms"]["term_months"]) == ("request_funded", 6)
    assert preview.json()["terms"]["monthly_minimum"] == Money(33_333_334, "COP", 2).to_api()

    # Montos inválidos (match del negocio con su solicitud y la oferta A).
    for amount, code in ((cop(2_020_000), "validation_error"), (cop(450_000), "validation_error"),
                         (Money(200_000_000, "USD", 2), "asset_mismatch"),
                         (Money(2_000_000, "COP", 0), "asset_mismatch"),
                         (Money(2**63, "COP", 2), "validation_error"), (cop(1_000_000), "no_overlap")):
        _error(client.post(PREVIEW, json={"offer_id": oid, "amount": amount.to_api()}, headers=merchant), 422, code,
               "previewLoan")
    ok = client.post(PREVIEW, json={"offer_id": oid, "amount": cop(2_500_000).to_api()}, headers=merchant)
    assert ok.status_code == 200 and ok.json()["terms"]["principal"] == cop(2_500_000).to_api()
    assert_contract(ok, "previewLoan")

    # Sin solicitud `open`: `offer_taken` exige monto y respeta el rango de la oferta.
    solo = make_merchant()
    _assessment(db, solo)
    _account(db, solo)
    _error(client.post(PREVIEW, json={"offer_id": oid}, headers=auth(solo)), 422, "validation_error", "previewLoan")
    taken = client.post(PREVIEW, json={"offer_id": oid, "amount": cop(1_000_000).to_api()}, headers=auth(solo))
    assert taken.status_code == 200 and taken.json()["origin"] == "offer_taken"
    assert_contract(taken, "previewLoan")
    assert taken.json()["request_id"] is None and taken.json()["terms"]["pct_max_bps"] == 2000  # 1500 + 500

    # Elegibilidad, wallets y assets (en el orden de R4).
    newcomer = make_merchant()
    _account(db, newcomer)  # sin ventas: no elegible
    _error(client.post(PREVIEW, json={"offer_id": oid, "amount": cop(1_000_000).to_api()}, headers=auth(newcomer)),
           422, "not_eligible", "previewLoan")
    base.lender.payout_wallet_url = None
    db.commit()
    error = _error(client.post(PREVIEW, json={"offer_id": oid}, headers=merchant), 422, "wallet_required",
                   "previewLoan")
    assert error["details"] == {"party": "lender"}
    base.lender.payout_wallet_url = "https://ilp.interledger-test.dev/camilo"
    base.merchant.payout_asset_code = "USD"
    db.commit()
    _error(client.post(PREVIEW, json={"offer_id": oid}, headers=merchant), 422, "asset_mismatch", "previewLoan")
    base.merchant.payout_asset_code = "COP"
    base.account.asset_scale = 0
    db.commit()
    error = _error(client.post(PREVIEW, json={"offer_id": oid}, headers=merchant), 422, "asset_mismatch",
                   "previewLoan")
    assert error["details"]["party"] == "collection_account"

    # Solicitud no abierta (409) antes que todo lo de elegibilidad.
    base.request.status = "cancelled"
    db.commit()
    _error(client.post(PREVIEW, json={"request_id": rid, "offer_id": oid}, headers=merchant), 409, "invalid_state",
           "previewLoan")
    assert _loan_count(db) == 0


def test_match_pct_and_lender_offer(client, db, auth, base, make_lender):
    """R2/R3: en `match` se usa `fits()`: una oferta con más % que la solicitud no encaja; el prestamista puede
    crear el `match` con su propia oferta (`pending_acceptance`)."""
    pricey = _offer(db, base.lender, cop(1_000_000), cop(2_500_000), 2000)
    body = {"request_id": str(base.request.id), "offer_id": str(pricey.id)}
    _error(client.post(PREVIEW, json=body, headers=auth(base.lender)), 422, "no_overlap", "previewLoan")
    _error(client.post(PREVIEW, json={**body, "amount": cop(2_000_000).to_api()}, headers=auth(base.lender)), 422,
           "no_overlap", "previewLoan")

    loan = _create(client, auth, base.lender, {**_match_body(base), "fee_bps": 9_000, "term_months": 12})
    assert (loan["status"], loan["origin"]) == ("pending_acceptance", "match")
    assert (loan["terms"]["fee_bps"], loan["terms"]["term_months"]) == (500, 3)  # los de la oferta
    assert _get(db, LoanOffer, base.offer.id).status == "taken"


def test_create_integrity_errors(client, db, auth, base, make_merchant, monkeypatch):
    """R6: una carrera perdida contra los índices únicos parciales → 409."""
    _create(client, auth, base.merchant, {"offer_id": str(base.offer.id)})
    offer = _get(db, LoanOffer, base.offer.id)
    offer.status = "open"  # como si otra transacción no hubiera visto el `taken`
    db.commit()
    other = make_merchant()
    _assessment(db, other)
    _account(db, other)
    body = {"offer_id": str(base.offer.id), "amount": cop(2_000_000).to_api()}
    _error(client.post(LOANS, json=body, headers=auth(other)), 409, "invalid_state", "createLoan")

    second_offer = _offer(db, base.lender, cop(1_000_000), cop(2_000_000), 1500)
    monkeypatch.setattr(service, "_active_loan_exists", lambda db_, merchant_id: False)
    body = {"offer_id": str(second_offer.id), "amount": cop(1_000_000).to_api()}
    _error(client.post(LOANS, json=body, headers=auth(base.merchant)), 409, "active_loan_exists", "createLoan")
    assert _loan_count(db) == 1
    assert _get(db, LoanOffer, second_offer.id).status == "open"  # rollback completo


def test_reject_cancel_rules(client, db, auth, base, make_merchant, make_lender):
    """R8: quién puede rechazar o cancelar y en qué estado."""
    loan_id = _create(client, auth, base.merchant, {"offer_id": str(base.offer.id)})["id"]  # pending_disbursement

    _error(client.post(f"{LOANS}/{loan_id}/reject", headers=auth(base.merchant)), 409, "invalid_state", "rejectLoan")
    _error(client.post(f"{LOANS}/{loan_id}/cancel", headers=auth(make_merchant())), 403, "forbidden", "cancelLoan")
    _error(client.post(f"{LOANS}/{loan_id}/reject", headers=auth(make_lender())), 403, "forbidden", "rejectLoan")
    _error(client.post(f"{LOANS}/{uuid.uuid4()}/cancel", headers=auth(base.lender)), 404, "not_found", "cancelLoan")
    _error(client.post(f"{LOANS}/no-es-uuid/accept", headers=auth(base.merchant)), 404, "not_found", "acceptLoan")

    body = _action(client, auth, base.lender, loan_id, "reject", "rejectLoan")
    assert body["status"] == "rejected"
    [note] = _notes(db, base.merchant, "loan_rejected")
    assert note.body == "Camilo P. rechazó el crédito de $2.000.000."
    _error(client.post(f"{LOANS}/{loan_id}/cancel", headers=auth(base.lender)), 409, "invalid_state", "cancelLoan")

    funded = _create(client, auth, base.lender, {**_match_body(base)})["id"]  # pending_acceptance
    _error(client.post(f"{LOANS}/{funded}/reject", headers=auth(base.lender)), 409, "invalid_state", "rejectLoan")
    assert _action(client, auth, base.merchant, funded, "cancel", "cancelLoan")["status"] == "cancelled"
    [note] = _notes(db, base.lender, "loan_cancelled")
    assert note.body.startswith("Tienda La Esquina canceló el crédito de $2.000.000.")

    active = _active_loan(db, make_merchant(), base.lender)
    _error(client.post(f"{LOANS}/{active.id}/cancel", headers=auth(base.lender)), 409, "invalid_state", "cancelLoan")


def test_disburse_rules(client, db, auth, base, fake_op, freeze, make_lender, make_merchant):
    """R9/R10: dueño, estados, vencimiento cercano, 4xx de Go, conflicto de idempotencia y Go ocupado."""
    funded = _create(client, auth, base.lender, {**_match_body(base)})["id"]  # pending_acceptance
    _error(_disburse(client, auth, base.lender, funded), 409, "invalid_state", "disburseLoan")
    _error(_disburse(client, auth, make_lender(), funded), 403, "forbidden", "disburseLoan")
    response = client.post(f"{LOANS}/{funded}/disburse", json={"client": "web"}, headers=auth(base.merchant))
    _error(response, 403, "forbidden", "disburseLoan")
    response = client.post(f"{LOANS}/{funded}/disburse", json={"client": "tv"}, headers=auth(base.lender))
    _error(response, 422, "validation_error", "disburseLoan")
    _error(_disburse(client, auth, base.lender, str(uuid.uuid4())), 404, "not_found", "disburseLoan")
    _action(client, auth, base.merchant, funded, "accept", "acceptLoan")

    # A menos de 1 min de vencer: lo resuelve el worker.
    freeze(NOW + 48 * H - timedelta(seconds=30))
    _error(_disburse(client, auth, base.lender, funded), 409, "invalid_state", "disburseLoan")
    freeze(NOW)

    # 4xx definitivo de Go → intento + 1 y 422 con op_code.
    fake_op.fail_next("create_payment", OpError(422, "return_url_not_allowed", "no permitido"))
    error = _error(_disburse(client, auth, base.lender, funded, "mobile"), 422, "disbursement_rejected",
                   "disburseLoan")
    assert error["details"] == {"op_code": "return_url_not_allowed"}
    assert _get(db, Loan, funded).disbursement_attempt == 2
    assert _keys(fake_op) == [f"loan:{funded}:disb:1"]

    # Go ocupado con la misma clave (409 request_in_progress, reintentable) o 401 → 503, mismo intento.
    fake_op.fail_next("create_payment", OpError(409, "request_in_progress", "en curso", retryable=True))
    _error(_disburse(client, auth, base.lender, funded, "mobile"), 503, "op_unavailable", "disburseLoan")
    fake_op.fail_next("create_payment", OpError(401, "unauthorized", "API key"))
    _error(_disburse(client, auth, base.lender, funded, "mobile"), 503, "op_unavailable", "disburseLoan")
    assert _get(db, Loan, funded).disbursement_attempt == 2

    response = _disburse(client, auth, base.lender, funded, "mobile")
    assert response.status_code == 200, response.text
    assert_contract(response, "disburseLoan")
    call = fake_op.calls_to("create_payment")[-1]
    assert call.idempotency_key == f"loan:{funded}:disb:2"
    assert call.body.return_url == WEB_RETURN.format(id=funded, client="mobile")

    # Autorización vencida en `disbursing` → 409 (lo resuelve el worker), sin llamar a Go.
    freeze(NOW + timedelta(minutes=11))
    calls = len(fake_op.calls_to("create_payment"))
    _error(_disburse(client, auth, base.lender, funded), 409, "invalid_state", "disburseLoan")
    assert len(fake_op.calls_to("create_payment")) == calls

    # Mismo intento con otro `client` (conflicto de idempotencia en Go) → 409 sin tocar el intento.
    other = _base(db, make_merchant, make_lender)
    loan_id = _create(client, auth, other.merchant, {"offer_id": str(other.offer.id)})["id"]
    fake_op.fail_next("create_payment", OpError(409, "idempotency_conflict", "otro cuerpo"))
    error = _error(_disburse(client, auth, other.lender, loan_id), 409, "invalid_state", "disburseLoan")
    assert error["details"] == {"op_code": "idempotency_conflict"}
    loan = _get(db, Loan, loan_id)
    assert (loan.status, loan.disbursement_attempt) == ("pending_disbursement", 1)


def test_on_disbursement_update_other_statuses(client, db, auth, base, fake_op):
    """R11: `processing` limpia la autorización (sigue `disbursing`); otro pago, otro estado o un crédito que no
    está `disbursing` no cambian nada."""
    loan_id, payment_id = _disbursing(client, auth, base, fake_op)

    service.on_disbursement_update(db, _locked(db, loan_id), fake_op.get_payment(payment_id))  # awaiting
    db.commit()
    assert _get(db, Loan, loan_id).disbursement_authorization_url is not None

    stranger = fake_op.payments[0].model_copy(update={"id": uuid.uuid4(), "status": "completed"})
    service.on_disbursement_update(db, _locked(db, loan_id), stranger)
    db.commit()
    assert _get(db, Loan, loan_id).status == "disbursing"

    fake_op.approve_payment(payment_id)
    service.on_disbursement_update(db, _locked(db, loan_id), fake_op.get_payment(payment_id))
    db.commit()
    loan = _get(db, Loan, loan_id)
    assert (loan.status, loan.disbursement_authorization_url, loan.disbursement_authorization_expires_at) == (
        "disbursing", None, None
    )
    assert _detail(client, auth, base.lender, loan_id)["next_action"] == {"type": "none", "actor": None}
    assert "disbursement_authorized" in _events(db, loan_id)
    _error(_disburse(client, auth, base.lender, loan_id), 409, "invalid_state", "disburseLoan")

    fake_op.complete_payment(payment_id)
    completed = fake_op.get_payment(payment_id)
    service.on_disbursement_update(db, _locked(db, loan_id), completed)
    db.commit()
    service.on_disbursement_update(db, _locked(db, loan_id), completed)  # ya `active`: nada
    db.commit()
    assert len(_notes(db, base.lender, "loan_active")) == 1


def test_expire_pending_only_due(client, db, auth, base, make_merchant, make_lender):
    """R12: solo `pending_*` con `expires_at ≤ now`; un `pending_acceptance` también vence."""
    funded = _create(client, auth, base.lender, {**_match_body(base)})["id"]
    other = _base(db, make_merchant, make_lender)
    later = _create(client, auth, other.merchant, {"offer_id": str(other.offer.id)})
    _get(db, Loan, later["id"]).expires_at = NOW + 72 * H
    db.commit()
    _active_loan(db, make_merchant(), base.lender, expires_at=NOW - H)

    assert service.expire_pending(db, NOW + 48 * H) == 1
    db.commit()

    assert _get(db, Loan, funded).status == "expired"
    assert _get(db, Loan, later["id"]).status == "pending_disbursement"
    assert (_get(db, LoanRequest, base.request.id).status, _get(db, LoanOffer, base.offer.id).status) == (
        "open", "open"
    )
    assert service.expire_pending(db, NOW + 48 * H) == 0


def test_disbursement_flow_with_fake_wallet_and_worker(client, db, auth, base, fake_op, settings, monkeypatch):
    """R9–R11 de punta a punta: desembolso → wallet falsa (03) aprueba → el worker (08 J3) sigue el pago."""
    monkeypatch.setattr(settings, "dev_endpoints_enabled", True)
    loan_id, payment_id = _disbursing(client, auth, base, fake_op)

    redirect = client.get(f"/api/v1/dev/fake-wallet/authorize/{payment_id}?decision=approve", follow_redirects=False)
    assert redirect.status_code == 302
    assert_contract(redirect, "devFakeWalletAuthorize")
    assert redirect.headers["location"].startswith(WEB_RETURN.format(id=loan_id, client="web"))

    run_once(db, fake_op, NOW)  # pago `processing`: ya autorizó, sigue `disbursing`
    detail = _detail(client, auth, base.lender, loan_id)
    assert (detail["status"], detail["next_action"]["type"], detail["disbursement"]["authorization_url"]) == (
        "disbursing", "none", None
    )

    fake_op.complete_payment(payment_id)
    run_once(db, fake_op, NOW)
    detail = _detail(client, auth, base.merchant, loan_id)
    assert (detail["status"], detail["disbursement"]["status"]) == ("active", "completed")
    assert detail["period"]["target"] == cop(700_000).to_api()
