"""10-marketplace: un test por criterio de aceptación (más los endpoints y reglas sin AC propio)."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.events import Event
from app.core.money import Money
from app.modules.marketplace.models import LoanOffer, LoanRequest
from app.modules.scoring.models import CreditAssessment
from app.modules.users.models import User
from tests.helpers import assert_contract, cop
from tests.test_scoring import CUPO_6M, NOW, _assessment, _loan

REQUESTS = "/api/v1/loan-requests"
OFFERS = "/api/v1/loan-offers"
MATCHES = "/api/v1/matches"


@pytest.fixture(autouse=True)
def _env(freeze):
    freeze(NOW)


# ── Ayudas ────────────────────────────────────────────────────────────────────


def _body(low: Money, high: Money, pct_bps: int = 1500, **extra) -> dict:
    return {"min_amount": low.to_api(), "max_amount": high.to_api(), "pct_bps": pct_bps, **extra}


def _error(response, status: int, code: str, operation_id: str) -> dict:
    assert response.status_code == status, response.text
    assert_contract(response, operation_id)
    error = response.json()["error"]
    assert error["code"] == code, error
    return error


def _eligible(db: Session, make_merchant, cupo: Money = CUPO_6M, **fields) -> SimpleNamespace:
    """Negocio elegible (assessment de hoy, A 845) con `cupo_disponible` = `cupo`."""
    merchant = make_merchant(**fields)
    return SimpleNamespace(user=merchant, assessment=_assessment(db, merchant, cupo=cupo))


def _request(client, auth, merchant: User, low: Money, high: Money, pct_bps: int = 1500) -> dict:
    response = client.post(REQUESTS, json=_body(low, high, pct_bps), headers=auth(merchant))
    assert response.status_code == 201, response.text
    assert_contract(response, "createLoanRequest")
    return response.json()


def _offer(db: Session, lender: User, low: Money, high: Money, pct_bps: int, *, fee_bps: int = 500,
           term_months: int = 3, status: str = "open", created_at: datetime | None = None) -> LoanOffer:
    offer = LoanOffer(
        lender_id=lender.id, min_amount=low.value, max_amount=high.value, asset_code="COP", asset_scale=2,
        pct_bps=pct_bps, fee_bps=fee_bps, term_months=term_months, status=status,
        created_at=created_at or clock.now(), updated_at=created_at or clock.now(),
    )
    db.add(offer)
    db.commit()
    return offer


def _ac7_offers(db: Session, make_lender) -> dict[str, LoanOffer]:
    """Ofertas A–E de AC-7, cada una de un prestamista distinto y con `created_at` distinto (A la más antigua)."""
    specs = {
        "A": (cop(1_000_000), cop(2_500_000), 1500),
        "B": (cop(3_500_000), cop(4_000_000), 1500),
        "C": (cop(1_000_000), cop(2_000_000), 2000),
        "D": (cop(2_000_000), cop(3_000_000), 1000),
        "E": (cop(500_000), cop(1_500_000), 1500),
    }
    start = NOW - timedelta(days=10)
    return {
        name: _offer(
            db, make_lender(first_name=f"Prestamista{name}"), low, high, pct, created_at=start + timedelta(hours=i)
        )
        for i, (name, (low, high, pct)) in enumerate(specs.items())
    }


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _proposals(body: dict, offers: dict[str, LoanOffer]) -> dict[str, str | None]:
    names = {str(offer.id): name for name, offer in offers.items()}
    return {
        names[item["id"]]: (item["proposal"]["amount"]["value"] if item["proposal"] else None) for item in body["items"]
    }


# ── Criterios de aceptación ───────────────────────────────────────────────────


def test_ac1_create_request(client, db, make_merchant, auth):
    merchant = _eligible(db, make_merchant)

    body = _request(client, auth, merchant.user, cop(1_500_000), cop(3_000_000), 1500)

    assert body["status"] == "open"
    assert body["credit_assessment_id"] == str(merchant.assessment.id)
    assert body["min_amount"] == cop(1_500_000).to_api() and body["max_amount"] == cop(3_000_000).to_api()
    assert body["pct_bps"] == 1500
    assert body["merchant"] is None and body["credit"] is None
    assert body["created_at"] == "2026-10-16T15:00:00Z"
    row = db.get(LoanRequest, uuid.UUID(body["id"]))
    assert (row.merchant_id, row.status, row.asset_code, row.asset_scale) == (merchant.user.id, "open", "COP", 2)
    event = db.scalar(select(Event).where(Event.entity_type == "loan_request", Event.entity_id == row.id))
    assert (event.type, event.actor_id) == ("created", merchant.user.id)


def test_ac2_request_cupo_and_eligibility(client, db, make_merchant, auth):
    merchant = _eligible(db, make_merchant)
    response = client.post(REQUESTS, json=_body(cop(1_500_000), cop(7_000_000)), headers=auth(merchant.user))
    error = _error(response, 422, "exceeds_cupo", "createLoanRequest")
    assert error["details"]["cupo_disponible"] == cop(6_000_000).to_api()

    newcomer = make_merchant()  # sin ventas: no elegible
    response = client.post(REQUESTS, json=_body(cop(1_500_000), cop(3_000_000)), headers=auth(newcomer))
    _error(response, 422, "not_eligible", "createLoanRequest")
    assert db.scalar(select(LoanRequest.id)) is None


def test_ac3_request_validation(client, db, make_merchant, auth):
    merchant = _eligible(db, make_merchant).user
    headers = auth(merchant)

    _error(client.post(REQUESTS, json=_body(cop(1_500_000), cop(3_000_000), 1200), headers=headers), 422,
           "pct_not_allowed", "createLoanRequest")
    error = _error(client.post(REQUESTS, json=_body(cop(1_502_000), cop(3_000_000)), headers=headers), 422,
                   "validation_error", "createLoanRequest")
    assert error["details"]["fields"][0]["field"] == "min_amount"
    _error(client.post(REQUESTS, json=_body(cop(3_000_000), cop(1_500_000)), headers=headers), 422,
           "validation_error", "createLoanRequest")
    _error(client.post(REQUESTS, json=_body(cop(450_000), cop(1_500_000)), headers=headers), 422,
           "validation_error", "createLoanRequest")  # mínimo < MIN_LOAN_AMOUNT ($500.000)
    _error(client.post(REQUESTS, json=_body(cop(1_500_000), cop(3_020_000)), headers=headers), 422,
           "validation_error", "createLoanRequest")  # máximo no múltiplo de $50.000
    eur = _body(cop(1_500_000), cop(3_000_000))
    eur["min_amount"]["asset_code"] = "EUR"
    _error(client.post(REQUESTS, json=eur, headers=headers), 422, "asset_mismatch", "createLoanRequest")
    scale0 = _body(Money(1_500_000, "COP", 0), Money(3_000_000, "COP", 0))
    _error(client.post(REQUESTS, json=scale0, headers=headers), 422, "asset_mismatch", "createLoanRequest")
    _error(client.post(REQUESTS, json={"min_amount": 150_000_000, "max_amount": cop(3_000_000).to_api(),
                                       "pct_bps": 1500}, headers=headers), 422, "validation_error", "createLoanRequest")
    assert db.scalar(select(LoanRequest.id)) is None


def test_ac4_request_conflicts(client, db, make_merchant, make_lender, auth):
    merchant = _eligible(db, make_merchant).user
    _request(client, auth, merchant, cop(1_500_000), cop(3_000_000))
    response = client.post(REQUESTS, json=_body(cop(1_000_000), cop(2_000_000)), headers=auth(merchant))
    _error(response, 409, "open_request_exists", "createLoanRequest")

    other = _eligible(db, make_merchant).user
    _loan(db, other, make_lender(), "pending_disbursement")
    response = client.post(REQUESTS, json=_body(cop(1_500_000), cop(3_000_000)), headers=auth(other))
    _error(response, 409, "active_loan_exists", "createLoanRequest")


def test_request_unique_index_race(client, db, make_merchant, auth, monkeypatch):
    """R2: si otra solicitud `open` gana la carrera, el índice único parcial también da 409 `open_request_exists`."""
    eligible = _eligible(db, make_merchant)
    merchant = eligible.user
    db.add(LoanRequest(
        merchant_id=merchant.id, min_amount=150_000_000, max_amount=300_000_000, asset_code="COP", asset_scale=2,
        pct_bps=1500, status="open", credit_assessment_id=eligible.assessment.id,
        created_at=clock.now(), updated_at=clock.now(),
    ))
    db.commit()
    # Simula la carrera: el chequeo explícito no ve la otra solicitud, pero el INSERT choca con el índice.
    monkeypatch.setattr("app.modules.marketplace.service._open_request", lambda db, merchant_id: None)

    response = client.post(REQUESTS, json=_body(cop(1_000_000), cop(2_000_000)), headers=auth(merchant))

    _error(response, 409, "open_request_exists", "createLoanRequest")
    db.expire_all()
    open_ids = db.scalars(
        select(LoanRequest.id).where(LoanRequest.merchant_id == merchant.id, LoanRequest.status == "open")
    ).all()
    assert len(open_ids) == 1


def test_ac5_create_offer(client, db, make_lender, auth):
    no_wallet = make_lender(wallet=False)
    body = _body(cop(1_000_000), cop(2_500_000), 1500, fee_bps=500)
    _error(client.post(OFFERS, json=body, headers=auth(no_wallet)), 422, "wallet_required", "createLoanOffer")

    lender = make_lender()
    response = client.post(OFFERS, json=body, headers=auth(lender))
    assert response.status_code == 201, response.text
    assert_contract(response, "createLoanOffer")
    offer = response.json()
    assert (offer["status"], offer["term_months"], offer["fee_bps"], offer["pct_bps"]) == ("open", 3, 500, 1500)
    assert offer["lender"] == {"id": str(lender.id), "public_name": "Camilo P."}
    assert offer["proposal"] is None
    assert offer["min_amount"] == cop(1_000_000).to_api()
    row = db.get(LoanOffer, uuid.UUID(offer["id"]))
    assert (row.status, row.term_months, row.asset_code) == ("open", 3, "COP")


def test_ac6_requests_market_in_my_range(client, db, make_merchant, make_lender, auth):
    first = _eligible(db, make_merchant, business_name="Tienda La Esquina").user
    second = _eligible(db, make_merchant, cupo=cop(4_000_000), business_name="Panadería Nova").user
    first_req = _request(client, auth, first, cop(1_500_000), cop(3_000_000))
    _request(client, auth, second, cop(3_000_000), cop(4_000_000))
    lender = make_lender(lender_range_min=cop(1_000_000).value, lender_range_max=cop(2_500_000).value)

    response = client.get(REQUESTS, params={"scope": "market", "in_my_range": "true"}, headers=auth(lender))

    assert response.status_code == 200, response.text
    assert_contract(response, "listLoanRequests")
    items = response.json()["items"]
    assert [item["id"] for item in items] == [first_req["id"]]
    assert items[0]["merchant"] == {
        "id": str(first.id), "business_name": "Tienda La Esquina", "city": "Bucaramanga", "initials": "TL"
    }
    assert items[0]["credit"] == {
        "score": 845, "grade": "A", "cupo_total": cop(6_000_000).to_api(), "cupo_disponible": cop(6_000_000).to_api()
    }

    response = client.get(REQUESTS, params={"scope": "market"}, headers=auth(lender))
    assert_contract(response, "listLoanRequests")
    assert len(response.json()["items"]) == 2
    # Sin rango, `in_my_range` se ignora.
    response = client.get(REQUESTS, params={"scope": "market", "in_my_range": "true"}, headers=auth(make_lender()))
    assert_contract(response, "listLoanRequests")
    assert len(response.json()["items"]) == 2


def test_ac7_offers_market_fit_count(client, db, make_merchant, make_lender, auth):
    merchant = _eligible(db, make_merchant).user
    offers = _ac7_offers(db, make_lender)
    request = _request(client, auth, merchant, cop(1_500_000), cop(3_000_000), 1500)
    expected = {"A": str(cop(2_000_000).value), "B": None, "C": None, "D": str(cop(2_500_000).value),
                "E": str(cop(1_500_000).value)}

    response = client.get(OFFERS, params={"scope": "market"}, headers=auth(merchant))
    assert response.status_code == 200, response.text
    assert_contract(response, "listLoanOffers")
    body = response.json()
    assert body["count"] == 3
    assert _proposals(body, offers) == expected
    assert [item["id"] for item in body["items"]] == [str(offers[n].id) for n in "EDCBA"]  # más recientes primero
    a = next(item for item in body["items"] if item["id"] == str(offers["A"].id))
    assert a["proposal"] == {"amount": cop(2_000_000).to_api(), "overlap_min": cop(1_500_000).to_api(),
                             "overlap_max": cop(2_500_000).to_api(), "pct_bps": 1500}

    # Sin solicitud y con la hipotética de fit_min/fit_max/pct_bps: lo mismo.
    cancel = client.post(f"{REQUESTS}/{request['id']}/cancel", headers=auth(merchant))
    assert cancel.status_code == 200, cancel.text
    params = {"scope": "market", "fit_min": 150000000, "fit_max": 300000000, "pct_bps": 1500}
    response = client.get(OFFERS, params=params, headers=auth(merchant))
    assert_contract(response, "listLoanOffers")
    assert response.json()["count"] == 3
    assert _proposals(response.json(), offers) == expected

    response = client.get(OFFERS, params={**params, "fits_only": "true"}, headers=auth(merchant))
    assert_contract(response, "listLoanOffers")
    assert response.json()["count"] == 3
    assert _proposals(response.json(), offers) == {k: v for k, v in expected.items() if v is not None}

    # `count` cuenta todas las páginas.
    response = client.get(OFFERS, params={**params, "fits_only": "true", "limit": 2}, headers=auth(merchant))
    assert_contract(response, "listLoanOffers")
    page = response.json()
    assert (page["count"], len(page["items"]), page["next_offset"]) == (3, 2, 2)
    response = client.get(OFFERS, params={**params, "fits_only": "true", "limit": 2, "offset": 2},
                          headers=auth(merchant))
    assert (len(response.json()["items"]), response.json()["next_offset"]) == (1, None)


def test_ac8_matches_both_roles(client, db, make_merchant, make_lender, auth):
    merchant = _eligible(db, make_merchant).user
    offers = _ac7_offers(db, make_lender)
    request = _request(client, auth, merchant, cop(1_500_000), cop(3_000_000), 1500)

    response = client.get(MATCHES, headers=auth(merchant))
    assert response.status_code == 200, response.text
    assert_contract(response, "listMatches")
    items = response.json()["items"]
    assert [item["offer"]["id"] for item in items] == [str(offers[n].id) for n in "EDA"]  # received_at desc
    for item in items:
        offer = next(o for o in offers.values() if str(o.id) == item["offer"]["id"])
        assert item["received_at"] == _iso(offer.created_at)
        assert item["request"]["id"] == request["id"]

    owner_a = db.get(User, offers["A"].lender_id)
    response = client.get(MATCHES, headers=auth(owner_a))
    assert response.status_code == 200, response.text
    assert_contract(response, "listMatches")
    items = response.json()["items"]
    assert len(items) == 1
    match = items[0]
    assert match["proposal"]["amount"] == cop(2_000_000).to_api()
    assert match["request"] == {
        "id": request["id"],
        "merchant": {"id": str(merchant.id), "business_name": "Tienda La Esquina", "city": "Bucaramanga",
                     "initials": "TL"},
        "min_amount": cop(1_500_000).to_api(), "max_amount": cop(3_000_000).to_api(), "pct_bps": 1500,
        "cupo_total": cop(6_000_000).to_api(),
    }
    assert match["offer"]["lender"]["public_name"] == "PrestamistaA P."
    assert (match["offer"]["fee_bps"], match["offer"]["term_months"]) == (500, 3)


def test_ac9_cancel_request(client, db, make_merchant, auth):
    owner = _eligible(db, make_merchant).user
    request = _request(client, auth, owner, cop(1_500_000), cop(3_000_000))
    url = f"{REQUESTS}/{request['id']}/cancel"

    _error(client.post(url, headers=auth(make_merchant())), 403, "forbidden", "cancelLoanRequest")

    response = client.post(url, headers=auth(owner))
    assert response.status_code == 200, response.text
    assert_contract(response, "cancelLoanRequest")
    assert response.json()["status"] == "cancelled"
    assert response.json()["merchant"] is None and response.json()["credit"] is None
    event = db.scalar(select(Event).where(Event.entity_type == "loan_request", Event.type == "cancelled"))
    assert (event.entity_id, event.actor_id) == (uuid.UUID(request["id"]), owner.id)

    _error(client.post(url, headers=auth(owner)), 409, "invalid_state", "cancelLoanRequest")
    _error(client.post(f"{REQUESTS}/{uuid.uuid4()}/cancel", headers=auth(owner)), 404, "not_found",
           "cancelLoanRequest")
    _error(client.post(f"{REQUESTS}/no-uuid/cancel", headers=auth(owner)), 404, "not_found", "cancelLoanRequest")


def test_ac10_scope_roles_and_fit_params(client, db, make_merchant, make_lender, auth):
    merchant, lender = _eligible(db, make_merchant).user, make_lender()

    _error(client.get(REQUESTS, params={"scope": "market"}, headers=auth(merchant)), 403, "forbidden",
           "listLoanRequests")
    _error(client.get(OFFERS, params={"scope": "market"}, headers=auth(lender)), 403, "forbidden", "listLoanOffers")
    _error(client.get(OFFERS, params={"scope": "market", "fit_min": 150000000}, headers=auth(merchant)), 422,
           "validation_error", "listLoanOffers")
    _error(client.get(OFFERS, params={"scope": "market", "fit_min": 150000000, "fit_max": 300000000},
                      headers=auth(merchant)), 422, "validation_error", "listLoanOffers")
    # El scope es obligatorio y solo admite mine | market; el rol del scope `mine` también se exige.
    _error(client.get(OFFERS, headers=auth(merchant)), 422, "validation_error", "listLoanOffers")
    _error(client.get(REQUESTS, headers=auth(lender)), 422, "validation_error", "listLoanRequests")
    _error(client.get(REQUESTS, params={"scope": "todo"}, headers=auth(lender)), 422, "validation_error",
           "listLoanRequests")
    _error(client.get(REQUESTS, params={"scope": "mine"}, headers=auth(lender)), 403, "forbidden",
           "listLoanRequests")
    _error(client.get(OFFERS, params={"scope": "mine"}, headers=auth(merchant)), 403, "forbidden", "listLoanOffers")


# ── Endpoints y reglas sin AC propio ──────────────────────────────────────────


def test_offers_market_virtual_request_and_not_eligible(client, db, make_merchant, make_lender, auth):
    """R7/R8: sin solicitud ni fit_*, la virtual {MIN_LOAN_AMOUNT, cupo_disponible, max(PCT_OPTIONS_BPS)};
    no elegible o cupo < MIN_LOAN_AMOUNT: ninguna encaja."""
    merchant = _eligible(db, make_merchant).user
    offers = _ac7_offers(db, make_lender)

    response = client.get(OFFERS, params={"scope": "market"}, headers=auth(merchant))
    assert_contract(response, "listLoanOffers")
    assert response.json()["count"] == 5
    assert _proposals(response.json(), offers) == {
        "A": str(cop(1_750_000).value), "B": str(cop(3_750_000).value), "C": str(cop(1_500_000).value),
        "D": str(cop(2_500_000).value), "E": str(cop(1_000_000).value),
    }

    for blocked in (make_merchant(), _eligible(db, make_merchant, cupo=cop(450_000)).user):
        response = client.get(OFFERS, params={"scope": "market"}, headers=auth(blocked))
        assert response.status_code == 200, response.text
        assert_contract(response, "listLoanOffers")
        assert response.json()["count"] == 0
        assert all(item["proposal"] is None for item in response.json()["items"])
        assert len(response.json()["items"]) == 5
        response = client.get(OFFERS, params={"scope": "market", "fits_only": "true"}, headers=auth(blocked))
        assert response.json()["items"] == []

    # Con un crédito en curso, el cupo usado baja el disponible (y por eso la virtual).
    _loan(db, merchant, make_lender(), "active", cop(4_000_000))
    response = client.get(OFFERS, params={"scope": "market"}, headers=auth(merchant))
    assert response.json()["count"] == 4  # B ($3.500.000 mínimo) ya no cabe en $2.000.000
    assert _proposals(response.json(), offers)["B"] is None


def test_offers_mine_count_detail_and_cancel(client, db, make_merchant, make_lender, auth):
    lender, other = make_lender(), make_lender()
    open_offer = _offer(db, lender, cop(1_000_000), cop(2_500_000), 1500, created_at=NOW - timedelta(hours=2))
    taken = _offer(
        db, lender, cop(1_000_000), cop(2_000_000), 1000, status="taken", created_at=NOW - timedelta(hours=1)
    )
    _offer(db, other, cop(1_000_000), cop(2_000_000), 1000)
    merchant = _eligible(db, make_merchant).user

    response = client.get(OFFERS, params={"scope": "mine"}, headers=auth(lender))
    assert response.status_code == 200, response.text
    assert_contract(response, "listLoanOffers")
    body = response.json()
    assert body["count"] == 2 and [i["id"] for i in body["items"]] == [str(taken.id), str(open_offer.id)]
    assert all(item["proposal"] is None for item in body["items"])
    response = client.get(OFFERS, params={"scope": "mine", "status": "open", "limit": 1}, headers=auth(lender))
    assert_contract(response, "listLoanOffers")
    assert (response.json()["count"], len(response.json()["items"])) == (1, 1)

    # Detalle: el dueño siempre; un negocio solo si está `open`; otro prestamista no.
    for user in (lender, merchant):
        response = client.get(f"{OFFERS}/{open_offer.id}", headers=auth(user))
        assert response.status_code == 200, response.text
        assert_contract(response, "getLoanOffer")
        assert response.json()["proposal"] is None
    response = client.get(f"{OFFERS}/{taken.id}", headers=auth(lender))
    assert_contract(response, "getLoanOffer")
    assert response.json()["status"] == "taken"
    _error(client.get(f"{OFFERS}/{taken.id}", headers=auth(merchant)), 403, "forbidden", "getLoanOffer")
    _error(client.get(f"{OFFERS}/{open_offer.id}", headers=auth(other)), 403, "forbidden", "getLoanOffer")
    _error(client.get(f"{OFFERS}/{uuid.uuid4()}", headers=auth(lender)), 404, "not_found", "getLoanOffer")

    # Cancelar: solo el dueño; `open → cancelled`; otra vez 409.
    url = f"{OFFERS}/{open_offer.id}/cancel"
    _error(client.post(url, headers=auth(other)), 403, "forbidden", "cancelLoanOffer")
    _error(client.post(url, headers=auth(merchant)), 403, "forbidden", "cancelLoanOffer")
    response = client.post(url, headers=auth(lender))
    assert response.status_code == 200, response.text
    assert_contract(response, "cancelLoanOffer")
    assert response.json()["status"] == "cancelled"
    event = db.scalar(select(Event).where(Event.entity_type == "loan_offer", Event.entity_id == open_offer.id))
    assert (event.type, event.actor_id) == ("cancelled", lender.id)
    _error(client.post(url, headers=auth(lender)), 409, "invalid_state", "cancelLoanOffer")
    _error(client.post(f"{OFFERS}/{taken.id}/cancel", headers=auth(lender)), 409, "invalid_state", "cancelLoanOffer")
    _error(client.post(f"{OFFERS}/{uuid.uuid4()}/cancel", headers=auth(lender)), 404, "not_found", "cancelLoanOffer")


def test_offer_validation(client, make_lender, auth):
    """R1/R3: rangos, %, cargo 0–10000 y plazo 1–24."""
    lender = make_lender()
    headers = auth(lender)
    base = _body(cop(1_000_000), cop(2_500_000), 1500, fee_bps=500)
    cases = [
        ({**base, "pct_bps": 1200}, "pct_not_allowed"),
        ({**base, "fee_bps": -1}, "validation_error"),
        ({**base, "fee_bps": 10_001}, "validation_error"),
        ({**base, "term_months": 0}, "validation_error"),
        ({**base, "term_months": 25}, "validation_error"),
        ({**base, "min_amount": cop(1_020_000).to_api()}, "validation_error"),
        ({**base, "min_amount": Money(100_000_000, "USD", 2).to_api()}, "asset_mismatch"),
        # Múltiplo de $50.000 por encima de BIGINT_MAX: no cabe en la columna `bigint` (422, no 500).
        ({**base, "max_amount": Money(2**63 + 5_000_000 - 2**63 % 5_000_000, "COP", 2).to_api()}, "validation_error"),
    ]
    for body, code in cases:
        _error(client.post(OFFERS, json=body, headers=headers), 422, code, "createLoanOffer")
    response = client.post(OFFERS, json={**base, "fee_bps": 0, "term_months": 24}, headers=headers)
    assert response.status_code == 201, response.text
    assert_contract(response, "createLoanOffer")
    assert (response.json()["fee_bps"], response.json()["term_months"]) == (0, 24)


def test_requests_mine_and_detail(client, db, make_merchant, make_lender, make_admin, auth):
    merchant = _eligible(db, make_merchant).user
    first = _request(client, auth, merchant, cop(1_500_000), cop(3_000_000))
    clock.freeze(NOW + timedelta(minutes=1))
    client.post(f"{REQUESTS}/{first['id']}/cancel", headers=auth(merchant))
    second = _request(client, auth, merchant, cop(1_000_000), cop(2_000_000))
    lender = make_lender()

    response = client.get(REQUESTS, params={"scope": "mine"}, headers=auth(merchant))
    assert response.status_code == 200, response.text
    assert_contract(response, "listLoanRequests")
    items = response.json()["items"]
    assert [i["id"] for i in items] == [second["id"], first["id"]]
    assert all(i["merchant"] is None and i["credit"] is None for i in items)
    response = client.get(REQUESTS, params={"scope": "mine", "status": "cancelled"}, headers=auth(merchant))
    assert [i["id"] for i in response.json()["items"]] == [first["id"]]
    assert response.json()["next_offset"] is None

    # Market solo trae `open` (un filtro de otro estado deja la lista vacía).
    response = client.get(REQUESTS, params={"scope": "market", "status": "cancelled"}, headers=auth(lender))
    assert_contract(response, "listLoanRequests")
    assert response.json()["items"] == []

    # Detalle: dueño (con merchant y credit) o prestamista si está `open`.
    for user in (merchant, lender):
        response = client.get(f"{REQUESTS}/{second['id']}", headers=auth(user))
        assert response.status_code == 200, response.text
        assert_contract(response, "getLoanRequest")
        assert response.json()["merchant"]["initials"] == "TL"
        assert response.json()["credit"]["cupo_disponible"] == cop(6_000_000).to_api()
    response = client.get(f"{REQUESTS}/{first['id']}", headers=auth(merchant))
    assert response.status_code == 200 and response.json()["status"] == "cancelled"
    _error(client.get(f"{REQUESTS}/{first['id']}", headers=auth(lender)), 403, "forbidden", "getLoanRequest")
    _error(client.get(f"{REQUESTS}/{second['id']}", headers=auth(make_merchant())), 403, "forbidden",
           "getLoanRequest")
    _error(client.get(f"{REQUESTS}/{second['id']}", headers=auth(make_admin())), 403, "forbidden", "getLoanRequest")
    _error(client.get(f"{REQUESTS}/{uuid.uuid4()}", headers=auth(lender)), 404, "not_found", "getLoanRequest")
    _error(client.post(f"{REQUESTS}/{second['id']}/cancel", headers=auth(lender)), 403, "forbidden",
           "cancelLoanRequest")


def test_matches_lender_many_requests_and_roles(client, db, make_merchant, make_lender, make_admin, auth):
    """R9: el prestamista cruza cada solicitud `open` con sus ofertas, con el cupo de cada negocio."""
    lender = make_lender()
    offer = _offer(db, lender, cop(1_000_000), cop(2_500_000), 1500)
    _offer(db, lender, cop(1_000_000), cop(2_500_000), 1500, status="cancelled")
    big = _eligible(db, make_merchant).user
    small = _eligible(db, make_merchant, cupo=cop(1_800_000)).user
    _request(client, auth, big, cop(1_500_000), cop(3_000_000))
    # Solicitud publicada cuando tenía más cupo: hoy su cupo es $1.800.000 y la propuesta se recorta a ese cupo.
    small_assessment = db.scalar(select(CreditAssessment).where(CreditAssessment.merchant_id == small.id))
    db.add(LoanRequest(merchant_id=small.id, min_amount=cop(1_500_000).value, max_amount=cop(3_000_000).value,
                       asset_code="COP", asset_scale=2, pct_bps=1500, status="open",
                       credit_assessment_id=small_assessment.id))
    db.commit()

    response = client.get(MATCHES, headers=auth(lender))
    assert response.status_code == 200, response.text
    assert_contract(response, "listMatches")
    amounts = {item["request"]["merchant"]["id"]: item["proposal"]["amount"] for item in response.json()["items"]}
    assert amounts == {str(big.id): cop(2_000_000).to_api(), str(small.id): cop(1_800_000).to_api()}
    assert all(item["offer"]["id"] == str(offer.id) for item in response.json()["items"])

    response = client.get(MATCHES, headers=auth(make_lender()))  # sin ofertas
    assert_contract(response, "listMatches")
    assert response.json() == {"items": []}
    response = client.get(MATCHES, headers=auth(make_merchant()))  # sin solicitud
    assert response.json() == {"items": []}
    _error(client.get(MATCHES, headers=auth(make_admin())), 403, "forbidden", "listMatches")
    assert db.scalar(select(CreditAssessment.id).where(CreditAssessment.merchant_id == big.id)) is not None


def test_money_with_thousands_of_digits_is_422(client, db, make_merchant, make_lender, auth):
    """(revisión final) 00-core "Dinero": `int()` fallaba sobre 4300 dígitos (500); más de 19 dígitos → 422."""
    merchant = _eligible(db, make_merchant).user
    lender = make_lender()
    huge = {"value": "9" * 5000, "asset_code": "COP", "asset_scale": 2}
    body = {"min_amount": cop(1_000_000).to_api(), "max_amount": huge, "pct_bps": 1500}
    for url, user, operation, extra in (
        (REQUESTS, merchant, "createLoanRequest", {}),
        (OFFERS, lender, "createLoanOffer", {"fee_bps": 500}),
    ):
        error = _error(client.post(url, json={**body, **extra}, headers=auth(user)), 422, "validation_error", operation)
        assert "demasiado grande" in str(error["details"])
    assert db.scalar(select(LoanRequest.id)) is None and db.scalar(select(LoanOffer.id)) is None
