"""00-core: un test por criterio de aceptación."""

import re
import uuid
from datetime import UTC, date, datetime, timedelta

import httpx
import jwt
import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError
from sqlalchemy import inspect

from app.core import clock
from app.core.config import Settings, get_settings
from app.core.db import engine
from app.core.deps import CurrentUser, Pagination, next_offset, parse_id, require_role
from app.core.errors import AppError
from app.core.money import Money, MoneySchema, bps_of, format_es, require_market_asset
from app.main import create_app
from app.modules.users.schemas import lender_ref, merchant_ref
from tests.helpers import HTTP_METHODS, assert_contract, contract, cop


def _app_with_test_routes() -> FastAPI:
    """App real más rutas de prueba (solo en esta app: la de `client` queda igual al contrato)."""
    app = create_app()
    router = APIRouter(prefix="/api/v1/_test")

    class Body(BaseModel):
        amount: MoneySchema
        note: str

    @router.post("/echo")
    def echo(body: Body) -> dict:
        return {"ok": True}

    @router.get("/me")
    def me(user: CurrentUser) -> dict:
        return {"id": str(user.id)}

    @router.get("/merchant-only", dependencies=[Depends(require_role("merchant"))])
    def merchant_only() -> dict:
        return {"ok": True}

    @router.get("/boom")
    def boom() -> dict:
        raise RuntimeError("falla inesperada")

    @router.get("/page")
    def page(page: Pagination) -> dict:
        return {"limit": page.limit, "offset": page.offset}

    @router.get("/items/{item_id}")
    def item(item_id: str) -> dict:
        return {"id": str(parse_id(item_id))}

    app.include_router(router)
    return app


def _assert_error(response: httpx.Response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    assert isinstance(body["error"]["details"], dict)
    return body["error"]


def test_ac1_app_boots(client):
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert_contract(response, "getHealth")
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["op_client"] == "fake"
    assert body["warnings"] == []
    assert body["time"].endswith("Z")
    assert {"users", "loans", "payouts", "events"} <= set(inspect(engine).get_table_names())


def test_ac2_validation_error_format():
    with TestClient(_app_with_test_routes()) as client:
        invalid = client.post("/api/v1/_test/echo", json={"amount": {"value": 48000.5, "asset_code": "COP"}})
        error = _assert_error(invalid, 422, "validation_error")
        fields = {f["field"] for f in error["details"]["fields"]}
        assert {"amount.value", "amount.asset_scale", "note"} <= fields

        # MoneySchema es estricto: la escala no se convierte desde texto, booleano ni float.
        for scale in ("2", True, 2.0):
            response = client.post(
                "/api/v1/_test/echo", json={"amount": {"value": "100", "asset_code": "COP", "asset_scale": scale},
                                            "note": "x"},
            )
            error = _assert_error(response, 422, "validation_error")
            assert {f["field"] for f in error["details"]["fields"]} == {"amount.asset_scale"}, scale
        valid = {"amount": {"value": "100", "asset_code": "COP", "asset_scale": 2}, "note": "x"}
        assert client.post("/api/v1/_test/echo", json=valid).json() == {"ok": True}

        malformed = client.post(
            "/api/v1/_test/echo", content=b"{no es json", headers={"Content-Type": "application/json"}
        )
        _assert_error(malformed, 422, "validation_error")

        # Los demás errores del framework salen con el mismo formato.
        _assert_error(client.get("/api/v1/no-existe"), 404, "not_found")
        _assert_error(client.delete("/api/v1/health"), 405, "bad_request")


def test_ac2_unhandled_error_format_with_cors():
    with TestClient(_app_with_test_routes(), raise_server_exceptions=False) as client:
        response = client.get("/api/v1/_test/boom", headers={"Origin": "http://localhost:3000"})

    _assert_error(response, 500, "internal_error")
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_ac3_auth_roles(make_merchant, make_lender, auth, freeze):
    merchant, lender = make_merchant(), make_lender()
    with TestClient(_app_with_test_routes()) as client:
        _assert_error(client.get("/api/v1/_test/merchant-only"), 401, "unauthorized")
        _assert_error(client.get("/api/v1/_test/me"), 401, "unauthorized")
        _assert_error(client.get("/api/v1/_test/me", headers={"Authorization": "Bearer basura"}), 401, "unauthorized")
        _assert_error(client.get("/api/v1/_test/me", headers={"Authorization": "Basic abc"}), 401, "unauthorized")

        _assert_error(client.get("/api/v1/_test/merchant-only", headers=auth(lender)), 403, "forbidden")
        assert client.get("/api/v1/_test/merchant-only", headers=auth(merchant)).json() == {"ok": True}
        assert client.get("/api/v1/_test/me", headers=auth(lender)).json() == {"id": str(lender.id)}

        # Claims y vencimiento: sub, role, exp a JWT_EXPIRES_HOURS, medido con clock.now().
        start = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
        freeze(start)
        headers = auth(merchant)
        claims = jwt.decode(headers["Authorization"][7:], options={"verify_signature": False})
        assert claims["sub"] == str(merchant.id)
        assert claims["role"] == "merchant"
        assert claims["exp"] == int((start + timedelta(hours=get_settings().jwt_expires_hours)).timestamp())
        assert client.get("/api/v1/_test/me", headers=headers).status_code == 200
        freeze(start + timedelta(hours=get_settings().jwt_expires_hours, seconds=1))
        _assert_error(client.get("/api/v1/_test/me", headers=headers), 401, "unauthorized")


def test_ac4_money():
    def rejects(value):
        with pytest.raises(AppError) as exc:
            Money.from_api({"value": value, "asset_code": "COP", "asset_scale": 2})
        assert (exc.value.status, exc.value.code) == (422, "validation_error")

    for bad in (48000.5, 48000.0, "48000.5", "4,8", "-100", -100, "abc", "", " 100", "0100", "100\n", 4800000, None):
        rejects(bad)
    for bad_asset in ({"asset_code": "", "asset_scale": 2}, {"asset_code": "COP", "asset_scale": -1},
                      {"asset_code": "COP", "asset_scale": "2"}, {"asset_code": "COP"}):
        with pytest.raises(AppError):
            Money.from_api({"value": "100", **bad_asset})

    money = Money.from_api({"value": "4800000", "asset_code": "COP", "asset_scale": 2})
    assert money == Money(4_800_000, "COP", 2) == cop(48_000)
    assert money.to_api() == {"value": "4800000", "asset_code": "COP", "asset_scale": 2}
    assert Money.from_api({"value": "0", "asset_code": "COP", "asset_scale": 2}).value == 0
    assert cop("130,90") == Money(13_090, "COP", 2)

    assert bps_of(4_800_000, 1500) == 720_000
    assert bps_of(999, 1500) == 149  # floor

    assert format_es(cop(48_000)) == "$48.000"
    assert format_es(cop("130,90")) == "$130,90"
    assert format_es(cop("7.069,10")) == "$7.069,10"
    assert format_es(cop(1_590_000)) == "$1.590.000"
    assert format_es(cop(0)) == "$0"

    for bad_value in (1.5, True):
        with pytest.raises(TypeError):
            Money(bad_value, "COP", 2)
    with pytest.raises(TypeError):
        Money(100, "COP", 2.0)

    assert require_market_asset(cop(48_000)) == cop(48_000)
    for other in (Money(100, "USD", 2), Money(100, "COP", 0)):
        with pytest.raises(AppError) as exc:
            require_market_asset(other)
        assert (exc.value.status, exc.value.code) == (422, "asset_mismatch")


def test_ac5_local_day(freeze):
    sale_at = datetime(2026, 10, 17, 3, 0, tzinfo=UTC)  # 22:00 del 16 oct en Bogotá

    start, end = clock.local_day_bounds(date(2026, 10, 16))
    assert start == datetime(2026, 10, 16, 5, 0, tzinfo=UTC)
    assert end == datetime(2026, 10, 17, 5, 0, tzinfo=UTC)
    assert start <= sale_at < end
    oct17_start, _ = clock.local_day_bounds(date(2026, 10, 17))
    assert sale_at < oct17_start
    assert clock.local_date(sale_at) == date(2026, 10, 16)

    freeze(sale_at)
    assert clock.now() == sale_at
    assert clock.today_local() == date(2026, 10, 16)

    assert clock.format_hora(datetime(2026, 10, 16, 15, 42, tzinfo=UTC)) == "10:42 a. m."
    assert clock.format_hora(datetime(2026, 10, 16, 17, 5, tzinfo=UTC)) == "12:05 p. m."
    assert clock.format_hora(datetime(2026, 10, 16, 5, 7, tzinfo=UTC)) == "12:07 a. m."
    assert clock.format_hora(datetime(2026, 10, 17, 1, 30, tzinfo=UTC)) == "8:30 p. m."


def _path_params(params: list[dict], document: dict) -> set[str]:
    names = set()
    for param in params:
        if "$ref" in param:
            node = document
            for part in param["$ref"].removeprefix("#/").split("/"):
                node = node[part]
            param = node
        if param.get("in") == "path":
            names.add(param["name"])
    return names


def _routes(document: dict) -> dict[tuple[str, str], set[str]]:
    """{(MÉTODO, ruta): nombres de parámetros de ruta declarados} de un documento OpenAPI."""
    routes = {}
    for path, item in document["paths"].items():
        shared = item.get("parameters", [])
        for method in HTTP_METHODS:
            if method in item:
                declared = _path_params(shared + item[method].get("parameters", []), document)
                routes[(method.upper(), path)] = declared
    return routes


def test_ac6_contract_routes(client):
    yaml_routes = _routes(contract())
    app_routes = _routes(client.app.openapi())
    assert app_routes, "la app no expone rutas"

    for route, params in app_routes.items():
        assert route in yaml_routes, f"{route} no existe en contracts/trust-api.openapi.yaml"
        assert params == set(re.findall(r"{(\w+)}", route[1])), f"{route}: parámetros de ruta {params}"
        assert params == yaml_routes[route], f"{route}: la app declara {params}, el YAML {yaml_routes[route]}"

    # assert_contract valida status y body contra el YAML, resolviendo $ref.
    assert_contract(client.get("/api/v1/health"), "getHealth")

    bad_body = httpx.Response(200, json={"status": "fine", "db": "ok"})
    with pytest.raises(AssertionError, match="no cumple el contrato"):
        assert_contract(bad_body, "getHealth")
    with pytest.raises(AssertionError, match="no declara el status"):
        assert_contract(httpx.Response(418, json={}), "getHealth")
    not_found = {"error": {"code": "not_found", "message": "No encontramos lo que buscas.", "details": {}}}
    assert_contract(httpx.Response(404, json=not_found), "getLoan")  # $ref a components/responses
    with pytest.raises(AssertionError, match="no cumple el contrato"):
        assert_contract(httpx.Response(404, json={"error": {"code": "not_found"}}), "getLoan")
    assert_contract(httpx.Response(200, json={"items": [], "next_offset": None}), "listLoans")
    with pytest.raises(AssertionError, match="no cumple el contrato"):  # $ref anidados a components/schemas
        assert_contract(httpx.Response(200, json={"items": [{"id": "x"}], "next_offset": None}), "listLoans")

    # Los `format` también se validan, `uri` incluido (necesita rfc3986-validator).
    account = {
        "id": str(uuid.uuid4()),
        "wallet_address": "https://ilp.interledger-test.dev/trust-recaudo-1",
        "qr_payload": "https://trustservices.com.co/pagar/abc",
        "asset": {"asset_code": "COP", "asset_scale": 2},
        "status": "active",
        "balance": {"value": "0", "asset_code": "COP", "asset_scale": 2},
        "created_at": "2026-10-17T15:00:00Z",
        "revoked_at": None,
    }
    assert_contract(httpx.Response(200, json=account), "getCollectionAccount")
    for field, bad, fmt in (("wallet_address", "no es una url", "uri"), ("created_at", "ayer", "date-time"),
                            ("id", "x", "uuid")):
        with pytest.raises(AssertionError, match=f"no cumple el contrato:\n- {field}: .* is not a '{fmt}'"):
            assert_contract(httpx.Response(200, json={**account, field: bad}), "getCollectionAccount")


def test_settings_require_secrets():
    for bad in ({"jwt_secret": ""}, {"jwt_secret": "corto"}, {"database_url": ""}):
        values = {"database_url": "postgresql+psycopg://x", "jwt_secret": "s" * 32, **bad}
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **values)
    assert Settings(_env_file=None, database_url="postgresql+psycopg://x", jwt_secret="s" * 32).jwt_secret


def test_api_helpers(make_merchant, make_lender):
    with TestClient(_app_with_test_routes()) as client:
        assert client.get("/api/v1/_test/page").json() == {"limit": 50, "offset": 0}
        assert client.get("/api/v1/_test/page?limit=200&offset=400").json() == {"limit": 200, "offset": 400}
        for query in ("limit=0", "limit=201", "offset=-1", "limit=abc", f"offset={2**63}"):
            _assert_error(client.get(f"/api/v1/_test/page?{query}"), 422, "validation_error")

        item_id = uuid.uuid4()
        assert client.get(f"/api/v1/_test/items/{item_id}").json() == {"id": str(item_id)}
        for bad_id in ("abc", "123", "00000000-0000-0000-0000-00000000000g"):
            _assert_error(client.get(f"/api/v1/_test/items/{bad_id}"), 404, "not_found")

    assert next_offset(0, 50, 50) == 50
    assert next_offset(100, 50, 50) == 150
    assert next_offset(100, 50, 7) is None
    assert next_offset(0, 50, 0) is None

    merchant = make_merchant(business_name="Tienda La Esquina", city="Bucaramanga")
    assert merchant_ref(merchant).model_dump(mode="json") == {
        "id": str(merchant.id), "business_name": "Tienda La Esquina", "city": "Bucaramanga", "initials": "TL",
    }
    assert merchant_ref(make_merchant(business_name="ferretería")).initials == "F"
    assert merchant_ref(make_merchant(business_name="  panadería  el  trigal  ")).initials == "PE"

    lender = make_lender(first_name="Camilo", last_name="Pardo")
    assert lender_ref(lender).model_dump(mode="json") == {"id": str(lender.id), "public_name": "Camilo P."}


@pytest.mark.skip(reason="se activa en la fase final del loop")
def test_ac6_all_contract_routes_implemented(client):
    assert set(_routes(client.app.openapi())) == set(_routes(contract()))
