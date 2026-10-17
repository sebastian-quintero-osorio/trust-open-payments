"""Catálogo único de avisos in-app (14-notifications, "Catálogo"): tipos, destinatarios y textos.

Los textos de la tabla de la spec 14 mandan; la spec emisora solo decide cuándo. `render` formatea las variables:
`Money` con `money.format_es`, porcentajes (`pct*`, en bps) como `15 %` / `12,5 %` y `datetime` con `format_hora`.
"""

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from app.core import clock
from app.core.money import Money, format_es

NOTIFICATION_TYPES = (
    "offer_received",
    "loan_accepted",
    "action_required",
    "loan_active",
    "loan_rejected",
    "loan_cancelled",
    "loan_expired",
    "loan_paid",
    "loan_late",
    "pct_changed",
    "abono_received",
    "sale_split",
    "payout_failed",
)

Body = str | Callable[[Mapping[str, Any]], str]


def _sale_split_body(values: Mapping[str, Any]) -> str:
    """Con abono: `{abono} abonado a tu crédito.`; sin crédito (abono 0): `El 100 % va a tu wallet.`."""
    abono = values.get("abono")
    if isinstance(abono, Money) and abono.value > 0:
        return f"{format_es(abono)} abonado a tu crédito."
    return "El 100 % va a tu wallet."


# type → audience → (título, cuerpo). Las audiencias son las que reciben ese tipo según la spec 14.
CATALOG: dict[str, dict[str, tuple[str, Body]]] = {
    "offer_received": {
        "merchant": (
            "Recibiste una oferta",
            "{prestamista} te ofrece {monto} con el {pct} de cada venta. Revísala antes de aceptar.",
        ),
    },
    "loan_accepted": {
        "lender": ("{negocio} aceptó tu oferta", "Autoriza en tu wallet el desembolso de {monto}."),
    },
    "action_required": {
        "lender": ("El desembolso no se completó", "Vuelve a autorizar el desembolso de {monto} a {negocio}."),
    },
    "loan_active": {
        "merchant": (
            "Tu crédito está activo",
            "Recibiste {monto}. Desde hoy, el {pct} de cada venta abona a tu crédito.",
        ),
        "lender": ("Desembolso completado", "{negocio} recibió {monto}. Los abonos llegan con cada venta."),
    },
    "loan_rejected": {
        "merchant": ("Crédito rechazado", "{quien} rechazó el crédito de {monto}."),
        "lender": ("Crédito rechazado", "{quien} rechazó el crédito de {monto}."),
    },
    "loan_cancelled": {
        "merchant": (
            "Crédito cancelado",
            "{quien} canceló el crédito de {monto}. Tu solicitud u oferta vuelve a estar abierta.",
        ),
        "lender": (
            "Crédito cancelado",
            "{quien} canceló el crédito de {monto}. Tu solicitud u oferta vuelve a estar abierta.",
        ),
    },
    "loan_expired": {
        "merchant": (
            "El crédito venció",
            "Pasaron {horas} h sin desembolso. Tu solicitud u oferta vuelve a estar abierta.",
        ),
        "lender": (
            "El crédito venció",
            "Pasaron {horas} h sin desembolso. Tu solicitud u oferta vuelve a estar abierta.",
        ),
    },
    "loan_paid": {
        "merchant": ("¡Pagaste tu crédito!", "Completaste {total} con tus ventas."),
        "lender": ("Crédito pagado", "{negocio} completó el pago de {total}."),
    },
    "loan_late": {
        "merchant": ("Tu crédito está atrasado", "No alcanzaste el mínimo en {n} periodos."),
        "lender": ("Crédito atrasado", "{negocio} no alcanzó el mínimo en {n} periodos."),
    },
    "pct_changed": {
        "merchant": (
            "Cambió tu porcentaje",
            "Desde ahora abonas el {pct_nuevo} de cada venta (antes {pct_anterior}).",
        ),
        "lender": (
            "{negocio} cambió de porcentaje",
            "Ahora abona el {pct_nuevo} de cada venta (antes {pct_anterior}).",
        ),
    },
    "abono_received": {
        "lender": (
            "Abono recibido · +{bruto} · {negocio}",
            "Venta de {venta} a las {hora}. Parte Trust {trust}, neto {neto}.",
        ),
    },
    "sale_split": {
        "merchant": ("Venta de {venta} · {para_ti} para ti", _sale_split_body),
    },
    "payout_failed": {
        "admin": ("Transferencia fallida", "Pata {leg} de {monto} · {failure_code}. La plata queda en recaudo."),
    },
}


def format_pct(bps: int) -> str:
    """Puntos básicos → `15 %`, `12,5 %`, `12,25 %` (coma decimal y espacio antes del signo)."""
    units, rest = divmod(abs(bps), 100)
    text = str(units) if rest == 0 else f"{units},{rest:02d}".rstrip("0")
    return f"{'-' if bps < 0 else ''}{text} %"


def _format(key: str, value: Any) -> str:
    if isinstance(value, Money):
        return format_es(value)
    if isinstance(value, datetime):
        return clock.format_hora(value)
    if key.startswith("pct") and isinstance(value, int) and not isinstance(value, bool):
        return format_pct(value)
    return str(value)


def render(type: str, audience: str, **vars: Any) -> tuple[str, str]:
    """(título, cuerpo) del catálogo para `type` y `audience` (`merchant` | `lender` | `admin`).

    `ValueError` si el tipo no existe o esa audiencia no lo recibe; `KeyError` si falta una variable del texto.
    """
    by_audience = CATALOG.get(type)
    if by_audience is None:
        raise ValueError(f"Tipo de aviso fuera del catálogo: {type!r}")
    texts = by_audience.get(audience)
    if texts is None:
        raise ValueError(f"El aviso {type!r} no tiene textos para {audience!r}")
    title, body = texts
    formatted = {key: _format(key, value) for key, value in vars.items()}
    body_text = body(vars) if callable(body) else body.format_map(formatted)
    return title.format_map(formatted), body_text
