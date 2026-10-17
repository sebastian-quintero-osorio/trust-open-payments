"""04-users: un test por criterio de aceptación (más reglas sin AC propio)."""

import uuid

import jwt
from sqlalchemy import select

from app.core import clock, security
from app.core.events import Event
from app.integrations.op.client import OpError, OpUnavailable
from app.modules.notifications.models import Notification
from app.modules.recaudo.models import CollectionAccount
from app.modules.users.models import User
from tests.helpers import assert_contract, cop
from tests.test_data_model import _loan

WALLET = "https://ilp.interledger-test.dev/tienda"


def _error(response, status: int, code: str, operation_id: str) -> dict:
    assert response.status_code == status, response.text
    assert_contract(response, operation_id)
    error = response.json()["error"]
    assert error["code"] == code
    return error


def _register_body(**fields) -> dict:
    return {
        "email": "nueva@demo.trust",
        "password": "trust2026",
        "role": "merchant",
        "first_name": "Marta",
        "last_name": "Rojas",
        "city": "Bucaramanga",
        "business_name": "Tienda La Esquina",
        **fields,
    }


def _account(db, merchant: User, status: str = "active") -> CollectionAccount:
    op_id = uuid.uuid4()
    account = CollectionAccount(
        merchant_id=merchant.id,
        op_collection_account_id=op_id,
        wallet_address="https://ilp.interledger-test.dev/trust-recaudo-01",
        qr_payload=f"https://pay.trustservices.com.co/qr/{op_id}",
        asset_code="COP",
        asset_scale=2,
        status=status,
    )
    db.add(account)
    db.commit()
    return account


def _range(low, high) -> dict:
    return {"lender_range": {"min": low.to_api(), "max": high.to_api()}}


# ── AC ────────────────────────────────────────────────────────────────────────


def test_ac1_login_ok(client, make_merchant, settings):
    merchant = make_merchant(email="marta@demo.trust", password="trust2026")

    response = client.post("/api/v1/auth/login", json={"email": "marta@demo.trust", "password": "trust2026"})

    assert response.status_code == 200, response.text
    assert_contract(response, "login")
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["role"] == "merchant"
    assert body["user"]["id"] == str(merchant.id)
    claims = jwt.decode(body["access_token"], settings.jwt_secret, algorithms=["HS256"], options={"verify_exp": False})
    assert claims["sub"] == str(merchant.id)
    assert claims["role"] == "merchant"
    assert "exp" in claims
    # El token sirve para /me.
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200 and me.json()["email"] == "marta@demo.trust"


def test_ac2_login_invalid(client, make_merchant, monkeypatch):
    make_merchant(email="marta@demo.trust", password="trust2026")

    wrong_password = client.post("/api/v1/auth/login", json={"email": "marta@demo.trust", "password": "otra-clave"})
    unknown_email = client.post("/api/v1/auth/login", json={"email": "nadie@demo.trust", "password": "trust2026"})

    first = _error(wrong_password, 401, "invalid_credentials", "login")
    second = _error(unknown_email, 401, "invalid_credentials", "login")
    assert first["message"] == second["message"]

    # R1: más de 72 bytes → 401 sin llamar a bcrypt.
    def no_bcrypt(*_args, **_kwargs):
        raise AssertionError("no debe llamar a bcrypt")

    monkeypatch.setattr(security, "verify_password", no_bcrypt)
    too_long = client.post("/api/v1/auth/login", json={"email": "marta@demo.trust", "password": "x" * 73})
    assert _error(too_long, 401, "invalid_credentials", "login")["message"] == first["message"]

    # Forma inválida → 422.
    _error(client.post("/api/v1/auth/login", json={"email": "no-es-correo", "password": "x"}), 422,
           "validation_error", "login")
    _error(client.post("/api/v1/auth/login", json={"email": "marta@demo.trust"}), 422, "validation_error", "login")


def test_ac3_login_case_insensitive(client, make_merchant):
    merchant = make_merchant(email="marta@demo.trust", password="trust2026")

    response = client.post("/api/v1/auth/login", json={"email": "MARTA@Demo.Trust", "password": "trust2026"})

    assert response.status_code == 200, response.text
    assert_contract(response, "login")
    assert response.json()["user"]["id"] == str(merchant.id)
    assert response.json()["user"]["email"] == "marta@demo.trust"


def test_ac4_me_shape(client, db, make_merchant, make_lender, make_admin, auth):
    marta = make_merchant(first_name="Marta", last_name="Rojas", business_name="Tienda La Esquina", wallet=False)
    for read_at in (None, None, clock.now()):
        db.add(Notification(user_id=marta.id, type="sale_split", title="Venta", body="…", data={}, read_at=read_at))
    db.commit()

    response = client.get("/api/v1/me", headers=auth(marta))

    assert response.status_code == 200, response.text
    assert_contract(response, "getMe")
    me = response.json()
    assert me["initials"] == "TL"
    assert me["public_name"] == "Marta R."
    assert me["payout_wallet"] is None
    assert me["collection_account"] is None
    assert me["unread_notifications"] == 2
    assert me["lender_range"] is None
    assert me["business_name"] == "Tienda La Esquina"
    assert (me["role"], me["first_name"], me["last_name"], me["city"]) == ("merchant", "Marta", "Rojas", "Bucaramanga")

    camilo = make_lender(first_name="Camilo", last_name="Pardo")
    lender_me = client.get("/api/v1/me", headers=auth(camilo))
    assert_contract(lender_me, "getMe")
    body = lender_me.json()
    assert body["initials"] == "CP"
    assert body["public_name"] == "Camilo P."
    assert body["business_name"] is None
    assert body["collection_account"] is None
    assert body["payout_wallet"]["asset_code"] == "COP"
    assert body["unread_notifications"] == 0

    admin = client.get("/api/v1/me", headers=auth(make_admin()))
    assert_contract(admin, "getMe")
    assert (admin.json()["initials"], admin.json()["payout_wallet"]) == ("OT", None)

    _error(client.get("/api/v1/me"), 401, "unauthorized", "getMe")


def test_ac5_register_rules(client, db):
    no_business = _register_body()
    del no_business["business_name"]
    error = _error(client.post("/api/v1/auth/register", json=no_business), 422, "validation_error", "register")
    assert error["details"]["fields"][0]["field"] == "business_name"

    _error(client.post("/api/v1/auth/register", json=_register_body(role="admin")), 422, "validation_error",
           "register")

    created = client.post("/api/v1/auth/register", json=_register_body(email="Marta@Demo.Trust"))
    assert created.status_code == 201, created.text
    assert_contract(created, "register")

    _error(client.post("/api/v1/auth/register", json=_register_body(email="MARTA@demo.trust")), 409,
           "already_exists", "register")
    assert db.scalar(select(User).where(User.email == "marta@demo.trust")) is not None
    assert len(db.scalars(select(User)).all()) == 1


def test_ac6_payout_wallet_ok(client, db, make_merchant, auth, fake_op):
    merchant = make_merchant(wallet=False)

    response = client.put(
        "/api/v1/me/payout-wallet", json={"wallet_address": "$ilp.interledger-test.dev/tienda"}, headers=auth(merchant)
    )

    assert response.status_code == 200, response.text
    assert_contract(response, "setPayoutWallet")
    wallet = response.json()["payout_wallet"]
    assert wallet == {"url": WALLET, "public_name": "tienda", "asset_code": "COP", "asset_scale": 2}
    assert [c.args["url"] for c in fake_op.calls_to("resolve_wallet_address")] == ["$ilp.interledger-test.dev/tienda"]
    db.refresh(merchant)
    assert (merchant.payout_wallet_url, merchant.payout_asset_code, merchant.payout_asset_scale) == (WALLET, "COP", 2)
    events = db.scalars(select(Event).where(Event.entity_id == merchant.id)).all()
    assert [e.type for e in events] == ["payout_wallet_set"]

    # R9: repetir es idempotente (mismo resultado, sin evento nuevo).
    again = client.put("/api/v1/me/payout-wallet", json={"wallet_address": WALLET}, headers=auth(merchant))
    assert again.status_code == 200
    assert again.json() == response.json()
    assert len(db.scalars(select(Event).where(Event.entity_id == merchant.id)).all()) == 1


def test_ac7_payout_wallet_asset_rules(client, db, make_merchant, make_lender, auth, fake_op):
    merchant, lender = make_merchant(wallet=False), make_lender(wallet=False)
    usd = "https://ilp.interledger-test.dev/usd-wallet"
    cop0 = "https://ilp.interledger-test.dev/cop-0"
    fake_op.set_wallet(usd, "USD", 2)
    fake_op.set_wallet(cop0, "COP", 0)

    for url in (usd, cop0):
        response = client.put("/api/v1/me/payout-wallet", json={"wallet_address": url}, headers=auth(merchant))
        _error(response, 422, "asset_mismatch", "setPayoutWallet")
    db.refresh(merchant)
    assert merchant.payout_wallet_url is None and merchant.payout_asset_code is None

    response = client.put("/api/v1/me/payout-wallet", json={"wallet_address": usd}, headers=auth(lender))
    assert response.status_code == 200, response.text
    assert_contract(response, "setPayoutWallet")
    assert response.json()["payout_wallet"]["asset_code"] == "USD"
    assert response.json()["payout_wallet"]["asset_scale"] == 2


def test_ac8_payout_wallet_op_errors(client, db, make_merchant, auth, fake_op):
    merchant = make_merchant(wallet=False)
    put = lambda url: client.put("/api/v1/me/payout-wallet", json={"wallet_address": url}, headers=auth(merchant))  # noqa: E731

    fake_op.set_wallet(WALLET, exists=False)
    _error(put(WALLET), 422, "wallet_not_found", "setPayoutWallet")

    fake_op.fail_next("resolve_wallet_address", OpUnavailable("x"))
    _error(put("https://ilp.interledger-test.dev/otra"), 503, "op_unavailable", "setPayoutWallet")

    _error(put("ftp://ilp.interledger-test.dev/otra"), 422, "invalid_url", "setPayoutWallet")
    # R8: cualquier otro OpError (también uno reintentable) → 502.
    fake_op.fail_next("resolve_wallet_address", OpError(400, "validation_error", "url requerida"))
    _error(put("https://ilp.interledger-test.dev/otra"), 502, "op_unavailable", "setPayoutWallet")
    fake_op.fail_next("resolve_wallet_address", OpError(409, "request_in_progress", "", True))
    _error(put("https://ilp.interledger-test.dev/otra"), 502, "op_unavailable", "setPayoutWallet")

    db.refresh(merchant)
    assert merchant.payout_wallet_url is None  # ante cualquier error no se guarda nada


def test_ac9_payout_wallet_blocked_by_loan(client, db, make_merchant, make_lender, make_admin, auth, fake_op):
    merchant, lender = make_merchant(), make_lender()
    account = _account(db, merchant)
    _loan(db, merchant, lender, "pending_disbursement", collection_account_id=account.id)
    db.commit()

    for user in (merchant, lender):
        response = client.put("/api/v1/me/payout-wallet", json={"wallet_address": WALLET}, headers=auth(user))
        _error(response, 409, "active_loan_exists", "setPayoutWallet")
    assert fake_op.calls_to("resolve_wallet_address") == []

    # R7.1: el admin no tiene wallet propia.
    response = client.put("/api/v1/me/payout-wallet", json={"wallet_address": WALLET}, headers=auth(make_admin()))
    _error(response, 403, "forbidden", "setPayoutWallet")
    assert fake_op.calls_to("resolve_wallet_address") == []


def test_ac10_patch_lender_range(client, make_merchant, make_lender, auth):
    camilo = make_lender()

    response = client.patch("/api/v1/me", json=_range(cop(1_000_000), cop(2_500_000)), headers=auth(camilo))

    assert response.status_code == 200, response.text
    assert_contract(response, "updateMe")
    expected = {"min": cop(1_000_000).to_api(), "max": cop(2_500_000).to_api()}
    assert response.json()["lender_range"] == expected
    assert client.get("/api/v1/me", headers=auth(camilo)).json()["lender_range"] == expected

    _error(client.patch("/api/v1/me", json=_range(cop(3_000_000), cop(2_500_000)), headers=auth(camilo)), 422,
           "validation_error", "updateMe")
    merchant = make_merchant()
    _error(client.patch("/api/v1/me", json=_range(cop(1_000_000), cop(2_500_000)), headers=auth(merchant)), 422,
           "validation_error", "updateMe")
    _error(client.patch("/api/v1/me", json={"lender_range": None}, headers=auth(merchant)), 422,
           "validation_error", "updateMe")
    assert client.get("/api/v1/me", headers=auth(camilo)).json()["lender_range"] == expected  # nada cambió


# ── Reglas sin AC propio ──────────────────────────────────────────────────────


def test_register_ok_and_password_bytes(client, db):
    created = client.post("/api/v1/auth/register", json=_register_body(role="lender", business_name="Ignorado"))
    assert created.status_code == 201, created.text
    assert_contract(created, "register")
    body = created.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["role"] == "lender"
    assert body["user"]["business_name"] is None  # R2: ignorado para prestamistas
    assert body["user"]["initials"] == "MR"
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.json()["id"] == body["user"]["id"]

    # R1: de 8 a 72 bytes UTF-8 (no caracteres).
    for password in ("1234567", "x" * 73, "ñ" * 37):
        response = client.post("/api/v1/auth/register", json=_register_body(email="otra@demo.trust", password=password))
        _error(response, 422, "validation_error", "register")
    ok = client.post("/api/v1/auth/register", json=_register_body(email="otra@demo.trust", password="ñ" * 36))
    assert ok.status_code == 201
    login = client.post("/api/v1/auth/login", json={"email": "otra@demo.trust", "password": "ñ" * 36})
    assert login.status_code == 200

    for missing in ("first_name", "last_name", "city", "email", "password", "role"):
        payload = _register_body(email="tercera@demo.trust")
        del payload[missing]
        _error(client.post("/api/v1/auth/register", json=payload), 422, "validation_error", "register")
    _error(client.post("/api/v1/auth/register", json=_register_body(email="tercera@demo.trust", city="  ")), 422,
           "validation_error", "register")


def test_me_collection_account_summary(client, db, make_merchant, make_lender, auth):
    merchant = make_merchant()
    revoked = _account(db, merchant, status="revoked")
    me = client.get("/api/v1/me", headers=auth(merchant))
    assert_contract(me, "getMe")
    assert me.json()["collection_account"] == {
        "id": str(revoked.id), "status": "revoked", "qr_payload": revoked.qr_payload,
    }

    active = _account(db, merchant)
    me = client.get("/api/v1/me", headers=auth(merchant))
    assert_contract(me, "getMe")
    assert me.json()["collection_account"] == {
        "id": str(active.id), "status": "active", "qr_payload": active.qr_payload,
    }
    assert me.json()["payout_wallet"]["url"] == merchant.payout_wallet_url

    lender = make_lender(lender_range_min=100_000_000, lender_range_max=250_000_000)
    body = client.get("/api/v1/me", headers=auth(lender)).json()
    assert body["lender_range"] == {"min": cop(1_000_000).to_api(), "max": cop(2_500_000).to_api()}


def test_patch_me_fields(client, db, make_merchant, make_lender, auth):
    merchant = make_merchant()

    response = client.patch(
        "/api/v1/me",
        json={"first_name": "  Martha ", "last_name": "Rojas Díaz", "city": "Floridablanca",
              "business_name": "Otro nombre", "email": "otro@demo.trust"},
        headers=auth(merchant),
    )

    assert response.status_code == 200, response.text
    assert_contract(response, "updateMe")
    me = response.json()
    assert (me["first_name"], me["last_name"], me["city"]) == ("Martha", "Rojas Díaz", "Floridablanca")
    assert me["business_name"] == "Tienda La Esquina"  # R6: no se edita
    assert me["email"] == merchant.email
    assert me["public_name"] == "Martha R."

    for bad in ({"first_name": ""}, {"city": "   "}, {"last_name": None}):
        _error(client.patch("/api/v1/me", json=bad, headers=auth(merchant)), 422, "validation_error", "updateMe")
    assert client.patch("/api/v1/me", json={}, headers=auth(merchant)).status_code == 200
    _error(client.patch("/api/v1/me", json={"city": "X"}), 401, "unauthorized", "updateMe")

    # Rango: asset de mercado, 0 < min ≤ max, null lo borra.
    camilo = make_lender()
    usd = {"lender_range": {"min": {"value": "100", "asset_code": "USD", "asset_scale": 2},
                            "max": {"value": "200", "asset_code": "USD", "asset_scale": 2}}}
    _error(client.patch("/api/v1/me", json=usd, headers=auth(camilo)), 422, "asset_mismatch", "updateMe")
    _error(client.patch("/api/v1/me", json=_range(cop(0), cop(1_000_000)), headers=auth(camilo)), 422,
           "validation_error", "updateMe")
    _error(client.patch("/api/v1/me", json=_range(cop(1_000_000), cop(10**18)), headers=auth(camilo)), 422,
           "validation_error", "updateMe")  # no cabe en `lender_range_max` (bigint): 422, no 500
    same = client.patch("/api/v1/me", json=_range(cop(1_000_000), cop(1_000_000)), headers=auth(camilo))
    assert same.status_code == 200
    cleared = client.patch("/api/v1/me", json={"lender_range": None}, headers=auth(camilo))
    assert cleared.status_code == 200
    assert_contract(cleared, "updateMe")
    assert cleared.json()["lender_range"] is None
    db.refresh(camilo)
    assert (camilo.lender_range_min, camilo.lender_range_max) == (None, None)


def test_payout_wallet_revalidates_loan_after_go(client, db, make_merchant, make_lender, auth, fake_op, monkeypatch):
    """R7.5: si nace un crédito mientras se resuelve la wallet en Go, no se guarda nada."""
    merchant, lender = make_merchant(), make_lender()
    previous = merchant.payout_wallet_url
    account = _account(db, merchant)
    original = fake_op.resolve_wallet_address

    def resolve_and_create_loan(url):
        result = original(url)
        _loan(db, merchant, lender, "pending_disbursement", collection_account_id=account.id)
        db.commit()
        return result

    monkeypatch.setattr(fake_op, "resolve_wallet_address", resolve_and_create_loan)
    response = client.put("/api/v1/me/payout-wallet", json={"wallet_address": WALLET}, headers=auth(merchant))
    _error(response, 409, "active_loan_exists", "setPayoutWallet")
    db.refresh(merchant)
    assert merchant.payout_wallet_url == previous


# ── Revisión final ────────────────────────────────────────────────────────────


def test_text_fields_reject_nul_and_overlong(client, db, make_merchant, auth):
    """(revisión final) 00-core: Postgres no guarda `\\u0000` en text (el insert daba 500); nombres y negocio ≤ 120,
    ciudad ≤ 80 → 422 `validation_error` en register y en PATCH /me."""
    invalid = [{field: "a\u0000b"} for field in ("first_name", "last_name", "city", "business_name")]
    invalid += [{"first_name": "n" * 121}, {"last_name": "n" * 121}, {"business_name": "n" * 121}, {"city": "c" * 81}]
    for fields in invalid:
        _error(client.post("/api/v1/auth/register", json=_register_body(**fields)), 422, "validation_error", "register")
    assert db.scalar(select(User.id)) is None

    ok = client.post("/api/v1/auth/register", json=_register_body(first_name="n" * 120, city="c" * 80))
    assert ok.status_code == 201, ok.text
    assert_contract(ok, "register")

    merchant = make_merchant()
    for fields in ({"first_name": "x\u0000y"}, {"last_name": "x\u0000"}, {"city": "\u0000"}, {"city": "c" * 81}):
        _error(client.patch("/api/v1/me", json=fields, headers=auth(merchant)), 422, "validation_error", "updateMe")
    db.expire_all()
    assert db.get(User, merchant.id).city == "Bucaramanga"


def test_payout_wallet_rejects_nul_and_overlong_without_go(client, db, make_lender, auth, fake_op):
    """(revisión final) R7: una dirección con `\\u0000` o de más de 2048 caracteres nunca es una URL válida → 422
    `invalid_url` sin llamar a Go (con el fake se resolvía y el evento `payout_wallet_set` daba 500)."""
    lender = make_lender(wallet=False)
    for address in ("$ilp.interledger-test.dev/\u0000", "https://x.dev/a\u0000b", "https://x.dev/" + "a" * 2048):
        response = client.put("/api/v1/me/payout-wallet", json={"wallet_address": address}, headers=auth(lender))
        _error(response, 422, "invalid_url", "setPayoutWallet")
    assert fake_op.calls_to("resolve_wallet_address") == []
    db.expire_all()
    assert db.get(User, lender.id).payout_wallet_url is None


def test_money_input_with_too_many_digits_is_422(client, make_merchant, make_lender, auth):
    """(revisión final) 00-core "Dinero": un `value` de miles de dígitos hacía fallar `int()` (500); más de 19
    dígitos nunca cabe en bigint → 422 `validation_error`."""
    huge = {"value": "9" * 5000, "asset_code": "COP", "asset_scale": 2}
    lender = make_lender()
    response = client.patch("/api/v1/me", json={"lender_range": {"min": cop(1).to_api(), "max": huge}},
                            headers=auth(lender))
    error = _error(response, 422, "validation_error", "updateMe")
    assert "demasiado grande" in str(error["details"])
