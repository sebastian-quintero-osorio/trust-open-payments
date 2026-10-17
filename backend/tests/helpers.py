"""Ayudas de tests: montos en pesos (`cop`) y validación de respuestas contra el contrato (`assert_contract`)."""

from functools import cache
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from app.core.money import Money

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "contracts" / "trust-api.openapi.yaml"
CONTRACT_URI = "urn:trust-api"
OP_CONTRACT_PATH = CONTRACT_PATH.with_name("op-service.openapi.yaml")
OP_CONTRACT_URI = "urn:op-service"
HTTP_METHODS = ("get", "put", "post", "delete", "patch", "options", "head", "trace")


def cop(amount: int | str) -> Money:
    """Pesos como en la prosa de las specs → Money COP/2: `cop(48_000)`, `cop("130,90")`, `cop("1.590.000")`."""
    if isinstance(amount, int):
        return Money(amount * 100, "COP", 2)
    units, _, cents = amount.replace(".", "").partition(",")
    if len(cents) > 2:
        raise ValueError(f"COP tiene 2 decimales: {amount!r}")
    return Money(int(units or "0") * 100 + int(cents.ljust(2, "0")), "COP", 2)


@cache
def contract() -> dict[str, Any]:
    with CONTRACT_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@cache
def op_contract() -> dict[str, Any]:
    """`contracts/op-service.openapi.yaml` (contrato con Go; solo se lee)."""
    with OP_CONTRACT_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@cache
def _registry() -> Registry:
    return Registry().with_resources(
        [
            (CONTRACT_URI, Resource.from_contents(contract(), default_specification=DRAFT202012)),
            (OP_CONTRACT_URI, Resource.from_contents(op_contract(), default_specification=DRAFT202012)),
        ]
    )


def assert_op_schema(data: Any, pointer: str) -> None:
    """Valida un JSON contra `contracts/op-service.openapi.yaml`. `pointer`: un schema de `components/schemas`
    (`"Transfer"`) o un puntero JSON completo (`"/paths/~1v1~1collection-accounts/get/..."`)."""
    ref = OP_CONTRACT_URI + "#" + (pointer if pointer.startswith("/") else _pointer("components", "schemas", pointer))
    validator = Draft202012Validator(
        {"$ref": ref}, registry=_registry(), format_checker=Draft202012Validator.FORMAT_CHECKER
    )
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    assert not errors, f"{pointer}: no cumple op-service.openapi.yaml:\n" + "\n".join(
        f"- {'/'.join(str(p) for p in e.absolute_path) or '<raíz>'}: {e.message}" for e in errors
    )


def _pointer(*parts: str) -> str:
    return "".join("/" + part.replace("~", "~0").replace("/", "~1") for part in parts)


def find_operation(operation_id: str) -> tuple[str, str, dict[str, Any]]:
    """(ruta, método, operación) del YAML para un `operationId`."""
    for path, item in contract()["paths"].items():
        for method in HTTP_METHODS:
            operation = item.get(method)
            if operation and operation.get("operationId") == operation_id:
                return path, method, operation
    raise AssertionError(f"operationId {operation_id!r} no existe en {CONTRACT_PATH.name}")


def _resolve_local_ref(ref: str) -> tuple[list[str], dict[str, Any]]:
    assert ref.startswith("#/"), f"Solo se soportan $ref locales: {ref}"
    parts = [p.replace("~1", "/").replace("~0", "~") for p in ref[2:].split("/")]
    node: Any = contract()
    for part in parts:
        node = node[part]
    return parts, node


def assert_contract(response: Any, operation_id: str) -> None:
    """Valida que el status esté declarado para la operación y que el body JSON cumpla su schema del YAML."""
    path, method, operation = find_operation(operation_id)
    status = str(response.status_code)
    declared = operation.get("responses", {})
    assert status in declared, (
        f"{operation_id} ({method.upper()} {path}) no declara el status {status}; declara {sorted(declared)}. "
        f"Body: {response.text[:500]}"
    )

    location = ["paths", path, method, "responses", status]
    spec_response = declared[status]
    if "$ref" in spec_response:
        location, spec_response = _resolve_local_ref(spec_response["$ref"])

    content = spec_response.get("content") or {}
    if "application/json" not in content:
        for media_type in content:
            assert response.headers.get("content-type", "").startswith(media_type), (
                f"{operation_id} {status}: se esperaba {media_type}, llegó {response.headers.get('content-type')}"
            )
        return

    assert response.headers.get("content-type", "").startswith("application/json"), (
        f"{operation_id} {status}: se esperaba application/json, llegó {response.headers.get('content-type')}"
    )
    schema_ref = CONTRACT_URI + "#" + _pointer(*location, "content", "application/json", "schema")
    validator = Draft202012Validator(
        {"$ref": schema_ref},
        registry=_registry(),
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )
    body = response.json()
    errors = sorted(validator.iter_errors(body), key=lambda e: list(e.absolute_path))
    assert not errors, f"{operation_id} {status}: el body no cumple el contrato:\n" + "\n".join(
        f"- {'/'.join(str(p) for p in e.absolute_path) or '<raíz>'}: {e.message}" for e in errors
    )
