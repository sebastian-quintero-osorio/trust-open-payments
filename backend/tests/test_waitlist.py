"""15-waitlist: un test por criterio de aceptación (más reglas sin AC propio).

Los envíos imitan `landing/src/components/waitlist/WaitlistForm.astro`: con JS, `fetch` con JSON
(`Content-Type: application/json`, `Accept: application/json`) y sin `website`; sin JS, el formulario nativo
urlencoded con `tipo, nombre, correo, ciudad, website` (el honeypot llega vacío).
"""

import threading
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.integrations.op.client import get_op_client
from app.main import create_app
from app.modules.notifications.models import Notification
from app.modules.users.models import User
from app.modules.waitlist import service
from app.modules.waitlist.models import WaitlistEntry
from app.modules.waitlist.router import client_ip
from app.modules.waitlist.schemas import WaitlistRequest
from tests.helpers import assert_contract

URL = "/api/v1/waitlist"
LANDING = "https://trustservices.com.co"
OK = f"{LANDING}/?lista=ok#lista-de-espera"
ERROR = f"{LANDING}/?lista=error#lista-de-espera"
JSON_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}
MARTA = {"tipo": "comercio", "nombre": "Marta Rojas", "correo": "Marta@Ejemplo.com", "ciudad": "Bucaramanga"}


@pytest.fixture(autouse=True)
def _fresh_limiter(settings, monkeypatch):
    """Todos los envíos del TestClient salen de la misma IP: el límite empieza en cero en cada test. El TestClient
    (`testclient`) hace de Caddy: su `X-Forwarded-For` se cree (R7, `TRUSTED_PROXIES`)."""
    monkeypatch.setattr(settings, "trusted_proxies", ["testclient"])
    service.limiter.reset()
    yield
    service.limiter.reset()


def _json(client, body, ip: str | None = None):
    headers = dict(JSON_HEADERS)
    if ip is not None:
        headers["X-Forwarded-For"] = f"{ip}, 10.0.0.1"
    return client.post(URL, json=body, headers=headers)


def _form(client, fields: dict, ip: str | None = None):
    headers = dict(FORM_HEADERS)
    if ip is not None:
        headers["X-Forwarded-For"] = ip
    return client.post(URL, content=urlencode(fields), headers=headers, follow_redirects=False)


def _rows(db: Session) -> list[WaitlistEntry]:
    db.expire_all()
    return list(db.scalars(select(WaitlistEntry).order_by(WaitlistEntry.created_at)))


def _error(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    assert_contract(response, "joinWaitlist")
    error = response.json()["error"]
    assert error["code"] == code and error["message"]
    return error


def _redirect(response, location: str) -> None:
    assert response.status_code == 303, response.text
    assert_contract(response, "joinWaitlist")
    assert response.headers["location"] == location


# ── AC ────────────────────────────────────────────────────────────────────────


def test_ac1_waitlist_created(client, db):
    response = _json(client, {**MARTA, "nombre": "  Marta Rojas ", "ciudad": " Bucaramanga  "})

    assert response.status_code == 201, response.text
    assert_contract(response, "joinWaitlist")
    assert response.json() == {"status": "created"}
    (row,) = _rows(db)
    assert (row.tipo, row.nombre, row.correo, row.ciudad) == (
        "comercio", "Marta Rojas", "marta@ejemplo.com", "Bucaramanga",
    )
    # R8: sin token, sin usuarios ni avisos.
    assert db.scalar(select(func.count()).select_from(User)) == 0
    assert db.scalar(select(func.count()).select_from(Notification)) == 0


def test_ac2_waitlist_upsert_no_409(client, db):
    assert _json(client, MARTA).status_code == 201

    response = _json(client, {**MARTA, "correo": "MARTA@ejemplo.com", "nombre": "Marta R.", "tipo": "prestamista",
                              "ciudad": "Bogotá"})

    assert response.status_code == 200, response.text
    assert_contract(response, "joinWaitlist")
    assert response.json() == {"status": "exists"}
    (row,) = _rows(db)
    assert (row.nombre, row.tipo, row.ciudad, row.correo) == ("Marta R.", "prestamista", "Bogotá", "marta@ejemplo.com")

    # Por formulario también: 303 ok, nunca 409, y sigue habiendo una fila.
    _redirect(_form(client, {**MARTA, "correo": "marta@EJEMPLO.com", "nombre": "Marta", "website": ""}), OK)
    (row,) = _rows(db)
    assert row.nombre == "Marta"


def test_ac3_waitlist_validation(client, db):
    ips = (f"198.51.100.{n}" for n in range(1, 255))  # una IP por envío: el límite de R7 no interviene
    for field, bad in (("tipo", "banco"), ("correo", "marta@")):
        error = _error(_json(client, {**MARTA, field: bad}, ip=next(ips)), 422, "validation_error")
        assert field in {f["field"] for f in error["details"]["fields"]}, error

    invalid = (
        {**MARTA, "nombre": "   "},
        {**MARTA, "nombre": "x" * 121},
        {**MARTA, "ciudad": ""},
        {**MARTA, "ciudad": "y" * 81},
        {**MARTA, "correo": "a" * 250 + "@ejemplo.com"},
        {**MARTA, "nombre": 123},
        {k: v for k, v in MARTA.items() if k != "ciudad"},
        {},
        {**MARTA, "nombre": "Marta\u0000"},  # Postgres no guarda NUL: 422, no 500
        {**MARTA, "ciudad": "Cali\u0000"},
    )
    for body in invalid:
        _error(_json(client, body, ip=next(ips)), 422, "validation_error")
    for raw in (b"{no es json", b"", b"[1, 2]", b'"texto"', b"[" * 20000):  # el último: RecursionError (< 64 KiB)
        headers = {**JSON_HEADERS, "X-Forwarded-For": next(ips)}
        _error(client.post(URL, content=raw, headers=headers), 422, "validation_error")
    assert _rows(db) == []

    # En el límite sí pasa: nombre de 120 y ciudad de 80.
    ok = _json(client, {**MARTA, "nombre": "n" * 120, "ciudad": "c" * 80}, ip=next(ips))
    assert ok.status_code == 201, ok.text


def test_ac4_waitlist_form_post(client, db, settings, monkeypatch):
    monkeypatch.setattr(settings, "landing_url", LANDING)

    response = _form(client, {**MARTA, "website": ""})

    _redirect(response, OK)
    (row,) = _rows(db)
    assert (row.correo, row.nombre, row.tipo) == ("marta@ejemplo.com", "Marta Rojas", "comercio")

    # Datos inválidos (el navegador ya valida `required` y `type=email`, pero igual): 303 a ?lista=error.
    _redirect(_form(client, {**MARTA, "correo": "otra@", "website": ""}), ERROR)
    _redirect(_form(client, {"nombre": "Sin tipo", "correo": "x@ejemplo.com", "ciudad": "Cali", "website": ""}), ERROR)
    form_charset = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
    _redirect(client.post(URL, content=b"\xff\xfe", headers=form_charset, follow_redirects=False), ERROR)
    nul = "tipo=comercio&nombre=Marta%00&correo=m2%40ejemplo.com&ciudad=Cali&website="
    _redirect(client.post(URL, content=nul, headers=FORM_HEADERS, follow_redirects=False), ERROR)
    assert len(_rows(db)) == 1

    # LANDING_URL con "/" final no duplica la barra.
    monkeypatch.setattr(settings, "landing_url", LANDING + "/")
    _redirect(_form(client, {**MARTA, "correo": "nuevo@ejemplo.com", "website": ""}), OK)
    assert len(_rows(db)) == 2


def test_ac5_waitlist_honeypot(client, db):
    response = _json(client, {**MARTA, "website": "http://spam"})

    assert response.status_code == 201, response.text
    assert_contract(response, "joinWaitlist")
    assert response.json() == {"status": "created"}
    assert _rows(db) == []

    _redirect(_form(client, {**MARTA, "website": "http://spam"}), OK)
    # Antes de validar: un bot con datos inválidos tampoco recibe pistas.
    assert _json(client, {"tipo": "banco", "website": "x"}).status_code == 201
    assert _rows(db) == []

    # Vacío (el formulario sin JS siempre lo manda) no es spam.
    assert _json(client, {**MARTA, "website": ""}).status_code == 201
    assert len(_rows(db)) == 1


def test_ac6_waitlist_rate_limit(client, db):
    for n in range(10):
        response = _json(client, {**MARTA, "correo": f"persona{n}@ejemplo.com"})
        assert response.status_code == 201, (n, response.text)

    blocked = _json(client, {**MARTA, "correo": "once@ejemplo.com"})

    _error(blocked, 429, "rate_limited")
    assert int(blocked.headers["retry-after"]) >= 1
    _redirect(_form(client, {**MARTA, "correo": "form@ejemplo.com", "website": ""}), ERROR)  # form: 303 error

    other_ip = _json(client, {**MARTA, "correo": "otra-ip@ejemplo.com"}, ip="203.0.113.7")
    assert other_ip.status_code == 201, other_ip.text
    assert_contract(other_ip, "joinWaitlist")
    assert {row.correo for row in _rows(db)} == {f"persona{n}@ejemplo.com" for n in range(10)} | {
        "otra-ip@ejemplo.com"
    }


def test_ac7_waitlist_cors(fake_op, settings, monkeypatch):
    monkeypatch.setattr(settings, "cors_origins", ["http://localhost:3000", LANDING])
    app = create_app()  # CORS se configura al crear la app
    app.dependency_overrides[get_op_client] = lambda: fake_op
    with TestClient(app) as client:
        preflight = client.options(
            URL,
            headers={
                "Origin": LANDING,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,accept",
            },
        )
        assert preflight.status_code == 200, preflight.text
        assert preflight.headers["access-control-allow-origin"] == LANDING
        assert "POST" in preflight.headers["access-control-allow-methods"]
        allowed_headers = preflight.headers["access-control-allow-headers"].lower()
        assert "content-type" in allowed_headers and "accept" in allowed_headers

        post = client.post(URL, json=MARTA, headers={**JSON_HEADERS, "Origin": LANDING})
        assert post.status_code == 201, post.text
        assert_contract(post, "joinWaitlist")
        assert post.headers["access-control-allow-origin"] == LANDING

        stranger = client.options(
            URL, headers={"Origin": "https://otro-sitio.example", "Access-Control-Request-Method": "POST"}
        )
        assert "access-control-allow-origin" not in stranger.headers


# ── Reglas sin AC propio ──────────────────────────────────────────────────────


def test_r3_concurrent_same_correo(db):
    """R3: envíos simultáneos con el mismo correo → una inserción, el resto actualiza; nunca un error."""
    barrier = threading.Barrier(6)
    results, errors = [], []

    def submit(n: int) -> None:
        data = WaitlistRequest.model_validate({**MARTA, "nombre": f"Marta {n}"})
        try:
            with SessionLocal() as session:
                barrier.wait()
                results.append(service.upsert(session, data))
        except Exception as exc:  # pragma: no cover - el test falla abajo
            errors.append(exc)

    threads = [threading.Thread(target=submit, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert sorted(results) == [False] * 5 + [True]
    assert len(_rows(db)) == 1


def test_r7_window_slides():
    now = [1000.0]
    limiter = service.RateLimiter(limit=10, window=60, now=lambda: now[0])
    for _ in range(10):  # una por segundo: 1000 … 1009
        assert limiter.hit("1.2.3.4") is None
        now[0] += 1
    assert limiter.hit("1.2.3.4") == 50  # la más vieja (1000) sale de la ventana en 1060
    assert limiter.hit("5.6.7.8") is None  # otra IP, otra cuenta
    now[0] = 1059.5
    assert limiter.hit("1.2.3.4") == 1  # rechazada y no contada
    now[0] = 1060.0
    assert limiter.hit("1.2.3.4") is None  # salió la de 1000: hay cupo para una
    assert limiter.hit("1.2.3.4") is not None
    limiter.reset()
    assert limiter.hit("1.2.3.4") is None


# ── Revisión final ────────────────────────────────────────────────────────────


def test_r7_forwarded_for_only_from_trusted_proxy(client, db, settings, monkeypatch):
    """(revisión final) R7: sin proxy de confianza, `X-Forwarded-For` no cuenta (antes cada petición inventaba una IP
    y nunca llegaba el 429); detrás de un proxy de `TRUSTED_PROXIES` (IP o CIDR) se usa su primer valor."""
    monkeypatch.setattr(settings, "trusted_proxies", [])
    for n in range(10):
        response = _json(client, {**MARTA, "correo": f"spoof{n}@ejemplo.com"}, ip=f"10.0.0.{n}")
        assert response.status_code == 201, (n, response.text)
    _error(_json(client, {**MARTA, "correo": "spoof10@ejemplo.com"}, ip="10.0.0.10"), 429, "rate_limited")

    request = SimpleNamespace(headers={"x-forwarded-for": "203.0.113.9, 10.0.0.1"}, client=SimpleNamespace(host=""))
    for peer, trusted, expected in (
        ("172.18.0.5", [], "172.18.0.5"),
        ("172.18.0.5", ["172.16.0.0/12"], "203.0.113.9"),
        ("172.18.0.5", ["172.18.0.5"], "203.0.113.9"),
        ("8.8.8.8", ["172.16.0.0/12", "no-es-ip"], "8.8.8.8"),
    ):
        monkeypatch.setattr(settings, "trusted_proxies", trusted)
        request.client.host = peer
        assert client_ip(request) == expected, (peer, trusted)


def test_r7_limiter_memory_is_bounded():
    """(revisión final) R7: con muchas IPs distintas dentro de la ventana, el limitador recuerda a lo sumo `max_keys`
    (olvida las menos recientes) y borra las que salieron de la ventana."""
    now = [1000.0]
    limiter = service.RateLimiter(limit=2, window=60, now=lambda: now[0], max_keys=100)
    for n in range(1000):
        assert limiter.hit(f"10.0.{n // 256}.{n % 256}") is None
    assert len(limiter) == 100
    assert limiter.hit("10.0.3.231") is None and limiter.hit("10.0.3.231") is not None  # la más reciente sigue
    now[0] += 61
    assert limiter.hit("1.1.1.1") is None
    assert len(limiter) == 1
