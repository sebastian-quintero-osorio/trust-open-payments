"""Imports perezosos de specs posteriores (03: el seed y `/dev/*` se activan cuando esos módulos existen)."""

import importlib
from typing import Any


def optional_attr(module: str, attr: str) -> Any | None:
    """`module.attr` si el módulo existe y define `attr`; si no, `None`.

    Solo se tolera que falte **ese** módulo (o un paquete padre): un `ModuleNotFoundError` de otra dependencia, o
    cualquier otro error al importarlo, es un bug y se propaga.
    """
    try:
        loaded = importlib.import_module(module)
    except ModuleNotFoundError as exc:
        if exc.name and (module == exc.name or module.startswith(exc.name + ".")):
            return None
        raise
    return getattr(loaded, attr, None)
