"""Office dashboard statistics (counts only — no amounts per customer in logs)."""

import uuid

from fastapi import APIRouter, Query
from sqlalchemy import text

from app.api.deps import ALL_ROLES, DB, require_firm_role

router = APIRouter(prefix="/firms/{firm_id}", tags=["dashboard"])


@router.get("/dashboard", summary="Document and processing statistics for the office")
def dashboard(firm_id: uuid.UUID, conn: DB, days: int = Query(30, ge=7, le=180),
              taxpayer_id: uuid.UUID | None = None) -> dict:
    require_firm_role(conn, firm_id, ALL_ROLES)
    p = {"f": firm_id, "t": taxpayer_id, "days": days}
    scope = "firm_id = :f AND deleted_at IS NULL AND (CAST(:t AS uuid) IS NULL OR taxpayer_id = :t)"
    by_status = dict(conn.execute(text(f"SELECT status, count(*) FROM public.documents WHERE {scope} GROUP BY 1"),
                                  p).all())
    totals = {
        "total": sum(by_status.values()),
        "pending": by_status.get("needs_review", 0),
        "in_progress": by_status.get("queued", 0) + by_status.get("processing", 0) + by_status.get("uploaded", 0),
        "approved": by_status.get("approved", 0),
        "exported": by_status.get("exported", 0),
        "rejected": by_status.get("rejected", 0),
        "failed": by_status.get("failed", 0),
        "duplicates_flagged": conn.execute(text(f"""SELECT count(*) FROM public.documents WHERE {scope}
                                                    AND duplicate_of IS NOT NULL AND status = 'needs_review'"""),
                                           p).scalar_one(),
        "low_confidence": conn.execute(text(f"""SELECT count(*) FROM public.documents WHERE {scope}
                                                AND status = 'needs_review' AND overall_confidence < 0.85"""),
                                       p).scalar_one(),
    }
    daily = conn.execute(text(f"""
        SELECT g.day::date AS day,
               (SELECT count(*) FROM public.documents d WHERE {scope.replace('firm_id', 'd.firm_id')
                    .replace('deleted_at', 'd.deleted_at').replace('taxpayer_id', 'd.taxpayer_id')}
                   AND d.created_at >= g.day AND d.created_at < g.day + interval '1 day') AS uploaded,
               (SELECT count(*) FROM public.approvals a WHERE a.firm_id = :f AND a.action = 'approve'
                   AND a.created_at >= g.day AND a.created_at < g.day + interval '1 day') AS approved
          FROM generate_series(date_trunc('day', now()) - make_interval(days => :days - 1),
                               date_trunc('day', now()), interval '1 day') AS g(day)
         ORDER BY 1"""), p).mappings().all()
    monthly = conn.execute(text(f"""
        SELECT to_char(date_trunc('month', created_at), 'YYYY-MM') AS month, count(*) AS uploaded,
               count(*) FILTER (WHERE status IN ('approved','exported')) AS approved,
               count(*) FILTER (WHERE status = 'exported') AS exported
          FROM public.documents WHERE {scope} AND created_at >= date_trunc('month', now()) - interval '11 months'
         GROUP BY 1 ORDER BY 1"""), p).mappings().all()
    taxpayers = conn.execute(text("""
        SELECT t.id, t.title,
               count(d.id) AS documents,
               count(d.id) FILTER (WHERE d.status = 'needs_review') AS pending,
               count(d.id) FILTER (WHERE d.status = 'failed') AS failed,
               count(d.id) FILTER (WHERE d.status = 'approved') AS ready_to_export,
               max(d.created_at) AS last_upload
          FROM public.taxpayers t
          LEFT JOIN public.documents d ON d.taxpayer_id = t.id AND d.deleted_at IS NULL
         WHERE t.firm_id = :f AND t.deleted_at IS NULL AND (CAST(:t AS uuid) IS NULL OR t.id = :t)
         GROUP BY t.id, t.title
         ORDER BY pending DESC, documents DESC LIMIT 50"""), p).mappings().all()
    failures = conn.execute(text(f"""SELECT coalesce(error_code, 'unknown') AS error_code, count(*) AS count
                                      FROM public.documents WHERE {scope} AND status = 'failed'
                                     GROUP BY 1 ORDER BY 2 DESC"""), p).mappings().all()
    avg_conf = conn.execute(text(f"""SELECT round(avg(overall_confidence), 3) FROM public.documents WHERE {scope}
                                      AND overall_confidence IS NOT NULL
                                      AND created_at >= now() - make_interval(days => :days)"""), p).scalar()
    return {
        "totals": totals,
        "by_status": by_status,
        "daily": [dict(r) for r in daily],
        "monthly": [dict(r) for r in monthly],
        "taxpayers": [dict(r) for r in taxpayers],
        "failures": [dict(r) for r in failures],
        "average_confidence": float(avg_conf) if avg_conf is not None else None,
    }
