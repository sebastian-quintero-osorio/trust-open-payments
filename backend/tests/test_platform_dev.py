"""03-platform-dev: un test por criterio de aceptación (más reglas sin AC propio)."""

import logging
import re
import uuid
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import parse_qsl, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.db import get_db
from app.core.money import Money, bps_of
from app.integrations.op.client import OpError, OpUnavailable, get_op_client
from app.integrations.op.http import HttpOpClient
from app.integrations.op.schemas import CreatePaymentRequest, PositiveAmount
from app.integrations.op.schemas import Health as OpHealth
from app.main import create_app
from app.modules.loans.models import Loan, LoanPctChange
from app.modules.marketplace.models import LoanOffer, LoanRequest
from app.modules.platform import seed as seed_module
from app.modules.platform.lazy import optional_attr
from app.modules.recaudo.models import CollectionAccount
from app.modules.sales.models import Payout, Sale, WebhookEvent
from app.modules.users.models import User
from tests.helpers import assert_contract, cop
from tests.test_data_model import _loan

SEED_EMAILS = ["marta@demo.trust", "trigal@demo.trust", "nova@demo.trust", "camilo@demo.trust", "admin@demo.trust"]
RETURN_URL = "https://app.trustservices.com.co/op/return?client=web&loan_id=L"
NOW = datetime(2026, 10, 17, 15, 42, tzinfo=UTC)  # 10:42 a. m. en Bogotá

_wallet_seq = iter(range(1, 10_000))


@pytest.fixture
def dev(settings, monkeypatch):
    """`DEV_ENDPOINTS_ENABLED=true` para este test."""
    monkeypatch.setattr(settings, "dev_endpoints_enabled", True)


def _error(response, status: int, code: str, operation_id: str) -> dict:
    assert response.status_code == status, response.text
    assert_contract(response, operation_id)
    error = response.json()["error"]
    assert error["code"] == code
    return error


def _account(db: Session, fake_op, merchant: User, status="active", asset_code="COP", asset_scale=2):
    """Cuenta de recaudo insertada directo (sin spec 06) y registrada en el fake."""
    op_id = uuid.uuid4()
    wallet = f"https://ilp.interledger-test.dev/trust-recaudo-t{next(_wallet_seq)}"
    account = CollectionAccount(
        merchant_id=merchant.id,
        op_collection_account_id=op_id,
        wallet_address=wallet,
        qr_payload=f"https://pay.trustservices.com.co/qr/{op_id}",
        asset_code=asset_code,
        asset_scale=asset_scale,
        status=status,
    )
    db.add(account)
    db.commit()
    fake_op.adopt_collection_account(
        op_id, f"merchant:{merchant.id}", wallet, account.qr_payload, asset_code, asset_scale, status,
        display_name=merchant.business_name or "",
    )
    return account


def _seed(client, **body):
    response = client.post("/api/v1/dev/seed", json=body or None)
    assert response.status_code == 200, response.text
    assert_contract(response, "devSeed")
    return response.json()


def _counts(db: Session) -> dict[str, int]:
    return {
        model.__tablename__: db.scalar(select(func.count()).select_from(model))
        for model in (User, CollectionAccount, Sale, LoanOffer, Loan, LoanRequest)
    }


def _user(db: Session, email: str) -> User:
    return db.scalar(select(User).where(User.email == email))


def _payment(fake_op, return_url: str = RETURN_URL):
    body = CreatePaymentRequest(
        sender_wallet_address="https://ilp.interledger-test.dev/camilo",
        receiver_wallet_address="https://ilp.interledger-test.dev/tienda",
        receive_amount=PositiveAmount.from_money(cop(2_000_000)),
        description="Desembolso crédito Trust · Tienda La Esquina",
        external_ref="loan:L:disb",
        return_url=return_url,
    )
    return fake_op.create_payment(body, "loan:L:disb:1")


def _months_back(day: date, months: int) -> tuple[int, int]:
    index = day.year * 12 + day.month - 1 - months
    return index // 12, index % 12 + 1


# ── AC ────────────────────────────────────────────────────────────────────────


def test_ac1_health_ok(client, fake_op, settings):
    response = client.get("/api/v1/health")

    assert response.status_code == 200, response.text
    assert_contract(response, "getHealth")
    body = response.json()
    assert (body["status"], body["db"], body["op"], body["warnings"]) == ("ok", "ok", "ok", [])
    assert body["op_client"] == "fake" and body["version"] == "0.1.0"
    assert len(fake_op.calls_to("health")) == 1
    assert [c.args["url"] for c in fake_op.calls_to("resolve_wallet_address")] == [
        settings.trust_revenue_wallet_address
    ]


def test_ac2_health_degraded(client, fake_op):
    fake_op.fail_next("health", OpUnavailable("down"))

    response = client.get("/api/v1/health")

    assert response.status_code == 200, response.text
    assert_contract(response, "getHealth")
    body = response.json()
    assert (body["status"], body["op"], body["db"], body["warnings"]) == ("degraded", "down", "ok", [])
    assert fake_op.calls_to("resolve_wallet_address") == []  # con Go caído no se resuelve la wallet

    fake_op.fail_next("health", OpError(401, "unauthorized", "API key inválida"))
    body = client.get("/api/v1/health").json()
    assert (body["status"], body["op"]) == ("degraded", "down")

    assert client.get("/api/v1/health").json()["status"] == "ok"  # solo falló la siguiente llamada


def test_ac3_config(client):
    response = client.get("/api/v1/config")

    assert response.status_code == 200, response.text
    assert_contract(response, "getConfig")
    body = response.json()
    assert body["market_asset"] == {"asset_code": "COP", "asset_scale": 2}
    assert body["pct_options_bps"] == [1000, 1500, 2000]
    splits = {s["pct_bps"]: s for s in body["example_splits"]}
    assert splits[1500]["abono"]["value"] == "720000"  # $7.200
    assert splits[1500]["merchant"]["value"] == "4080000"  # $40.800
    assert (splits[1000]["abono"]["value"], splits[1000]["merchant"]["value"]) == ("480000", "4320000")
    assert (splits[2000]["abono"]["value"], splits[2000]["merchant"]["value"]) == ("960000", "3840000")
    assert body["example_sale"] == cop(48_000).to_api()
    assert body["min_loan_amount"] == cop(500_000).to_api()
    assert body["amount_step"] == cop(50_000).to_api()
    assert body["cupo_cap"] == cop(10_000_000).to_api()
    assert (body["default_term_months"], body["timezone"], body["dev_endpoints_enabled"]) == (
        3, "America/Bogota", False,
    )


def test_ac4_dev_disabled(client, db, fake_op):
    _error(client.post("/api/v1/dev/seed"), 404, "not_found", "devSeed")
    _error(client.post("/api/v1/dev/seed", json={"with_active_loan_for_marta": "quizás"}), 404, "not_found", "devSeed")
    assert _counts(db)["users"] == 0

    simulate = {"merchant_id": str(uuid.uuid4()), "amount": cop(48_000).to_api()}
    _error(client.post("/api/v1/dev/sales/simulate", json=simulate), 404, "not_found", "devSimulateSale")
    _error(client.post(f"/api/v1/dev/loans/{uuid.uuid4()}/close-period"), 404, "not_found", "devClosePeriod")
    payment = _payment(fake_op)
    _error(client.get(f"/api/v1/dev/fake-wallet/authorize/{payment.id}?decision=approve", follow_redirects=False),
           404, "not_found", "devFakeWalletAuthorize")
    assert fake_op.payments[0].status == "awaiting_authorization"


def test_ac5_seed_idempotent(client, db, dev, settings):
    first = _seed(client)
    counts = _counts(db)
    second = _seed(client)

    assert [u["email"] for u in first["users"]] == SEED_EMAILS
    assert first["users"] == second["users"]  # mismos ids
    assert first["seed_sales"] == second["seed_sales"]
    assert (first["open_offers"], first["loans"]) == (second["open_offers"], second["loans"])
    assert _counts(db) == counts  # la segunda corrida no crea filas
    assert counts["users"] == 5

    response = client.post("/api/v1/auth/login", json={"email": "marta@demo.trust", "password": settings.demo_password})
    assert response.status_code == 200, response.text
    assert_contract(response, "login")
    assert response.json()["user"]["id"] == first["users"][0]["id"]


def test_ac6_seed_history(client, db, dev, settings):
    _seed(client)

    marta = _user(db, "marta@demo.trust")
    account = db.scalar(
        select(CollectionAccount).where(CollectionAccount.merchant_id == marta.id, CollectionAccount.status == "active")
    )
    assert account is not None
    assert marta.payout_wallet_url == settings.demo_merchant_wallet_url
    _assert_history(db, marta, account, settings)


def test_ac7_seed_offers_no_marta_loan(client, db, dev):
    result = _seed(client)

    assert result["open_offers"] == 4
    assert result["loans"] == 2  # R9: El Trigal y Nova (cada uno con su oferta `taken`)
    camilo, marta = _user(db, "camilo@demo.trust"), _user(db, "marta@demo.trust")
    offers = db.scalars(
        select(LoanOffer).where(LoanOffer.lender_id == camilo.id, LoanOffer.status == "open")
        .order_by(LoanOffer.created_at, LoanOffer.min_amount)
    ).all()
    terms = sorted((o.min_amount, o.max_amount, o.pct_bps, o.fee_bps, o.term_months, o.asset_code, o.asset_scale)
                   for o in offers)
    assert terms == sorted([
        (cop(1_000_000).value, cop(2_500_000).value, 1500, 600, 3, "COP", 2),
        (cop(1_500_000).value, cop(3_000_000).value, 1500, 600, 3, "COP", 2),
        (cop(500_000).value, cop(2_000_000).value, 1000, 600, 3, "COP", 2),
        (cop(1_000_000).value, cop(2_000_000).value, 2000, 600, 3, "COP", 2),
    ])
    assert db.scalar(select(func.count()).select_from(LoanRequest).where(LoanRequest.merchant_id == marta.id)) == 0
    assert db.scalar(select(func.count()).select_from(Loan).where(Loan.merchant_id == marta.id)) == 0


def test_ac8_simulate_sale_fake(client, db, dev, fake_op):
    result = _seed(client)
    marta_id = result["users"][0]["id"]

    response = client.post("/api/v1/dev/sales/simulate", json={"merchant_id": marta_id, "amount": cop(48_000).to_api()})

    assert response.status_code == 201, response.text
    assert_contract(response, "devSimulateSale")
    body = response.json()
    assert body["processed"] is True and body["sale_id"] is not None
    sale = db.get(Sale, uuid.UUID(body["sale_id"]))
    assert (sale.source, sale.amount, sale.bre_b_reference) == ("live", cop(48_000).value, body["bre_b_reference"])
    calls = fake_op.calls_to("create_sim_qr_payment")
    assert len(calls) == 1 and calls[0].idempotency_key.startswith("sim:")
    webhook = db.scalar(select(WebhookEvent).where(WebhookEvent.sale_id == sale.id))
    assert (webhook.source, webhook.status) == ("reconciliation", "processed")  # 03 R11: procesada por 07 R3


def test_ac9_simulate_asset_mismatch(client, db, dev, fake_op, make_merchant):
    merchant = make_merchant()
    _account(db, fake_op, merchant)

    for amount in (Money(4_800_000, "USD", 2), Money(48_000, "COP", 0)):
        response = client.post(
            "/api/v1/dev/sales/simulate", json={"merchant_id": str(merchant.id), "amount": amount.to_api()}
        )
        _error(response, 422, "asset_mismatch", "devSimulateSale")
    assert fake_op.calls_to("create_sim_qr_payment") == []


def test_ac10_fake_wallet_authorize(client, dev, fake_op):
    payment = _payment(fake_op)

    response = client.get(
        f"/api/v1/dev/fake-wallet/authorize/{payment.id}?decision=approve", follow_redirects=False
    )

    assert response.status_code == 302, response.text
    assert_contract(response, "devFakeWalletAuthorize")
    location = urlsplit(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == "https://app.trustservices.com.co/op/return"
    assert dict(parse_qsl(location.query)) == {
        "client": "web",
        "loan_id": "L",
        "op_resource": "payment",
        "op_id": str(payment.id),
        "op_status": "processing",
    }
    assert fake_op.payments[0].status == "processing"
    assert fake_op.calls_to("get_payment") == []  # sin efectos laterales (auto-completar)


def test_ac11_dev_close_period(client, db, dev):
    result = _seed(client, with_active_loan_for_marta=True)
    assert result["loans"] == 3
    # R9/R10: El Trigal ≈ 58 %, Nova ≈ 12 % y Marta ≈ 32 % abonado; `loans.terms` con la forma del Preview (11 R6).
    paid = {}
    for seeded in db.scalars(select(Loan)):
        assert (seeded.status, seeded.origin) == ("active", "offer_taken")
        paid[db.get(User, seeded.merchant_id).email] = seeded.allocated_total * 10_000 // seeded.total_repayable
        assert set(seeded.terms) == {"origin", "request_id", "offer_id", "merchant", "lender", "terms", "example",
                                     "expires_in_hours"}
        assert seeded.terms["offer_id"] == str(seeded.offer_id)
        assert seeded.terms["terms"]["total_repayable"] == Money(seeded.total_repayable, "COP", 2).to_api()
    targets = {"trigal@demo.trust": 5800, "nova@demo.trust": 1200, "marta@demo.trust": 3200}
    assert set(paid) == set(targets)
    assert all(abs(paid[email] - target) <= 5 for email, target in targets.items()), paid
    marta = _user(db, "marta@demo.trust")
    loan = db.scalar(select(Loan).where(Loan.merchant_id == marta.id, Loan.status == "active"))
    before = db.scalar(select(func.count()).select_from(LoanPctChange).where(LoanPctChange.loan_id == loan.id))

    response = client.post(f"/api/v1/dev/loans/{loan.id}/close-period")

    assert response.status_code == 200, response.text
    assert_contract(response, "devClosePeriod")
    after = db.scalar(select(func.count()).select_from(LoanPctChange).where(LoanPctChange.loan_id == loan.id))
    assert after == before + 1
    assert response.json()["allocated_in_period"]["asset_code"] == "COP"


def test_ac12_health_market_asset_mismatch(client, db, fake_op, settings, make_merchant, caplog):
    revenue = settings.trust_revenue_wallet_address

    fake_op.set_wallet(revenue, "COP", 0)
    response = client.get("/api/v1/health")
    assert response.status_code == 200, response.text
    assert_contract(response, "getHealth")
    body = response.json()
    assert (body["status"], body["op"]) == ("degraded", "ok")
    assert body["warnings"] == ["market_asset_mismatch: expected COP/2, wallet COP/0"]

    fake_op.set_wallet(revenue, "COP", 2)
    first, second = make_merchant(), make_merchant()
    for merchant in (first, second):  # dos cuentas en COP/0 → una sola alerta (una por asset distinto)
        _account(db, fake_op, merchant, asset_code="COP", asset_scale=0)
    _account(db, fake_op, make_merchant(), status="revoked", asset_code="USD", asset_scale=2)  # revocada: no cuenta
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert_contract(response, "getHealth")
    assert response.json()["status"] == "degraded"
    assert response.json()["warnings"] == ["market_asset_mismatch: expected COP/2, collection_account COP/0"]

    # Arranque: un ERROR por cada alerta (con la wallet otra vez en COP/0 son dos).
    fake_op.set_wallet(revenue, "COP", 0)
    with caplog.at_level(logging.ERROR, logger="app.modules.platform.service"):
        with TestClient(create_app()):
            pass
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert errors == [
        "market_asset_mismatch: expected COP/2, wallet COP/0",
        "market_asset_mismatch: expected COP/2, collection_account COP/0",
    ]

    # Sin desajustes: ok.
    fake_op.set_wallet(revenue, "COP", 2)
    for account in db.scalars(select(CollectionAccount).where(CollectionAccount.asset_scale == 0)):
        account.status = "revoked"
    db.commit()
    body = client.get("/api/v1/health").json()
    assert (body["status"], body["warnings"]) == ("ok", [])


# ── Reglas sin AC propio ──────────────────────────────────────────────────────


def test_health_op_reports_degraded(client, fake_op, monkeypatch):
    monkeypatch.setattr(fake_op, "health", lambda: OpHealth(status="degraded"))

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert_contract(response, "getHealth")
    assert (response.json()["status"], response.json()["op"]) == ("degraded", "degraded")


def test_health_db_down_returns_503(client):
    broken = create_engine("postgresql+psycopg://trust:trust@127.0.0.1:1/nada", connect_args={"connect_timeout": 2})

    def broken_db():
        session = Session(bind=broken)
        try:
            yield session
        finally:
            session.close()

    client.app.dependency_overrides[get_db] = broken_db
    response = client.get("/api/v1/health")
    broken.dispose()

    assert response.status_code == 503, response.text
    assert_contract(response, "getHealth")
    body = response.json()
    assert (body["status"], body["db"], body["op"], body["warnings"]) == ("down", "down", "ok", [])


def test_health_wallet_check_never_breaks(client, fake_op, settings, monkeypatch, caplog):
    fake_op.fail_next("resolve_wallet_address", OpUnavailable("x"))
    with caplog.at_level(logging.WARNING, logger="app.modules.platform.service"):
        response = client.get("/api/v1/health")
    assert_contract(response, "getHealth")
    body = response.json()
    assert (body["status"], body["warnings"]) == ("ok", [])
    assert any("TRUST_REVENUE_WALLET_ADDRESS" in r.getMessage() for r in caplog.records)

    fake_op.fail_next("resolve_wallet_address", OpError(422, "wallet_not_found", "no existe"))
    assert client.get("/api/v1/health").json()["warnings"] == []

    monkeypatch.setattr(settings, "trust_revenue_wallet_address", "")
    fake_op.calls.clear()
    assert client.get("/api/v1/health").json()["status"] == "ok"
    assert fake_op.calls_to("resolve_wallet_address") == []

    # El arranque tampoco se rompe si Go no responde.
    monkeypatch.setattr(settings, "trust_revenue_wallet_address", "https://ilp.interledger-test.dev/trust-revenue")
    fake_op.fail_next("resolve_wallet_address", OpUnavailable("x"))
    with TestClient(create_app()) as other:
        assert other.get("/api/v1/health").status_code == 200


def test_seed_users_and_wallets(client, db, dev, settings):
    result = _seed(client)

    labels = {u["email"]: (u["role"], u["label"], u["password"]) for u in result["users"]}
    assert labels == {
        "marta@demo.trust": ("merchant", "Tienda La Esquina", "trust2026"),
        "trigal@demo.trust": ("merchant", "Panadería El Trigal", "trust2026"),
        "nova@demo.trust": ("merchant", "Papelería Nova", "trust2026"),
        "camilo@demo.trust": ("lender", "Camilo Pardo", "trust2026"),
        "admin@demo.trust": ("admin", "Operador Trust", "trust2026"),
    }
    marta, trigal, nova = (_user(db, f"{s}@demo.trust") for s in ("marta", "trigal", "nova"))
    camilo, admin = _user(db, "camilo@demo.trust"), _user(db, "admin@demo.trust")
    assert (marta.first_name, marta.last_name, marta.city) == ("Marta", "Rojas", "Bucaramanga")
    assert (trigal.first_name, trigal.last_name) == ("Lucía", "Gómez")
    assert (nova.first_name, nova.last_name, nova.business_name) == ("Andrés", "Niño", "Papelería Nova")
    assert (camilo.city, camilo.business_name, admin.city) == ("Bogotá", None, "Bogotá")
    assert marta.payout_wallet_url == settings.demo_merchant_wallet_url
    assert trigal.payout_wallet_url == "https://ilp.interledger-test.dev/demo-trigal"
    assert nova.payout_wallet_url == "https://ilp.interledger-test.dev/demo-nova"
    assert camilo.payout_wallet_url == settings.demo_lender_wallet_url
    assert admin.payout_wallet_url is None
    for user, name in ((marta, "Tienda La Esquina"), (camilo, "Camilo Pardo")):
        assert (user.payout_asset_code, user.payout_asset_scale, user.payout_wallet_public_name) == ("COP", 2, name)
    assert (camilo.lender_range_min, camilo.lender_range_max) == (cop(1_000_000).value, cop(2_500_000).value)

    for email in SEED_EMAILS:  # todos con DEMO_PASSWORD
        login = client.post("/api/v1/auth/login", json={"email": email, "password": settings.demo_password})
        assert login.status_code == 200, email


def test_seed_assigns_accounts_and_history_idempotent(client, db, dev, fake_op, settings, freeze):
    """R6 + R7: el seed asigna las cuentas con `ensure_collection_account` (06) y siembra el historial; reejecutarlo
    con las cuentas ya existentes no vuelve a llamar a Go ni duplica ventas."""
    freeze(NOW)
    result = _seed(client)
    users = {u["email"].split("@")[0]: uuid.UUID(u["id"]) for u in result["users"]}
    accounts = {
        slug: db.scalar(
            select(CollectionAccount).where(
                CollectionAccount.merchant_id == users[slug], CollectionAccount.status == "active"
            )
        )
        for slug in ("marta", "trigal", "nova")
    }
    assert all(accounts.values())
    assert sorted(c.idempotency_key for c in fake_op.calls_to("create_collection_account")) == sorted(
        f"merchant:{users[slug]}:recaudo" for slug in accounts
    )

    counts = _counts(db)
    assert result["seed_sales"] == counts["sales"] > 1000
    fake_op.calls.clear()
    assert _seed(client)["seed_sales"] == result["seed_sales"]  # idempotente también con historial
    assert _counts(db) == counts
    assert fake_op.calls_to("create_collection_account") == []

    marta = db.get(User, users["marta"])
    _assert_history(db, marta, accounts["marta"], settings)
    for slug, monthly in (("trigal", 10_000_000), ("nova", 12_000_000)):
        _assert_monthly_average(db, users[slug], monthly)


@pytest.mark.parametrize(
    "today", [NOW, datetime(2026, 11, 5, 15, 0, tzinfo=UTC), datetime(2027, 1, 1, 13, 0, tzinfo=UTC)]
)
def test_seed_history_generator_calibration(today):
    """R7 + calibración para R9/R10: promedio exacto de los 3 meses completos y ventas posteriores a la activación
    cuyo abono da ≈ 58 % (Trigal), 12 % (Nova) y 32 % (Marta) del total a pagar, aunque la ventana cruce de mes."""
    clock.freeze(today)
    try:
        activated_at = today - seed_module.LOAN_AGE
        activation_day = clock.local_date(activated_at)
        specs = {spec.slug: spec for spec in (*seed_module.SEED_LOANS, seed_module.MARTA_LOAN)}
        plans = [seed_module.HistoryPlan(slug, seed_module.MONTHLY_SALES[slug], specs[slug].window_total)
                 for slug in seed_module.MERCHANT_SLUGS]
        local_today = clock.today_local()
        history = seed_module.generate_history(plans, local_today, activation_day)
        assert history == seed_module.generate_history(plans, local_today, activation_day)  # determinista

        for slug, sales in history.items():
            monthly = seed_module.MONTHLY_SALES[slug]
            last3 = {_months_back(local_today, n) for n in (1, 2, 3)}
            total = sum(s.amount for s in sales if (clock.local_date(s.paid_at).year,
                                                   clock.local_date(s.paid_at).month) in last3)
            assert total == 3 * monthly
            assert all(s.amount % 100 == 0 and s.amount > 0 for s in sales)
            assert all(clock.local_date(s.paid_at) < local_today for s in sales)
            assert not any(clock.local_date(s.paid_at) == activation_day for s in sales)

            spec = specs[slug]
            total_repayable = spec.principal + bps_of(spec.principal, seed_module.OFFER_FEE_BPS)
            abonos = sum(bps_of(s.amount, spec.pct_bps) for s in sales if s.paid_at >= activated_at)
            target = bps_of(total_repayable, spec.allocated_target_bps)
            assert target <= abonos <= target + 100, slug  # pesos enteros: a lo sumo $1 de diferencia
        expected = {"trigal": cop(922_200).value, "nova": cop(254_400).value, "marta": cop(678_400).value}
        assert {spec.slug: bps_of(spec.principal + bps_of(spec.principal, 600), spec.allocated_target_bps)
                for spec in specs.values()} == expected
    finally:
        clock.unfreeze()


def test_simulate_sale_calls_go_with_sim_key(client, db, dev, fake_op, make_merchant):
    merchant = make_merchant()
    account = _account(db, fake_op, merchant)

    response = client.post(
        "/api/v1/dev/sales/simulate",
        json={"merchant_id": str(merchant.id), "amount": cop(48_000).to_api(), "payer_bank": "Bancolombia"},
    )

    assert response.status_code == 201, response.text
    assert_contract(response, "devSimulateSale")
    body = response.json()
    assert (body["bre_b_reference"], body["status"]) == ("BREB-SIM-000001", "completed")
    calls = fake_op.calls_to("create_sim_qr_payment")
    assert len(calls) == 1
    assert calls[0].idempotency_key.startswith("sim:")
    assert uuid.UUID(calls[0].idempotency_key.removeprefix("sim:"))
    assert calls[0].body.collection_account_id == account.op_collection_account_id
    assert calls[0].body.amount.to_money() == cop(48_000)
    assert calls[0].body.payer_bank == "Bancolombia"
    assert body["sim_payment_id"] == str(fake_op.sim_qr_payments[0].id)
    if optional_attr("app.modules.sales.service", "process_puntored_event") is None:
        assert (body["processed"], body["sale_id"]) == (False, None)

    by_account = client.post(
        "/api/v1/dev/sales/simulate", json={"collection_account_id": str(account.id), "amount": cop(1_000).to_api()}
    )
    assert by_account.status_code == 201, by_account.text
    assert_contract(by_account, "devSimulateSale")
    assert by_account.json()["bre_b_reference"] == "BREB-SIM-000002"
    assert len({c.idempotency_key for c in fake_op.calls_to("create_sim_qr_payment")}) == 2  # clave nueva cada vez


def test_simulate_sale_validation(client, db, dev, fake_op, make_merchant):
    merchant, without_account = make_merchant(), make_merchant()
    account = _account(db, fake_op, merchant)
    revoked = _account(db, fake_op, make_merchant(), status="revoked")
    amount = cop(48_000).to_api()
    post = lambda body: client.post("/api/v1/dev/sales/simulate", json=body)  # noqa: E731

    _error(post({"amount": amount}), 422, "validation_error", "devSimulateSale")
    _error(post({"merchant_id": str(merchant.id), "collection_account_id": str(account.id), "amount": amount}), 422,
           "validation_error", "devSimulateSale")
    _error(post({"merchant_id": str(merchant.id), "amount": cop(0).to_api()}), 422, "validation_error",
           "devSimulateSale")
    _error(post({"merchant_id": str(merchant.id), "amount": amount, "payer_bank": "B" * 61}), 422, "validation_error",
           "devSimulateSale")
    _error(post({"merchant_id": str(merchant.id), "amount": {"value": 48000, "asset_code": "COP", "asset_scale": 2}}),
           422, "validation_error", "devSimulateSale")
    _error(post({"merchant_id": str(without_account.id), "amount": amount}), 404, "not_found", "devSimulateSale")
    _error(post({"merchant_id": str(uuid.uuid4()), "amount": amount}), 404, "not_found", "devSimulateSale")
    _error(post({"collection_account_id": str(uuid.uuid4()), "amount": amount}), 404, "not_found", "devSimulateSale")
    _error(post({"collection_account_id": str(revoked.id), "amount": amount}), 409, "collection_account_not_active",
           "devSimulateSale")
    assert fake_op.calls_to("create_sim_qr_payment") == []


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        (OpError(404, "not_found", "no existe"), 404, "not_found"),
        (OpError(409, "collection_account_not_active", "revocada"), 409, "collection_account_not_active"),
        (OpError(409, "sim_disabled", "apagado"), 409, "sim_disabled"),
        (OpError(409, "sim_payer_not_authorized", "sin grant"), 409, "sim_payer_not_authorized"),
        (OpError(422, "asset_mismatch", "otro asset"), 422, "asset_mismatch"),
        (OpError(422, "invalid_amount", "monto"), 422, "invalid_amount"),
        (OpError(409, "request_in_progress", "", True), 502, "op_unavailable"),
        (OpError(409, "idempotency_conflict", "otra"), 502, "op_unavailable"),
        (OpError(400, "validation_error", "forma"), 502, "op_unavailable"),
        (OpUnavailable("caído"), 503, "op_unavailable"),
    ],
)
def test_simulate_sale_op_errors(client, db, dev, fake_op, make_merchant, failure, status, code):
    merchant = make_merchant()
    _account(db, fake_op, merchant)
    fake_op.fail_next("create_sim_qr_payment", failure)

    response = client.post(
        "/api/v1/dev/sales/simulate", json={"merchant_id": str(merchant.id), "amount": cop(48_000).to_api()}
    )

    _error(response, status, code, "devSimulateSale")
    assert db.scalar(select(func.count()).select_from(Sale)) == 0


def test_fake_wallet_reject_page_and_errors(client, dev, fake_op):
    payment = _payment(fake_op)
    url = f"/api/v1/dev/fake-wallet/authorize/{payment.id}"

    page = client.get(url)
    assert page.status_code == 200
    assert_contract(page, "devFakeWalletAuthorize")
    assert "Aprobar" in page.text and "Rechazar" in page.text and "$2.000.000" in page.text

    rejected = client.get(f"{url}?decision=reject", follow_redirects=False)
    assert rejected.status_code == 302
    assert_contract(rejected, "devFakeWalletAuthorize")
    assert dict(parse_qsl(urlsplit(rejected.headers["location"]).query))["op_status"] == "rejected"
    # Ya no está esperando autorización: no cambia nada y redirige con el estado actual.
    again = client.get(f"{url}?decision=approve", follow_redirects=False)
    assert dict(parse_qsl(urlsplit(again.headers["location"]).query))["op_status"] == "rejected"
    assert fake_op.payments[0].status == "rejected"

    _error(client.get(f"{url}?decision=maybe"), 422, "validation_error", "devFakeWalletAuthorize")
    _error(client.get(f"/api/v1/dev/fake-wallet/authorize/{uuid.uuid4()}?decision=approve"), 404, "not_found",
           "devFakeWalletAuthorize")
    _error(client.get("/api/v1/dev/fake-wallet/authorize/no-es-uuid"), 404, "not_found", "devFakeWalletAuthorize")

    # Sin el cliente falso (OP_CLIENT=http) la wallet falsa no existe.
    http = HttpOpClient(base_url="http://op-service:8080", api_key="k")
    client.app.dependency_overrides[get_op_client] = lambda: http
    try:
        _error(client.get(f"{url}?decision=approve", follow_redirects=False), 404, "not_found",
               "devFakeWalletAuthorize")
    finally:
        client.app.dependency_overrides[get_op_client] = lambda: fake_op
        http.close()


def test_fake_wallet_keeps_existing_query(client, dev, fake_op):
    payment = _payment(fake_op, "https://app.trustservices.com.co/op/return?loan_id=L&client=mobile&op_status=viejo")

    response = client.get(f"/api/v1/dev/fake-wallet/authorize/{payment.id}?decision=approve", follow_redirects=False)

    assert response.status_code == 302
    assert_contract(response, "devFakeWalletAuthorize")
    query = parse_qsl(urlsplit(response.headers["location"]).query)
    assert query == [("loan_id", "L"), ("client", "mobile"), ("op_resource", "payment"), ("op_id", str(payment.id)),
                     ("op_status", "processing")]


def test_close_period_errors(client, db, dev, make_merchant, make_lender):
    _error(client.post("/api/v1/dev/loans/no-es-uuid/close-period"), 404, "not_found", "devClosePeriod")
    _error(client.post(f"/api/v1/dev/loans/{uuid.uuid4()}/close-period"), 404, "not_found", "devClosePeriod")

    loan = _loan(db, make_merchant(), make_lender(), "pending_disbursement")
    db.commit()
    _error(client.post(f"/api/v1/dev/loans/{loan.id}/close-period"), 409, "invalid_state", "devClosePeriod")


# ── Ayudas de aserción ────────────────────────────────────────────────────────


def _monthly_totals(db: Session, merchant_id: uuid.UUID) -> dict[tuple[int, int], int]:
    totals: dict[tuple[int, int], int] = {}
    for paid_at, amount in db.execute(
        select(Sale.paid_at, Sale.amount).where(Sale.merchant_id == merchant_id, Sale.source == "seed")
    ):
        local = clock.local_date(paid_at)
        totals[(local.year, local.month)] = totals.get((local.year, local.month), 0) + amount
    return totals


def _assert_monthly_average(db: Session, merchant_id: uuid.UUID, monthly_pesos: int) -> None:
    totals = _monthly_totals(db, merchant_id)
    today = clock.today_local()
    average = sum(totals.get(_months_back(today, n), 0) for n in (1, 2, 3)) // 3
    target = cop(monthly_pesos).value
    assert abs(average - target) <= target // 100, (average, target)


def _assert_history(db: Session, marta: User, account: CollectionAccount, settings) -> None:
    """AC-6: cuenta activa, wallet propia, ventas seed en ≥ MIN_MONTHS meses, ninguna hoy, promedio de los 3 meses
    completos anteriores $16.000.000 ± 1 % y `ledger_in == 0`."""
    db.refresh(account)
    assert account.status == "active" and account.ledger_in == 0
    assert marta.payout_wallet_url is not None
    sales = db.scalars(select(Sale).where(Sale.merchant_id == marta.id)).all()
    assert sales and all(s.source == "seed" and s.status == "processed" and s.puntored_event_id is None for s in sales)
    assert all(s.collection_account_id == account.id for s in sales)
    today = clock.today_local()
    start, _ = clock.local_day_bounds(today)
    assert not any(s.paid_at >= start for s in sales)  # nada hoy
    first_day = date(*_months_back(today, 4), 1)
    for sale in sales:
        local = clock.to_local(sale.paid_at)
        assert time(7) <= local.time() < time(20)
        assert local.date() >= first_day
        assert re.fullmatch(rf"SEED-marta-{local:%Y%m%d}-\d+", sale.bre_b_reference)
    per_month: dict[tuple[int, int], int] = {}
    for sale in sales:
        local = clock.local_date(sale.paid_at)
        per_month[(local.year, local.month)] = per_month.get((local.year, local.month), 0) + 1
    closed = [m for m, n in per_month.items() if m != (today.year, today.month) and n >= settings.min_sales_per_month]
    assert len(closed) >= settings.min_months
    _assert_monthly_average(db, marta.id, 16_000_000)
    assert db.scalar(select(func.count()).select_from(Payout)) == 0
    assert timedelta(0) <= clock.now() - max(s.paid_at for s in sales) < timedelta(days=5)  # hasta ayer (casi)
