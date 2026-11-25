"""Documents: upload, list, review, corrections, journal editing, approval."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, File, Form, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Connection, text

from app.api.deps import ALL_ROLES, BOOKERS, DB, MANAGERS, WRITERS, require_firm_role
from app.api.errors import ApiError, not_found
from app.api.util import audit
from app.core.config import get_settings
from app.domain.rules import ACCOUNT_RE
from app.services.extraction.schema import ExtractedDocument
from app.services.files import FileRejected, validate_upload
from app.services.pipeline import ProcessingError, build_journal, save_extraction
from app.services.storage import StorageError, get_storage

router = APIRouter(prefix="/firms/{firm_id}", tags=["documents"])

DOC_STATUSES = ("uploaded", "queued", "processing", "needs_review", "approved", "rejected", "exported", "failed")


class DocumentSummary(BaseModel):
    id: uuid.UUID
    taxpayer_id: uuid.UUID
    taxpayer_title: str | None = None
    source: str
    file_kind: str
    document_type: str | None
    direction: str | None
    status: str
    error_code: str | None
    supplier_tax_number: str | None
    supplier_name: str | None = None
    document_number: str | None
    document_date: date | None
    total_amount: Decimal | None
    currency: str | None
    overall_confidence: Decimal | None
    duplicate_of: uuid.UUID | None
    original_filename: str | None
    created_at: datetime


_SUMMARY_SQL = """
SELECT d.id, d.taxpayer_id, t.title AS taxpayer_title, d.source, d.file_kind, d.document_type, d.direction, d.status,
       d.error_code, d.supplier_tax_number, ed.data #>> '{supplier,name}' AS supplier_name, d.document_number,
       d.document_date, d.total_amount, d.currency, d.overall_confidence, d.duplicate_of, d.original_filename,
       d.created_at
  FROM public.documents d
  JOIN public.taxpayers t ON t.id = d.taxpayer_id
  LEFT JOIN public.extracted_data ed ON ed.document_id = d.id AND ed.is_current
"""


def _doc_row(conn: Connection, firm_id: uuid.UUID, doc_id: uuid.UUID) -> dict:
    row = conn.execute(text("SELECT * FROM public.documents WHERE firm_id = :f AND id = :d AND deleted_at IS NULL"),
                       {"f": firm_id, "d": doc_id}).mappings().one_or_none()
    if row is None:
        raise not_found("document")
    return dict(row)


def _enqueue(conn: Connection, firm_id, doc_id, job_type: str = "extract_document", priority: int = 100) -> None:
    conn.execute(
        text("""INSERT INTO public.processing_jobs (firm_id, document_id, job_type, priority, created_by)
                VALUES (:f, :d, :t, :p, auth.uid())
                ON CONFLICT (document_id, job_type) WHERE status IN ('queued','running') DO NOTHING"""),
        {"f": firm_id, "d": doc_id, "t": job_type, "p": priority},
    )


# ============================================================================ upload
@router.post("/taxpayers/{taxpayer_id}/documents", response_model=DocumentSummary, status_code=201,
             summary="Upload a receipt/invoice (JPEG/PNG/WEBP/PDF/UBL-TR XML) and queue it for processing")
async def upload_document(
    firm_id: uuid.UUID, taxpayer_id: uuid.UUID, conn: DB,
    file: UploadFile = File(...),
    source: Literal["camera", "upload", "email", "api"] = Form("upload"),
    capture_metadata: str | None = Form(None, max_length=4000),
) -> DocumentSummary:
    require_firm_role(conn, firm_id, WRITERS)
    tp = conn.execute(text("SELECT id FROM public.taxpayers WHERE firm_id = :f AND id = :t AND deleted_at IS NULL "
                           "AND is_active"), {"f": firm_id, "t": taxpayer_id}).first()
    if not tp:
        raise not_found("taxpayer")
    settings = get_settings()
    raw = await file.read(settings.max_upload_bytes + 1)
    try:
        vf = validate_upload(raw, settings.max_upload_bytes)
    except FileRejected as exc:
        raise ApiError(413 if exc.code == "file_too_large" else 415 if exc.code == "unsupported_type" else 422,
                       exc.code, str(exc)) from exc

    existing = conn.execute(text("""SELECT id FROM public.documents WHERE firm_id = :f AND taxpayer_id = :t
                                    AND sha256 = :h AND deleted_at IS NULL"""),
                            {"f": firm_id, "t": taxpayer_id, "h": vf.sha256}).scalar()
    if existing:
        raise ApiError(409, "duplicate_file", f"this exact file was already uploaded (document {existing})")
    meta = {}
    if capture_metadata:
        try:
            parsed = json.loads(capture_metadata)
            # keep only small scalar values (device/crop info); never location data
            meta = {k: v for k, v in parsed.items() if isinstance(v, (int, float, str, bool))
                    and k not in ("lat", "lng", "latitude", "longitude", "gps")}
        except (ValueError, AttributeError):
            meta = {}
    doc_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    path = f"{firm_id}/{taxpayer_id}/{now:%Y/%m}/{doc_id}.{vf.extension}"
    try:
        get_storage().put("documents", path, vf.content, vf.mime_type)
    except StorageError as exc:
        raise ApiError(503, "storage_unavailable", "file storage is unavailable") from exc
    try:
        conn.execute(
            text("""INSERT INTO public.documents (id, firm_id, taxpayer_id, uploaded_by, source, file_kind,
                        storage_path, original_filename, mime_type, file_size, sha256, page_count, capture_metadata,
                        status)
                    VALUES (:id, :f, :t, auth.uid(), :src, :kind, :p, :fn, :mime, :size, :h, :pages,
                            CAST(:meta AS jsonb), 'queued')"""),
            {"id": doc_id, "f": firm_id, "t": taxpayer_id, "src": source, "kind": vf.file_kind, "p": path,
             "fn": (file.filename or "")[:200] or None, "mime": vf.mime_type, "size": len(vf.content), "h": vf.sha256,
             "pages": vf.page_count, "meta": json.dumps(meta)},
        )
        _enqueue(conn, firm_id, doc_id, priority=50 if vf.file_kind == "xml" else 100)
    except Exception:
        try:
            get_storage().delete("documents", path)  # no orphan files when the DB insert fails (e.g. plan limit)
        except StorageError:
            pass
        raise
    audit(conn, firm_id, "document.uploaded", "document", doc_id, {"kind": vf.file_kind, "source": source})
    row = conn.execute(text(_SUMMARY_SQL + " WHERE d.id = :d"), {"d": doc_id}).mappings().one()
    return DocumentSummary(**row)


# ============================================================================ list / detail
@router.get("/documents", response_model=dict, summary="List documents (filters + pagination)")
def list_documents(
    firm_id: uuid.UUID, conn: DB,
    taxpayer_id: uuid.UUID | None = None,
    status: str | None = Query(None, description="comma-separated statuses"),
    q: str | None = Query(None, max_length=100),
    date_from: date | None = None, date_to: date | None = None,
    duplicates_only: bool = False,
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
) -> dict:
    require_firm_role(conn, firm_id, ALL_ROLES)
    statuses = [s for s in (status or "").split(",") if s]
    if any(s not in DOC_STATUSES for s in statuses):
        raise ApiError(422, "validation_error", "unknown status filter")
    where = """WHERE d.firm_id = :f AND d.deleted_at IS NULL
               AND (CAST(:t AS uuid) IS NULL OR d.taxpayer_id = :t)
               AND (cardinality(CAST(:st AS text[])) = 0 OR d.status = ANY(CAST(:st AS text[])))
               AND (CAST(:df AS date) IS NULL OR d.document_date >= :df)
               AND (CAST(:dt AS date) IS NULL OR d.document_date <= :dt)
               AND (NOT :dup OR d.duplicate_of IS NOT NULL)
               AND (CAST(:q AS text) IS NULL OR d.document_number ILIKE '%' || :q || '%'
                    OR d.supplier_tax_number LIKE :q || '%' OR (ed.data #>> '{supplier,name}') ILIKE '%' || :q || '%')"""
    params = {"f": firm_id, "t": taxpayer_id, "st": statuses, "df": date_from, "dt": date_to, "dup": duplicates_only,
              "q": q}
    total = conn.execute(text("SELECT count(*) FROM public.documents d LEFT JOIN public.extracted_data ed "
                              "ON ed.document_id = d.id AND ed.is_current " + where), params).scalar_one()
    rows = conn.execute(text(_SUMMARY_SQL + where + " ORDER BY d.created_at DESC LIMIT :l OFFSET :o"),
                        {**params, "l": limit, "o": offset}).mappings().all()
    return {"total": total, "items": [DocumentSummary(**r).model_dump(mode="json") for r in rows]}


def _journal(conn: Connection, firm_id, doc_id) -> dict | None:
    je = conn.execute(
        text("""SELECT id, entry_date, voucher_type, document_number, description, status, generated_by, warnings,
                       total_debit, total_credit, approved_by, approved_at, exported_at, export_job_id
                  FROM public.journal_entries WHERE firm_id = :f AND document_id = :d AND status <> 'rejected'"""),
        {"f": firm_id, "d": doc_id}).mappings().one_or_none()
    if je is None:
        return None
    lines = conn.execute(
        text("""SELECT jl.line_no, jl.account_code, coa.name AS account_name, jl.description, jl.debit, jl.credit,
                       jl.vat_rate, jl.source_note
                  FROM public.journal_lines jl
                  LEFT JOIN public.chart_of_accounts coa ON coa.taxpayer_id = (
                        SELECT taxpayer_id FROM public.journal_entries WHERE id = jl.journal_entry_id)
                       AND coa.code = jl.account_code
                 WHERE jl.journal_entry_id = :j ORDER BY jl.line_no"""), {"j": je["id"]}).mappings().all()
    return {**dict(je), "lines": [dict(l) for l in lines]}


@router.get("/documents/{document_id}", summary="Full review payload: file URL, extraction, journal, history")
def get_document(firm_id: uuid.UUID, document_id: uuid.UUID, conn: DB) -> dict:
    require_firm_role(conn, firm_id, ALL_ROLES)
    d = _doc_row(conn, firm_id, document_id)
    ed = conn.execute(
        text("""SELECT id, version, source, ai_model, ocr_provider, data, field_confidence, overall_confidence,
                       validation_issues, created_by, created_at
                  FROM public.extracted_data WHERE firm_id = :f AND document_id = :d AND is_current"""),
        {"f": firm_id, "d": document_id}).mappings().one_or_none()
    history = conn.execute(
        text("""SELECT a.action, a.comment, a.created_at, a.actor_user_id, u.full_name, u.email
                  FROM public.approvals a JOIN public.users u ON u.id = a.actor_user_id
                 WHERE a.firm_id = :f AND a.document_id = :d ORDER BY a.created_at"""),
        {"f": firm_id, "d": document_id}).mappings().all()
    job = conn.execute(
        text("""SELECT job_type, status, attempts, last_error_code, updated_at FROM public.processing_jobs
                 WHERE firm_id = :f AND document_id = :d ORDER BY created_at DESC LIMIT 1"""),
        {"f": firm_id, "d": document_id}).mappings().one_or_none()
    try:
        file_url = get_storage().signed_url(d["storage_bucket"], d["storage_path"])
    except StorageError:
        file_url = None
    keep = ("id", "taxpayer_id", "source", "file_kind", "document_type", "direction", "status", "error_code",
            "mime_type", "file_size", "page_count", "original_filename", "document_number", "document_date",
            "total_amount", "currency", "overall_confidence", "duplicate_of", "created_at", "updated_at",
            "uploaded_by", "retention_until")
    return {
        "document": {k: d[k] for k in keep},
        "file_url": file_url,
        "extraction": dict(ed) if ed else None,
        "journal": _journal(conn, firm_id, document_id),
        "history": [dict(h) for h in history],
        "job": dict(job) if job else None,
        "review_threshold": get_settings().review_confidence_threshold,
    }


@router.get("/documents/{document_id}/versions", summary="All extraction versions (AI + corrections)")
def list_versions(firm_id: uuid.UUID, document_id: uuid.UUID, conn: DB) -> list[dict]:
    require_firm_role(conn, firm_id, ALL_ROLES)
    _doc_row(conn, firm_id, document_id)
    rows = conn.execute(
        text("""SELECT id, version, source, ai_model, overall_confidence, is_current, created_by, created_at, data
                  FROM public.extracted_data WHERE firm_id = :f AND document_id = :d ORDER BY version DESC"""),
        {"f": firm_id, "d": document_id}).mappings().all()
    return [dict(r) for r in rows]


# ============================================================================ corrections
@router.put("/documents/{document_id}/extraction", summary="Save a corrected extraction (new version) and rebuild draft")
def correct_extraction(firm_id: uuid.UUID, document_id: uuid.UUID, body: ExtractedDocument, conn: DB) -> dict:
    role = require_firm_role(conn, firm_id, WRITERS)
    d = _doc_row(conn, firm_id, document_id)
    if d["status"] in ("approved", "exported"):
        raise ApiError(409, "document_locked", "approved/exported documents cannot be corrected; reopen first")
    body.confidence = {}
    save_extraction(conn, firm_id=firm_id, document_id=document_id, doc=body, meta={"source": "manual"},
                    created_by=conn.execute(text("SELECT auth.uid()")).scalar_one())
    conn.execute(text("""INSERT INTO public.approvals (firm_id, document_id, action, actor_user_id, comment)
                         VALUES (:f, :d, 'correct', auth.uid(), 'Belge verisi düzeltildi')"""),
                 {"f": firm_id, "d": document_id})
    if role in BOOKERS:
        try:
            build_journal(conn, firm_id=firm_id, document_id=document_id, created_by=None)
        except ProcessingError as exc:
            raise ApiError(409, exc.code, str(exc)) from exc
    else:
        _enqueue(conn, firm_id, document_id, job_type="generate_journal", priority=10)
    conn.execute(text("UPDATE public.documents SET status = 'needs_review' WHERE firm_id = :f AND id = :d"),
                 {"f": firm_id, "d": document_id})
    audit(conn, firm_id, "document.corrected", "document", document_id)
    return get_document(firm_id, document_id, conn)


class JournalLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_code: str
    description: str | None = Field(default=None, max_length=500)
    debit: Decimal = Field(default=Decimal(0), ge=0, decimal_places=2)
    credit: Decimal = Field(default=Decimal(0), ge=0, decimal_places=2)
    vat_rate: Decimal | None = None

    @field_validator("account_code")
    @classmethod
    def _code(cls, v: str) -> str:
        if not ACCOUNT_RE.match(v):
            raise ValueError("invalid account code")
        return v


class JournalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry_date: date
    description: str | None = Field(default=None, max_length=500)
    document_number: str | None = Field(default=None, max_length=100)
    voucher_type: Literal["mahsup", "tediye", "tahsil"] = "mahsup"
    lines: list[JournalLineIn] = Field(min_length=2, max_length=400)


@router.put("/documents/{document_id}/journal", summary="Replace the draft journal (manual edit; must balance)")
def edit_journal(firm_id: uuid.UUID, document_id: uuid.UUID, body: JournalIn, conn: DB) -> dict:
    require_firm_role(conn, firm_id, BOOKERS)
    d = _doc_row(conn, firm_id, document_id)
    for i, l in enumerate(body.lines, 1):
        if (l.debit > 0) == (l.credit > 0):
            raise ApiError(422, "line_invalid", f"line {i}: exactly one of debit/credit must be positive")
    td, tc = sum(l.debit for l in body.lines), sum(l.credit for l in body.lines)
    if td != tc:
        raise ApiError(422, "unbalanced", f"debit {td} ≠ credit {tc}")
    chart = {r[0] for r in conn.execute(text("""SELECT code FROM public.chart_of_accounts WHERE firm_id = :f
                AND taxpayer_id = :t AND is_active AND is_postable"""), {"f": firm_id, "t": d["taxpayer_id"]})}
    missing = sorted({l.account_code for l in body.lines} - chart) if chart else []
    if missing:
        raise ApiError(422, "account_not_in_chart", f"accounts not in chart: {', '.join(missing)}")
    je = conn.execute(text("""SELECT id, status FROM public.journal_entries WHERE firm_id = :f AND document_id = :d
                              AND status <> 'rejected'"""), {"f": firm_id, "d": document_id}).mappings().one_or_none()
    if je and je["status"] != "draft":
        raise ApiError(409, "journal_locked", "only draft journals can be edited")
    ed_id = conn.execute(text("""SELECT id FROM public.extracted_data WHERE firm_id = :f AND document_id = :d
                                 AND is_current"""), {"f": firm_id, "d": document_id}).scalar()
    if je:
        conn.execute(text("DELETE FROM public.journal_lines WHERE firm_id = :f AND journal_entry_id = :j"),
                     {"f": firm_id, "j": je["id"]})
        conn.execute(text("""UPDATE public.journal_entries SET entry_date = :dt, description = :desc,
                                document_number = :no, voucher_type = :vt, generated_by = 'manual', warnings = '[]'
                             WHERE id = :j"""),
                     {"dt": body.entry_date, "desc": body.description, "no": body.document_number,
                      "vt": body.voucher_type, "j": je["id"]})
        j = je["id"]
    else:
        j = conn.execute(text("""INSERT INTO public.journal_entries (firm_id, taxpayer_id, document_id,
                                    extracted_data_id, entry_date, voucher_type, document_number, description,
                                    generated_by, created_by)
                                 VALUES (:f, :t, :d, :e, :dt, :vt, :no, :desc, 'manual', auth.uid()) RETURNING id"""),
                         {"f": firm_id, "t": d["taxpayer_id"], "d": document_id, "e": ed_id, "dt": body.entry_date,
                          "vt": body.voucher_type, "no": body.document_number, "desc": body.description}).scalar_one()
    for i, l in enumerate(body.lines, 1):
        conn.execute(text("""INSERT INTO public.journal_lines (firm_id, journal_entry_id, line_no, account_code,
                                description, debit, credit, vat_rate, source_note)
                             VALUES (:f, :j, :n, :a, :desc, :d, :c, :r, 'manual')"""),
                     {"f": firm_id, "j": j, "n": i, "a": l.account_code, "desc": l.description, "d": l.debit,
                      "c": l.credit, "r": l.vat_rate})
    audit(conn, firm_id, "journal.edited", "journal_entry", j, {"lines": len(body.lines)})
    return _journal(conn, firm_id, document_id) or {}


# ============================================================================ approval workflow
class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    comment: str | None = Field(default=None, max_length=2000)
    acknowledge_duplicate: bool = False
    learn_mapping: bool = True


class ReasonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    comment: str | None = Field(default=None, max_length=2000)


def _approve_one(conn: Connection, firm_id, document_id, body: ApproveIn) -> None:
    d = _doc_row(conn, firm_id, document_id)
    if d["status"] != "needs_review":
        raise ApiError(409, "invalid_state", f"document is {d['status']}, not awaiting review")
    if d["duplicate_of"] and not body.acknowledge_duplicate:
        raise ApiError(409, "possible_duplicate", f"possible duplicate of document {d['duplicate_of']}")
    je = conn.execute(text("""SELECT id FROM public.journal_entries WHERE firm_id = :f AND document_id = :d
                              AND status = 'draft'"""), {"f": firm_id, "d": document_id}).scalar()
    if not je:
        raise ApiError(409, "journal_missing", "no draft journal to approve; edit the journal first")
    # DB trigger enforces balance, locked periods and approver role.
    conn.execute(text("UPDATE public.journal_entries SET status = 'approved' WHERE firm_id = :f AND id = :j"),
                 {"f": firm_id, "j": je})
    conn.execute(text("UPDATE public.documents SET status = 'approved' WHERE firm_id = :f AND id = :d"),
                 {"f": firm_id, "d": document_id})
    conn.execute(text("""INSERT INTO public.approvals (firm_id, document_id, journal_entry_id, action, actor_user_id,
                            comment) VALUES (:f, :d, :j, 'approve', auth.uid(), :c)"""),
                 {"f": firm_id, "d": document_id, "j": je, "c": body.comment})
    if body.learn_mapping and d["supplier_tax_number"] and d["direction"] != "sales":
        acc = conn.execute(text("""SELECT account_code FROM public.journal_lines WHERE journal_entry_id = :j
                                   AND debit > 0 AND coalesce(source_note,'') <> 'vat'
                                   ORDER BY debit DESC LIMIT 1"""), {"j": je}).scalar()
        if acc:
            conn.execute(text("""INSERT INTO public.account_mappings (firm_id, taxpayer_id, match_field, match_value,
                                    account_code, source, priority, created_by)
                                 VALUES (:f, :t, 'supplier_tax_number', :v, :a, 'learned', 500, auth.uid())
                                 ON CONFLICT DO NOTHING"""),
                         {"f": firm_id, "t": d["taxpayer_id"], "v": d["supplier_tax_number"], "a": acc})
    audit(conn, firm_id, "document.approved", "document", document_id)


@router.post("/documents/{document_id}/approve", summary="Approve document + journal (owner/admin/accountant)")
def approve(firm_id: uuid.UUID, document_id: uuid.UUID, body: ApproveIn, conn: DB) -> dict:
    require_firm_role(conn, firm_id, BOOKERS)
    _approve_one(conn, firm_id, document_id, body)
    return {"status": "approved"}


class BulkApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


@router.post("/documents/bulk-approve",
             summary="Approve many reviewed documents; skips duplicates, low confidence and error cases")
def bulk_approve(firm_id: uuid.UUID, body: BulkApproveIn, conn: DB) -> dict:
    require_firm_role(conn, firm_id, BOOKERS)
    threshold = get_settings().review_confidence_threshold
    approved, skipped = [], []
    for doc_id in dict.fromkeys(body.document_ids):
        row = conn.execute(text("""SELECT d.overall_confidence, d.duplicate_of,
                                      (SELECT count(*) FROM jsonb_array_elements(ed.validation_issues) i
                                        WHERE i->>'severity' = 'error') AS errors
                                     FROM public.documents d LEFT JOIN public.extracted_data ed
                                       ON ed.document_id = d.id AND ed.is_current
                                    WHERE d.firm_id = :f AND d.id = :d"""), {"f": firm_id, "d": doc_id}).mappings().one_or_none()
        reason = None
        if row is None:
            reason = "not_found"
        elif row["duplicate_of"]:
            reason = "possible_duplicate"
        elif row["errors"]:
            reason = "validation_errors"
        elif row["overall_confidence"] is None or float(row["overall_confidence"]) < threshold:
            reason = "low_confidence"
        if reason:
            skipped.append({"document_id": str(doc_id), "reason": reason})
            continue
        try:
            with conn.begin_nested():
                _approve_one(conn, firm_id, doc_id, ApproveIn())
            approved.append(str(doc_id))
        except ApiError as exc:
            skipped.append({"document_id": str(doc_id), "reason": exc.detail["code"]})
        except Exception:  # noqa: BLE001 — e.g. locked period/unbalanced (DB trigger)
            skipped.append({"document_id": str(doc_id), "reason": "rejected_by_database_rules"})
    return {"approved": approved, "skipped": skipped}


@router.post("/documents/{document_id}/reject", summary="Reject document (owner/admin/accountant)")
def reject(firm_id: uuid.UUID, document_id: uuid.UUID, body: ReasonIn, conn: DB) -> dict:
    require_firm_role(conn, firm_id, BOOKERS)
    d = _doc_row(conn, firm_id, document_id)
    if d["status"] in ("approved", "exported"):
        raise ApiError(409, "invalid_state", "reopen the document before rejecting it")
    je = conn.execute(text("""UPDATE public.journal_entries SET status = 'rejected' WHERE firm_id = :f
                              AND document_id = :d AND status = 'draft' RETURNING id"""),
                      {"f": firm_id, "d": document_id}).scalar()
    conn.execute(text("UPDATE public.documents SET status = 'rejected' WHERE firm_id = :f AND id = :d"),
                 {"f": firm_id, "d": document_id})
    conn.execute(text("""INSERT INTO public.approvals (firm_id, document_id, journal_entry_id, action, actor_user_id,
                            comment) VALUES (:f, :d, :j, 'reject', auth.uid(), :c)"""),
                 {"f": firm_id, "d": document_id, "j": je, "c": body.comment})
    audit(conn, firm_id, "document.rejected", "document", document_id)
    return {"status": "rejected"}


@router.post("/documents/{document_id}/reopen", summary="Reopen an approved (not exported) or rejected document")
def reopen(firm_id: uuid.UUID, document_id: uuid.UUID, body: ReasonIn, conn: DB) -> dict:
    require_firm_role(conn, firm_id, BOOKERS)
    d = _doc_row(conn, firm_id, document_id)
    if d["status"] == "exported":
        raise ApiError(409, "already_exported", "exported documents cannot be reopened; correct them in Luca")
    if d["status"] not in ("approved", "rejected"):
        raise ApiError(409, "invalid_state", "only approved or rejected documents can be reopened")
    je = conn.execute(text("""SELECT id, status FROM public.journal_entries WHERE firm_id = :f AND document_id = :d
                              ORDER BY created_at DESC LIMIT 1"""), {"f": firm_id, "d": document_id}).mappings().one_or_none()
    if je and je["status"] in ("approved", "rejected"):
        conn.execute(text("UPDATE public.journal_entries SET status = 'draft' WHERE id = :j"), {"j": je["id"]})
    conn.execute(text("UPDATE public.documents SET status = 'needs_review' WHERE firm_id = :f AND id = :d"),
                 {"f": firm_id, "d": document_id})
    conn.execute(text("""INSERT INTO public.approvals (firm_id, document_id, journal_entry_id, action, actor_user_id,
                            comment) VALUES (:f, :d, :j, 'reopen', auth.uid(), :c)"""),
                 {"f": firm_id, "d": document_id, "j": je["id"] if je else None, "c": body.comment})
    audit(conn, firm_id, "document.reopened", "document", document_id)
    return {"status": "needs_review"}


@router.post("/documents/{document_id}/reprocess", summary="Run extraction again (not for approved/exported)")
def reprocess(firm_id: uuid.UUID, document_id: uuid.UUID, conn: DB) -> dict:
    require_firm_role(conn, firm_id, WRITERS)
    d = _doc_row(conn, firm_id, document_id)
    if d["status"] in ("approved", "exported", "processing"):
        raise ApiError(409, "invalid_state", f"document is {d['status']}")
    _enqueue(conn, firm_id, document_id, priority=20)
    conn.execute(text("UPDATE public.documents SET status = 'queued', error_code = NULL WHERE firm_id = :f AND id = :d"),
                 {"f": firm_id, "d": document_id})
    return {"status": "queued"}


def _withdraw_journals(conn: Connection, firm_id: uuid.UUID, doc_ids: list[uuid.UUID]) -> None:
    """Approved → draft → rejected (DB state machine); exported entries are never touched."""
    conn.execute(text("""UPDATE public.journal_entries SET status = 'draft' WHERE firm_id = :f
                         AND document_id = ANY(:ids) AND status = 'approved'"""), {"f": firm_id, "ids": doc_ids})
    conn.execute(text("""UPDATE public.journal_entries SET status = 'rejected' WHERE firm_id = :f
                         AND document_id = ANY(:ids) AND status = 'draft'"""), {"f": firm_id, "ids": doc_ids})


@router.delete("/documents/{document_id}", status_code=204,
               summary="Delete (soft) a document; approved ones too (journal withdrawn), exported ones are kept")
def delete_document(firm_id: uuid.UUID, document_id: uuid.UUID, conn: DB) -> Response:
    require_firm_role(conn, firm_id, MANAGERS)
    d = _doc_row(conn, firm_id, document_id)
    if d["status"] == "exported":
        raise ApiError(409, "document_locked", "exported documents are retained (already sent to Luca)")
    _withdraw_journals(conn, firm_id, [document_id])
    conn.execute(text("UPDATE public.documents SET deleted_at = now() WHERE firm_id = :f AND id = :d"),
                 {"f": firm_id, "d": document_id})
    audit(conn, firm_id, "document.deleted", "document", document_id)
    return Response(status_code=204)


class BulkDeleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    taxpayer_id: uuid.UUID | None = None   # None → every taxpayer of the firm
    document_ids: list[uuid.UUID] | None = Field(default=None, max_length=5000)  # None → all deletable


@router.post("/documents/bulk-delete",
             summary="Delete (soft) many documents incl. approved; exported documents are always kept")
def bulk_delete(firm_id: uuid.UUID, body: BulkDeleteIn, conn: DB) -> dict:
    require_firm_role(conn, firm_id, MANAGERS)
    params = {"f": firm_id, "t": body.taxpayer_id, "ids": body.document_ids}
    scope = """firm_id = :f AND deleted_at IS NULL
               AND (CAST(:t AS uuid) IS NULL OR taxpayer_id = :t)
               AND (CAST(:ids AS uuid[]) IS NULL OR id = ANY(:ids))"""
    kept = conn.execute(text(f"SELECT count(*) FROM public.documents WHERE {scope} "
                             "AND status = 'exported'"), params).scalar_one()
    ids = [r[0] for r in conn.execute(text(f"""SELECT id FROM public.documents WHERE {scope}
                                               AND status <> 'exported' FOR UPDATE"""), params)]
    if ids:
        _withdraw_journals(conn, firm_id, ids)
        conn.execute(text("UPDATE public.documents SET deleted_at = now() WHERE firm_id = :f AND id = ANY(:ids)"),
                     {"f": firm_id, "ids": ids})
        audit(conn, firm_id, "document.bulk_deleted", "document", None, {"count": len(ids)})
    return {"deleted": len(ids), "kept_locked": kept}
