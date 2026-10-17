"""14-notifications: un test por criterio de aceptación (más reglas sin AC propio)."""

import ast
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import worker
from app.core.db import SessionLocal
from app.core.money import Money, format_es
from app.modules.loans.service import close_period
from app.modules.notifications import catalog
from app.modules.notifications.catalog import CATALOG, NOTIFICATION_TYPES, format_pct, render
from app.modules.notifications.models import Notification
from app.modules.notifications.service import notify
from app.modules.sales.models import Payout
from app.modules.sales.service import process_puntored_event
from app.modules.users.models import User
from tests.helpers import assert_contract, contract, cop
from tests.test_sales import _ct, _event

NOW = datetime(2026, 10, 16, 15, 42, 9, tzinfo=UTC)  # 10:42 a. m. en Bogotá
URL = "/api/v1/notifications"

# 00-core, "Auditoría y notificaciones": el catálogo de `type`, en este orden.
CORE_TYPES = (
    "offer_received", "loan_accepted", "action_required", "loan_active", "loan_rejected", "loan_cancelled",
    "loan_expired", "loan_paid", "loan_late", "pct_changed", "abono_received", "sale_split", "payout_failed",
)

# Columna "Recibe" de la tabla de la spec 14 ("la otra parte" = comercio o prestamista; "cada admin" = admin).
AUDIENCES = {
    "offer_received": {"merchant"},
    "loan_accepted": {"lender"},
    "action_required": {"lender"},
    "loan_active": {"merchant", "lender"},
    "loan_rejected": {"merchant", "lender"},
    "loan_cancelled": {"merchant", "lender"},
    "loan_expired": {"merchant", "lender"},
    "loan_paid": {"merchant", "lender"},
    "loan_late": {"merchant", "lender"},
    "pct_changed": {"merchant", "lender"},
    "abono_received": {"lender"},
    "sale_split": {"merchant"},
    "payout_failed": {"admin"},
}

# Todas las variables de los textos del catálogo, con valores de ejemplo.
SAMPLE_VARS = {
    "negocio": "Tienda La Esquina",
    "prestamista": "Camilo P.",
    "quien": "Camilo P.",
    "monto": cop(2_000_000),
    "pct": 1500,
    "horas": 48,
    "total": cop(2_200_000),
    "n": 2,
    "pct_nuevo": 1750,
    "pct_anterior": 1500,
    "bruto": cop(7_200),
    "venta": cop(48_000),
    "hora": NOW,
    "trust": cop("130,90"),
    "neto": cop("7.069,10"),
    "para_ti": cop(40_800),
    "abono": cop(7_200),
    "leg": "prestamista",
    "failure_code": "invalid_amount",
}


@pytest.fixture(autouse=True)
def _frozen(freeze):
    freeze(NOW)


def _note(db: Session, user: User, minutes_ago: int, read: bool = False, type: str = "sale_split") -> Notification:
    """Aviso con `created_at` explícito (el reloj está congelado: sin esto, todos empatan)."""
    created_at = NOW - timedelta(minutes=minutes_ago)
    note = Notification(
        user_id=user.id,
        type=type,
        title="Venta de $48.000 · $40.800 para ti",
        body="$7.200 abonado a tu crédito.",
        data={
            "sale_id": str(uuid.uuid4()),
            "amount": cop(48_000).to_api(),
            "merchant_amount": cop(40_800).to_api(),
            "abono": cop(7_200).to_api(),
            "loan_id": str(uuid.uuid4()),
        },
        read_at=created_at + timedelta(seconds=30) if read else None,
        created_at=created_at,
    )
    db.add(note)
    db.commit()
    return note


def _list(client, auth, user, **params) -> dict:
    response = client.get(URL, headers=auth(user), params=params)
    assert response.status_code == 200, response.text
    assert_contract(response, "listNotifications")
    return response.json()


def _error(response, status: int, code: str, operation_id: str) -> None:
    assert response.status_code == status, response.text
    assert_contract(response, operation_id)
    assert response.json()["error"]["code"] == code


# ── AC ────────────────────────────────────────────────────────────────────────


def test_ac1_list_own(client, db, auth, make_merchant, make_lender):
    marta, otro = make_merchant(), make_merchant(business_name="Panadería El Trigal")
    newest, middle, oldest = _note(db, marta, 1), _note(db, marta, 5, read=True), _note(db, marta, 10)
    _note(db, otro, 0), _note(db, otro, 3)

    body = _list(client, auth, marta)

    assert [item["id"] for item in body["items"]] == [str(newest.id), str(middle.id), str(oldest.id)]
    assert body["unread_count"] == 2
    assert body["next_offset"] is None
    first = body["items"][0]
    assert first == {
        "id": str(newest.id),
        "type": "sale_split",
        "title": "Venta de $48.000 · $40.800 para ti",
        "body": "$7.200 abonado a tu crédito.",
        "data": newest.data,
        "read_at": None,
        "created_at": "2026-10-16T15:41:09Z",
    }
    assert body["items"][1]["read_at"] == "2026-10-16T15:37:39Z"

    page = _list(client, auth, marta, limit=2)
    assert [item["id"] for item in page["items"]] == [str(newest.id), str(middle.id)]
    assert page["next_offset"] == 2
    assert page["unread_count"] == 2  # no depende de la página
    rest = _list(client, auth, marta, limit=2, offset=2)
    assert [item["id"] for item in rest["items"]] == [str(oldest.id)]
    assert rest["next_offset"] is None and rest["unread_count"] == 2

    # Los tres roles listan las suyas; sin avisos, lista vacía.
    assert _list(client, auth, make_lender()) == {"unread_count": 0, "items": [], "next_offset": None}

    for query in ("limit=0", "limit=201", "offset=-1", "limit=abc", f"offset={2**63}"):
        _error(client.get(f"{URL}?{query}", headers=auth(marta)), 422, "validation_error", "listNotifications")
    _error(client.get(URL), 401, "unauthorized", "listNotifications")


def test_ac2_read_all(client, db, auth, make_merchant):
    marta, otro = make_merchant(), make_merchant()
    _note(db, marta, 1), _note(db, marta, 10)
    already = _note(db, marta, 5, read=True)
    previous_read_at = already.read_at
    others = [_note(db, otro, 0), _note(db, otro, 3)]

    response = client.post(f"{URL}/read-all", headers=auth(marta))

    assert response.status_code == 200, response.text
    assert_contract(response, "readAllNotifications")
    assert response.json() == {"unread_count": 0, "updated": 2}
    body = _list(client, auth, marta)
    assert body["unread_count"] == 0
    assert all(item["read_at"] is not None for item in body["items"])
    db.expire_all()
    assert db.get(Notification, already.id).read_at == previous_read_at  # la que ya estaba leída no cambia
    assert all(db.get(Notification, note.id).read_at is None for note in others)
    assert _list(client, auth, otro)["unread_count"] == 2

    again = client.post(f"{URL}/read-all", headers=auth(marta))
    assert_contract(again, "readAllNotifications")
    assert again.json() == {"unread_count": 0, "updated": 0}

    _error(client.post(f"{URL}/read-all"), 401, "unauthorized", "readAllNotifications")


def test_ac3_read_one(client, db, auth, freeze, make_merchant, make_admin):
    marta, otro = make_merchant(), make_merchant()
    mine, foreign = _note(db, marta, 1), _note(db, otro, 1)

    response = client.post(f"{URL}/{mine.id}/read", headers=auth(marta))

    assert response.status_code == 200, response.text
    assert_contract(response, "readNotification")
    body = response.json()
    assert body["id"] == str(mine.id) and body["read_at"] == "2026-10-16T15:42:09Z"
    assert _list(client, auth, marta)["unread_count"] == 0

    freeze(NOW + timedelta(hours=1))
    again = client.post(f"{URL}/{mine.id}/read", headers=auth(marta))
    assert_contract(again, "readNotification")
    assert again.json()["read_at"] == "2026-10-16T15:42:09Z"  # repetir no cambia read_at

    _error(client.post(f"{URL}/{foreign.id}/read", headers=auth(marta)), 403, "forbidden", "readNotification")
    _error(client.post(f"{URL}/{foreign.id}/read", headers=auth(make_admin())), 403, "forbidden", "readNotification")
    db.expire_all()
    assert db.get(Notification, foreign.id).read_at is None
    for missing in (uuid.uuid4(), "no-es-un-uuid"):
        _error(client.post(f"{URL}/{missing}/read", headers=auth(marta)), 404, "not_found", "readNotification")
    _error(client.post(f"{URL}/{mine.id}/read"), 401, "unauthorized", "readNotification")


def test_ac4_catalog_render():
    title, body = render(
        "abono_received",
        "lender",
        bruto=cop(7_200),
        negocio="Tienda La Esquina",
        venta=cop(48_000),
        hora=NOW,
        trust=cop("130,90"),
        neto=Money(706_910, "COP", 2),
    )
    assert title == "Abono recibido · +$7.200 · Tienda La Esquina"
    assert body == "Venta de $48.000 a las 10:42 a. m. Parte Trust $130,90, neto $7.069,10."  # sin ". ." (R2)

    assert format_es(cop(48_000)) == "$48.000"
    assert format_es(cop("130,90")) == "$130,90"
    assert format_es(Money(706_910, "COP", 2)) == "$7.069,10"
    assert format_es(Money(48_000, "COP", 0)) == "$48.000"  # escala 0: nunca decimales
    assert format_es(Money(177, "USD", 2)) == "$1,77"

    assert (format_pct(1500), format_pct(1250), format_pct(1000), format_pct(1225)) == (
        "15 %", "12,5 %", "10 %", "12,25 %",
    )
    assert render("pct_changed", "merchant", pct_nuevo=1750, pct_anterior=1500) == (
        "Cambió tu porcentaje",
        "Desde ahora abonas el 17,5 % de cada venta (antes 15 %).",
    )
    assert render("sale_split", "merchant", venta=cop(48_000), para_ti=cop(48_000), abono=cop(0)) == (
        "Venta de $48.000 · $48.000 para ti",
        "El 100 % va a tu wallet.",
    )


def test_ac5_notify_transactional(db, make_merchant):
    marta = make_merchant()

    with SessionLocal() as session:
        notify(session, marta.id, "sale_split", "Venta de $48.000 · $48.000 para ti", "El 100 % va a tu wallet.", {})
        session.flush()  # la fila existe dentro de la transacción…
        assert session.scalar(select(func.count()).select_from(Notification)) == 1
        session.rollback()  # …y el rollback la borra

    assert db.scalar(select(func.count()).select_from(Notification)) == 0

    with pytest.raises(ValueError):
        notify(db, marta.id, "promo", "Hola", "Texto", {})
    with pytest.raises(ValueError):
        render("promo", "merchant")
    with pytest.raises(ValueError):  # una audiencia que no recibe ese tipo
        render("offer_received", "lender", **SAMPLE_VARS)

    with SessionLocal() as session:
        note = notify(session, marta.id, "loan_paid", "¡Pagaste tu crédito!", "Completaste $2.200.000 con tus ventas.")
        session.commit()
        assert note.read_at is None and note.data == {}
    assert db.scalar(select(func.count()).select_from(Notification)) == 1


def test_ac6_catalog_complete():
    assert NOTIFICATION_TYPES == CORE_TYPES
    yaml_enum = contract()["components"]["schemas"]["NotificationType"]["enum"]
    assert tuple(yaml_enum) == CORE_TYPES
    assert set(CATALOG) == set(CORE_TYPES)

    for type, audiences in AUDIENCES.items():
        assert set(CATALOG[type]) == audiences, type
        for audience in audiences:
            title, body = render(type, audience, **SAMPLE_VARS)
            assert title and body, (type, audience)
            for text in (title, body):
                assert "{" not in text and "}" not in text, (type, audience, text)
                assert ". ." not in text and ".." not in text, (type, audience, text)
                # R3 (COPY §0): nunca "goteo", "gotereado" ni "gota a gota"; sin tasas ni rendimientos.
                lowered = text.lower()
                for banned in ("goteo", "gotere", "gota a gota", "tasa", "rendimiento", "interés"):
                    assert banned not in lowered, (type, audience, text)
        for other in {"merchant", "lender", "admin"} - audiences:
            with pytest.raises(ValueError):
                render(type, other, **SAMPLE_VARS)

    # Los dos textos de "loan_active", en el orden de la tabla (comercio, prestamista).
    assert render("loan_active", "merchant", **SAMPLE_VARS) == (
        "Tu crédito está activo",
        "Recibiste $2.000.000. Desde hoy, el 15 % de cada venta abona a tu crédito.",
    )
    assert render("loan_active", "lender", **SAMPLE_VARS) == (
        "Desembolso completado",
        "Tienda La Esquina recibió $2.000.000. Los abonos llegan con cada venta.",
    )
    assert render("loan_expired", "lender", **SAMPLE_VARS)[1] == (
        "Pasaron 48 h sin desembolso. Tu solicitud u oferta vuelve a estar abierta."
    )


def _notify_calls(tree: ast.AST) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "id", None) == "notify" or getattr(node.func, "attr", None) == "notify")
    ]


def test_r2_emitters_use_catalog():
    """R2: los emisores (07, 08, 11) nunca escriben títulos ni cuerpos a mano: pasan lo que devuelve `render`."""
    app_dir = Path(catalog.__file__).resolve().parents[2]
    emitters = set()
    for path in app_dir.rglob("*.py"):
        if path.name == "service.py" and path.parent.name == "notifications":
            continue
        source = path.read_text(encoding="utf-8")
        calls = _notify_calls(ast.parse(source))
        for call in calls:
            emitters.add(path.relative_to(app_dir).as_posix())
            if any(isinstance(arg, ast.Starred) for arg in call.args):
                continue  # notify(db, user, type, *render(...), data)
            title, body = call.args[3], call.args[4]
            for arg in (title, body):
                assert not isinstance(arg, ast.Constant | ast.JoinedStr), (
                    f"{path.name}:{call.lineno}: texto escrito a mano; usa catalog.render"
                )
        if calls:
            assert "render" in source, f"{path.name} llama notify sin catalog.render"
    assert {"worker.py", "modules/sales/service.py", "modules/loans/service.py"} <= emitters


def test_r2_emitted_data_matches_contract(client, db, auth, make_merchant, make_lender, make_admin):
    """Los `data` de los emisores reales (07 venta, 08 patas, 11 cierre de periodo) cumplen `NotificationData`."""
    ct = _ct(db, make_merchant, make_lender)
    admin = make_admin()
    result = process_puntored_event(db, _event(ct.account, cop(48_000)), "puntored")  # 07: sale_split
    db.commit()
    legs = {p.leg: p for p in db.scalars(select(Payout).where(Payout.sale_id == result.sale_id))}
    worker._notify_abono(db, legs["lender"])  # 08: abono_received (received null: aún sin receive_*)
    worker._fail_final(db, legs["trust"], "invalid_amount", "Go rechazó el monto")  # 08: payout_failed
    db.commit()
    for _ in range(2):  # 11: pct_changed dos veces y loan_late al segundo periodo sin mínimo
        close_period(db, ct.loan.id, NOW)
        db.commit()

    types = {}
    for user in (ct.merchant, ct.lender, admin):
        items = _list(client, auth, user)["items"]  # assert_contract valida cada `data`
        types[user.role] = sorted(item["type"] for item in items)
    assert types == {
        "merchant": ["loan_late", "pct_changed", "pct_changed", "sale_split"],
        "lender": ["abono_received", "loan_late", "pct_changed", "pct_changed"],
        "admin": ["payout_failed"],
    }
