"""Fixtures comunes (00-core, "Tests"). Postgres real: tablas recreadas por sesión y truncadas entre tests."""

import os

# El entorno de tests va antes de importar `app`: la configuración y el engine se leen al importar.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://trust:trust@localhost:5433/trust_test"
)
os.environ.update(
    {
        "DATABASE_URL": TEST_DATABASE_URL,
        "JWT_SECRET": "test-secret-de-al-menos-32-bytes-para-hs256",
        "CORS_ORIGINS": "http://localhost:3000",
        "WORKER_ENABLED": "false",
        "OP_CLIENT": "fake",
        "OP_FAKE_AUTO_COMPLETE": "false",
        "TRUST_REVENUE_WALLET_ADDRESS": "https://ilp.interledger-test.dev/trust-revenue",
        "DEV_ENDPOINTS_ENABLED": "false",
    }
)

# Los tests no leen `backend/.env`: solo el entorno de arriba y los defaults. Va antes de cualquier
# `get_settings()` (el engine de `app.core.db` la llama al importarse).
from app.core import config  # noqa: E402

config.Settings.model_config["env_file"] = None

import itertools  # noqa: E402
from collections.abc import Callable, Iterator  # noqa: E402
from datetime import datetime  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core import clock, security  # noqa: E402
from app.core.config import Settings, get_settings  # noqa: E402
from app.core.db import SessionLocal, engine  # noqa: E402
from app.integrations.op.client import get_op_client, set_op_client  # noqa: E402
from app.integrations.op.fake import FakeOpClient  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base  # noqa: E402
from app.modules.users.models import User, UserRole  # noqa: E402
from tests.helpers import assert_contract, cop  # noqa: E402, F401  (conftest expone assert_contract, 00-core AC-6)

security.BCRYPT_ROUNDS = 4  # bcrypt rápido en tests

_ALL_TABLES = ", ".join(table.name for table in Base.metadata.sorted_tables)

# El FakeOpClient del proceso de tests (02 R21): ids deterministas (`salt="test"`) y sin auto-completar. Es el
# singleton de `get_op_client()` (worker, lifespan) y el override de la dependencia en la app de `client`.
FAKE_OP = FakeOpClient(salt="test", auto_complete=False)
set_op_client(FAKE_OP)


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    # Esquema limpio por sesión: también borra tablas viejas que ya no están en los modelos.
    assert (engine.url.database or "").endswith("_test"), "TEST_DATABASE_URL debe apuntar a una base *_test"
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    Base.metadata.create_all(engine)
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE {_ALL_TABLES} RESTART IDENTITY CASCADE"))
    FAKE_OP.reset()
    yield
    clock.unfreeze()


@pytest.fixture
def settings() -> Settings:
    """La configuración de la app; para cambiarla en un test: `monkeypatch.setattr(settings, "campo", valor)`."""
    return get_settings()


@pytest.fixture
def db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def fake_op() -> FakeOpClient:
    """El FakeOpClient de la app (el de `get_op_client()`), ya reiniciado: para configurarlo e inspeccionarlo."""
    return FAKE_OP


@pytest.fixture
def client(fake_op: FakeOpClient) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_op_client] = lambda: fake_op
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def freeze() -> Iterator[Callable[[datetime], None]]:
    """`freeze(dt)` congela `clock.now()`; se descongela al terminar el test."""
    yield clock.freeze
    clock.unfreeze()


_user_seq = itertools.count(1)


def _make_user(db: Session, role: UserRole, wallet: bool, defaults: dict[str, Any], fields: dict[str, Any]) -> User:
    n = next(_user_seq)
    password = fields.pop("password", "secret123")
    values = {"email": f"{role.value}{n}@test.trust", **defaults, **fields}
    if wallet:
        settings = get_settings()
        values = {
            "payout_wallet_url": f"https://ilp.interledger-test.dev/{role.value}-{n}",
            "payout_wallet_public_name": values.get("business_name") or f"{values['first_name']} {values['last_name']}",
            "payout_asset_code": settings.market_asset_code,
            "payout_asset_scale": settings.market_asset_scale,
            **values,
        }
    user = User(role=role, password_hash=security.hash_password(password), **values)
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def make_merchant(db: Session) -> Callable[..., User]:
    """`make_merchant(wallet=True, password="secret123", **columnas)` crea y confirma un comercio."""

    def factory(wallet: bool = True, **fields: Any) -> User:
        defaults = {"first_name": "Marta", "last_name": "Rojas", "business_name": "Tienda La Esquina",
                    "city": "Bucaramanga"}
        return _make_user(db, UserRole.MERCHANT, wallet, defaults, fields)

    return factory


@pytest.fixture
def make_lender(db: Session) -> Callable[..., User]:
    """`make_lender(wallet=True, password="secret123", **columnas)` crea y confirma un prestamista."""

    def factory(wallet: bool = True, **fields: Any) -> User:
        defaults = {"first_name": "Camilo", "last_name": "Pardo", "city": "Bogotá"}
        return _make_user(db, UserRole.LENDER, wallet, defaults, fields)

    return factory


@pytest.fixture
def make_admin(db: Session) -> Callable[..., User]:
    def factory(**fields: Any) -> User:
        defaults = {"first_name": "Operador", "last_name": "Trust", "city": "Bogotá"}
        return _make_user(db, UserRole.ADMIN, False, defaults, fields)

    return factory


@pytest.fixture
def auth() -> Callable[[User], dict[str, str]]:
    """`auth(user)` → headers con el JWT del usuario."""

    def headers(user: User) -> dict[str, str]:
        return {"Authorization": f"Bearer {security.create_access_token(user.id, user.role)}"}

    return headers
