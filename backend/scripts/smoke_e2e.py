"""Smoke E2E de Trust: arranca la API real y recorre la demo completa por HTTP, validando cada respuesta contra el YAML.

Qué hace
--------
1. Recrea una base vacía (`trust_smoke` por defecto) en el Postgres de `docker compose` con psycopg.
2. Arranca `uvicorn app.main:app` como subproceso en un puerto libre, con el worker cada 1 s, `/dev/*` encendido y
   `OP_CLIENT=fake` (o `http` contra un op-service real o el mock). El subproceso corre en un directorio temporal
   (con `--app-dir backend`), así que un `backend/.env` local no cambia las cifras esperadas. Con `--with-mock`
   arranca antes el mock de op-service (`tools/mock_op_service`) en otro puerto libre, con webhook firmado hacia
   esta API, y corre todo con `OP_CLIENT=http`.
3. Recorre la demo con httpx: health → config → seed → login Marta y Camilo → perfil → recaudo → venta sin crédito
   → cupo → solicitud → ofertas (3 encajan) → matches → preview → crédito → desembolso con la wallet falsa →
   crédito `active` (worker) → 4 ventas con abono → pagos `completed` (worker) → ventas de hoy, abonos y su traza,
   dashboards, notificaciones → lista de espera → dashboard admin.
4. Cada respuesta se valida contra `contracts/trust-api.openapi.yaml` con `tests.helpers.assert_contract` (status
   declarado y body con su schema) y además se revisan las cifras de la demo. Imprime un paso por línea y sale con
   código 1 en el primer fallo (con el final del log del servidor).

Cómo correrlo
-------------
    cd backend
    docker compose up -d db                       # Postgres en localhost:5433 (trust/trust)
    uv run python scripts/smoke_e2e.py            # OP_CLIENT=fake (por defecto)
    uv run python scripts/smoke_e2e.py --with-mock   # OP_CLIENT=http contra el mock (o scripts/smoke_http.sh)

Opciones:
    --with-mock          arranca el mock de op-service (puerto libre, API key y secreto del webhook aleatorios),
                         corre con OP_CLIENT=http, aprueba el desembolso en su página de consentimiento y al final
                         revisa que los 5 webhooks de venta se entregaron (firma aceptada por Trust).
    --op-client http --op-url URL [--op-api-key KEY] [--webhook-secret S]
                         usa op-service real (o el mock). Las ventas llegan por webhook/reconciliación y el link de
                         autorización se sigue con `?decision=approve` esperando un 302 con `op_status`.
    --pg-url URL         Postgres de administración (por defecto postgresql://trust:trust@localhost:5433/postgres).
    --db-name NAME       base que se recrea (por defecto trust_smoke; se borra al final).
    --keep-db            no borra la base al terminar (para inspeccionarla).
    --port N             puerto de la API (por defecto uno libre).
    --timeout S          espera máxima de cada polling (por defecto 60 s).

Códigos de salida: 0 todo bien · 1 falló un paso · 2 no se pudo preparar el entorno (Postgres, uvicorn, mock).
"""

from __future__ import annotations

import argparse
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import psycopg
from psycopg import sql

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings  # noqa: E402
from tests.helpers import assert_contract, assert_op_schema  # noqa: E402

API = "/api/v1"
DEFAULT_PG_URL = "postgresql://trust:trust@localhost:5433/postgres"
TRUST_WALLET = "https://ilp.interledger-test.dev/trust-revenue"
CORS_ORIGIN = "http://localhost:3000"

# Cifras de la demo (specs 03, 05, 07, 10, 13): COP con escala 2, montos en centavos.
CUPO_TOTAL = 600_000_000  # $6.000.000 (grado A)
MATCH_OFFER = ("100000000", "250000000", 1500)  # la oferta del match: $1.000.000–$2.500.000 · 15 %
LOAN_PRINCIPAL = 200_000_000  # punto medio del cruce $1.500.000–$2.500.000
LOAN_FEE_BPS = 600
FIRST_SALE = 48_000  # sin crédito: 100 % al negocio
SALES_WITH_LOAN = (48_000, 26_000, 12_500, 85_000)  # abonos $7.200 + $3.900 + $1.875 + $12.750
ABONOS = (720_000, 390_000, 187_500, 1_275_000)
ABONADO_TOTAL = 2_572_500  # $25.725
PORTFOLIO = 550_000_000  # El Trigal $1.500.000 + Nova $2.000.000 + Marta $2.000.000


class SmokeFailure(Exception):
    """Un paso de la demo no dio lo esperado."""


def cop(pesos: int | float) -> dict[str, Any]:
    """Money COP/2 desde pesos: `cop(12_500)` → `{"value": "1250000", ...}`."""
    return {"value": str(round(pesos * 100)), "asset_code": "COP", "asset_scale": 2}


def check(condition: Any, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def eq(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise SmokeFailure(f"{label}: esperaba {expected!r}, llegó {actual!r}")


def value(money: dict[str, Any]) -> int:
    return int(money["value"])


def fmt(cents: int) -> str:
    """Centavos COP como en la app: `fmt(2543378)` → `$25.433,78`, `fmt(2572500)` → `$25.725`."""
    pesos, rest = divmod(cents, 100)
    text = f"${pesos:,}".replace(",", ".")
    return f"{text},{rest:02d}" if rest else text


# ── Entorno: base de datos y servidor ─────────────────────────────────────────


def recreate_database(pg_url: str, name: str) -> None:
    with psycopg.connect(pg_url, autocommit=True, connect_timeout=5) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


def drop_database(pg_url: str, name: str) -> None:
    with psycopg.connect(pg_url, autocommit=True, connect_timeout=5) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


def database_url(pg_url: str, name: str) -> str:
    parts = urlsplit(pg_url)
    return parts._replace(scheme="postgresql+psycopg", path=f"/{name}").geturl()


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Server:
    """`uvicorn <app>` en un subproceso, con su log en un archivo temporal (la API Trust o el mock de op-service)."""

    def __init__(self, port: int, env: dict[str, str], app: str = "app.main:app", health_path: str = f"{API}/health",
                 name: str = "uvicorn"):
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}"
        self.env = env
        self.app = app
        self.health_path = health_path
        self.name = name
        self.workdir = Path(tempfile.mkdtemp(prefix=f"trust-smoke-{name}-"))
        self.log_path = self.workdir / f"{name}.log"
        self.proc: subprocess.Popen[bytes] | None = None

    def start(self, timeout: float = 30) -> None:
        log = self.log_path.open("wb")
        cmd = [sys.executable, "-m", "uvicorn", self.app, "--app-dir", str(BACKEND_DIR),
               "--host", "127.0.0.1", "--port", str(self.port), "--workers", "1"]
        # cwd temporal: pydantic-settings lee `.env` del cwd y la demo debe correr solo con este entorno.
        self.proc = subprocess.Popen(cmd, cwd=self.workdir, env=self.env, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"{self.name} terminó al arrancar (código {self.proc.returncode})")
            try:
                if httpx.get(f"{self.base_url}{self.health_path}", timeout=2).status_code in (200, 503):
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
        raise RuntimeError(f"{self.name} no respondió en {timeout:.0f} s")

    def stop(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)

    def log_tail(self, lines: int = 60) -> str:
        try:
            return "\n".join(self.log_path.read_text(errors="replace").splitlines()[-lines:])
        except OSError:
            return "(sin log)"


# ── Cliente HTTP con validación de contrato ───────────────────────────────────


class Api:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.http = httpx.Client(base_url=base_url, timeout=20, follow_redirects=False)
        self.calls = 0

    def call(self, method: str, path: str, operation_id: str | None, expect: int, token: str | None = None,
             **kwargs: Any) -> httpx.Response:
        """Hace la petición, exige el status `expect` y valida status + body contra el YAML (`operation_id`)."""
        headers = dict(kwargs.pop("headers", None) or {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        url = path if path.startswith("http") else f"{API}{path}"
        response = self.http.request(method, url, headers=headers, **kwargs)
        self.calls += 1
        if response.status_code != expect:
            raise SmokeFailure(
                f"{method} {url}: esperaba {expect}, llegó {response.status_code}. Body: {response.text[:800]}"
            )
        if operation_id is not None:
            try:
                assert_contract(response, operation_id)
            except AssertionError as exc:
                raise SmokeFailure(f"{method} {url} no cumple el contrato ({operation_id}): {exc}") from exc
        return response

    def get(self, path: str, operation_id: str | None, token: str | None = None, expect: int = 200,
            **kwargs: Any) -> Any:
        return self.call("GET", path, operation_id, expect, token, **kwargs).json()

    def post(self, path: str, operation_id: str | None, token: str | None = None, expect: int = 200,
             **kwargs: Any) -> Any:
        return self.call("POST", path, operation_id, expect, token, **kwargs).json()


def poll[T](fetch: Callable[[], T], done: Callable[[T], bool], timeout: float, what: str,
            interval: float = 0.5) -> T:
    """Repite `fetch()` (cada respuesta validada) hasta `done(resultado)` o falla al vencer `timeout`."""
    deadline = time.monotonic() + timeout
    while True:
        result = fetch()
        if done(result):
            return result
        if time.monotonic() >= deadline:
            raise SmokeFailure(f"{what}: no ocurrió en {timeout:.0f} s. Último estado: {str(result)[:800]}")
        time.sleep(interval)


class MockOp:
    """El mock de op-service (`tools/mock_op_service`) en un subproceso, para `--with-mock`."""

    APP = "tools.mock_op_service.app:app"
    # Variables que lee el mock (`tools/mock_op_service/config.py`); las del shell no se heredan.
    ENV_VARS = ("INTERNAL_API_KEY", "PUBLIC_BASE_URL", "POOL_WALLET_ADDRESSES", "OP_CLIENT_WALLET_ADDRESS",
                "TRUST_WEBHOOK_URL", "TRUST_WEBHOOK_SECRET", "ALLOWED_RETURN_URL_PREFIXES", "SIM_ENABLED",
                "SIM_MAX_AMOUNT", "INTERACTION_TIMEOUT_SECONDS")

    def __init__(self, port: int, api_port: int, api_key: str, webhook_secret: str):
        self.api_key = api_key
        env = {k: v for k, v in os.environ.items() if k not in self.ENV_VARS and not k.startswith("MOCK_")}
        env.update(
            {
                "INTERNAL_API_KEY": api_key,
                "PUBLIC_BASE_URL": f"http://127.0.0.1:{port}",  # authorization_url = {esto}/mock/consent/{id}
                "TRUST_WEBHOOK_URL": f"http://127.0.0.1:{api_port}{API}/webhooks/puntored/payments",
                "TRUST_WEBHOOK_SECRET": webhook_secret,
                "PYTHONUNBUFFERED": "1",
            }
        )
        self.server = Server(port, env, app=self.APP, health_path="/v1/health", name="mock-op")
        self.base_url = self.server.base_url
        self.http = httpx.Client(base_url=self.base_url, timeout=10, headers={"X-Internal-Api-Key": api_key})


# ── La demo ───────────────────────────────────────────────────────────────────


class Demo:
    def __init__(self, api: Api, op_client: str, timeout: float, mock: MockOp | None = None):
        self.api = api
        self.op_client = op_client
        self.timeout = timeout
        self.mock = mock  # con --with-mock: el mock de op-service (página de consentimiento y webhooks)
        self.step_no = 0
        self.ids: dict[str, str] = {}
        self.tokens: dict[str, str] = {}
        self.sale_ids: list[str] = []
        self.summary: dict[str, Any] = {}
        self.repayments: list[dict[str, Any]] = []

    def step(self, title: str, fn: Callable[[], str | None]) -> None:
        self.step_no += 1
        started = time.monotonic()
        try:
            detail = fn()
        except Exception as exc:
            if not isinstance(exc, SmokeFailure):  # KeyError, error HTTP, etc.: también es un fallo de la demo
                frame = traceback.extract_tb(exc.__traceback__)[-1]
                exc = SmokeFailure(f"{exc!r} en {Path(frame.filename).name}:{frame.lineno} ({frame.line})")
            print(f"[{self.step_no:02d}] FAIL {title} ({time.monotonic() - started:.2f}s)\n     {exc}", flush=True)
            raise exc from None
        suffix = f" · {detail}" if detail else ""
        print(f"[{self.step_no:02d}] OK   {title} ({time.monotonic() - started:.2f}s){suffix}", flush=True)

    def run(self) -> None:
        self.step("GET /health", self.health)
        self.step("GET /config", self.config)
        self.step("POST /dev/seed", self.seed)
        self.step("POST /auth/login (Marta y Camilo)", self.login)
        self.step("GET /me (Marta y Camilo)", self.me)
        self.step("GET /me/collection-account (Marta)", self.collection_account)
        self.step("POST /dev/sales/simulate $48.000 sin crédito", self.first_sale)
        self.step("GET /sales/today (100 % al negocio)", self.sales_today_before_loan)
        self.step("GET /me/credit-profile (Marta)", self.credit_profile)
        self.step("POST /loan-requests $1.500.000–$3.000.000 @15 %", self.loan_request)
        self.step("GET /loan-offers?scope=market", self.offers_market)
        self.step("GET /matches (Marta y Camilo)", self.matches)
        self.step("POST /loans/preview (oferta del match)", self.preview)
        self.step("POST /loans (Marta toma la oferta)", self.create_loan)
        self.step("POST /loans/{id}/disburse {client: web} (Camilo)", self.disburse)
        self.step("Wallet: aprobar el desembolso (302 al return_url)", self.authorize)
        self.step("GET /loans/{id} hasta active (worker)", self.wait_active)
        self.step("POST /dev/sales/simulate × 4 con crédito", self.sales_with_loan)
        self.step("GET /sales/today hasta pagos completed (worker)", self.wait_payouts)
        self.step("GET /sales/today (resumen del día)", self.sales_today_after)
        self.step("GET /repayments (Camilo) y traza de un abono", self.repayments_lender)
        self.step("GET /repayments (Marta) y traza con las 3 patas", self.repayments_merchant)
        self.step("GET /loans/{id} (abonado y entregado)", self.loan_after)
        self.step("GET /dashboard/merchant", self.dashboard_merchant)
        self.step("GET /dashboard/lender", self.dashboard_lender)
        self.step("GET /notifications + read-all (Marta)", self.notifications_marta)
        self.step("GET /notifications + read-all (Camilo)", self.notifications_camilo)
        self.step("POST /waitlist (JSON)", self.waitlist)
        self.step("GET /admin/dashboard (admin@demo.trust)", self.admin_dashboard)
        if self.mock is not None:
            self.step("Mock op-service: ventas simuladas y webhooks entregados", self.mock_webhooks)

    # 1. Plataforma y seed

    def health(self) -> str:
        response = self.api.call("GET", "/health", "getHealth", 200, headers={"Origin": CORS_ORIGIN})
        body = response.json()
        eq(body["status"], "ok", "health.status")
        eq((body["db"], body["op"]), ("ok", "ok"), "health db/op")
        eq(body["op_client"], self.op_client, "health.op_client")
        eq(body["warnings"], [], "health.warnings")
        eq(response.headers.get("access-control-allow-origin"), CORS_ORIGIN, "CORS allow-origin")
        return f"status=ok op_client={body['op_client']} version={body['version']}"

    def config(self) -> str:
        body = self.api.get("/config", "getConfig")
        eq(body["market_asset"], {"asset_code": "COP", "asset_scale": 2}, "config.market_asset")
        eq(body["pct_options_bps"], [1000, 1500, 2000], "config.pct_options_bps")
        eq(body["dev_endpoints_enabled"], True, "config.dev_endpoints_enabled")
        split = next(s for s in body["example_splits"] if s["pct_bps"] == 1500)
        eq((split["abono"]["value"], split["merchant"]["value"]), ("720000", "4080000"), "config 15 % de $48.000")
        return "COP/2 · 15 % de $48.000 = $7.200 abono / $40.800 negocio"

    def seed(self) -> str:
        body = self.api.post("/dev/seed", "devSeed", json={})
        users = {u["email"]: u for u in body["users"]}
        eq(len(users), 5, "usuarios sembrados")
        for email in ("marta@demo.trust", "camilo@demo.trust", "admin@demo.trust"):
            check(email in users, f"falta {email} en el seed")
        eq(body["open_offers"], 4, "seed.open_offers")
        eq(body["loans"], 2, "seed.loans")
        check(body["seed_sales"] > 0, "el seed no creó historial de ventas")
        self.seeded = users
        self.ids["marta"] = users["marta@demo.trust"]["id"]
        self.ids["camilo"] = users["camilo@demo.trust"]["id"]
        return f"users=5 seed_sales={body['seed_sales']} open_offers=4 loans=2"

    def login_as(self, email: str) -> str:
        password = self.seeded[email]["password"]
        body = self.api.post("/auth/login", "login", json={"email": email, "password": password})
        eq(body["token_type"], "bearer", "login.token_type")
        eq(body["user"]["email"], email, "login.user.email")
        return body["access_token"]

    def login(self) -> str:
        self.tokens["marta"] = self.login_as("marta@demo.trust")
        self.tokens["camilo"] = self.login_as("camilo@demo.trust")
        return "2 tokens bearer"

    def me(self) -> str:
        marta = self.api.get("/me", "getMe", self.tokens["marta"])
        eq((marta["role"], marta["business_name"], marta["first_name"]), ("merchant", "Tienda La Esquina", "Marta"),
           "me de Marta")
        check(marta["payout_wallet"], "Marta sin payout_wallet")
        check(marta["collection_account"] and marta["collection_account"]["status"] == "active",
              "Marta sin cuenta de recaudo activa")
        camilo = self.api.get("/me", "getMe", self.tokens["camilo"])
        eq(camilo["role"], "lender", "rol de Camilo")
        eq((camilo["lender_range"]["min"]["value"], camilo["lender_range"]["max"]["value"]),
           ("100000000", "250000000"), "rango de Camilo")
        self.ids["account_from_me"] = marta["collection_account"]["id"]
        return f"Marta={marta['initials']} ({marta['public_name']}) · Camilo rango $1.000.000–$2.500.000"

    def collection_account(self) -> str:
        body = self.api.get("/me/collection-account", "getCollectionAccount", self.tokens["marta"])
        eq(body["status"], "active", "collection_account.status")
        eq(body["asset"], {"asset_code": "COP", "asset_scale": 2}, "collection_account.asset")
        eq(body["id"], self.ids["account_from_me"], "id de la cuenta (me vs collection-account)")
        self.ids["account"] = body["id"]
        return f"active · {body['wallet_address']}"

    # 2. Venta sin crédito y cupo

    def simulate(self, pesos: int) -> str | None:
        body = self.api.post("/dev/sales/simulate", "devSimulateSale", expect=201,
                             json={"merchant_id": self.ids["marta"], "amount": cop(pesos), "payer_bank": "Bancolombia"})
        if self.op_client == "fake":
            eq(body["processed"], True, "simulate.processed (fake)")
            check(body["sale_id"], "simulate sin sale_id con el fake")
        return body["sale_id"]

    def sales_today(self) -> dict[str, Any]:
        return self.api.get("/sales/today", "getSalesToday", self.tokens["marta"])

    def wait_sales_count(self, count: int) -> dict[str, Any]:
        return poll(self.sales_today, lambda b: b["summary"]["sales_count"] >= count, self.timeout,
                    f"ventas de hoy = {count}")

    def first_sale(self) -> str:
        sale_id = self.simulate(FIRST_SALE)
        return f"sale_id={sale_id}" if sale_id else "processed=false · la venta llega por webhook o reconciliación"

    def sales_today_before_loan(self) -> str:
        body = self.wait_sales_count(1)
        s = body["summary"]
        eq(s["sales_count"], 1, "ventas de hoy")
        eq((value(s["sales_total"]), value(s["merchant_total"]), value(s["abonado_total"])), (4_800_000, 4_800_000, 0),
           "resumen sin crédito (total, negocio, abonado)")
        eq(body["rule"], {"merchant_bps": 10000, "lender_bps": 0}, "regla sin crédito")
        eq(len(body["rows"]), 1, "filas de hoy")
        eq(value(body["rows"][0]["abono_amount"]), 0, "abono de la venta sin crédito")
        return f"$48.000 → $48.000 al negocio, abono $0 (pagos: {body['rows'][0]['payout_status']})"

    def credit_profile(self) -> str:
        body = self.api.get("/me/credit-profile", "getMyCreditProfile", self.tokens["marta"])
        eq(body["eligible"], True, "credit_profile.eligible")
        eq(value(body["cupo_total"]), CUPO_TOTAL, "cupo_total")
        eq(value(body["cupo_disponible"]), CUPO_TOTAL, "cupo_disponible")
        return f"elegible · grado {body['grade']} · score {body['score']} · cupo $6.000.000"

    # 3. Mercado

    def loan_request(self) -> str:
        body = self.api.post("/loan-requests", "createLoanRequest", self.tokens["marta"], expect=201,
                             json={"min_amount": cop(1_500_000), "max_amount": cop(3_000_000), "pct_bps": 1500})
        eq(body["status"], "open", "loan_request.status")
        eq((value(body["min_amount"]), value(body["max_amount"]), body["pct_bps"]), (150_000_000, 300_000_000, 1500),
           "rango de la solicitud")
        self.ids["request"] = body["id"]
        return f"request_id={body['id']}"

    def offers_market(self) -> str:
        body = self.api.get("/loan-offers", "listLoanOffers", self.tokens["marta"], params={"scope": "market"})
        eq(body["count"], 3, "ofertas que encajan (count)")
        fitting = [o for o in body["items"] if o["proposal"] is not None]
        eq(len(fitting), 3, "ofertas con proposal")
        match = [o for o in body["items"]
                 if (o["min_amount"]["value"], o["max_amount"]["value"], o["pct_bps"]) == MATCH_OFFER]
        eq(len(match), 1, "oferta $1.000.000–$2.500.000 @15 % de Camilo")
        offer = match[0]
        eq(offer["lender"]["id"], self.ids["camilo"], "dueño de la oferta del match")
        eq(value(offer["proposal"]["amount"]), LOAN_PRINCIPAL, "proposal.amount")
        self.ids["offer"] = offer["id"]
        return f"count=3 · oferta del match {offer['id']} propone $2.000.000"

    def matches(self) -> str:
        marta = self.api.get("/matches", "listMatches", self.tokens["marta"])["items"]
        eq(len(marta), 3, "matches de Marta")
        check(all(m["request"]["id"] == self.ids["request"] for m in marta), "match con otra solicitud")
        check(any(m["offer"]["id"] == self.ids["offer"] for m in marta), "falta la oferta del match")
        camilo = self.api.get("/matches", "listMatches", self.tokens["camilo"])["items"]
        eq(len(camilo), 3, "matches de Camilo")
        return "Marta 3 · Camilo 3"

    def preview(self) -> str:
        draft = {"offer_id": self.ids["offer"]}
        body = self.api.post("/loans/preview", "previewLoan", self.tokens["marta"], json=draft)
        eq(body["origin"], "match", "preview.origin")
        eq((body["request_id"], body["offer_id"]), (self.ids["request"], self.ids["offer"]), "preview ids")
        terms = body["terms"]
        eq(value(terms["principal"]), LOAN_PRINCIPAL, "terms.principal")
        eq((terms["fee_bps"], terms["pct_initial_bps"]), (LOAN_FEE_BPS, 1500), "terms fee/pct")
        total = LOAN_PRINCIPAL + LOAN_PRINCIPAL * LOAN_FEE_BPS // 10_000
        eq(value(terms["total_repayable"]), total, "terms.total_repayable")
        self.total_repayable = total
        return f"match · $2.000.000 @15 % · cargo 6 % · total {fmt(total)}"

    def create_loan(self) -> str:
        draft = {"offer_id": self.ids["offer"]}
        body = self.api.post("/loans", "createLoan", self.tokens["marta"], expect=201, json=draft)
        eq(body["status"], "pending_disbursement", "loan.status")
        eq(body["origin"], "match", "loan.origin")
        eq(value(body["terms"]["principal"]), LOAN_PRINCIPAL, "loan principal")
        eq(body["next_action"], {"type": "disburse", "actor": "lender"}, "loan.next_action")
        self.ids["loan"] = body["id"]
        return f"loan_id={body['id']} · pending_disbursement"

    # 4. Desembolso

    def disburse(self) -> str:
        body = self.api.post(f"/loans/{self.ids['loan']}/disburse", "disburseLoan", self.tokens["camilo"],
                             json={"client": "web"})
        eq(body["status"], "disbursing", "disburse.status")
        url = body["authorization_url"]
        if self.op_client == "fake":
            prefix = f"{self.api.base_url}{API}/dev/fake-wallet/authorize/"
            check(url.startswith(prefix), f"authorization_url {url!r} no es la wallet falsa de esta API ({prefix})")
        elif self.mock is not None:
            prefix = f"{self.mock.base_url}/mock/consent/"
            check(url.startswith(prefix), f"authorization_url {url!r} no es la página de consentimiento del mock")
        self.authorization_url = url
        return f"authorization_url={url}"

    def authorize(self) -> str:
        url = self.authorization_url
        operation = "devFakeWalletAuthorize" if self.op_client == "fake" else None
        if self.op_client == "fake" or self.mock is not None:  # wallet falsa de Trust o consentimiento del mock
            page = self.api.call("GET", url, operation, 200)
            check("Aprobar" in page.text and "Rechazar" in page.text, "la wallet no muestra Aprobar/Rechazar")
        response = self.api.call("GET", url, operation, 302, params={"decision": "approve"})
        location = response.headers.get("location", "")
        query = {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}
        eq(query.get("op_status"), "processing", "op_status en el return_url")
        eq(query.get("op_resource"), "payment", "op_resource en el return_url")
        eq((query.get("loan_id"), query.get("client")), (self.ids["loan"], "web"), "loan_id/client en el return_url")
        check(query.get("op_id"), "return_url sin op_id")
        check(urlsplit(location).path.endswith("/op/return"), f"return_url inesperado: {location}")
        return f"302 → {location}"

    def get_loan(self, who: str = "camilo") -> dict[str, Any]:
        return self.api.get(f"/loans/{self.ids['loan']}", "getLoan", self.tokens[who])

    def wait_active(self) -> str:
        def done(loan: dict[str, Any]) -> bool:
            if loan["status"] not in ("disbursing", "active"):
                raise SmokeFailure(f"el crédito pasó a {loan['status']} esperando active: {loan}")
            return loan["status"] == "active"

        loan = poll(self.get_loan, done, self.timeout, "crédito active")
        eq(loan["disbursement"]["status"], "completed", "disbursement.status")
        check(loan["activated_at"], "crédito active sin activated_at")
        eq(loan["pct_bps"], 1500, "pct vigente")
        check(loan["period"] is not None, "crédito active sin periodo")
        return f"active desde {loan['activated_at']}"

    # 5. Ventas con crédito y pagos

    def sales_with_loan(self) -> str:
        for pesos in SALES_WITH_LOAN:
            sale_id = self.simulate(pesos)
            if sale_id:
                self.sale_ids.append(sale_id)
        return "$48.000, $26.000, $12.500, $85.000"

    def wait_payouts(self) -> str:
        expected = 1 + len(SALES_WITH_LOAN)

        def done(body: dict[str, Any]) -> bool:
            statuses = [row["payout_status"] for row in body["rows"]]
            bad = [s for s in statuses if s in ("failed", "held")]
            if bad:
                raise SmokeFailure(f"pagos {bad} en las ventas de hoy: {body['rows']}")
            return body["summary"]["sales_count"] == expected and statuses == ["completed"] * expected

        started = time.monotonic()
        body = poll(self.sales_today, done, self.timeout, "pagos completed en las 5 ventas")
        self.today = body
        return f"5 ventas con pagos completed en {time.monotonic() - started:.1f}s"

    def sales_today_after(self) -> str:
        body = self.sales_today()
        s = body["summary"]
        self.summary = s
        total = (FIRST_SALE + sum(SALES_WITH_LOAN)) * 100
        eq(s["sales_count"], 5, "ventas de hoy")
        eq(value(s["sales_total"]), total, "sales_total")
        eq(value(s["abonado_total"]), ABONADO_TOTAL, "abonado_total ($25.725)")
        eq(value(s["merchant_total"]), total - ABONADO_TOTAL, "merchant_total")
        eq(body["wallet_today"], s["merchant_total"], "wallet_today")
        eq(body["rule"], {"merchant_bps": 8500, "lender_bps": 1500}, "regla con crédito")
        abonos = sorted(value(r["abono_amount"]) for r in body["rows"])
        eq(abonos, sorted([0, *ABONOS]), "abonos por venta")
        for row in body["rows"]:
            eq(value(row["merchant_amount"]) + value(row["abono_amount"]), value(row["amount"]), "negocio + abono")
        # Con `since` = next_since no hay filas nuevas, pero el resumen sigue siendo del día completo.
        since = {"since": body["next_since"]}
        again = self.api.get("/sales/today", "getSalesToday", self.tokens["marta"], params=since)
        eq(again["rows"], [], "filas con since = next_since")
        eq(again["summary"], s, "resumen con since")
        return "$219.500 en 5 ventas · negocio $193.775 · abonado $25.725 · regla 85/15"

    def repayments_lender(self) -> str:
        query = {"loan_id": self.ids["loan"]}
        body = self.api.get("/repayments", "listRepayments", self.tokens["camilo"], params=query)
        items = body["items"]
        eq(sorted(value(r["abono"]) for r in items), sorted(ABONOS), "abonos de Camilo")
        for r in items:
            eq(value(r["trust_cut"]) + value(r["lender_net"]), value(r["abono"]), "trust_cut + lender_net = abono")
            eq(r["delivery"]["status"], "completed", f"entrega del abono {r['id']}")
            eq(r["pct_applied_bps"], 1500, "pct aplicado")
        self.repayments = items
        first = next(r for r in items if value(r["abono"]) == ABONOS[0])
        trace = self.api.get(f"/repayments/{first['id']}", "getRepaymentTrace", self.tokens["camilo"])
        eq(value(trace["sale"]["amount"]), 4_800_000, "traza: venta")
        eq(value(trace["repayment"]["abono"]), ABONOS[0], "traza: abono")
        legs = {p["leg"]: p for p in trace["payouts"]}
        check("merchant" not in legs, "el prestamista no debe ver la pata merchant")
        eq(legs["lender"]["status"], "completed", "traza: pata lender")
        eq(value(legs["lender"]["amount"]), value(first["lender_net"]), "traza: monto de la pata lender")
        eq(trace["loan"]["id"], self.ids["loan"], "traza: crédito")
        eq(value(trace["loan"]["allocated_total"]), ABONADO_TOTAL, "traza: allocated_total")
        net = sum(value(r["lender_net"]) for r in items)
        trust = sum(value(r["trust_cut"]) for r in items)
        self.lender_net_total = net
        self.trust_cut_total = trust
        return f"4 abonos completed (neto {fmt(net)}, parte Trust {fmt(trust)}) · traza $48.000 → patas {sorted(legs)}"

    def repayments_merchant(self) -> str:
        body = self.api.get("/repayments", "listRepayments", self.tokens["marta"])
        eq(sorted(r["id"] for r in body["items"]), sorted(r["id"] for r in self.repayments), "abonos de Marta")
        target = next(r for r in self.repayments if value(r["abono"]) == ABONOS[-1])
        trace = self.api.get(f"/repayments/{target['id']}", "getRepaymentTrace", self.tokens["marta"])
        legs = {p["leg"]: p for p in trace["payouts"]}
        check({"lender", "merchant"} <= set(legs), f"el comercio debe ver lender y merchant: {sorted(legs)}")
        eq(value(legs["merchant"]["amount"]), 8_500_000 - ABONOS[-1], "pata merchant de $85.000")
        check(all(p["status"] == "completed" for p in trace["payouts"]), "pagos de la traza sin completar")
        return f"4 abonos · traza $85.000 → patas {sorted(legs)}"

    def loan_after(self) -> str:
        loan = self.get_loan("marta")
        eq(loan["status"], "active", "loan.status")
        eq(value(loan["allocated"]), ABONADO_TOTAL, "loan.allocated")
        eq(value(loan["outstanding"]), self.total_repayable - ABONADO_TOTAL, "loan.outstanding")
        eq(loan["pct_paid_bps"], ABONADO_TOTAL * 10_000 // self.total_repayable, "loan.pct_paid_bps")
        delivered = value(loan["delivered"])
        check(self.lender_net_total <= delivered <= ABONADO_TOTAL,
              f"delivered {delivered} fuera de [neto prestamista {self.lender_net_total}, abonado {ABONADO_TOTAL}]")
        eq(value(loan["period"]["allocated_in_period"]), ABONADO_TOTAL, "period.allocated_in_period")
        return f"abonado $25.725 · entregado {fmt(delivered)} · avance {loan['pct_paid_bps']} bps"

    # 6. Dashboards y notificaciones

    def dashboard_merchant(self) -> str:
        body = self.api.get("/dashboard/merchant", "getMerchantDashboard", self.tokens["marta"])
        eq(body["today"], self.summary, "dashboard.today vs /sales/today")
        active = body["active_loan"]
        check(active, "dashboard sin crédito activo")
        eq((active["id"], active["status"]), (self.ids["loan"], "active"), "active_loan")
        eq(value(active["allocated"]), ABONADO_TOTAL, "active_loan.allocated")
        eq(value(body["credit"]["cupo_total"]), CUPO_TOTAL, "credit.cupo_total")
        eq(value(body["credit"]["cupo_disponible"]), CUPO_TOTAL - LOAN_PRINCIPAL, "credit.cupo_disponible")
        eq(body["collection_account"]["status"], "active", "collection_account")
        eq(body["offers_fit_count"], 3, "offers_fit_count")
        eq(body["next_action"], "view_loan", "next_action")
        return (f"Hola, {body['greeting']['first_name']} · cupo $4.000.000 de $6.000.000 · "
                f"{body['offers_fit_count']} ofertas para ti · {body['unread_notifications']} sin leer")

    def dashboard_lender(self) -> str:
        body = self.api.get("/dashboard/lender", "getLenderDashboard", self.tokens["camilo"])
        eq(body["active_loans_count"], 3, "active_loans_count")
        totals = body["totals"][0]
        eq((totals["asset_code"], totals["asset_scale"]), ("COP", 2), "totals[0] asset")
        eq(value(totals["total_prestado"]), PORTFOLIO, "total_prestado ($5.500.000)")
        eq(value(totals["abonos_hoy"]["gross"]), ABONADO_TOTAL, "abonos_hoy.gross")
        eq(value(totals["abonos_hoy"]["net"]), self.lender_net_total, "abonos_hoy.net")
        eq(value(totals["recibido_hoy"]), self.lender_net_total, "recibido_hoy")
        loan = next((x for x in body["loans"] if x["id"] == self.ids["loan"]), None)
        check(loan, "el crédito de Marta no aparece en el portafolio")
        eq(value(loan["today_gross"]), ABONADO_TOTAL, "today_gross del crédito de Marta")
        check(body["abonos_por_hora"], "abonos_por_hora vacío")
        eq(sum(value(h["amount"]) for h in body["abonos_por_hora"]), ABONADO_TOTAL, "suma de abonos_por_hora")
        mine = {r["id"] for r in self.repayments}
        eq({r["id"] for r in body["last_repayments"][:4]}, mine, "últimos abonos")
        return f"3 créditos · prestado $5.500.000 · abonos hoy $25.725 · recibido {fmt(self.lender_net_total)}"

    def notifications(self, who: str) -> tuple[dict[str, Any], Counter[str]]:
        body = self.api.get("/notifications", "listNotifications", self.tokens[who])
        types = Counter(n["type"] for n in body["items"])
        eq(body["unread_count"], sum(1 for n in body["items"] if n["read_at"] is None), f"unread_count de {who}")
        return body, types

    def read_all(self, who: str, unread: int) -> None:
        body = self.api.post("/notifications/read-all", "readAllNotifications", self.tokens[who])
        eq((body["unread_count"], body["updated"]), (0, unread), f"read-all de {who}")
        after = self.api.get("/notifications", "listNotifications", self.tokens[who])
        eq(after["unread_count"], 0, f"unread_count de {who} tras read-all")
        check(all(n["read_at"] for n in after["items"]), f"notificaciones de {who} sin leer tras read-all")

    def notifications_marta(self) -> str:
        body, types = self.notifications("marta")
        eq(types["sale_split"], 5, "sale_split de Marta")
        eq(types["loan_active"], 1, "loan_active de Marta")
        check("abono_received" not in types, "Marta no debe recibir abono_received")
        self.read_all("marta", body["unread_count"])
        return f"{dict(types)} · read-all {body['unread_count']} → 0"

    def notifications_camilo(self) -> str:
        body, types = self.notifications("camilo")
        eq(types["abono_received"], 4, "abono_received de Camilo")
        eq(types["loan_accepted"], 1, "loan_accepted de Camilo")
        eq(types["loan_active"], 1, "loan_active de Camilo")
        titles = {n["title"] for n in body["items"] if n["type"] == "abono_received"}
        check("Abono recibido · +$7.200 · Tienda La Esquina" in titles, f"títulos de abono_received: {titles}")
        self.read_all("camilo", body["unread_count"])
        return f"{dict(types)} · read-all {body['unread_count']} → 0"

    # 7. Landing y admin

    def waitlist(self) -> str:
        payload = {"tipo": "comercio", "nombre": "Prueba Smoke", "correo": "smoke@trustservices.com.co",
                   "ciudad": "Medellín"}
        created = self.api.post("/waitlist", "joinWaitlist", expect=201, json=payload)
        eq(created, {"status": "created"}, "waitlist nuevo")
        exists = self.api.post("/waitlist", "joinWaitlist", expect=200, json={**payload, "tipo": "prestamista"})
        eq(exists, {"status": "exists"}, "waitlist repetido")
        return "201 created · 200 exists"

    def admin_dashboard(self) -> str:
        token = self.login_as("admin@demo.trust")
        body = self.api.get("/admin/dashboard", "getAdminDashboard", token)
        loans = body["loans_by_status"]
        eq(loans["active"], 3, "loans_by_status.active")
        payouts = body["payouts_by_status"]
        for status in ("pending", "processing", "failed_final", "held"):
            eq(payouts[status], 0, f"payouts_by_status.{status}")
        check(payouts["completed"] >= 1 + 2 * len(SALES_WITH_LOAN), f"pocos pagos completed: {payouts}")
        eq(body["collection_accounts"], {"active": 3, "revoked": 0}, "collection_accounts")
        eq(body["retained_payouts"], [], "retained_payouts")
        def cop_total(field: str) -> int:
            return sum(value(m) for m in body[field] if (m["asset_code"], m["asset_scale"]) == ("COP", 2))

        # Los créditos sembrados no tienen patas ni parte Trust pendiente (03 R9): todo sale del crédito de Marta.
        revenue, pending = cop_total("trust_revenue"), cop_total("trust_pending")
        eq(revenue + pending, self.trust_cut_total, "trust_revenue + trust_pending = Σ parte Trust de los abonos")
        eq((cop_total("retained_funds"), cop_total("in_flight")), (0, 0), "retained_funds / in_flight")
        return (f"créditos activos 3 · pagos completed {payouts['completed']} · ingresos Trust {fmt(revenue)} · "
                f"pendiente {fmt(pending)}")

    # 8. Lado op-service (solo con --with-mock)

    def mock_webhooks(self) -> str:
        """Las 5 ventas entraron por el webhook firmado (no por la reconciliación): el mock lo marca entregado."""
        assert self.mock is not None

        def fetch() -> dict[str, Any]:
            response = self.mock.http.get("/v1/sim/puntored/qr-payments", params={"after": 0, "limit": 200})
            self.api.calls += 1
            eq(response.status_code, 200, "GET /v1/sim/puntored/qr-payments del mock")
            body = response.json()
            try:
                assert_op_schema(body, "SimQrPaymentList")
            except AssertionError as exc:
                raise SmokeFailure(f"el listado del mock no cumple op-service.openapi.yaml: {exc}") from exc
            return body

        body = poll(fetch, lambda b: all(p["webhook_delivered_at"] for p in b["items"]), 10, "webhooks entregados")
        items = body["items"]
        eq(len(items), 1 + len(SALES_WITH_LOAN), "pagos simulados en el mock")
        eq(sorted(p["amount"]["value"] for p in items),
           sorted(str(pesos * 100) for pesos in (FIRST_SALE, *SALES_WITH_LOAN)), "montos simulados")
        check(all(p["status"] == "completed" for p in items), f"pagos simulados sin completar: {items}")
        return f"{len(items)} pagos Bre-B simulados · {len(items)} webhooks firmados entregados (2xx de Trust)"


# ── main ──────────────────────────────────────────────────────────────────────


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke E2E de la API Trust (demo completa validada contra el YAML)")
    parser.add_argument("--with-mock", action="store_true",
                        help="arranca el mock de op-service y corre con OP_CLIENT=http contra él")
    parser.add_argument("--op-client", choices=("fake", "http"), default="fake")
    parser.add_argument("--op-url", default="", help="URL base de op-service (con --op-client http)")
    parser.add_argument("--op-api-key", default=None,
                        help="X-Internal-Api-Key (por defecto $OP_SERVICE_API_KEY; aleatoria con --with-mock)")
    parser.add_argument("--webhook-secret", default=None, help="secreto de PUNTORED_WEBHOOK_SECRETS (aleatorio)")
    parser.add_argument("--pg-url", default=DEFAULT_PG_URL, help="Postgres de administración (crea/borra la base)")
    parser.add_argument("--db-name", default="trust_smoke")
    parser.add_argument("--keep-db", action="store_true", help="no borrar la base al terminar")
    parser.add_argument("--port", type=int, default=0, help="puerto de la API (0 = uno libre)")
    parser.add_argument("--timeout", type=float, default=60, help="espera máxima de cada polling, en segundos")
    args = parser.parse_args(argv)
    if args.with_mock:
        if args.op_url:
            parser.error("--with-mock arranca su propio op-service: no uses --op-url")
        args.op_client = "http"
        args.op_api_key = args.op_api_key or secrets.token_urlsafe(24)
        args.webhook_secret = args.webhook_secret or secrets.token_hex(32)
    elif args.op_client == "http" and not args.op_url:
        parser.error("--op-client http necesita --op-url")
    if args.op_api_key is None:
        args.op_api_key = os.environ.get("OP_SERVICE_API_KEY", "smoke-op-key")
    if args.db_name in ("trust_dev", "trust_test", "postgres"):
        parser.error(f"--db-name {args.db_name} borraría una base de uso diario; usa una dedicada")
    return args


def server_env(args: argparse.Namespace, port: int) -> dict[str, str]:
    # Ninguna variable de la configuración de la app se hereda del shell: solo cuenta lo de abajo (y los defaults).
    settings_vars = {name.lower() for name in Settings.model_fields}
    env = {k: v for k, v in os.environ.items() if k.lower() not in settings_vars}
    env.update(
        {
            "DATABASE_URL": database_url(args.pg_url, args.db_name),
            "JWT_SECRET": secrets.token_urlsafe(48),
            "CORS_ORIGINS": CORS_ORIGIN,
            "OP_CLIENT": args.op_client,
            "OP_FAKE_AUTO_COMPLETE": "true",
            "DEV_ENDPOINTS_ENABLED": "true",
            "WORKER_ENABLED": "true",
            "WORKER_INTERVAL_SECONDS": "1",
            "TRUST_REVENUE_WALLET_ADDRESS": TRUST_WALLET,
            "PUNTORED_WEBHOOK_SECRETS": args.webhook_secret or secrets.token_hex(32),
            "PUBLIC_API_URL": f"http://127.0.0.1:{port}",  # el authorization_url de la wallet falsa apunta aquí
            "PYTHONUNBUFFERED": "1",
        }
    )
    if args.op_client == "http":
        env["OP_SERVICE_BASE_URL"] = args.op_url
        env["OP_SERVICE_API_KEY"] = args.op_api_key
    return env


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    port = args.port or free_port()
    mock: MockOp | None = None
    if args.with_mock:
        mock = MockOp(free_port(), port, args.op_api_key, args.webhook_secret)
        args.op_url = mock.base_url
    target = " (mock de op-service)" if mock else ""
    print(f"Trust smoke E2E · OP_CLIENT={args.op_client}{target} · base {args.db_name} · puerto {port}", flush=True)
    try:
        recreate_database(args.pg_url, args.db_name)
    except psycopg.Error as exc:
        print(f"No se pudo recrear la base {args.db_name} en {args.pg_url}: {exc}\n"
              "¿Está arriba Postgres? (cd backend && docker compose up -d db)", file=sys.stderr)
        return 2

    server = Server(port, server_env(args, port))
    started = time.monotonic()
    code = 0
    demo: Demo | None = None
    try:
        if mock is not None:  # antes que la API: al arrancar, Trust resuelve su wallet en op-service (03 R1)
            try:
                mock.server.start()
            except RuntimeError as exc:
                print(f"No arrancó el mock: {exc}\n--- log ({mock.server.log_path}) ---\n{mock.server.log_tail()}",
                      file=sys.stderr)
                return 2
            print(f"Mock op-service en {mock.base_url} (log: {mock.server.log_path})", flush=True)
        try:
            server.start()
        except RuntimeError as exc:
            print(f"No arrancó la API: {exc}\n--- log ({server.log_path}) ---\n{server.log_tail()}", file=sys.stderr)
            return 2
        print(f"API en {server.base_url} (log: {server.log_path})", flush=True)
        api = Api(server.base_url)
        demo = Demo(api, args.op_client, args.timeout, mock)
        try:
            demo.run()
        except SmokeFailure:
            print(f"\nFALLÓ el paso {demo.step_no}. Últimas líneas del log del servidor ({server.log_path}):")
            print(server.log_tail())
            if mock is not None:
                print(f"\nÚltimas líneas del log del mock ({mock.server.log_path}):")
                print(mock.server.log_tail())
            code = 1
        else:
            print(f"\nOK · {demo.step_no} pasos · {api.calls} peticiones validadas contra los contratos · "
                  f"{time.monotonic() - started:.1f}s")
    finally:
        server.stop()
        if mock is not None:
            mock.http.close()
            mock.server.stop()
        if not args.keep_db:
            try:
                drop_database(args.pg_url, args.db_name)
            except psycopg.Error as exc:
                print(f"(no se pudo borrar la base {args.db_name}: {exc})", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
