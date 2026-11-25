"""Document processing pipeline (runs in the worker, as service_role).

    file → (UBL XML | embedded XML in PDF | Claude [+ OCR hint]) → validation
         → extracted_data version + items → duplicate check → journal draft
         → status needs_review

Every query filters by firm_id explicitly because service_role bypasses RLS.
Logs carry identifiers only.
"""

from __future__ import annotations

import io
import json
import logging
import uuid
from decimal import Decimal

from sqlalchemy import Connection, text

from app.core.config import Settings
from app.services.accounting.engine import EngineError, Mapping, Rule, TaxpayerConfig, generate_journal
from app.services.extraction.claude import ClaudeExtractor, ExtractionError
from app.services.extraction.ocr import OcrError, get_ocr_provider
from app.services.extraction.schema import SCHEMA_VERSION, ExtractedDocument
from app.services.extraction.ubl import UblError, parse_ubl
from app.services.extraction.validate import duplicate_fingerprint, validate_extraction
from app.services.storage import Storage

log = logging.getLogger("maliscan.pipeline")


class ProcessingError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _embedded_ubl(pdf: bytes) -> bytes | None:
    """Some e-invoice PDFs embed the original UBL XML as an attachment."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(pdf))
        for name, blobs in (reader.attachments or {}).items():
            if name.lower().endswith(".xml"):
                for blob in blobs:
                    if b"Invoice" in blob[:2000]:
                        return blob
    except Exception:  # noqa: BLE001 — attachments are best-effort
        return None
    return None


def extract(content: bytes, mime_type: str, file_kind: str, *, taxpayer_tax_number: str, settings: Settings,
            claude: ClaudeExtractor | None = None) -> tuple[ExtractedDocument, dict]:
    """Return (document, metadata) choosing the most authoritative source."""
    if file_kind == "xml":
        try:
            return parse_ubl(content, taxpayer_tax_number=taxpayer_tax_number), {"source": "ubl_xml"}
        except UblError as exc:
            raise ProcessingError("invalid_ubl", str(exc)) from exc
    if file_kind == "pdf":
        xml = _embedded_ubl(content)
        if xml:
            try:
                return parse_ubl(xml, taxpayer_tax_number=taxpayer_tax_number), {"source": "ubl_xml",
                                                                                  "note": "embedded"}
            except UblError:
                pass
    if claude is None and not settings.anthropic_api_key:
        return _extract_free(content, mime_type, file_kind, taxpayer_tax_number=taxpayer_tax_number,
                             settings=settings)
    ocr_text, ocr_name = None, None
    try:
        provider = get_ocr_provider(settings)
        ocr_name = provider.name if provider.name != "none" else None
        ocr_text = provider.read_text(content, mime_type)
    except OcrError as exc:
        log.warning("ocr failed; continuing with vision only", extra={"error_code": type(exc).__name__})
    try:
        extractor = claude or ClaudeExtractor(settings)
        result = extractor.extract(content, mime_type, ocr_text=ocr_text)
    except ExtractionError as exc:
        retry = exc.code in ("ai_rate_limited", "ai_unavailable")
        raise ProcessingError(exc.code, str(exc), retryable=retry) from exc
    doc = result.document
    if doc.direction is None:
        if doc.supplier.tax_number and doc.supplier.tax_number == taxpayer_tax_number:
            doc.direction = "sales"
        else:
            doc.direction = "purchase"
    return doc, {"source": "ocr_ai", "ai_model": result.model, "ocr_provider": ocr_name, "raw_text": ocr_text,
                 "usage": result.usage}


def _extract_free(content: bytes, mime_type: str, file_kind: str, *, taxpayer_tax_number: str,
                  settings: Settings) -> tuple[ExtractedDocument, dict]:
    """No AI key: PDF text layer or on-server Tesseract OCR + Turkish receipt rules.
    Falls back to manual entry when nothing readable is found."""
    from app.services.extraction import local_ocr
    from app.services.extraction.receipt_parser import parse_text

    text, how = "", None
    try:
        if file_kind == "pdf":
            text = local_ocr.pdf_text(content)
            how = "pdf_text"
            if len(text.strip()) < 40 and settings.local_ocr_enabled and local_ocr.available():
                text, how = local_ocr.ocr_pdf(content), "ocr_rules"
        elif file_kind == "image" and settings.local_ocr_enabled and local_ocr.available():
            text, how = local_ocr.ocr_image(content), "ocr_rules"
    except (local_ocr.LocalOcrError, OSError, ValueError) as exc:
        log.warning("local ocr failed", extra={"error_code": type(exc).__name__})
        text = ""
    if len(text.strip()) < 20:
        return ExtractedDocument(document_type=None, direction="purchase", currency="TRY"), {
            "source": "manual", "manual_reason": "ai_not_configured"}
    doc, notes = parse_text(text, source=how or "ocr_rules")
    if doc.supplier.tax_number and doc.supplier.tax_number == taxpayer_tax_number:
        doc.direction = "sales"
    return doc, {"source": how, "ocr_provider": "tesseract" if how == "ocr_rules" else None,
                 "raw_text": text[:20000], "notes": notes}


def _taxpayer_config(conn: Connection, firm_id, taxpayer_id) -> tuple[TaxpayerConfig, set[str]]:
    tp = conn.execute(text("""SELECT * FROM public.taxpayers WHERE firm_id = :f AND id = :t"""),
                      {"f": firm_id, "t": taxpayer_id}).mappings().one()
    cfg = TaxpayerConfig(**{k: tp[k] for k in TaxpayerConfig.__dataclass_fields__ if k in tp and tp[k] is not None})
    chart = {r[0] for r in conn.execute(text("""SELECT code FROM public.chart_of_accounts WHERE firm_id = :f
                    AND taxpayer_id = :t AND is_active AND is_postable"""), {"f": firm_id, "t": taxpayer_id})}
    return cfg, chart


def _mappings_rules(conn: Connection, firm_id, taxpayer_id) -> tuple[list[Mapping], list[Rule]]:
    maps = [Mapping(match_field=r["match_field"], match_value=r["match_value"], account_code=r["account_code"],
                    vat_account_code=r["vat_account_code"], is_non_deductible=r["is_non_deductible"],
                    vat_deductible=r["vat_deductible"], priority=r["priority"],
                    taxpayer_specific=r["taxpayer_id"] is not None)
            for r in conn.execute(text("""SELECT * FROM public.account_mappings WHERE firm_id = :f AND is_active
                    AND (taxpayer_id = :t OR taxpayer_id IS NULL)"""), {"f": firm_id, "t": taxpayer_id}).mappings()]
    rules = [Rule(name=r["name"], conditions=r["conditions"], actions=r["actions"], priority=r["priority"],
                  stop_processing=r["stop_processing"])
             for r in conn.execute(text("""SELECT * FROM public.accounting_rules WHERE firm_id = :f AND is_active
                    AND (taxpayer_id = :t OR taxpayer_id IS NULL)"""), {"f": firm_id, "t": taxpayer_id}).mappings()]
    return maps, rules


def save_extraction(conn: Connection, *, firm_id, document_id, doc: ExtractedDocument, meta: dict,
                    created_by: uuid.UUID | None) -> uuid.UUID:
    """Store a new current extraction version + items and update document key fields."""
    doc, issues, confidence = validate_extraction(doc, source=meta["source"])
    for note in meta.get("notes") or []:
        issues.append({**note, "field": None})
    if meta.get("source") in ("ocr_rules", "pdf_text"):
        issues.insert(0, {"severity": "info", "code": "read_by_free_ocr",
                          "message": "Belge ücretsiz OCR ile otomatik okundu. Rakamları fişle karşılaştırıp onaylayın.",
                          "field": None})
    if meta.get("manual_reason") == "ai_not_configured":
        issues.insert(0, {"severity": "info", "code": "manual_entry_required",
                          "message": "Belge otomatik okunamadı: verileri (tarih, satıcı, KDV, toplam) "
                                     "görüntüye bakarak girin ve kaydedin.", "field": None})
        confidence = 0.0
    version = conn.execute(text("""SELECT coalesce(max(version), 0) + 1 FROM public.extracted_data
                                   WHERE firm_id = :f AND document_id = :d"""),
                           {"f": firm_id, "d": document_id}).scalar_one()
    conn.execute(text("UPDATE public.extracted_data SET is_current = false WHERE firm_id = :f AND document_id = :d"),
                 {"f": firm_id, "d": document_id})
    ed_id = conn.execute(
        text("""INSERT INTO public.extracted_data (firm_id, document_id, version, source, ocr_provider, ai_model,
                    schema_version, data, field_confidence, overall_confidence, validation_issues, raw_text, created_by)
                VALUES (:f, :d, :v, :src, :ocr, :ai, :sv, CAST(:data AS jsonb), CAST(:conf AS jsonb), :oc,
                        CAST(:iss AS jsonb), :raw, :by) RETURNING id"""),
        {"f": firm_id, "d": document_id, "v": version, "src": meta["source"], "ocr": meta.get("ocr_provider"),
         "ai": meta.get("ai_model"), "sv": SCHEMA_VERSION, "data": json.dumps(doc.to_json()),
         "conf": json.dumps(doc.confidence), "oc": confidence, "iss": json.dumps(issues),
         "raw": meta.get("raw_text"), "by": created_by},
    ).scalar_one()
    for i, ln in enumerate(doc.lines, start=1):
        conn.execute(
            text("""INSERT INTO public.document_items (firm_id, document_id, extracted_data_id, line_no, description,
                        quantity, unit, unit_price, discount_amount, net_amount, vat_rate, vat_amount, withholding_code,
                        withholding_ratio, withholding_amount, total_amount)
                    VALUES (:f, :d, :e, :n, :description, :quantity, :unit, :unit_price, :discount_amount, :net_amount,
                            :vat_rate, :vat_amount, :withholding_code, :withholding_ratio, :withholding_amount,
                            :total_amount)"""),
            {"f": firm_id, "d": document_id, "e": ed_id, "n": i,
             **{k: getattr(ln, k) for k in ("description", "quantity", "unit", "unit_price", "discount_amount",
                                            "net_amount", "vat_rate", "vat_amount", "withholding_code",
                                            "withholding_ratio", "withholding_amount", "total_amount")}},
        )
    fp = duplicate_fingerprint(doc)
    dup = None
    if fp:
        dup = conn.execute(
            text("""SELECT id FROM public.documents WHERE firm_id = :f AND duplicate_fingerprint = :fp AND id <> :d
                       AND deleted_at IS NULL AND status <> 'rejected'
                       AND taxpayer_id = (SELECT taxpayer_id FROM public.documents WHERE id = :d)
                     ORDER BY created_at LIMIT 1"""), {"f": firm_id, "fp": fp, "d": document_id}).scalar()
    conn.execute(
        text("""UPDATE public.documents SET document_type = :dt, direction = :dir, supplier_tax_number = :stn,
                    document_number = :no, document_date = :date, total_amount = :tot, currency = :cur,
                    overall_confidence = :conf, duplicate_fingerprint = :fp, duplicate_of = :dup
                 WHERE firm_id = :f AND id = :d"""),
        {"dt": doc.document_type if doc.document_type != "other" else "other", "dir": doc.direction,
         "stn": doc.supplier.tax_number, "no": doc.document_number, "date": doc.document_date,
         "tot": doc.total_amount, "cur": (doc.currency or "TRY")[:3], "conf": confidence, "fp": fp, "dup": dup,
         "f": firm_id, "d": document_id},
    )
    return ed_id


def build_journal(conn: Connection, *, firm_id, document_id, created_by: uuid.UUID | None) -> uuid.UUID | None:
    """(Re)generate the draft journal for a document from its current extraction.

    Returns the journal id, or None when a journal cannot be generated (the
    reason is stored in the extraction's validation issues for the reviewer).
    """
    doc_row = conn.execute(text("SELECT taxpayer_id FROM public.documents WHERE firm_id = :f AND id = :d"),
                           {"f": firm_id, "d": document_id}).mappings().one()
    ed = conn.execute(text("""SELECT id, data, validation_issues FROM public.extracted_data
                              WHERE firm_id = :f AND document_id = :d AND is_current"""),
                      {"f": firm_id, "d": document_id}).mappings().one()
    existing = conn.execute(text("""SELECT id, status FROM public.journal_entries WHERE firm_id = :f
                                    AND document_id = :d AND status <> 'rejected'"""),
                            {"f": firm_id, "d": document_id}).mappings().one_or_none()
    if existing and existing["status"] != "draft":
        raise ProcessingError("journal_locked", "journal is already approved or exported; reopen it first")
    if existing:
        conn.execute(text("DELETE FROM public.journal_entries WHERE firm_id = :f AND id = :j"),
                     {"f": firm_id, "j": existing["id"]})

    doc = ExtractedDocument.model_validate(ed["data"])
    cfg, chart = _taxpayer_config(conn, firm_id, doc_row["taxpayer_id"])
    maps, rules = _mappings_rules(conn, firm_id, doc_row["taxpayer_id"])
    try:
        draft = generate_journal(doc, cfg, mappings=maps, rules=rules, chart=chart or None)
    except EngineError as exc:
        issues = list(ed["validation_issues"]) + [{"severity": "error", "code": f"journal_{exc.code}",
                                                   "message": str(exc), "field": None}]
        conn.execute(text("""UPDATE public.extracted_data SET validation_issues = CAST(:i AS jsonb)
                             WHERE firm_id = :f AND id = :e"""), {"i": json.dumps(issues), "f": firm_id, "e": ed["id"]})
        return None

    j = conn.execute(
        text("""INSERT INTO public.journal_entries (firm_id, taxpayer_id, document_id, extracted_data_id, entry_date,
                    voucher_type, document_number, description, currency, generated_by, engine_version, warnings,
                    created_by)
                VALUES (:f, :t, :d, :e, :date, :vt, :no, :desc, :cur, :gen, :ev, CAST(:w AS jsonb), :by) RETURNING id"""),
        {"f": firm_id, "t": doc_row["taxpayer_id"], "d": document_id, "e": ed["id"], "date": draft.entry_date,
         "vt": draft.voucher_type, "no": (draft.document_number or "")[:100] or None, "desc": draft.description,
         "cur": draft.currency, "gen": "engine", "ev": draft.engine_version, "w": json.dumps(draft.warnings),
         "by": created_by},
    ).scalar_one()
    for i, l in enumerate(draft.lines, start=1):
        conn.execute(
            text("""INSERT INTO public.journal_lines (firm_id, journal_entry_id, line_no, account_code, description,
                        debit, credit, vat_rate, source_note)
                    VALUES (:f, :j, :n, :a, :desc, :d, :c, :r, :src)"""),
            {"f": firm_id, "j": j, "n": i, "a": l.account_code, "desc": (l.description or "")[:500] or None,
             "d": l.debit, "c": l.credit, "r": l.vat_rate, "src": (l.source_note or "")[:200] or None},
        )
    return j


class DocumentGone(Exception):
    """The document was deleted (or booked) while waiting/processing — skip quietly."""


def process_document_unlocked(engine, *, firm_id, document_id, storage: Storage, settings: Settings,
                              claude: ClaudeExtractor | None = None) -> str:
    """Pipeline in three short steps so slow OCR/AI never holds a row lock:
    1) read the document row, 2) download + extract with no transaction open,
    3) lock the row, re-check it is still processable, save results."""
    from app.db.session import service_transaction

    with service_transaction(engine) as conn:
        row = conn.execute(
            text("""SELECT d.*, t.tax_number AS taxpayer_tax_number FROM public.documents d
                      JOIN public.taxpayers t ON t.id = d.taxpayer_id AND t.firm_id = d.firm_id
                     WHERE d.firm_id = :f AND d.id = :d"""),
            {"f": firm_id, "d": document_id}).mappings().one_or_none()
    if row is None or row["deleted_at"] is not None or row["status"] in ("approved", "exported"):
        raise DocumentGone()
    content = storage.get(row["storage_bucket"], row["storage_path"])
    doc, meta = extract(content, row["mime_type"], row["file_kind"],
                        taxpayer_tax_number=row["taxpayer_tax_number"], settings=settings, claude=claude)
    with service_transaction(engine) as conn:
        current = conn.execute(text("""SELECT status, deleted_at FROM public.documents
                                       WHERE firm_id = :f AND id = :d FOR UPDATE"""),
                               {"f": firm_id, "d": document_id}).mappings().one_or_none()
        if current is None or current["deleted_at"] is not None or current["status"] in ("approved", "exported"):
            raise DocumentGone()
        save_extraction(conn, firm_id=firm_id, document_id=document_id, doc=doc, meta=meta, created_by=None)
        build_journal(conn, firm_id=firm_id, document_id=document_id, created_by=None)
        conn.execute(text("""UPDATE public.documents SET status = 'needs_review', error_code = NULL
                             WHERE firm_id = :f AND id = :d"""), {"f": firm_id, "d": document_id})
    return "needs_review"


def process_document(conn: Connection, *, firm_id, document_id, storage: Storage, settings: Settings,
                     claude: ClaudeExtractor | None = None) -> str:
    """Single-transaction variant (kept for tools/tests that already hold a connection)."""
    row = conn.execute(
        text("""SELECT d.*, t.tax_number AS taxpayer_tax_number FROM public.documents d
                  JOIN public.taxpayers t ON t.id = d.taxpayer_id AND t.firm_id = d.firm_id
                 WHERE d.firm_id = :f AND d.id = :d FOR UPDATE OF d"""),
        {"f": firm_id, "d": document_id}).mappings().one_or_none()
    if row is None or row["deleted_at"] is not None:
        raise ProcessingError("document_missing", "document no longer exists")
    if row["status"] in ("approved", "exported"):
        return row["status"]
    content = storage.get(row["storage_bucket"], row["storage_path"])
    doc, meta = extract(content, row["mime_type"], row["file_kind"],
                        taxpayer_tax_number=row["taxpayer_tax_number"], settings=settings, claude=claude)
    save_extraction(conn, firm_id=firm_id, document_id=document_id, doc=doc, meta=meta, created_by=None)
    build_journal(conn, firm_id=firm_id, document_id=document_id, created_by=None)
    conn.execute(text("""UPDATE public.documents SET status = 'needs_review', error_code = NULL
                         WHERE firm_id = :f AND id = :d"""), {"f": firm_id, "d": document_id})
    return "needs_review"


__all__ = ["ProcessingError", "DocumentGone", "process_document", "process_document_unlocked", "save_extraction", "build_journal", "extract", "Decimal"]
