"""Test fixtures.

Tests run against a real PostgreSQL (16+) so that RLS policies, triggers and
constraints are exercised exactly as in production. A fresh database is
created per test session and dropped afterwards.

    TEST_DATABASE_ADMIN_URL=postgresql://postgres@127.0.0.1:5432/postgres pytest
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import jwt
import psycopg
import pytest

ADMIN_URL = os.environ.get("TEST_DATABASE_ADMIN_URL", "postgresql://postgres@127.0.0.1:54329/postgres")
JWT_SECRET = "test-secret-with-at-least-32-characters-xx"
DB_NAME = f"maliscan_test_{os.getpid()}"


def _db_url(name: str) -> str:
    base, _, _ = ADMIN_URL.rpartition("/")
    return f"{base}/{name}"


TEST_DB_URL = _db_url(DB_NAME)

# Configure the application before it is imported anywhere.
os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DATABASE_URL": TEST_DB_URL.replace("postgresql://", "postgresql+psycopg://", 1),
        "SUPABASE_JWT_SECRET": JWT_SECRET,
        "LOG_LEVEL": "WARNING",
        "DB_POOL_SIZE": "2",
    }
)


@pytest.fixture(scope="session", autouse=True)
def database() -> Iterator[str]:
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}"')
        conn.execute(f'CREATE DATABASE "{DB_NAME}"')
    from app.db.migrate import run

    run(TEST_DB_URL, with_shim=True)
    yield TEST_DB_URL

    from app.db.session import get_engine

    get_engine().dispose()
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')


@pytest.fixture(scope="session")
def engine(database):
    from app.db.session import get_engine

    return get_engine()


@pytest.fixture
def owner_conn(database) -> Iterator[psycopg.Connection]:
    """Superuser connection for test setup (bypasses RLS)."""
    with psycopg.connect(database, autocommit=True) as conn:
        yield conn


@pytest.fixture
def make_user(owner_conn) -> Callable[..., uuid.UUID]:
    def _make(email: str | None = None, *, super_admin: bool = False, full_name: str | None = None) -> uuid.UUID:
        email = email or f"user-{uuid.uuid4().hex[:10]}@example.test"
        uid = owner_conn.execute(
            "INSERT INTO auth.users (email, raw_user_meta_data) VALUES (%s, %s) RETURNING id",
            (email, psycopg.types.json.Jsonb({"full_name": full_name} if full_name else {})),
        ).fetchone()[0]
        if super_admin:
            owner_conn.execute("UPDATE public.users SET is_super_admin = true WHERE id = %s", (uid,))
        return uid

    return _make


def claims_for(uid: uuid.UUID) -> dict:
    return {"sub": str(uid), "role": "authenticated", "aud": "authenticated"}


@pytest.fixture
def as_user(engine):
    """Context manager: RLS-enforced SQLAlchemy connection for the given user."""
    from app.db.session import user_transaction

    @contextmanager
    def _as(uid: uuid.UUID | None):
        claims = claims_for(uid) if uid else {"role": "anon"}
        with user_transaction(claims, engine) as conn:
            yield conn

    return _as


def make_token(uid: uuid.UUID, *, secret: str = JWT_SECRET, exp_offset: int = 3600,
               aud: str = "authenticated", **extra) -> str:
    now = int(time.time())
    payload = {"sub": str(uid), "aud": aud, "role": "authenticated", "iat": now, "exp": now + exp_offset, **extra}
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture
def token():
    return make_token


@pytest.fixture(scope="session")
def client(database):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers(token):
    def _h(uid: uuid.UUID) -> dict[str, str]:
        return {"Authorization": f"Bearer {token(uid)}"}

    return _h


@pytest.fixture
def make_firm(as_user):
    """Create a firm owned by `owner_id` through the real bootstrap function."""
    from sqlalchemy import text

    def _make(owner_id: uuid.UUID, name: str | None = None) -> uuid.UUID:
        with as_user(owner_id) as conn:
            return conn.execute(
                text("SELECT app.create_firm(:n, NULL)"), {"n": name or f"Ofis {uuid.uuid4().hex[:6]}"}
            ).scalar_one()

    return _make


@pytest.fixture
def add_member(owner_conn):
    def _add(firm_id: uuid.UUID, user_id: uuid.UUID, role: str) -> None:
        owner_conn.execute(
            "INSERT INTO public.firm_members (firm_id, user_id, role) VALUES (%s, %s, %s)",
            (firm_id, user_id, role),
        )

    return _add


_TAX_COUNTER = iter(range(1_000_000_000, 9_999_999_999))


def next_vkn() -> str:
    return str(next(_TAX_COUNTER))


@pytest.fixture
def make_taxpayer(owner_conn):
    def _make(firm_id: uuid.UUID, title: str = "Örnek Ticaret A.Ş.") -> uuid.UUID:
        return owner_conn.execute(
            """INSERT INTO public.taxpayers (firm_id, taxpayer_type, title, tax_number)
               VALUES (%s, 'company', %s, %s) RETURNING id""",
            (firm_id, title, next_vkn()),
        ).fetchone()[0]

    return _make
