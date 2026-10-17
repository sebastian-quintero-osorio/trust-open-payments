"""09-scoring: un test por criterio de aceptación (más reglas sin AC propio)."""

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.money import Money
from app.modules.loans.models import Loan, LoanPctChange
from app.modules.marketplace.models import LoanRequest
from app.modules.recaudo.models import CollectionAccount
from app.modules.sales.models import Sale
from app.modules.scoring.features import build_features
from app.modules.scoring.models import CreditAssessment
from app.modules.scoring.service import SALES_BANDS, get_credit_profile, sales_band
from app.modules.users.models import User
from tests.helpers import assert_contract, cop
from tests.test_sales import _account

BOGOTA = ZoneInfo("America/Bogota")
NOW = datetime(2026, 10, 16, 10, 0, tzinfo=BOGOTA).astimezone(UTC)  # 16 oct 2026 10:00 en Bogotá
ME = "/api/v1/me/credit-profile"
CUPO_6M = cop(6_000_000)
PRINCIPAL_2M = cop(2_000_000)

_refs = iter(range(1, 1_000_000))


@pytest.fixture(autouse=True)
def _env(freeze):
    freeze(NOW)


# ── Ayudas ────────────────────────────────────────────────────────────────────


def _local(year: int, month: int, day: int, hour: int = 10, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=BOGOTA).astimezone(UTC)


def _sale(db: Session, account: CollectionAccount, amount: Money, paid_at: datetime, source: str = "seed", **fields):
    sale = Sale(
        merchant_id=account.merchant_id,
        collection_account_id=account.id,
        bre_b_reference=f"SEED-test-{next(_refs)}",
        amount=amount.value,
        asset_code=amount.asset_code,
        asset_scale=amount.asset_scale,
        paid_at=paid_at,
        source=source,
        status="processed",
        **fields,
    )
    db.add(sale)
    return sale


def _month_sales(db: Session, account: CollectionAccount, year: int, month: int, count: int, total: Money) -> list:
    """`count` ventas los días 2, 3, … del mes (10:00 local) que suman exactamente `total`."""
    each = total.value // count
    amounts = [each] * (count - 1) + [total.value - each * (count - 1)]
    return [
        _sale(db, account, Money(value, total.asset_code, total.asset_scale), _local(year, month, 2 + i))
        for i, value in enumerate(amounts)
    ]


def _ac1_history(db: Session, merchant: User) -> CollectionAccount:
    """AC-1: 12 ventas por mes en julio, agosto y septiembre ($15M, $16M y $17M) y 5 ventas en junio."""
    account = _account(db, merchant)
    _month_sales(db, account, 2026, 6, 5, cop(2_000_000))
    _month_sales(db, account, 2026, 7, 12, cop(15_000_000))
    _month_sales(db, account, 2026, 8, 12, cop(16_000_000))
    _month_sales(db, account, 2026, 9, 12, cop(17_000_000))
    db.commit()
    return account


def _assessment(db: Session, merchant: User, cupo: Money = CUPO_6M, **fields) -> CreditAssessment:
    """Assessment de hoy (v1) escrito a mano; sin ventas nuevas, `get_credit_profile` lo reutiliza."""
    values = {
        "policy_version": "v1",
        "score": 845,
        "grade": "A",
        "eligible": True,
        "unlocks_at": None,
        "features": {"active_months": 6, "avg_monthly_sales_3m": 1_600_000_000},
        "reasons": ["Buen volumen de ventas en los últimos 3 meses."],
        "computed_at": clock.now(),
        **fields,
    }
    assessment = CreditAssessment(
        merchant_id=merchant.id, cupo_total=cupo.value, asset_code=cupo.asset_code, asset_scale=cupo.asset_scale,
        **values,
    )
    db.add(assessment)
    db.commit()
    return assessment


def _loan(db: Session, merchant: User, lender: User, status: str, principal: Money = PRINCIPAL_2M, **fields) -> Loan:
    account = db.scalar(select(CollectionAccount).where(CollectionAccount.merchant_id == merchant.id)) or _account(
        db, merchant
    )
    assessment_id = fields.pop("credit_assessment_id", None) or db.scalar(
        select(CreditAssessment.id).where(CreditAssessment.merchant_id == merchant.id).limit(1)
    )
    loan = Loan(
        merchant_id=merchant.id,
        lender_id=lender.id,
        origin="offer_taken",
        created_by=merchant.id,
        credit_assessment_id=assessment_id,
        collection_account_id=account.id,
        merchant_payout_wallet_url=merchant.payout_wallet_url or "https://ilp.interledger-test.dev/m",
        lender_wallet_url=lender.payout_wallet_url or "https://ilp.interledger-test.dev/l",
        lender_asset_code="COP",
        lender_asset_scale=2,
        asset_code="COP",
        asset_scale=2,
        principal=principal.value,
        fee_bps=0,
        fee_amount=0,
        total_repayable=principal.value,
        trust_share_bps=2000,
        term_months=3,
        monthly_minimum=-(-principal.value // 3),
        pct_initial_bps=1500,
        pct_min_bps=1000,
        pct_max_bps=2000,
        terms={},
        status=status,
        pct_bps=1500,
        **fields,
    )
    db.add(loan)
    db.commit()
    return loan


def _count(db: Session, merchant: User) -> int:
    query = select(func.count()).select_from(CreditAssessment).where(CreditAssessment.merchant_id == merchant.id)
    return db.scalar(query)


# ── Criterios de aceptación ───────────────────────────────────────────────────


def test_ac1_features_from_sales(db, make_merchant):
    merchant = make_merchant()
    _ac1_history(db, merchant)

    features = build_features(db, merchant.id, clock.now())

    assert features.as_of == NOW
    assert features.active_months == 3  # junio tiene 5 ventas (< MIN_SALES_PER_MONTH)
    assert features.avg_monthly_sales_3m == cop(16_000_000).value
    assert features.first_sale_at == _local(2026, 6, 2)
    assert features.months_on_platform == 4  # del 2 jun al 16 oct
    assert (features.loans_paid, features.pct_increases) == (0, 0)


def test_ac2_assessment_cached_for_local_day(db, make_merchant):
    merchant = make_merchant()
    _ac1_history(db, merchant)

    first = get_credit_profile(db, merchant.id)
    clock.freeze(NOW + timedelta(hours=8))  # 18:00 en Bogotá, el mismo día local
    second = get_credit_profile(db, merchant.id)

    assert first.assessment.id == second.assessment.id
    rows = db.scalars(select(CreditAssessment).where(CreditAssessment.merchant_id == merchant.id)).all()
    assert len(rows) == 1
    assert rows[0].policy_version == "v1"
    assert rows[0].features["active_months"] == 3
    assert rows[0].computed_at == NOW
    assert (rows[0].asset_code, rows[0].asset_scale) == ("COP", 2)


def test_ac3_cache_invalidated_by_sale_or_day(db, make_merchant, settings, monkeypatch):
    merchant = make_merchant()
    account = _ac1_history(db, merchant)
    first = get_credit_profile(db, merchant.id).assessment

    # Una venta nueva (created_at > computed_at) invalida la caché.
    clock.freeze(NOW + timedelta(minutes=5))
    _sale(db, account, cop(48_000), clock.now(), source="live", puntored_event_id="evt-ac3")
    db.commit()
    second = get_credit_profile(db, merchant.id).assessment
    assert second.id != first.id
    assert second.computed_at == NOW + timedelta(minutes=5)
    assert _count(db, merchant) == 2
    assert get_credit_profile(db, merchant.id).assessment.id == second.id  # sin cambios: se reutiliza

    # El reloj pasa al 17 oct local (00:30 en Bogotá): otra fila.
    clock.freeze(_local(2026, 10, 17, 0, 30))
    third = get_credit_profile(db, merchant.id).assessment
    assert third.id not in (first.id, second.id)
    assert _count(db, merchant) == 3

    # Otra versión de política tampoco reutiliza (y v2 no existe: assess falla).
    monkeypatch.setattr(settings, "scoring_policy", "v2")
    with pytest.raises(ValueError):
        get_credit_profile(db, merchant.id)


def test_ac4_cupo_disponible(db, make_merchant, make_lender):
    merchant, lender = make_merchant(), make_lender()
    assessment = _assessment(db, merchant, cupo=cop(6_000_000))
    loan = _loan(db, merchant, lender, "active", cop(2_000_000), credit_assessment_id=assessment.id)

    profile = get_credit_profile(db, merchant.id)
    assert profile.assessment.id == assessment.id
    assert profile.cupo_total == cop(6_000_000)
    assert profile.cupo_usado == cop(2_000_000)
    assert profile.cupo_disponible == cop(4_000_000)

    for status in ("pending_acceptance", "pending_disbursement", "disbursing"):
        loan.status = status
        db.commit()
        profile = get_credit_profile(db, merchant.id)
        assert (profile.cupo_usado, profile.cupo_disponible) == (cop(2_000_000), cop(4_000_000)), status

    loan.status = "paid"
    db.commit()
    profile = get_credit_profile(db, merchant.id)
    assert (profile.cupo_usado, profile.cupo_disponible) == (cop(0), cop(6_000_000))

    # cupo_disponible nunca es negativo.
    small = make_merchant()
    _assessment(db, small, cupo=cop(1_000_000))
    _loan(db, small, lender, "active", cop(2_000_000))
    assert get_credit_profile(db, small.id).cupo_disponible == cop(0)


def test_ac5_me_profile_not_eligible(client, db, make_merchant, auth):
    merchant = make_merchant()
    account = _account(db, merchant)
    _month_sales(db, account, 2026, 9, 12, cop(12_000_000))  # un solo mes activo
    db.commit()

    response = client.get(ME, headers=auth(merchant))

    assert response.status_code == 200, response.text
    assert_contract(response, "getMyCreditProfile")
    body = response.json()
    assert body["eligible"] is False
    assert body["active_months"] == 1
    assert body["cupo_total"] == cop(0).to_api()
    assert body["cupo_usado"] == cop(0).to_api()
    assert body["cupo_disponible"] == cop(0).to_api()
    assert body["unlocks_at"] == "2026-12-01T05:00:00Z"
    assert body["reasons"] and body["reasons"][0] == "Te faltan 2 meses de ventas con el QR de Trust."
    assert body["policy_version"] == "v1"
    assert body["computed_at"] == "2026-10-16T15:00:00Z"
    assert body["assessment_id"] == str(db.scalar(select(CreditAssessment.id)))


def test_ac6_lender_view_requires_relation(client, db, make_merchant, make_lender, auth):
    merchant, lender = make_merchant(), make_lender()
    _ac1_history(db, merchant)
    url = f"/api/v1/merchants/{merchant.id}/credit-profile"

    response = client.get(url, headers=auth(lender))
    assert response.status_code == 403, response.text
    assert_contract(response, "getMerchantCreditProfile")
    assert response.json()["error"]["code"] == "forbidden"

    created = client.post(
        "/api/v1/loan-requests",
        json={"min_amount": cop(1_500_000).to_api(), "max_amount": cop(3_000_000).to_api(), "pct_bps": 1500},
        headers=auth(merchant),
    )
    assert created.status_code == 201, created.text

    response = client.get(url, headers=auth(lender))
    assert response.status_code == 200, response.text
    assert_contract(response, "getMerchantCreditProfile")
    body = response.json()
    assert body["sales_band"] == {"min": cop(10_000_000).to_api(), "max": cop(25_000_000).to_api()}
    assert body["merchant"] == {
        "id": str(merchant.id), "business_name": "Tienda La Esquina", "city": "Bucaramanga", "initials": "TL"
    }
    assert body["active_months"] == 3 and body["eligible"] is True
    assert "features" not in body and "avg_monthly_sales_3m" not in response.text
    assert str(cop(16_000_000).value) not in response.text  # nunca el promedio exacto
    assert body["cupo_total"] == body["cupo_disponible"]

    # Sin solicitud abierta, basta un crédito (en cualquier estado) con este prestamista; otro prestamista no.
    db.execute(LoanRequest.__table__.update().values(status="cancelled"))
    db.commit()
    other = make_lender()
    assert client.get(url, headers=auth(lender)).status_code == 403
    _loan(db, merchant, other, "cancelled")
    response = client.get(url, headers=auth(other))
    assert response.status_code == 200, response.text
    assert_contract(response, "getMerchantCreditProfile")
    assert client.get(url, headers=auth(lender)).status_code == 403


def test_ac7_lender_view_roles_and_404(client, make_merchant, make_lender, make_admin, auth):
    merchant, lender = make_merchant(), make_lender()

    response = client.get(f"/api/v1/merchants/{merchant.id}/credit-profile", headers=auth(make_merchant()))
    assert response.status_code == 403, response.text
    assert_contract(response, "getMerchantCreditProfile")
    assert client.get(f"/api/v1/merchants/{merchant.id}/credit-profile", headers=auth(make_admin())).status_code == 403

    for missing in (uuid.uuid4(), "no-es-un-uuid", lender.id):  # inexistente, id inválido, no es comercio
        response = client.get(f"/api/v1/merchants/{missing}/credit-profile", headers=auth(lender))
        assert response.status_code == 404, response.text
        assert_contract(response, "getMerchantCreditProfile")
        assert response.json()["error"]["code"] == "not_found"


# ── Reglas sin AC propio ──────────────────────────────────────────────────────


def test_features_weeks_days_timezone_and_loans(db, make_merchant, make_lender):
    """R1/05 R13: semanas lun–dom y días en hora local, ventana de 90 días, meses completos y créditos."""
    merchant, lender = make_merchant(), make_lender()
    account = _account(db, merchant)
    mondays = [_local(2026, 7, 20) + timedelta(weeks=i) for i in range(9)]  # 20 jul … 14 sep
    for paid_at in mondays:
        _sale(db, account, cop(1_000_000), paid_at)
    # Domingo 11 oct 23:30 en Bogotá = lunes 12 oct 04:30 UTC: cuenta en la semana cerrada del 5 oct.
    _sale(db, account, cop(1_000_000), _local(2026, 10, 11, 23, 30))
    _sale(db, account, cop(5_000_000), _local(2026, 10, 16, 9, 0), source="live", puntored_event_id="evt-hoy")
    _sale(db, account, cop(9_000_000), _local(2026, 10, 16, 11, 0), source="live", puntored_event_id="evt-futuro")
    db.commit()
    paid = _loan(db, merchant, lender, "paid", credit_assessment_id=_assessment(db, merchant).id)
    _loan(db, merchant, lender, "active")
    for reason in ("missed_minimum", "unchanged", "missed_minimum"):
        db.add(LoanPctChange(loan_id=paid.id, old_pct_bps=1500, new_pct_bps=1750, reason=reason, period_start=NOW,
                             period_end=NOW, allocated_in_period=0, target_amount=0))
    other = make_merchant()
    other_loan = _loan(db, other, lender, "paid", credit_assessment_id=_assessment(db, other).id)
    db.add(LoanPctChange(loan_id=other_loan.id, old_pct_bps=1500, new_pct_bps=1750, reason="missed_minimum",
                         period_start=NOW, period_end=NOW, allocated_in_period=0, target_amount=0))
    db.commit()

    features = build_features(db, merchant.id, clock.now())

    assert features.first_sale_at == _local(2026, 7, 20)
    assert features.consistency_bps == 10 * 10_000 // 12  # 10 de 12 semanas cerradas (sin el 21 y el 28 sep)
    assert features.active_days_bps == 10 * 10_000 // 90  # hoy no cuenta
    assert features.months_on_platform == 2  # del 20 jul al 16 oct
    assert features.active_months == 0  # ningún mes con 10 ventas
    assert features.avg_monthly_sales_3m == cop(9_000_000).value // 3  # jul 2 + ago 5 + sep 2 millones
    assert (features.loans_paid, features.pct_increases) == (1, 2)

    empty = build_features(db, make_merchant().id, clock.now())
    assert (empty.first_sale_at, empty.consistency_bps, empty.active_days_bps, empty.months_on_platform) == (
        None, 0, 0, 0
    )


def test_me_profile_without_sales(client, make_merchant, make_lender, auth):
    """R4: sin ventas, 200 no elegible con cupo 0, `unlocks_at` null y la razón de empezar a cobrar."""
    merchant = make_merchant()
    response = client.get(ME, headers=auth(merchant))
    assert response.status_code == 200, response.text
    assert_contract(response, "getMyCreditProfile")
    body = response.json()
    assert (body["eligible"], body["unlocks_at"], body["active_months"]) == (False, None, 0)
    assert body["reasons"][0] == "Empieza a cobrar con tu QR de Trust para construir historial."
    assert body["cupo_disponible"] == cop(0).to_api()

    response = client.get(ME, headers=auth(make_lender()))
    assert response.status_code == 403
    assert_contract(response, "getMyCreditProfile")


def test_sales_band_edges():
    """R6: mayor corte ≤ promedio y el siguiente; `max` null en el último tramo."""
    assert sales_band(0).model_dump() == {"min": cop(0).to_api(), "max": cop(2_500_000).to_api()}
    assert sales_band(cop(2_500_000).value).model_dump()["min"] == cop(2_500_000).to_api()
    assert sales_band(cop(2_500_000).value - 1).model_dump()["max"] == cop(2_500_000).to_api()
    assert sales_band(SALES_BANDS[-1]).model_dump() == {"min": cop(200_000_000).to_api(), "max": None}
    assert sales_band(SALES_BANDS[-1] * 3).model_dump()["max"] is None


def test_seed_marta_profile_grade_a(client, db, auth, settings, monkeypatch):
    """03 R7 calibra el seed con la política v1: Tienda La Esquina sale elegible, grado A y cupo $6.000.000."""
    monkeypatch.setattr(settings, "dev_endpoints_enabled", True)
    seeded = client.post("/api/v1/dev/seed")
    assert seeded.status_code == 200, seeded.text
    marta = db.scalar(select(User).where(User.email == "marta@demo.trust"))

    response = client.get(ME, headers=auth(marta))

    assert response.status_code == 200, response.text
    assert_contract(response, "getMyCreditProfile")
    body = response.json()
    assert (body["eligible"], body["grade"]) == (True, "A")
    assert body["cupo_total"] == CUPO_6M.to_api() and body["cupo_disponible"] == CUPO_6M.to_api()
    assert body["active_months"] >= settings.min_months
