"""Validadores de texto compartidos (00-core, "Errores": un dato inválido es 422, nunca 500)."""

from pydantic import AfterValidator

NUL_MESSAGE = "El texto tiene caracteres no permitidos."


def reject_nul(value: str) -> str:
    """Postgres no guarda `\\x00` en `text` ni en JSONB: sin esto el insert falla y la API daría 500 en vez de 422."""
    if "\x00" in value:
        raise ValueError(NUL_MESSAGE)
    return value


NoNul = AfterValidator(reject_nul)
"""Para `Annotated[str, …, NoNul]` en los schemas de entrada que terminan en una columna de texto."""
