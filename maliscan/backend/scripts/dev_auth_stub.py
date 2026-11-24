"""DEVELOPMENT / E2E TEST ONLY — minimal stand-in for Supabase Auth (GoTrue).

Implements just the endpoints supabase-js uses for password auth so the web
app can be exercised end-to-end without a Supabase project. It writes users
into the local `auth.users` shim table and issues HS256 JWTs signed with
SUPABASE_JWT_SECRET. Never deploy this: it auto-confirms e-mails and keeps
password hashes in memory.

    DATABASE_URL=... SUPABASE_JWT_SECRET=... uvicorn scripts.dev_auth_stub:app --port 9999
"""

from __future__ import annotations

import hashlib
import os
import secrets
import time
import uuid

import jwt
import psycopg
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

if os.environ.get("ENVIRONMENT") in ("staging", "production"):
    raise RuntimeError("dev_auth_stub must never run in staging/production")

DSN = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
SECRET = os.environ["SUPABASE_JWT_SECRET"]
PASSWORDS: dict[str, str] = {}
REFRESH: dict[str, str] = {}

app = FastAPI(title="DEV auth stub")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["*"])


def _hash(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


def _user(uid: str, email: str, meta: dict | None = None) -> dict:
    return {"id": uid, "aud": "authenticated", "role": "authenticated", "email": email,
            "email_confirmed_at": "2026-01-01T00:00:00Z", "app_metadata": {"provider": "email"},
            "user_metadata": meta or {}, "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"}


def _session(uid: str, email: str) -> dict:
    now = int(time.time())
    token = jwt.encode({"sub": uid, "email": email, "aud": "authenticated", "role": "authenticated",
                        "iat": now, "exp": now + 3600}, SECRET, algorithm="HS256")
    refresh = secrets.token_urlsafe(24)
    REFRESH[refresh] = uid
    return {"access_token": token, "token_type": "bearer", "expires_in": 3600, "expires_at": now + 3600,
            "refresh_token": refresh, "user": _user(uid, email)}


def _find(email: str) -> str | None:
    with psycopg.connect(DSN) as c:
        row = c.execute("SELECT id FROM auth.users WHERE lower(email) = lower(%s)", (email,)).fetchone()
    return str(row[0]) if row else None


@app.post("/auth/v1/signup")
async def signup(req: Request):
    body = await req.json()
    email, pw = body["email"], body["password"]
    if _find(email):
        return JSONResponse({"code": 422, "msg": "User already registered", "error_code": "user_already_exists"}, 422)
    uid = str(uuid.uuid4())
    meta = (body.get("data") or {})
    with psycopg.connect(DSN) as c:
        c.execute("INSERT INTO auth.users (id, email, email_confirmed_at, raw_user_meta_data) VALUES (%s, %s, now(), %s)",
                  (uid, email, psycopg.types.json.Jsonb(meta)))
    PASSWORDS[uid] = _hash(pw)
    return _session(uid, email)  # auto-confirmed in dev


@app.post("/auth/v1/token")
async def token(req: Request, grant_type: str):
    body = await req.json()
    if grant_type == "password":
        uid = _find(body.get("email", ""))
        if not uid or PASSWORDS.get(uid) != _hash(body.get("password", "")):
            return JSONResponse({"error": "invalid_grant", "error_description": "Invalid login credentials",
                                 "error_code": "invalid_credentials"}, 400)
        return _session(uid, body["email"])
    if grant_type == "refresh_token":
        uid = REFRESH.pop(body.get("refresh_token", ""), None)
        if not uid:
            return JSONResponse({"error": "invalid_grant", "error_description": "Invalid Refresh Token"}, 400)
        with psycopg.connect(DSN) as c:
            email = c.execute("SELECT email FROM auth.users WHERE id = %s", (uid,)).fetchone()[0]
        return _session(uid, email)
    raise HTTPException(400, "unsupported grant")


def _claims(authorization: str | None) -> dict:
    if not authorization:
        raise HTTPException(401)
    try:
        return jwt.decode(authorization.split()[-1], SECRET, algorithms=["HS256"], audience="authenticated")
    except jwt.PyJWTError as exc:
        raise HTTPException(401) from exc


@app.get("/auth/v1/user")
def get_user(authorization: str | None = Header(None)):
    c = _claims(authorization)
    return _user(c["sub"], c.get("email", ""))


@app.put("/auth/v1/user")
async def update_user(req: Request, authorization: str | None = Header(None)):
    c = _claims(authorization)
    body = await req.json()
    if body.get("password"):
        PASSWORDS[c["sub"]] = _hash(body["password"])
    return _user(c["sub"], c.get("email", ""))


@app.post("/auth/v1/logout")
def logout():
    return Response(status_code=204)


@app.post("/auth/v1/recover")
def recover():
    return {}


@app.post("/dev/make-super-admin")
async def make_admin(req: Request):
    body = await req.json()
    with psycopg.connect(DSN) as c:
        c.execute("UPDATE public.users SET is_super_admin = true WHERE lower(email) = lower(%s)", (body["email"],))
    return {"ok": True}
