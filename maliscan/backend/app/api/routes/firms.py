import json
import uuid

from fastapi import APIRouter, Query, status
from sqlalchemy import text

from app.api.deps import ALL_ROLES, DB, MANAGERS, require_firm_role
from app.api.errors import not_found
from app.schemas.common import AuditLogOut, FirmCreate, FirmOut, FirmUpdate, MemberOut, SubscriptionOut

router = APIRouter(prefix="/firms", tags=["firms"])

_FIRM_COLUMNS = "f.id, f.name, f.tax_number, f.tax_office, f.email, f.phone, f.address, f.status, f.created_at"


def _subscription(conn, firm_id: uuid.UUID) -> SubscriptionOut | None:
    row = conn.execute(
        text("""SELECT p.code AS plan_code, p.name AS plan_name, s.status, s.current_period_end,
                       p.max_users, p.max_taxpayers, p.max_documents_per_month
                  FROM public.subscriptions s JOIN public.subscription_plans p ON p.id = s.plan_id
                 WHERE s.firm_id = :f AND s.status IN ('trialing','active','past_due')"""),
        {"f": firm_id},
    ).mappings().one_or_none()
    return SubscriptionOut(**row) if row else None


def _firm(conn, firm_id: uuid.UUID) -> FirmOut:
    row = conn.execute(
        text(f"SELECT {_FIRM_COLUMNS}, app.firm_role(f.id) AS my_role FROM public.firms f WHERE f.id = :f"),
        {"f": firm_id},
    ).mappings().one_or_none()
    if row is None:
        raise not_found("firm")
    return FirmOut(**row, subscription=_subscription(conn, firm_id))


@router.post("", response_model=FirmOut, status_code=status.HTTP_201_CREATED,
             summary="Create an accounting office; caller becomes owner, trial subscription starts")
def create_firm(body: FirmCreate, conn: DB) -> FirmOut:
    firm_id = conn.execute(
        text("SELECT app.create_firm(:name, :tax)"), {"name": body.name, "tax": body.tax_number}
    ).scalar_one()
    return _firm(conn, firm_id)


@router.get("", response_model=list[FirmOut], summary="Firms the caller is an active member of")
def list_firms(conn: DB) -> list[FirmOut]:
    rows = conn.execute(
        text(f"""SELECT {_FIRM_COLUMNS}, app.firm_role(f.id) AS my_role
                   FROM public.firms f
                  WHERE app.is_firm_member(f.id) AND f.deleted_at IS NULL
                  ORDER BY f.name""")
    ).mappings().all()
    return [FirmOut(**r) for r in rows]


@router.get("/{firm_id}", response_model=FirmOut, summary="Firm details with subscription")
def get_firm(firm_id: uuid.UUID, conn: DB) -> FirmOut:
    require_firm_role(conn, firm_id, ALL_ROLES)
    return _firm(conn, firm_id)


@router.patch("/{firm_id}", response_model=FirmOut, summary="Update firm (owner/admin)")
def update_firm(firm_id: uuid.UUID, body: FirmUpdate, conn: DB) -> FirmOut:
    require_firm_role(conn, firm_id, MANAGERS)
    changes = body.model_dump(exclude_unset=True)
    if changes:
        assignments = ", ".join(f"{k} = :{k}" for k in changes)  # whitelisted keys
        conn.execute(text(f"UPDATE public.firms SET {assignments} WHERE id = :firm_id"),
                     {**changes, "firm_id": firm_id})
        conn.execute(
            text("SELECT app.write_audit_log(:f, 'firm.updated', 'firm', :f, CAST(:m AS jsonb))"),
            {"f": firm_id, "m": json.dumps({"fields": sorted(changes)})},
        )
    return _firm(conn, firm_id)


@router.get("/{firm_id}/members", response_model=list[MemberOut], summary="Firm members")
def list_members(firm_id: uuid.UUID, conn: DB) -> list[MemberOut]:
    require_firm_role(conn, firm_id, ALL_ROLES)
    rows = conn.execute(
        text("""SELECT m.user_id, u.email, u.full_name, m.role, m.status, m.created_at
                  FROM public.firm_members m JOIN public.users u ON u.id = m.user_id
                 WHERE m.firm_id = :f ORDER BY m.created_at"""),
        {"f": firm_id},
    ).mappings().all()
    return [MemberOut(**r) for r in rows]


@router.get("/{firm_id}/audit-logs", response_model=list[AuditLogOut], summary="Audit log (owner/admin)")
def list_audit_logs(
    firm_id: uuid.UUID,
    conn: DB,
    limit: int = Query(50, ge=1, le=200),
    before_id: int | None = Query(None, ge=1),
) -> list[AuditLogOut]:
    require_firm_role(conn, firm_id, MANAGERS)
    rows = conn.execute(
        text("""SELECT id, actor_user_id, action, entity_type, entity_id, metadata, created_at
                  FROM public.audit_logs
                 WHERE firm_id = :f AND (CAST(:b AS bigint) IS NULL OR id < :b)
                 ORDER BY id DESC LIMIT :l"""),
        {"f": firm_id, "b": before_id, "l": limit},
    ).mappings().all()
    return [AuditLogOut(**r) for r in rows]



@router.get("/{firm_id}/usage", summary="Subscription usage against plan limits")
def get_usage(firm_id: uuid.UUID, conn: DB) -> dict:
    require_firm_role(conn, firm_id, ALL_ROLES)
    row = conn.execute(
        text("""SELECT
                  (SELECT count(*) FROM public.firm_members WHERE firm_id = :f AND status = 'active') AS users,
                  (SELECT count(*) FROM public.taxpayers WHERE firm_id = :f AND deleted_at IS NULL) AS taxpayers,
                  (SELECT count(*) FROM public.documents WHERE firm_id = :f
                      AND created_at >= date_trunc('month', now())) AS documents_this_month"""),
        {"f": firm_id},
    ).mappings().one()
    sub = _subscription(conn, firm_id)
    return {
        "usage": dict(row),
        "limits": {
            "users": sub.max_users if sub else None,
            "taxpayers": sub.max_taxpayers if sub else None,
            "documents_per_month": sub.max_documents_per_month if sub else None,
        },
        "subscription": sub.model_dump() if sub else None,
    }
