"""12-repayments: un test por criterio de aceptación (más reglas sin AC propio)."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.money import Money
from app.modules.sales import service as sales_service
from app.modules.sales.models import Payout, Repayment
from tests.helpers import assert_contract, cop
from tests.test_sales import _ct, _error, _event, _legs

LIST = "/api/v1/repayments"
NOW = datetime(2026, 10, 16, 15, 42, tzinfo=UTC)  # 10:42 a. m. en Bogotá
USD_177 = {"value": "177", "asset_code": "USD", "asset_scale": 2}


@pytest.fixture(autouse=True)
def _frozen(freeze):
    freeze(NOW)


# ── Ayudas ────────────────────────────────────────────────────────────────────


def _repayment(db: Session, ct, amount: Money, paid_at: datetime | None = None) -> Repayment:
    """Venta procesada como en 07 (abono, patas y contabilidad reales) con `paid_at` dado."""
    result = sales_service.process_puntored_event(db, _event(ct.account, amount, occurred_at=paid_at), "puntored")
    db.commit()
    assert result.status == "processed"
    return db.scalar(select(Repayment).where(Repayment.sale_id == result.sale_id))


def _set_leg(db: Session, repayment: Repayment, leg: str, **fields) -> Payout:
    payout = _legs(db, repayment.sale_id)[leg]
    for name, value in fields.items():
        setattr(payout, name, value)
    db.commit()
    return payout


def _list(client, auth, user, **params) -> dict:
    response = client.get(LIST, params=params, headers=auth(user))
    assert response.status_code == 200, response.text
    assert_contract(response, "listRepayments")
    return response.json()


def _trace(client, auth, user, repayment_id) -> dict:
    response = client.get(f"{LIST}/{repayment_id}", headers=auth(user))
    assert response.status_code == 200, response.text
    assert_contract(response, "getRepaymentTrace")
    return response.json()


# ── AC ────────────────────────────────────────────────────────────────────────


def test_ac1_lender_lists_own(client, db, auth, make_merchant, make_lender):
    a = _ct(db, make_merchant, make_lender)
    b = _ct(db, make_merchant, make_lender)
    repayment = _repayment(db, a, cop(48_000))
    _repayment(db, b, cop(26_000))

    body = _list(client, auth, a.lender)

    assert body["next_offset"] is None
    assert body["items"] == [
        {
            "id": str(repayment.id),
            "sale_id": str(repayment.sale_id),
            "loan_id": str(a.loan.id),
            "merchant": {"id": str(a.merchant.id), "business_name": "Tienda La Esquina", "city": "Bucaramanga",
                         "initials": "TL"},
            "lender": {"id": str(a.lender.id), "public_name": "Camilo P."},
            "paid_at": "2026-10-16T15:42:00Z",
            "abono": cop(7_200).to_api(),
            "trust_cut": cop("130,90").to_api(),
            "lender_net": cop("7.069,10").to_api(),
            "pct_applied_bps": 1500,
            "delivery": {"status": "in_progress", "received": None},
        }
    ]


def test_ac2_delivery_status(client, db, auth, make_merchant, make_lender):
    ct = _ct(db, make_merchant, make_lender)
    repayment = _repayment(db, ct, cop(48_000))

    def delivery() -> dict:
        return _list(client, auth, ct.lender)["items"][0]["delivery"]

    _set_leg(db, repayment, "lender", status="completed", sent_total=cop("7.069,10").value, receive_value=177,
             receive_asset_code="USD", receive_asset_scale=2, completed_at=clock.now())
    assert delivery() == {"status": "completed", "received": USD_177}

    _set_leg(db, repayment, "lender", status="held", receive_value=None, receive_asset_code=None,
             receive_asset_scale=None)
    assert delivery() == {"status": "held", "received": None}
    _set_leg(db, repayment, "lender", status="failed_final")
    assert delivery() == {"status": "failed", "received": None}

    # R4: `processing` → in_progress; `skipped` o sin pata lender (abono sembrado) → none.
    _set_leg(db, repayment, "lender", status="processing")
    assert delivery() == {"status": "in_progress", "received": None}
    _set_leg(db, repayment, "lender", status="skipped")
    assert delivery() == {"status": "none", "received": None}
    db.execute(delete(Payout).where(Payout.sale_id == repayment.sale_id, Payout.leg == "lender"))
    db.commit()
    assert delivery() == {"status": "none", "received": None}


def test_ac3_visibility_and_loan_filter(client, db, auth, make_merchant, make_lender, make_admin):
    a = _ct(db, make_merchant, make_lender)
    b = _ct(db, make_merchant, make_lender)
    mine = _repayment(db, a, cop(48_000))
    other = _repayment(db, b, cop(26_000))

    assert [item["id"] for item in _list(client, auth, a.merchant)["items"]] == [str(mine.id)]
    assert [item["id"] for item in _list(client, auth, b.lender)["items"]] == [str(other.id)]
    assert {item["id"] for item in _list(client, auth, make_admin())["items"]} == {str(mine.id), str(other.id)}

    # Filtro por crédito propio (prestamista y negocio); un admin puede filtrar cualquiera.
    assert [i["id"] for i in _list(client, auth, a.lender, loan_id=str(a.loan.id))["items"]] == [str(mine.id)]
    assert [i["id"] for i in _list(client, auth, a.merchant, loan_id=str(a.loan.id))["items"]] == [str(mine.id)]
    assert [i["id"] for i in _list(client, auth, make_admin(), loan_id=str(b.loan.id))["items"]] == [str(other.id)]

    # R1: crédito ajeno → 403; inexistente → 404 (también para el admin); id malformado → 422.
    _error(client.get(LIST, params={"loan_id": str(b.loan.id)}, headers=auth(a.lender)), 403, "forbidden",
           "listRepayments")
    _error(client.get(LIST, params={"loan_id": str(b.loan.id)}, headers=auth(a.merchant)), 403, "forbidden",
           "listRepayments")
    _error(client.get(LIST, params={"loan_id": str(uuid.uuid4())}, headers=auth(a.lender)), 404, "not_found",
           "listRepayments")
    _error(client.get(LIST, params={"loan_id": str(uuid.uuid4())}, headers=auth(make_admin())), 404, "not_found",
           "listRepayments")
    _error(client.get(LIST, params={"loan_id": "no-es-uuid"}, headers=auth(a.lender)), 422, "validation_error",
           "listRepayments")
    _error(client.get(LIST), 401, "unauthorized", "listRepayments")


def test_ac4_local_date_filter(client, db, auth, freeze, make_merchant, make_lender):
    ct = _ct(db, make_merchant, make_lender)
    freeze(datetime(2026, 10, 17, 6, 0, tzinfo=UTC))
    late_on_16 = _repayment(db, ct, cop(48_000), datetime(2026, 10, 17, 3, 0, tzinfo=UTC))  # 22:00 del 16
    _repayment(db, ct, cop(26_000), datetime(2026, 10, 17, 5, 0, tzinfo=UTC))  # 00:00 del 17
    early_on_16 = _repayment(db, ct, cop(12_500), datetime(2026, 10, 16, 5, 0, tzinfo=UTC))  # 00:00 del 16
    _repayment(db, ct, cop(10_000), datetime(2026, 10, 16, 4, 59, tzinfo=UTC))  # 23:59 del 15

    body = _list(client, auth, ct.lender, **{"from": "2026-10-16", "to": "2026-10-16"})
    assert [item["id"] for item in body["items"]] == [str(late_on_16.id), str(early_on_16.id)]
    assert len(_list(client, auth, ct.lender, **{"from": "2026-10-17"})["items"]) == 1
    assert len(_list(client, auth, ct.lender, to="2026-10-15")["items"]) == 1
    assert len(_list(client, auth, ct.lender, to="9999-12-31")["items"]) == 4  # date.max: sin desbordar (500)
    assert _list(client, auth, ct.lender, **{"from": "9999-12-31"})["items"] == []

    _error(client.get(LIST, params={"from": "2026-10-17", "to": "2026-10-16"}, headers=auth(ct.lender)), 422,
           "validation_error", "listRepayments")
    for bad in ("16/10/2026", "1697414400", "2026-10-16T00:00:00", "2026-02-30"):  # solo YYYY-MM-DD válido
        _error(client.get(LIST, params={"from": bad}, headers=auth(ct.lender)), 422, "validation_error",
               "listRepayments")


def test_ac5_trace(client, db, auth, make_merchant, make_lender, make_admin):
    ct = _ct(db, make_merchant, make_lender)
    repayment = _repayment(db, ct, cop(48_000))
    _set_leg(db, repayment, "lender", status="completed", sent_total=cop("7.069,10").value, receive_value=177,
             receive_asset_code="USD", receive_asset_scale=2,
             completed_at=datetime(2026, 10, 16, 15, 42, 9, tzinfo=UTC),
             incoming_payment_url="https://ilp.interledger-test.dev/camilo/incoming-payments/ip-1",
             outgoing_payment_url="https://ilp.interledger-test.dev/trust-recaudo/outgoing-payments/op-1")
    db.refresh(ct.loan)
    ct.loan.delivered_total = cop("7.069,10").value
    db.commit()

    body = _trace(client, auth, ct.lender, repayment.id)

    assert list(body) == ["id", "sale", "repayment", "payouts", "loan"]  # venta → abono → patas → crédito
    assert body["id"] == str(repayment.id)
    assert body["sale"] == {
        "id": str(repayment.sale_id),
        "amount": cop(48_000).to_api(),
        "paid_at": "2026-10-16T15:42:00Z",
        "bre_b_reference": body["sale"]["bre_b_reference"],
        "incoming_payment_url": "https://ilp.interledger-test.dev/incoming-payments/abc",
    }
    assert body["sale"]["bre_b_reference"].startswith("BREB-SIM-")
    assert body["repayment"] == {
        "abono": cop(7_200).to_api(),
        "trust_cut": cop("130,90").to_api(),
        "lender_net": cop("7.069,10").to_api(),
        "pct_applied_bps": 1500,
        "allocated_before": cop(0).to_api(),
        "allocated_after": cop(7_200).to_api(),
    }
    assert [p["leg"] for p in body["payouts"]] == ["lender", "trust"]  # R5: el prestamista ve 2 patas
    lender_leg = body["payouts"][0]
    assert lender_leg["attempt"] == 1 and lender_leg["status"] == "completed"
    assert lender_leg["amount"] == lender_leg["sent_total"] == cop("7.069,10").to_api()
    assert lender_leg["receive"] == USD_177
    assert lender_leg["incoming_payment_url"] == "https://ilp.interledger-test.dev/camilo/incoming-payments/ip-1"
    assert lender_leg["outgoing_payment_url"].endswith("/outgoing-payments/op-1")
    assert (lender_leg["completed_at"], lender_leg["failure_code"]) == ("2026-10-16T15:42:09Z", None)
    trust_leg = body["payouts"][1]
    assert (trust_leg["amount"], trust_leg["sent_total"], trust_leg["status"]) == (
        cop("130,90").to_api(), cop(0).to_api(), "pending",
    )
    assert body["loan"] == {
        "id": str(ct.loan.id),
        "status": "active",
        "pct_bps": 1500,
        "merchant": {"id": str(ct.merchant.id), "business_name": "Tienda La Esquina", "city": "Bucaramanga",
                     "initials": "TL"},
        "lender": {"id": str(ct.lender.id), "public_name": "Camilo P."},
        "principal": cop(2_000_000).to_api(),
        "total_repayable": cop(2_200_000).to_api(),
        "allocated_total": cop(7_200).to_api(),
        "delivered_total": cop("7.069,10").to_api(),
    }

    # El negocio y el admin ven las tres patas, en orden lender → merchant → trust; nunca el receptor.
    for viewer in (ct.merchant, make_admin()):
        trace = _trace(client, auth, viewer, repayment.id)
        assert [p["leg"] for p in trace["payouts"]] == ["lender", "merchant", "trust"]
        assert all("receiver_wallet_url" not in p for p in trace["payouts"])
        assert "receiver_wallet_url" not in str(trace)

    # R1: ajeno → 403; inexistente o id malformado → 404.
    _error(client.get(f"{LIST}/{repayment.id}", headers=auth(make_lender())), 403, "forbidden", "getRepaymentTrace")
    _error(client.get(f"{LIST}/{repayment.id}", headers=auth(make_merchant())), 403, "forbidden", "getRepaymentTrace")
    _error(client.get(f"{LIST}/{uuid.uuid4()}", headers=auth(ct.lender)), 404, "not_found", "getRepaymentTrace")
    _error(client.get(f"{LIST}/no-es-uuid", headers=auth(ct.lender)), 404, "not_found", "getRepaymentTrace")
    _error(client.get(f"{LIST}/{repayment.id}"), 401, "unauthorized", "getRepaymentTrace")


def test_ac6_pagination(client, db, auth, make_merchant, make_lender):
    ct = _ct(db, make_merchant, make_lender)
    oldest = _repayment(db, ct, cop(48_000), datetime(2026, 10, 16, 13, 0, tzinfo=UTC))
    newest = _repayment(db, ct, cop(26_000), datetime(2026, 10, 16, 15, 0, tzinfo=UTC))
    middle = _repayment(db, ct, cop(12_500), datetime(2026, 10, 16, 14, 0, tzinfo=UTC))

    first = _list(client, auth, ct.lender, limit=2)
    assert [item["id"] for item in first["items"]] == [str(newest.id), str(middle.id)]  # paid_at desc
    assert first["next_offset"] == 2
    second = _list(client, auth, ct.lender, limit=2, offset=2)
    assert [item["id"] for item in second["items"]] == [str(oldest.id)]
    assert second["next_offset"] is None

    _error(client.get(LIST, params={"limit": 0}, headers=auth(ct.lender)), 422, "validation_error", "listRepayments")
    _error(client.get(LIST, params={"offset": -1}, headers=auth(ct.lender)), 422, "validation_error",
           "listRepayments")


def test_order_ties_by_id(client, db, auth, make_merchant, make_lender):
    """R2: con el mismo `paid_at`, orden por `id`."""
    ct = _ct(db, make_merchant, make_lender)
    at = datetime(2026, 10, 16, 14, 0, tzinfo=UTC)
    ids = sorted(str(_repayment(db, ct, cop(amount), at).id) for amount in (10_000, 20_000, 30_000))
    assert [item["id"] for item in _list(client, auth, ct.merchant)["items"]] == ids
