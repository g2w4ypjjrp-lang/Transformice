"""Verification of Supabase-issued access tokens.

The backend never handles passwords: Supabase Auth authenticates the user and
issues a JWT. We verify signature, expiry, audience and (when configured)
issuer, and extract the user id (``sub``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt

from app.core.config import Settings


class AuthError(Exception):
    """Raised for any invalid / missing credential. Message is safe to return."""


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    email: str | None
    claims: dict[str, Any]


@lru_cache(maxsize=4)
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=600)


def verify_access_token(token: str, settings: Settings) -> Principal:
    if not token:
        raise AuthError("missing token")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise AuthError("malformed token") from exc

    alg = header.get("alg")
    options = {"require": ["exp", "sub", "aud"]}
    common: dict[str, Any] = {
        "audience": settings.supabase_jwt_audience,
        "options": options,
        "leeway": 30,
    }
    if settings.jwt_issuer:
        common["issuer"] = settings.jwt_issuer

    try:
        if alg == "HS256":
            if not settings.supabase_jwt_secret:
                raise AuthError("HS256 tokens not accepted")
            claims = jwt.decode(
                token, settings.supabase_jwt_secret.get_secret_value(), algorithms=["HS256"], **common
            )
        elif alg in ("RS256", "ES256"):
            if not settings.supabase_url:
                raise AuthError("asymmetric tokens not accepted")
            jwks_url = settings.supabase_url.rstrip("/") + "/auth/v1/.well-known/jwks.json"
            key = _jwks_client(jwks_url).get_signing_key_from_jwt(token)
            claims = jwt.decode(token, key.key, algorithms=[alg], **common)
        else:
            raise AuthError("unsupported token algorithm")
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("token expired") from exc
    except jwt.PyJWTError as exc:
        raise AuthError("invalid token") from exc

    if claims.get("role") not in (None, "authenticated"):
        raise AuthError("invalid token role")
    try:
        user_id = uuid.UUID(str(claims["sub"]))
    except ValueError as exc:
        raise AuthError("invalid subject") from exc
    return Principal(user_id=user_id, email=claims.get("email"), claims=claims)
