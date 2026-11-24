"""FastAPI dependencies: authentication, tenant-scoped DB connection, roles."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy import Connection, text

from app.api.errors import ApiError, forbidden, not_found
from app.core.config import Settings, get_settings
from app.core.security import AuthError, Principal, verify_access_token
from app.db.session import get_engine, user_transaction

# Role groups (mirrors database/migrations/0005_rls.sql)
MANAGERS = ("owner", "admin")
BOOKERS = ("owner", "admin", "accountant")
WRITERS = ("owner", "admin", "accountant", "clerk")
ALL_ROLES = ("owner", "admin", "accountant", "clerk", "viewer")


def get_principal(
    authorization: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings),
) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError(401, "unauthenticated", "missing bearer token")
    try:
        return verify_access_token(authorization[7:].strip(), settings)
    except AuthError as exc:
        raise ApiError(401, "unauthenticated", str(exc)) from exc


def get_db(principal: Annotated[Principal, Depends(get_principal)]) -> Iterator[Connection]:
    """One RLS-enforced transaction per request; committed if the handler succeeds."""
    with user_transaction(principal.claims, get_engine()) as conn:
        active = conn.execute(
            text("SELECT is_active FROM public.users WHERE id = auth.uid()")
        ).scalar_one_or_none()
        if active is None:
            raise ApiError(403, "profile_missing", "user profile not provisioned")
        if not active:
            raise ApiError(403, "user_disabled", "user account is disabled")
        yield conn


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
DB = Annotated[Connection, Depends(get_db)]


def firm_role(conn: Connection, firm_id: uuid.UUID) -> str | None:
    return conn.execute(text("SELECT app.firm_role(:f)"), {"f": firm_id}).scalar_one()


def require_firm_role(conn: Connection, firm_id: uuid.UUID, roles: tuple[str, ...]) -> str:
    """Return caller's role in the firm or raise.

    Non-members get 404 (existence of other tenants is not disclosed);
    members lacking the role get 403.
    """
    role = firm_role(conn, firm_id)
    if role is None:
        raise not_found("firm")
    if role not in roles:
        raise forbidden()
    return role


def is_super_admin(conn: Connection) -> bool:
    return bool(conn.execute(text("SELECT app.is_super_admin()")).scalar_one())
