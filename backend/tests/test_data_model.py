"""01-data-model: un test por criterio de aceptación."""

import uuid

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.core import clock
from app.core.db import engine
from app.models import Base
from app.modules.loans.models import NON_TERMINAL_LOAN_STATUSES, TERMINAL_LOAN_STATUSES, Loan, LoanStatus
from app.modules.marketplace.models import LoanOffer, LoanRequest
from app.modules.recaudo.models import CollectionAccount
from app.modules.sales.models import Payout, Sale
from app.modules.scoring.models import CreditAssessment
from app.modules.users.models import User

TABLES = {
    "users", "collection_accounts", "credit_assessments", "loan_requests", "loan_offers", "loans",
    "loan_pct_changes", "sales", "repayments", "payouts", "webhook_events", "op_cursors", "notifications",
    "waitlist", "events",
}
COP = {"asset_code": "COP", "asset_scale": 2}


# ── Filas mínimas válidas ─────────────────────────────────────────────────────


def _collection_account(db, merchant: User) -> CollectionAccount:
    account = CollectionAccount(
        merchant_id=merchant.id,
        op_collection_account_id=uuid.uuid4(),
        wallet_address="https://ilp.interledger-test.dev/trust-recaudo-1",
        qr_payload="00020101021226...",
        status="active",
        **COP,
    )
    db.add(account)
    db.flush()
    return account


def _assessment(db, merchant: User) -> CreditAssessment:
    assessment = CreditAssessment(
        merchant_id=merchant.id,
        policy_version="v1",
        score=820,
        grade="A",
        eligible=True,
        cupo_total=600_000_000,
        features={},
        reasons=[],
        computed_at=clock.now(),
        **COP,
    )
    db.add(assessment)
    db.flush()
    return assessment


def _loan(db, merchant: User, lender: User, status: str, **fields) -> Loan:
    loan = Loan(
        merchant_id=merchant.id,
        lender_id=lender.id,
        origin="offer_taken",
        created_by=merchant.id,
        credit_assessment_id=_assessment(db, merchant).id,
        collection_account_id=fields.pop("collection_account_id", None) or _collection_account(db, merchant).id,
        merchant_payout_wallet_url=merchant.payout_wallet_url,
        lender_wallet_url=lender.payout_wallet_url,
        lender_asset_code="COP",
        lender_asset_scale=2,
        principal=200_000_000,
        fee_bps=600,
        fee_amount=12_000_000,
        total_repayable=212_000_000,
        trust_share_bps=2000,
        term_months=3,
        monthly_minimum=70_666_667,
        pct_initial_bps=1500,
        pct_min_bps=1000,
        pct_max_bps=2000,
        terms={},
        status=status,
        pct_bps=1500,
        **COP,
        **fields,
    )
    db.add(loan)
    db.flush()
    return loan


def _offer(db, lender: User) -> LoanOffer:
    offer = LoanOffer(
        lender_id=lender.id,
        min_amount=100_000_000,
        max_amount=300_000_000,
        pct_bps=1500,
        fee_bps=600,
        term_months=3,
        status="taken",
        **COP,
    )
    db.add(offer)
    db.flush()
    return offer


def _request(db, merchant: User, status: str) -> LoanRequest:
    request = LoanRequest(
        merchant_id=merchant.id,
        min_amount=150_000_000,
        max_amount=300_000_000,
        pct_bps=1500,
        status=status,
        credit_assessment_id=_assessment(db, merchant).id,
        **COP,
    )
    db.add(request)
    db.flush()
    return request


# ── AC ────────────────────────────────────────────────────────────────────────


def test_ac1_schema_creates(db, make_merchant):
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) == TABLES == set(Base.metadata.tables)

    def partial_indexes(table: str) -> dict[str, str]:
        return {
            ix["name"]: ix["dialect_options"]["postgresql_where"]
            for ix in inspector.get_indexes(table)
            if ix["unique"] and ix.get("dialect_options", {}).get("postgresql_where")
        }

    loan_preds = partial_indexes("loans")
    assert set(loan_preds) == {"uq_loans_open_merchant", "uq_loans_offer", "uq_loans_request"}
    # Postgres devuelve el predicado normalizado: `(status = ANY (ARRAY['pending_acceptance'::text, ...]))`.
    open_merchant = loan_preds["uq_loans_open_merchant"]
    assert all(f"'{status}'" in open_merchant for status in NON_TERMINAL_LOAN_STATUSES), open_merchant
    assert not any(f"'{status}'" in open_merchant for status in TERMINAL_LOAN_STATUSES), open_merchant
    for column, index in (("offer_id", "uq_loans_offer"), ("request_id", "uq_loans_request")):
        pred = loan_preds[index]
        assert f"{column} IS NOT NULL" in pred and "NOT (status" in pred, pred
        assert all(f"'{status}'" in pred for status in ("cancelled", "rejected", "expired")), pred
        assert not any(f"'{status}'" in pred for status in (*NON_TERMINAL_LOAN_STATUSES, "paid")), pred
    request_preds = partial_indexes("loan_requests")
    assert set(request_preds) == {"uq_loan_requests_open_merchant"}
    assert "'open'" in request_preds["uq_loan_requests_open_merchant"]
    account_preds = partial_indexes("collection_accounts")
    assert set(account_preds) == {"uq_collection_accounts_active_merchant"}
    assert "'active'" in account_preds["uq_collection_accounts_active_merchant"]

    def checks(table: str) -> set[str]:
        return {ck["name"] for ck in inspector.get_check_constraints(table)}

    assert {"ck_loans_status_enum", "ck_loans_origin_enum", "ck_loans_allocated_within_total"} <= checks("loans")
    assert {"ck_loan_requests_amount_range", "ck_loan_requests_status_enum"} <= checks("loan_requests")
    assert {"ck_loan_offers_amount_range", "ck_loan_offers_fee_bps_non_negative",
            "ck_loan_offers_term_months_positive"} <= checks("loan_offers")
    assert {"ck_payouts_leg_enum", "ck_payouts_status_enum"} <= checks("payouts")
    enum_tables = ["users", "collection_accounts", "credit_assessments", "sales", "webhook_events", "waitlist",
                   "loan_pct_changes"]
    for table in enum_tables:
        assert any(name.endswith("_enum") for name in checks(table)), table

    def uniques(table: str) -> list[list[str]]:
        return [uq["column_names"] for uq in inspector.get_unique_constraints(table)]

    assert ["sale_id", "leg"] in uniques("payouts")
    assert ["collection_account_id", "bre_b_reference"] in uniques("sales")
    assert ["sale_id"] in uniques("repayments")
    assert ["event_id"] in uniques("webhook_events")
    assert ["correo"] in uniques("waitlist")

    indexed = {table: [ix["column_names"] for ix in inspector.get_indexes(table)] for table in TABLES}
    assert ["merchant_id", "computed_at"] in indexed["credit_assessments"]
    assert ["merchant_id", "paid_at"] in indexed["sales"]
    assert ["loan_id", "created_at"] in indexed["repayments"]
    assert ["lender_id", "created_at"] in indexed["repayments"]
    assert ["status", "next_attempt_at"] in indexed["payouts"]
    assert ["user_id", "created_at"] in indexed["notifications"]
    assert ["entity_type", "entity_id", "created_at"] in indexed["events"]

    # Los CHECK de verdad rechazan valores fuera del enum o montos inválidos.
    merchant = make_merchant()
    with pytest.raises(IntegrityError, match="ck_loan_requests_status_enum"):
        _request(db, merchant, status="pending")
    db.rollback()
    with pytest.raises(IntegrityError, match="ck_loan_requests_amount_range"):
        db.add(LoanRequest(merchant_id=merchant.id, min_amount=0, max_amount=10, pct_bps=1500, status="open",
                           credit_assessment_id=_assessment(db, merchant).id, **COP))
        db.flush()
    db.rollback()
    with pytest.raises(IntegrityError, match="ck_users_role_enum"):
        db.add(User(email="x@test.trust", password_hash="x", role="root", first_name="X", last_name="Y", city="Z"))
        db.flush()
    db.rollback()


def test_ac2_one_open_loan_per_merchant(db, make_merchant, make_lender):
    merchant, lender = make_merchant(), make_lender()
    first = _loan(db, merchant, lender, LoanStatus.PENDING_ACCEPTANCE)
    db.commit()

    for status in (LoanStatus.PENDING_DISBURSEMENT, LoanStatus.DISBURSING, LoanStatus.ACTIVE):
        with pytest.raises(IntegrityError, match="uq_loans_open_merchant"):
            _loan(db, merchant, lender, status, collection_account_id=first.collection_account_id)
        db.rollback()

    # Terminados no cuentan: con el primero cancelado se puede crear otro, y varios terminados conviven.
    first.status = LoanStatus.CANCELLED
    db.commit()
    _loan(db, merchant, lender, LoanStatus.PAID, collection_account_id=first.collection_account_id)
    _loan(db, merchant, lender, LoanStatus.ACTIVE, collection_account_id=first.collection_account_id)
    db.commit()
    assert db.query(Loan).filter_by(merchant_id=merchant.id).count() == 3


def test_ac2_one_live_loan_per_offer_and_request(db, make_merchant, make_lender):
    """`uq_loans_offer` y `uq_loans_request`: un crédito por oferta o solicitud mientras no se libere."""
    merchant, other, lender = make_merchant(), make_merchant(), make_lender()
    offer = _offer(db, lender)
    first = _loan(db, merchant, lender, LoanStatus.PAID, offer_id=offer.id)
    db.commit()

    # Un crédito `paid` sigue ocupando la oferta.
    with pytest.raises(IntegrityError, match="uq_loans_offer"):
        _loan(db, other, lender, LoanStatus.PENDING_ACCEPTANCE, offer_id=offer.id)
    db.rollback()

    # Cancelado, la oferta se libera.
    first.status = LoanStatus.CANCELLED
    db.commit()
    _loan(db, other, lender, LoanStatus.PENDING_ACCEPTANCE, offer_id=offer.id)
    db.commit()

    request = _request(db, merchant, "matched")
    first = _loan(db, merchant, lender, LoanStatus.PAID, request_id=request.id,
                  collection_account_id=first.collection_account_id)
    db.commit()
    with pytest.raises(IntegrityError, match="uq_loans_request"):
        _loan(db, merchant, lender, LoanStatus.PENDING_ACCEPTANCE, request_id=request.id,
              collection_account_id=first.collection_account_id)
    db.rollback()

    first.status = LoanStatus.EXPIRED
    db.commit()
    _loan(db, merchant, lender, LoanStatus.PENDING_ACCEPTANCE, request_id=request.id,
          collection_account_id=first.collection_account_id)
    db.commit()
    assert db.query(Loan).filter_by(offer_id=offer.id).count() == 2
    assert db.query(Loan).filter_by(request_id=request.id).count() == 2


def test_ac3_one_open_request(db, make_merchant):
    merchant = make_merchant()
    _request(db, merchant, "open")
    db.commit()

    with pytest.raises(IntegrityError, match="uq_loan_requests_open_merchant"):
        _request(db, merchant, "open")
    db.rollback()

    other = make_merchant()
    _request(db, other, "cancelled")
    _request(db, other, "cancelled")
    _request(db, other, "open")
    db.commit()
    assert db.query(LoanRequest).filter_by(merchant_id=other.id).count() == 3


def test_ac4_payout_unique_leg(db, make_merchant):
    merchant = make_merchant()
    account = _collection_account(db, merchant)
    sale = Sale(
        merchant_id=merchant.id,
        collection_account_id=account.id,
        puntored_event_id="evt_1",
        bre_b_reference="BRB-1",
        amount=4_800_000,
        paid_at=clock.now(),
        source="live",
        status="processed",
        **COP,
    )
    db.add(sale)
    db.flush()

    def payout(leg: str) -> Payout:
        return Payout(
            sale_id=sale.id,
            leg=leg,
            receiver_wallet_url=merchant.payout_wallet_url,
            amount=4_080_000,
            current_debit_amount=4_080_000,
            description="Venta con Bre-B",
            status="pending",
            **COP,
        )

    db.add(payout("merchant"))
    db.add(payout("trust"))
    db.commit()

    db.add(payout("merchant"))
    with pytest.raises(IntegrityError, match="uq_payouts_sale_id_leg"):
        db.commit()
    db.rollback()
    assert db.query(Payout).filter_by(sale_id=sale.id).count() == 2
