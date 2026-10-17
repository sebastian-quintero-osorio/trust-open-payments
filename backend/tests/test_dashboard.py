"""13-dashboard: un test por criterio de aceptación (más reglas sin AC propio y la verificación con el seed)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.money import Money
from app.modules.loans.models import Loan
from app.modules.marketplace.models import LoanOffer, LoanRequest
from app.modules.notifications.models import Notification
from app.modules.recaudo.models import CollectionAccount
from app.modules.sales import service as sales_service
from app.modules.sales.models import Repayment
from app.modules.scoring.models import CreditAssessment
from app.modules.users.models import User
from tests.helpers import assert_contract, cop
from tests.test_sales import _account, _ct, _error, _event, _legs

MERCHANT = "/api/v1/dashboard/merchant"
LENDER = "/api/v1/dashboard/lender"
NOW = datetime(2026, 10, 16, 15, 42, tzinfo=UTC)  # 10:42 a. m. en Bogotá


@pytest.fixture(autouse=True)
def _frozen(freeze):
    freeze(NOW)


# ── Ayudas ────────────────────────────────────────────────────────────────────


SIX_M, TWO_M, ZERO = cop(6_000_000), cop(2_000_000), cop(0)


def _assessment(db: Session, merchant: User, *, cupo_total: Money = SIX_M, score=780, grade="A",
                eligible=True, unlocks_at=None) -> CreditAssessment:
    """Assessment de hoy (vigente para `get_credit_profile` mientras no haya ventas más nuevas)."""
    assessment = CreditAssessment(
        merchant_id=merchant.id,
        policy_version="v1",
        score=score,
        grade=grade,
        eligible=eligible,
        unlocks_at=unlocks_at,
        cupo_total=cupo_total.value,
        asset_code="COP",
        asset_scale=2,
        features={},
        reasons=[],
        computed_at=clock.now(),
    )
    db.add(assessment)
    db.commit()
    return assessment


def _loan(db: Session, merchant: User, lender: User, *, status="active", principal: Money = TWO_M,
          fee_bps=0, allocated: Money = ZERO, activated_at=None, created_at=None,
          account: CollectionAccount | None = None) -> Loan:
    account = account or _account(db, merchant)
    fee = principal.value * fee_bps // 10_000
    assessment = db.scalar(select(CreditAssessment).where(CreditAssessment.merchant_id == merchant.id).limit(1))
    loan = Loan(
        merchant_id=merchant.id,
        lender_id=lender.id,
        origin="offer_taken",
        created_by=merchant.id,
        credit_assessment_id=(assessment or _assessment(db, merchant)).id,
        collection_account_id=account.id,
        merchant_payout_wallet_url=merchant.payout_wallet_url or "https://ilp.interledger-test.dev/m",
        lender_wallet_url=lender.payout_wallet_url,
        lender_asset_code="COP",
        lender_asset_scale=2,
        asset_code="COP",
        asset_scale=2,
        principal=principal.value,
        fee_bps=fee_bps,
        fee_amount=fee,
        total_repayable=principal.value + fee,
        trust_share_bps=2000,
        term_months=3,
        monthly_minimum=(principal.value + fee) // 3,
        pct_initial_bps=1500,
        pct_min_bps=1000,
        pct_max_bps=2000,
        terms={},
        status=status,
        pct_bps=1500,
        allocated_total=allocated.value,
        activated_at=activated_at if activated_at is not None else (clock.now() if status == "active" else None),
        created_at=created_at or clock.now(),
    )
    db.add(loan)
    db.commit()
    return loan


def _offer(db: Session, lender: User, low: Money, high: Money, pct_bps: int) -> LoanOffer:
    offer = LoanOffer(lender_id=lender.id, min_amount=low.value, max_amount=high.value, asset_code="COP",
                      asset_scale=2, pct_bps=pct_bps, fee_bps=500, term_months=3, status="open")
    db.add(offer)
    db.commit()
    return offer


def _sale(db: Session, ct, amount: Money, paid_at: datetime) -> Repayment | None:
    """Venta procesada como en 07 (abono y patas reales), con `created_at` = `paid_at`."""
    clock.freeze(paid_at)
    result = sales_service.process_puntored_event(db, _event(ct.account, amount, occurred_at=paid_at), "puntored")
    db.commit()
    assert result.status == "processed"
    return db.scalar(select(Repayment).where(Repayment.sale_id == result.sale_id))


def _get(client, auth, user, path: str, operation_id: str) -> dict:
    response = client.get(path, headers=auth(user))
    assert response.status_code == 200, response.text
    assert_contract(response, operation_id)
    return response.json()


def _merchant_dashboard(client, auth, user) -> dict:
    return _get(client, auth, user, MERCHANT, "getMerchantDashboard")


def _lender_dashboard(client, auth, user) -> dict:
    return _get(client, auth, user, LENDER, "getLenderDashboard")


def _market_count(client, auth, merchant) -> int:
    response = client.get("/api/v1/loan-offers", params={"scope": "market"}, headers=auth(merchant))
    assert response.status_code == 200, response.text
    assert_contract(response, "listLoanOffers")
    return response.json()["count"]


def _zero_totals() -> dict:
    zero = cop(0).to_api()
    return {"asset_code": "COP", "asset_scale": 2, "total_prestado": zero,
            "abonos_hoy": {"gross": zero, "trust_cut": zero, "net": zero}, "recibido_hoy": zero}


# ── AC ────────────────────────────────────────────────────────────────────────


def test_ac1_merchant_dashboard_copy_numbers(client, db, auth, make_merchant, make_lender):
    marta = make_merchant(first_name="Marta", last_name="Rojas", business_name="Tienda La Esquina",
                          city="Bucaramanga")
    camilo = make_lender(first_name="Camilo", last_name="Pardo")
    _assessment(db, marta, cupo_total=cop(6_000_000), score=780, grade="A", eligible=True)
    account = _account(db, marta)
    loan = _loan(db, marta, camilo, principal=cop(2_000_000), fee_bps=0, allocated=cop(640_000),
                 activated_at=datetime(2026, 10, 2, 15, 0, tzinfo=UTC), account=account)
    db.add_all([
        Notification(user_id=marta.id, type="sale_split", title="t", body="b", data={}),
        Notification(user_id=marta.id, type="sale_split", title="t", body="b", data={}, read_at=clock.now()),
        Notification(user_id=camilo.id, type="loan_active", title="t", body="b", data={}),
    ])
    db.commit()

    body = _merchant_dashboard(client, auth, marta)

    assert body["greeting"] == {"first_name": "Marta", "business_name": "Tienda La Esquina", "city": "Bucaramanga",
                                "initials": "TL"}
    assert body["unread_notifications"] == 1  # R7: solo las propias sin leer
    assert body["credit"] == {
        "cupo_total": cop(6_000_000).to_api(),
        "cupo_disponible": cop(4_000_000).to_api(),
        "score": 780,
        "grade": "A",
        "eligible": True,
        "unlocks_at": None,
    }
    assert body["active_loan"] == {
        "id": str(loan.id),
        "status": "active",
        "principal": cop(2_000_000).to_api(),
        "pct_bps": 1500,
        "lender": {"id": str(camilo.id), "public_name": "Camilo P."},
        "allocated": cop(640_000).to_api(),
        "pct_paid_bps": 3200,
        "since": "2026-10-02T15:00:00Z",
        "is_late": False,
    }
    assert body["collection_account"] == {"qr_payload": account.qr_payload, "status": "active"}
    assert body["today"] == {"sales_total": cop(0).to_api(), "sales_count": 0, "merchant_total": cop(0).to_api(),
                             "abonado_total": cop(0).to_api()}
    assert body["next_action"] == "view_loan"


def test_ac2_today_matches_sales_feed(client, db, auth, make_merchant, make_lender):
    ct = _ct(db, make_merchant, make_lender)
    _sale(db, ct, cop(48_000), datetime(2026, 10, 15, 20, 0, tzinfo=UTC))  # ayer (3:00 p. m. del 15)
    for hour, amount in ((13, 48_000), (14, 26_000), (15, 12_500), (16, 85_000)):  # 8, 9, 10 y 11 a. m.
        _sale(db, ct, cop(amount), datetime(2026, 10, 16, hour, 0, tzinfo=UTC))
    clock.freeze(datetime(2026, 10, 16, 16, 30, tzinfo=UTC))

    today = _merchant_dashboard(client, auth, ct.merchant)["today"]

    assert today == {
        "sales_total": cop(171_500).to_api(),
        "sales_count": 4,
        "merchant_total": cop(145_775).to_api(),
        "abonado_total": cop(25_725).to_api(),  # 7.200 + 3.900 + 1.875 + 12.750
    }
    feed = client.get("/api/v1/sales/today", headers=auth(ct.merchant))
    assert feed.status_code == 200 and feed.json()["summary"] == today


def test_ac3_offers_fit_count(client, db, auth, freeze, make_merchant, make_lender):
    merchant, lender = make_merchant(), make_lender()
    _account(db, merchant)
    assessment = _assessment(db, merchant, cupo_total=cop(4_000_000))
    _offer(db, lender, cop(1_000_000), cop(2_500_000), 1500)  # A
    _offer(db, lender, cop(3_500_000), cop(5_000_000), 1500)  # B
    _offer(db, lender, cop(1_000_000), cop(2_000_000), 2000)  # C
    _offer(db, lender, cop(1_000_000), cop(2_000_000), 1000).status = "taken"  # no está open: no cuenta
    db.commit()
    request = LoanRequest(merchant_id=merchant.id, min_amount=cop(1_500_000).value, max_amount=cop(3_000_000).value,
                          asset_code="COP", asset_scale=2, pct_bps=1500, status="open",
                          credit_assessment_id=assessment.id)
    db.add(request)
    db.commit()

    # Con la solicitud $1.500.000–$3.000.000 al 15 %: solo A encaja.
    body = _merchant_dashboard(client, auth, merchant)
    assert body["credit"]["cupo_disponible"] == cop(4_000_000).to_api()
    assert body["offers_fit_count"] == 1 == _market_count(client, auth, merchant)
    assert body["next_action"] == "view_offers"

    # Sin solicitud: la virtual {$500.000, $4.000.000, 20 %} → A, B y C.
    request.status = "cancelled"
    db.commit()
    body = _merchant_dashboard(client, auth, merchant)
    assert body["offers_fit_count"] == 3 == _market_count(client, auth, merchant)
    assert body["next_action"] == "request_credit"

    # No elegible → 0 (y la siguiente acción es esperar el desbloqueo).
    freeze(NOW + timedelta(seconds=1))
    _assessment(db, merchant, cupo_total=cop(0), score=300, grade="D", eligible=False,
                unlocks_at=datetime(2026, 12, 1, 5, 0, tzinfo=UTC))
    body = _merchant_dashboard(client, auth, merchant)
    assert body["offers_fit_count"] == 0 == _market_count(client, auth, merchant)
    assert body["credit"]["eligible"] is False and body["credit"]["unlocks_at"] == "2026-12-01T05:00:00Z"
    assert body["next_action"] == "wait_unlock"

    # R4: elegible con cupo disponible menor que MIN_LOAN_AMOUNT ($500.000) → 0.
    freeze(NOW + timedelta(seconds=2))
    _assessment(db, merchant, cupo_total=cop(450_000))
    body = _merchant_dashboard(client, auth, merchant)
    assert body["offers_fit_count"] == 0 == _market_count(client, auth, merchant)


def test_ac4_merchant_empty_state(client, db, auth, make_merchant):
    merchant = make_merchant(wallet=False)

    body = _merchant_dashboard(client, auth, merchant)

    assert body["active_loan"] is None
    assert body["collection_account"] is None
    assert body["today"] == {"sales_total": cop(0).to_api(), "sales_count": 0, "merchant_total": cop(0).to_api(),
                             "abonado_total": cop(0).to_api()}
    assert body["credit"]["eligible"] is False and body["credit"]["cupo_total"] == cop(0).to_api()
    assert (body["offers_fit_count"], body["unread_notifications"]) == (0, 0)
    assert body["next_action"] == "link_wallet"


def test_ac5_lender_totals(client, db, auth, make_merchant, make_lender):
    lender = make_lender(first_name="Camilo", last_name="Pardo", lender_range_min=cop(1_000_000).value,
                         lender_range_max=cop(2_500_000).value)
    t0 = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)

    def loan(status: str, principal: Money, days: int) -> Loan:
        return _loan(db, make_merchant(), lender, status=status, principal=principal,
                     created_at=t0 + timedelta(days=days))

    pending = loan("pending_disbursement", cop(1_000_000), 4)
    active_old = loan("active", cop(2_000_000), 1)
    disbursing = loan("disbursing", cop(2_000_000), 3)
    active_new = loan("active", cop(1_500_000), 2)
    loan("paid", cop(3_000_000), 5)  # terminal: no aparece ni suma
    _loan(db, make_merchant(), make_lender(), principal=cop(9_000_000))  # de otro prestamista

    body = _lender_dashboard(client, auth, lender)

    assert body["greeting"] == {"first_name": "Camilo", "public_name": "Camilo P."}
    assert body["active_loans_count"] == 2
    assert body["totals"] == [{**_zero_totals(), "total_prestado": cop(5_500_000).to_api()}]
    assert [row["id"] for row in body["loans"]] == [
        str(active_new.id), str(active_old.id), str(pending.id), str(disbursing.id),
    ]  # `active` primero y luego `created_at` desc
    assert [row["status"] for row in body["loans"]] == ["active", "active", "pending_disbursement", "disbursing"]
    first = body["loans"][0]
    assert (first["principal"], first["pct_bps"], first["pct_paid_bps"], first["today_gross"], first["is_late"]) == (
        cop(1_500_000).to_api(), 1500, 0, cop(0).to_api(), False,
    )
    assert body["mi_rango"] == {"min": cop(1_000_000).to_api(), "max": cop(2_500_000).to_api()}
    assert (body["abonos_por_hora"], body["last_repayments"]) == ([], [])


def test_ac6_lender_hourly_and_last(client, db, auth, make_merchant, make_lender):
    ct = _ct(db, make_merchant, make_lender)
    other = _loan(db, make_merchant(business_name="Panadería El Trigal"), ct.lender, principal=cop(1_500_000))
    # La de $48.000 va primero en la contabilidad del crédito: así da los mismos centavos que 07 AC-1.
    first = _sale(db, ct, cop(48_000), datetime(2026, 10, 16, 13, 15, tzinfo=UTC))  # 08:15
    second = _sale(db, ct, cop(26_000), datetime(2026, 10, 16, 15, 5, tzinfo=UTC))  # 10:05
    yesterday = _sale(db, ct, cop(12_500), datetime(2026, 10, 15, 20, 0, tzinfo=UTC))
    assert (first.abono_amount, first.trust_cut_amount, first.lender_net_amount) == (
        cop(7_200).value, cop("130,90").value, cop("7.069,10").value,
    )
    assert second.abono_amount == cop(3_900).value
    clock.freeze(datetime(2026, 10, 16, 16, 0, tzinfo=UTC))

    body = _lender_dashboard(client, auth, ct.lender)

    hoy = body["totals"][0]["abonos_hoy"]
    assert hoy["gross"] == cop(11_100).to_api()
    assert hoy["trust_cut"] == Money(first.trust_cut_amount + second.trust_cut_amount, "COP", 2).to_api()
    assert hoy["net"] == Money(first.lender_net_amount + second.lender_net_amount, "COP", 2).to_api()
    assert body["abonos_por_hora"] == [
        {"hour": "08:00", "amount": cop(7_200).to_api()},
        {"hour": "09:00", "amount": cop(0).to_api()},
        {"hour": "10:00", "amount": cop(3_900).to_api()},
    ]
    today_gross = {row["id"]: row["today_gross"] for row in body["loans"]}
    assert today_gross == {str(ct.loan.id): cop(11_100).to_api(), str(other.id): cop(0).to_api()}
    assert [r["id"] for r in body["last_repayments"]] == [str(second.id), str(first.id), str(yesterday.id)]
    assert body["last_repayments"][1] == {
        "id": str(first.id),
        "paid_at": "2026-10-16T13:15:00Z",
        "merchant": {"id": str(ct.merchant.id), "business_name": "Tienda La Esquina", "city": "Bucaramanga",
                     "initials": "TL"},
        "gross": cop(7_200).to_api(),
        "trust_cut": cop("130,90").to_api(),
        "net": cop("7.069,10").to_api(),
    }
    db.refresh(ct.loan)
    pct_paid = ct.loan.allocated_total * 10_000 // ct.loan.total_repayable  # R8 = 11 R13
    assert next(row for row in body["loans"] if row["id"] == str(ct.loan.id)) == {
        "id": str(ct.loan.id),
        "merchant": body["last_repayments"][1]["merchant"],
        "principal": cop(2_000_000).to_api(),
        "pct_bps": 1500,
        "pct_paid_bps": pct_paid,
        "today_gross": cop(11_100).to_api(),
        "status": "active",
        "is_late": False,
    }

    # R11: como máximo 5, los más recientes por `paid_at`.
    for minute in range(1, 6):
        _sale(db, ct, cop(10_000), datetime(2026, 10, 16, 15, 30 + minute, tzinfo=UTC))
    clock.freeze(datetime(2026, 10, 16, 16, 0, tzinfo=UTC))
    last = _lender_dashboard(client, auth, ct.lender)["last_repayments"]
    assert [r["paid_at"] for r in last] == [f"2026-10-16T15:{35 - i}:00Z" for i in range(5)]


def test_ac7_received_today(client, db, auth, make_merchant, make_lender):
    ct = _ct(db, make_merchant, make_lender)
    new = _sale(db, ct, cop(48_000), datetime(2026, 10, 16, 14, 0, tzinfo=UTC))  # primero: neto $7.069,10 (07 AC-1)
    old = _sale(db, ct, cop(48_000), datetime(2026, 10, 15, 20, 0, tzinfo=UTC))
    for repayment, completed_at in ((old, datetime(2026, 10, 15, 20, 0, 9, tzinfo=UTC)),
                                    (new, datetime(2026, 10, 16, 14, 0, 9, tzinfo=UTC))):
        for leg in _legs(db, repayment.sale_id).values():  # también las patas merchant y trust: no cuentan
            leg.status, leg.sent_total, leg.completed_at = "completed", leg.amount, completed_at
            leg.receive_value, leg.receive_asset_code, leg.receive_asset_scale = 177, "USD", 2
    db.commit()
    clock.freeze(datetime(2026, 10, 16, 16, 0, tzinfo=UTC))

    body = _lender_dashboard(client, auth, ct.lender)

    assert body["totals"][0]["recibido_hoy"] == cop("7.069,10").to_api()  # asset del crédito, no del receptor
    assert body["totals"][0]["abonos_hoy"]["net"] == cop("7.069,10").to_api()


def test_ac8_roles(client, auth, make_merchant, make_lender, make_admin):
    merchant, lender = make_merchant(), make_lender()
    _error(client.get(LENDER, headers=auth(merchant)), 403, "forbidden", "getLenderDashboard")
    _error(client.get(MERCHANT, headers=auth(lender)), 403, "forbidden", "getMerchantDashboard")
    _error(client.get(MERCHANT, headers=auth(make_admin())), 403, "forbidden", "getMerchantDashboard")
    _error(client.get(LENDER, headers=auth(make_admin())), 403, "forbidden", "getLenderDashboard")
    _error(client.get(MERCHANT), 401, "unauthorized", "getMerchantDashboard")
    _error(client.get(LENDER), 401, "unauthorized", "getLenderDashboard")


# ── Reglas sin AC propio ──────────────────────────────────────────────────────


def test_next_action_priority(client, db, auth, make_merchant, make_lender):
    """R6: link_wallet → activate_qr → accept_loan → wait_disbursement → view_loan (en ese orden)."""
    merchant, lender = make_merchant(), make_lender()
    _assessment(db, merchant)
    assert _merchant_dashboard(client, auth, merchant)["next_action"] == "activate_qr"  # wallet, sin cuenta

    revoked = _account(db, merchant, status="revoked")
    body = _merchant_dashboard(client, auth, merchant)
    assert body["collection_account"] == {"qr_payload": revoked.qr_payload, "status": "revoked"}  # R5: la más reciente
    assert body["next_action"] == "activate_qr"

    account = _account(db, merchant)
    loan = _loan(db, merchant, lender, status="pending_acceptance", account=account)
    body = _merchant_dashboard(client, auth, merchant)
    assert body["collection_account"]["status"] == "active"
    assert body["next_action"] == "accept_loan"
    assert (body["active_loan"]["status"], body["active_loan"]["since"]) == ("pending_acceptance", None)
    assert body["credit"]["cupo_disponible"] == cop(4_000_000).to_api()  # el pendiente ya usa cupo (09 R3)

    for status, expected in (("pending_disbursement", "wait_disbursement"), ("disbursing", "wait_disbursement"),
                             ("active", "view_loan")):
        loan.status = status
        db.commit()
        assert _merchant_dashboard(client, auth, merchant)["next_action"] == expected

    merchant.payout_wallet_url = None  # sin wallet manda sobre todo lo demás
    db.commit()
    assert _merchant_dashboard(client, auth, merchant)["next_action"] == "link_wallet"


def test_lender_other_asset_totals(client, db, auth, make_merchant, make_lender):
    """R9: la entrada del asset de mercado siempre va (primero); otro asset solo si tiene datos."""
    lender = make_lender()
    usd = _loan(db, make_merchant(), lender, principal=cop(1_000_000))
    usd.asset_code = "USD"
    db.commit()

    totals = _lender_dashboard(client, auth, lender)["totals"]

    usd_money = Money(cop(1_000_000).value, "USD", 2).to_api()
    zero_usd = Money(0, "USD", 2).to_api()
    assert totals == [
        _zero_totals(),
        {"asset_code": "USD", "asset_scale": 2, "total_prestado": usd_money,
         "abonos_hoy": {"gross": zero_usd, "trust_cut": zero_usd, "net": zero_usd}, "recibido_hoy": zero_usd},
    ]


def test_seed_dashboards(client, db, auth, settings, monkeypatch, freeze):
    """Con `POST /dev/seed` (crédito de Marta incluido) los dos dashboards cumplen el contrato con cifras de COPY."""
    monkeypatch.setattr(settings, "dev_endpoints_enabled", True)
    freeze(datetime(2026, 10, 17, 15, 42, tzinfo=UTC))
    response = client.post("/api/v1/dev/seed", json={"with_active_loan_for_marta": True})
    assert response.status_code == 200, response.text
    marta = db.scalar(select(User).where(User.email == "marta@demo.trust"))
    camilo = db.scalar(select(User).where(User.email == "camilo@demo.trust"))

    merchant = _merchant_dashboard(client, auth, marta)
    assert merchant["greeting"]["initials"] == "TL"
    assert merchant["credit"]["cupo_total"] == cop(6_000_000).to_api()
    assert merchant["credit"]["cupo_disponible"] == cop(4_000_000).to_api()
    assert merchant["active_loan"]["principal"] == cop(2_000_000).to_api()
    assert merchant["active_loan"]["lender"]["public_name"] == "Camilo P."
    assert abs(merchant["active_loan"]["pct_paid_bps"] - 3200) <= 100
    assert merchant["collection_account"]["status"] == "active"
    assert merchant["next_action"] == "view_loan"

    lender = _lender_dashboard(client, auth, camilo)
    assert lender["active_loans_count"] == 3 and len(lender["loans"]) == 3
    assert lender["totals"][0]["total_prestado"] == cop(5_500_000).to_api()  # COPY 13d: "$5.500.000"
    assert lender["mi_rango"] is not None or camilo.lender_range_min is None
    assert 1 <= len(lender["last_repayments"]) <= 5
    gross = int(lender["totals"][0]["abonos_hoy"]["gross"]["value"])
    assert gross == sum(int(row["today_gross"]["value"]) for row in lender["loans"])
    assert gross == sum(int(h["amount"]["value"]) for h in lender["abonos_por_hora"])
    assert lender["totals"][0]["recibido_hoy"] == cop(0).to_api()  # el historial sembrado no tiene patas

    # Una venta en vivo de $48.000 de Marta (simulador de 03) aparece en los dos dashboards como en COPY 13a/13d.
    simulated = client.post("/api/v1/dev/sales/simulate",
                            json={"merchant_id": str(marta.id), "amount": cop(48_000).to_api()})
    assert simulated.status_code == 201, simulated.text
    merchant = _merchant_dashboard(client, auth, marta)
    assert merchant["today"] == {"sales_total": cop(48_000).to_api(), "sales_count": 1,
                                 "merchant_total": cop(40_800).to_api(), "abonado_total": cop(7_200).to_api()}
    lender = _lender_dashboard(client, auth, camilo)
    assert lender["totals"][0]["abonos_hoy"]["gross"] == Money(gross + cop(7_200).value, "COP", 2).to_api()
    assert {"hour": "10:00", "amount": cop(7_200).to_api()} in lender["abonos_por_hora"]
    marta_loan = next(row for row in lender["loans"] if row["merchant"]["id"] == str(marta.id))
    assert marta_loan["today_gross"] == cop(7_200).to_api()
    assert lender["last_repayments"][0]["paid_at"] == "2026-10-17T15:42:00Z"
    assert lender["last_repayments"][0]["gross"] == cop(7_200).to_api()
