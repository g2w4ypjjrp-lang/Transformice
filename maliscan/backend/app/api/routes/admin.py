"""Super-admin panel API. Every endpoint verifies `users.is_super_admin`
through the RLS-scoped connection; cross-tenant work then uses service_role."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, text

from app.api.deps import DB, is_super_admin
from app.api.errors import ApiError, forbidden, not_found
from app.api.util import audit
from app.db.session import get_engine, service_transaction

router = APIRouter(prefix="/admin", tags=["admin"])


def require_super_admin(conn: DB) -> Connection:
    if not is_super_admin(conn):
        raise forbidden("super admin only")
    return conn


Admin = Depends(require_super_admin)


@router.get("/overview", summary="Platform usage overview")
def overview(conn: Connection = Admin) -> dict:
    with service_transaction(get_engine()) as s:
        row = s.execute(text("""SELECT
            (SELECT count(*) FROM public.firms WHERE deleted_at IS NULL) AS firms,
            (SELECT count(*) FROM public.firms WHERE status = 'active' AND deleted_at IS NULL) AS active_firms,
            (SELECT count(*) FROM public.users) AS users,
            (SELECT count(*) FROM public.taxpayers WHERE deleted_at IS NULL) AS taxpayers,
            (SELECT count(*) FROM public.documents WHERE created_at >= date_trunc('day', now())) AS documents_today,
            (SELECT count(*) FROM public.documents WHERE created_at >= date_trunc('month', now())) AS documents_month,
            (SELECT count(*) FROM public.documents) AS documents_total,
            (SELECT count(*) FROM public.export_jobs) AS exports_total""")).mappings().one()
        jobs = dict(s.execute(text("SELECT status, count(*) FROM public.processing_jobs GROUP BY 1")).all())
        failed24 = s.execute(text("""SELECT count(*) FROM public.processing_jobs WHERE status IN ('dead','failed')
                                     AND updated_at >= now() - interval '24 hours'""")).scalar_one()
    return {**dict(row), "jobs": jobs, "failed_jobs_24h": failed24}


# ------------------------------------------------------------------ firms
@router.get("/firms", summary="All offices")
def list_firms(q: str | None = Query(None, max_length=100), conn: Connection = Admin) -> list[dict]:
    with service_transaction(get_engine()) as s:
        rows = s.execute(text("""
            SELECT f.id, f.name, f.tax_number, f.status, f.created_at,
                   p.code AS plan_code, sub.status AS subscription_status, sub.current_period_end,
                   (SELECT count(*) FROM public.firm_members m WHERE m.firm_id = f.id AND m.status = 'active') AS members,
                   (SELECT count(*) FROM public.taxpayers t WHERE t.firm_id = f.id AND t.deleted_at IS NULL) AS taxpayers,
                   (SELECT count(*) FROM public.documents d WHERE d.firm_id = f.id
                       AND d.created_at >= date_trunc('month', now())) AS documents_month
              FROM public.firms f
              LEFT JOIN public.subscriptions sub ON sub.firm_id = f.id AND sub.status IN ('trialing','active','past_due')
              LEFT JOIN public.subscription_plans p ON p.id = sub.plan_id
             WHERE f.deleted_at IS NULL AND (CAST(:q AS text) IS NULL OR f.name ILIKE '%' || :q || '%')
             ORDER BY f.created_at DESC LIMIT 500"""), {"q": q}).mappings().all()
    return [dict(r) for r in rows]


class FirmAdminUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["active", "suspended", "closed"]


@router.patch("/firms/{firm_id}", summary="Suspend / reactivate / close an office")
def update_firm(firm_id: uuid.UUID, body: FirmAdminUpdate, conn: Connection = Admin) -> dict:
    with service_transaction(get_engine()) as s:
        n = s.execute(text("UPDATE public.firms SET status = :s WHERE id = :f"), {"s": body.status, "f": firm_id}).rowcount
    if not n:
        raise not_found("firm")
    audit(conn, firm_id, "admin.firm_status", "firm", firm_id, {"status": body.status})
    return {"id": str(firm_id), "status": body.status}


class SubscriptionAssign(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_code: str
    status: Literal["trialing", "active", "past_due"] = "active"
    current_period_end: datetime | None = None


@router.put("/firms/{firm_id}/subscription", summary="Assign a plan to an office")
def assign_subscription(firm_id: uuid.UUID, body: SubscriptionAssign, conn: Connection = Admin) -> dict:
    with service_transaction(get_engine()) as s:
        plan = s.execute(text("SELECT id FROM public.subscription_plans WHERE code = :c"), {"c": body.plan_code}).scalar()
        if not plan:
            raise not_found("plan")
        if not s.execute(text("SELECT 1 FROM public.firms WHERE id = :f"), {"f": firm_id}).first():
            raise not_found("firm")
        s.execute(text("""UPDATE public.subscriptions SET status = 'canceled', canceled_at = now()
                          WHERE firm_id = :f AND status IN ('trialing','active','past_due')"""), {"f": firm_id})
        s.execute(text("""INSERT INTO public.subscriptions (firm_id, plan_id, status, current_period_end)
                          VALUES (:f, :p, :s, :e)"""),
                  {"f": firm_id, "p": plan, "s": body.status, "e": body.current_period_end})
    audit(conn, firm_id, "admin.subscription_assigned", "firm", firm_id, {"plan": body.plan_code})
    return {"firm_id": str(firm_id), "plan_code": body.plan_code, "status": body.status}


# ------------------------------------------------------------------ users
@router.get("/users", summary="All users")
def list_users(q: str | None = Query(None, max_length=100), conn: Connection = Admin) -> list[dict]:
    with service_transaction(get_engine()) as s:
        rows = s.execute(text("""
            SELECT u.id, u.email, u.full_name, u.is_active, u.is_super_admin, u.last_seen_at, u.created_at,
                   (SELECT json_agg(json_build_object('firm_id', m.firm_id, 'firm', f.name, 'role', m.role,
                                                      'status', m.status))
                      FROM public.firm_members m JOIN public.firms f ON f.id = m.firm_id
                     WHERE m.user_id = u.id) AS memberships
              FROM public.users u
             WHERE CAST(:q AS text) IS NULL OR u.email ILIKE '%' || :q || '%' OR u.full_name ILIKE '%' || :q || '%'
             ORDER BY u.created_at DESC LIMIT 500"""), {"q": q}).mappings().all()
    return [dict(r) for r in rows]


class UserAdminUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_active: bool | None = None
    is_super_admin: bool | None = None


@router.patch("/users/{user_id}", summary="Disable a user / grant super admin")
def update_user(user_id: uuid.UUID, body: UserAdminUpdate, conn: Connection = Admin) -> dict:
    me = conn.execute(text("SELECT auth.uid()")).scalar_one()
    changes = body.model_dump(exclude_none=True)
    if user_id == me and (changes.get("is_active") is False or changes.get("is_super_admin") is False):
        raise ApiError(409, "self_lockout", "you cannot disable or demote yourself")
    if not changes:
        return {"id": str(user_id)}
    with service_transaction(get_engine()) as s:
        sets = ", ".join(f"{k} = :{k}" for k in changes)
        n = s.execute(text(f"UPDATE public.users SET {sets} WHERE id = :u"), {**changes, "u": user_id}).rowcount
    if not n:
        raise not_found("user")
    audit(conn, None, "admin.user_updated", "user", user_id, changes)
    return {"id": str(user_id), **changes}


# ------------------------------------------------------------------ plans
class PlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^[a-z0-9_]{2,40}$")
    name: str = Field(min_length=2, max_length=100)
    price_monthly: Decimal = Field(ge=0, decimal_places=2)
    currency: str = Field(default="TRY", min_length=3, max_length=3)
    max_users: int | None = Field(default=None, gt=0)
    max_taxpayers: int | None = Field(default=None, gt=0)
    max_documents_per_month: int | None = Field(default=None, gt=0)
    trial_days: int = Field(default=0, ge=0, le=90)
    features: dict = Field(default_factory=dict)
    is_active: bool = True


@router.get("/plans", summary="Subscription plans")
def list_plans(conn: Connection = Admin) -> list[dict]:
    rows = conn.execute(text("SELECT * FROM public.subscription_plans ORDER BY price_monthly, code")).mappings().all()
    return [dict(r) for r in rows]


@router.put("/plans/{code}", summary="Create or update a plan")
def upsert_plan(code: str, body: PlanIn, conn: Connection = Admin) -> dict:
    if code != body.code:
        raise ApiError(422, "validation_error", "code mismatch")
    with service_transaction(get_engine()) as s:
        row = s.execute(text("""
            INSERT INTO public.subscription_plans (code, name, price_monthly, currency, max_users, max_taxpayers,
                    max_documents_per_month, trial_days, features, is_active)
            VALUES (:code, :name, :price_monthly, :currency, :max_users, :max_taxpayers, :max_documents_per_month,
                    :trial_days, CAST(:features AS jsonb), :is_active)
            ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, price_monthly = EXCLUDED.price_monthly,
                currency = EXCLUDED.currency, max_users = EXCLUDED.max_users, max_taxpayers = EXCLUDED.max_taxpayers,
                max_documents_per_month = EXCLUDED.max_documents_per_month, trial_days = EXCLUDED.trial_days,
                features = EXCLUDED.features, is_active = EXCLUDED.is_active
            RETURNING *"""), {**body.model_dump(), "features": json.dumps(body.features)}).mappings().one()
    audit(conn, None, "admin.plan_saved", "subscription_plan", row["id"])
    return dict(row)


# ------------------------------------------------------------------ jobs & errors
@router.get("/jobs", summary="Processing jobs monitor")
def list_jobs(status: str | None = Query(None), limit: int = Query(100, ge=1, le=500),
              conn: Connection = Admin) -> list[dict]:
    with service_transaction(get_engine()) as s:
        rows = s.execute(text("""
            SELECT j.id, j.firm_id, f.name AS firm_name, j.document_id, j.job_type, j.status, j.attempts,
                   j.max_attempts, j.last_error_code, j.locked_by, j.run_after, j.created_at, j.updated_at
              FROM public.processing_jobs j JOIN public.firms f ON f.id = j.firm_id
             WHERE CAST(:s AS text) IS NULL OR j.status = :s
             ORDER BY j.updated_at DESC LIMIT :l"""), {"s": status, "l": limit}).mappings().all()
    return [dict(r) for r in rows]


@router.post("/jobs/{job_id}/retry", summary="Re-queue a dead/failed job")
def retry_job(job_id: uuid.UUID, conn: Connection = Admin) -> dict:
    with service_transaction(get_engine()) as s:
        row = s.execute(text("""UPDATE public.processing_jobs SET status = 'queued', attempts = 0, run_after = now(),
                                   last_error_code = NULL, finished_at = NULL
                                WHERE id = :j AND status IN ('dead','failed') RETURNING firm_id, document_id"""),
                        {"j": job_id}).mappings().one_or_none()
        if row is None:
            raise ApiError(409, "invalid_state", "only dead or failed jobs can be retried")
        s.execute(text("""UPDATE public.documents SET status = 'queued', error_code = NULL
                          WHERE firm_id = :f AND id = :d AND status = 'failed'"""),
                  {"f": row["firm_id"], "d": row["document_id"]})
    audit(conn, row["firm_id"], "admin.job_retried", "processing_job", job_id)
    return {"id": str(job_id), "status": "queued"}


@router.get("/errors", summary="Error summary (failed documents and dead jobs by error code)")
def errors(days: int = Query(7, ge=1, le=90), conn: Connection = Admin) -> dict:
    with service_transaction(get_engine()) as s:
        by_code = s.execute(text("""SELECT coalesce(last_error_code, 'unknown') AS error_code, count(*) AS count,
                                           max(updated_at) AS last_seen
                                      FROM public.processing_jobs WHERE status IN ('dead','failed')
                                       AND updated_at >= now() - make_interval(days => :d)
                                     GROUP BY 1 ORDER BY 2 DESC"""), {"d": days}).mappings().all()
        retrying = s.execute(text("""SELECT count(*) FROM public.processing_jobs WHERE status = 'queued'
                                     AND attempts > 0""")).scalar_one()
    return {"by_error_code": [dict(r) for r in by_code], "retrying": retrying}


# ------------------------------------------------------------------ platform config & audit
@router.get("/config", summary="Platform configuration")
def get_config(conn: Connection = Admin) -> list[dict]:
    return [dict(r) for r in conn.execute(text("SELECT key, value, updated_at FROM public.platform_config ORDER BY key"))
            .mappings()]


class ConfigIn(BaseModel):
    value: Any


@router.put("/config/{key}", summary="Set a platform configuration value")
def set_config(key: str, body: ConfigIn, conn: Connection = Admin) -> dict:
    import re

    if not re.fullmatch(r"[a-z0-9_.]{2,80}", key):
        raise ApiError(422, "validation_error", "invalid key")
    me = conn.execute(text("SELECT auth.uid()")).scalar_one()
    with service_transaction(get_engine()) as s:
        s.execute(text("""INSERT INTO public.platform_config (key, value, updated_by) VALUES (:k, CAST(:v AS jsonb), :u)
                          ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by"""),
                  {"k": key, "v": json.dumps(body.value), "u": me})
    audit(conn, None, "admin.config_set", None, None, {"key": key})
    return {"key": key, "value": body.value}


@router.get("/audit-logs", summary="Platform-wide audit log")
def audit_logs(firm_id: uuid.UUID | None = None, limit: int = Query(100, ge=1, le=500),
               conn: Connection = Admin) -> list[dict]:
    rows = conn.execute(text("""SELECT id, firm_id, actor_user_id, action, entity_type, entity_id, metadata, created_at
                                  FROM public.audit_logs WHERE CAST(:f AS uuid) IS NULL OR firm_id = :f
                                 ORDER BY id DESC LIMIT :l"""), {"f": firm_id, "l": limit}).mappings().all()
    return [dict(r) for r in rows]
