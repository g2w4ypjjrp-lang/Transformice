"""Database engine and tenant-scoped transactions.

`user_transaction()` reproduces what Supabase PostgREST does for each request:
inside one transaction it switches to the `authenticated` role and publishes
the JWT claims, so every statement is subject to Row Level Security.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, text

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    s = get_settings()
    return create_engine(
        s.database_url.get_secret_value(),
        pool_size=s.db_pool_size,
        max_overflow=s.db_max_overflow,
        pool_pre_ping=True,
        future=True,
    )


@contextmanager
def user_transaction(claims: dict[str, Any], engine: Engine | None = None) -> Iterator[Connection]:
    """Transaction executing as `authenticated` with the given JWT claims (RLS enforced)."""
    eng = engine or get_engine()
    with eng.begin() as conn:
        conn.execute(text("SET LOCAL ROLE authenticated"))
        conn.execute(
            text("SELECT set_config('request.jwt.claims', :claims, true)"),
            {"claims": json.dumps(claims)},
        )
        yield conn


@contextmanager
def service_transaction(engine: Engine | None = None) -> Iterator[Connection]:
    """Transaction executing as `service_role` (bypasses RLS).

    Only for background workers and platform tasks. Every query MUST filter by
    firm_id explicitly.
    """
    eng = engine or get_engine()
    with eng.begin() as conn:
        conn.execute(text("SET LOCAL ROLE service_role"))
        yield conn
