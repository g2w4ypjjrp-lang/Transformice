"""Luca export endpoints (see docs/LUCA_INTEGRATION.md — template UNVERIFIED)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

import io
import zipfile

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.api.deps import ALL_ROLES, BOOKERS, DB, require_firm_role
from app.api.errors import ApiError, not_found
from app.services.luca import ExportOptions, LucaExportService
from app.services.luca.repository import _load_vouchers, export_journal_entries
from app.services.storage import BucketStorage, StorageError, get_storage

router = APIRouter(prefix="/firms/{firm_id}", tags=["exports"])


class ExportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry_ids: list[uuid.UUID] | None = Field(default=None, max_length=5000)
    all_approved: bool = False
    date_from: date | None = None
    date_to: date | None = None
    format: Literal["csv", "xlsx", "fatura_listesi"] = "csv"
    encoding: Literal["cp1254", "utf-8-sig"] = "cp1254"
    allowed_duplicate_ids: list[uuid.UUID] = Field(default_factory=list)


@router.post("/taxpayers/{taxpayer_id}/exports", summary="Create Luca import files (≤50 vouchers per file)")
def create_export(firm_id: uuid.UUID, taxpayer_id: uuid.UUID, body: ExportIn, conn: DB) -> dict:
    require_firm_role(conn, firm_id, BOOKERS)
    if not body.entry_ids and not body.all_approved:
        raise ApiError(422, "validation_error", "give entry_ids or all_approved=true")
    ids = body.entry_ids or [r[0] for r in conn.execute(
        text("""SELECT id FROM public.journal_entries WHERE firm_id = :f AND taxpayer_id = :t AND status = 'approved'
                   AND (CAST(:df AS date) IS NULL OR entry_date >= :df)
                   AND (CAST(:dt AS date) IS NULL OR entry_date <= :dt)
                 ORDER BY entry_date"""),
        {"f": firm_id, "t": taxpayer_id, "df": body.date_from, "dt": body.date_to})]
    if not ids:
        raise ApiError(422, "nothing_to_export", "no approved journal entries match")
    service = LucaExportService(options=ExportOptions(fmt=body.format, encoding="cp1254" if body.format == "fatura_listesi" else body.encoding))
    try:
        job_id, result = export_journal_entries(
            conn, firm_id=firm_id, taxpayer_id=taxpayer_id, entry_ids=ids, service=service,
            storage=BucketStorage(get_storage(), "exports"), allowed_duplicate_ids=body.allowed_duplicate_ids)
    except StorageError as exc:
        raise ApiError(503, "storage_unavailable", "file storage is unavailable") from exc
    issues = [i.as_dict() for i in result.issues]
    if job_id is None:
        raise _validation_error(issues)
    conn.execute(text("""UPDATE public.documents SET status = 'exported' WHERE firm_id = :f AND id IN (
                            SELECT document_id FROM public.journal_entries WHERE export_job_id = :j
                             AND document_id IS NOT NULL)"""), {"f": firm_id, "j": job_id})
    return {**get_export(firm_id, job_id, conn), "issues": issues}


def _validation_error(issues: list[dict]) -> ApiError:
    err = ApiError(422, "export_validation_failed", "export blocked by validation errors")
    err.detail["issues"] = issues
    return err


@router.get("/exports", summary="Export history")
def list_exports(firm_id: uuid.UUID, conn: DB, taxpayer_id: uuid.UUID | None = None,
                 limit: int = Query(50, ge=1, le=200)) -> list[dict]:
    require_firm_role(conn, firm_id, ALL_ROLES)
    rows = conn.execute(
        text("""SELECT j.id, j.taxpayer_id, t.title AS taxpayer_title, j.template_version, j.status, j.voucher_count,
                       j.file_count, j.created_at, j.completed_at, u.full_name AS created_by_name
                  FROM public.export_jobs j JOIN public.taxpayers t ON t.id = j.taxpayer_id
                  LEFT JOIN public.users u ON u.id = j.created_by
                 WHERE j.firm_id = :f AND (CAST(:t AS uuid) IS NULL OR j.taxpayer_id = :t)
                 ORDER BY j.created_at DESC LIMIT :l"""), {"f": firm_id, "t": taxpayer_id, "l": limit}).mappings().all()
    return [dict(r) for r in rows]


@router.get("/exports/{export_id}", summary="Export detail with short-lived download links")
def get_export(firm_id: uuid.UUID, export_id: uuid.UUID, conn: DB) -> dict:
    require_firm_role(conn, firm_id, ALL_ROLES)
    job = conn.execute(text("""SELECT id, taxpayer_id, template_version, status, voucher_count, file_count,
                                      created_at, completed_at FROM public.export_jobs WHERE firm_id = :f AND id = :j"""),
                       {"f": firm_id, "j": export_id}).mappings().one_or_none()
    if job is None:
        raise not_found("export")
    files = []
    storage = get_storage()
    for f in conn.execute(text("""SELECT id, file_index, storage_bucket, storage_path, voucher_count, sha256
                                  FROM public.export_files WHERE export_job_id = :j ORDER BY file_index"""),
                          {"j": export_id}).mappings():
        name = f["storage_path"].rsplit("/", 1)[-1]
        try:
            url = storage.signed_url(f["storage_bucket"], f["storage_path"], download_name=name)
        except StorageError:
            url = None
        files.append({"id": f["id"], "file_index": f["file_index"], "filename": name,
                      "voucher_count": f["voucher_count"], "sha256": f["sha256"], "download_url": url})
    return {**dict(job), "verified_template": False, "files": files}


@router.get("/exports/{export_id}/download", summary="Download an earlier export again in another format "
            "(no status change; >400 rows → zip)")
def download_export_as(firm_id: uuid.UUID, export_id: uuid.UUID, conn: DB,
                       format: Literal["fatura_listesi", "csv", "xlsx"] = "fatura_listesi") -> Response:
    require_firm_role(conn, firm_id, ALL_ROLES)
    job = conn.execute(text("SELECT taxpayer_id FROM public.export_jobs WHERE firm_id = :f AND id = :j"),
                       {"f": firm_id, "j": export_id}).mappings().one_or_none()
    if job is None:
        raise not_found("export")
    vouchers = [v for v, _ in _load_vouchers(conn, firm_id, job["taxpayer_id"], "export_job_id = :j",
                                             {"j": export_id}, lock=False)]
    if not vouchers:
        raise ApiError(422, "nothing_to_export", "export has no journal entries")
    files = LucaExportService(options=ExportOptions(fmt=format, encoding="cp1254")).rebuild_files(vouchers)
    if len(files) == 1:
        f = files[0]
        media = "text/csv" if f.filename.endswith(".csv") else \
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return Response(f.content, media_type=media,
                        headers={"Content-Disposition": f'attachment; filename="{f.filename}"'})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.writestr(f.filename, f.content)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="luca_{format}_{export_id.hex[:8]}.zip"'})
