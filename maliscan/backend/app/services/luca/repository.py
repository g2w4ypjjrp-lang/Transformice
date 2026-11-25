"""Database side of the Luca export: load approved journals, export atomically.

Runs inside an RLS-scoped user transaction (see app.db.session.user_transaction),
so the caller can only ever reach journals of their own firm. Duplicate exports
are prevented three ways:
  1. rows are locked (FOR UPDATE) and must be in status 'approved';
  2. status moves to 'exported' in the same transaction (DB state machine);
  3. content fingerprints of earlier exports are checked (same receipt booked twice).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol

from sqlalchemy import Connection, text

from app.services.luca.models import ExportResult, Issue, Voucher, VoucherLine
from app.services.luca.service import LucaExportService


class ExportPermissionError(PermissionError):
    """Caller's firm role may not export (only owner, admin, accountant)."""


class ExportStorage(Protocol):
    bucket: str

    def put(self, path: str, content: bytes, content_type: str) -> None: ...


_CONTENT_TYPES = {
    "csv": "text/csv",
    "fatura_listesi": "text/csv",
    "fatura_listesi_hesapli": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _load_vouchers(conn: Connection, firm_id: uuid.UUID, taxpayer_id: uuid.UUID,
                   where: str, params: dict, lock: bool) -> list[tuple[Voucher, str]]:
    rows = conn.execute(
        text(f"""SELECT id, taxpayer_id, entry_date, document_number, description, voucher_type,
                        currency, status
                   FROM public.journal_entries
                  WHERE firm_id = :firm AND taxpayer_id = :tp AND {where}
                  ORDER BY entry_date, id {'FOR UPDATE' if lock else ''}"""),
        {"firm": firm_id, "tp": taxpayer_id, **params},
    ).mappings().all()
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    lines_by_entry: dict[uuid.UUID, list[VoucherLine]] = defaultdict(list)
    for ln in conn.execute(
        text("""SELECT journal_entry_id, account_code, debit, credit, description
                  FROM public.journal_lines WHERE journal_entry_id = ANY(:ids)
                 ORDER BY journal_entry_id, line_no"""),
        {"ids": ids},
    ).mappings():
        lines_by_entry[ln["journal_entry_id"]].append(
            VoucherLine(ln["account_code"], Decimal(ln["debit"]), Decimal(ln["credit"]), ln["description"]))
    return [
        (Voucher(
            source_id=r["id"], taxpayer_id=r["taxpayer_id"], voucher_date=r["entry_date"],
            lines=tuple(lines_by_entry[r["id"]]), document_number=r["document_number"],
            document_date=r["entry_date"], description=r["description"],
            voucher_type=r["voucher_type"], currency=r["currency"],
        ), r["status"])
        for r in rows
    ]


def export_journal_entries(
    conn: Connection,
    *,
    firm_id: uuid.UUID,
    taxpayer_id: uuid.UUID,
    entry_ids: list[uuid.UUID],
    service: LucaExportService,
    storage: ExportStorage,
    allowed_duplicate_ids: list[uuid.UUID] | None = None,
) -> tuple[uuid.UUID | None, ExportResult]:
    """Export the given approved entries. Returns (export_job_id, result).

    On any validation error nothing is written and job id is None.
    """
    allowed = conn.execute(
        text("SELECT app.has_firm_role(:f, ARRAY['owner','admin','accountant'])"), {"f": firm_id}
    ).scalar_one()
    if not allowed:
        raise ExportPermissionError("Luca export requires owner, admin or accountant role")

    requested = list(dict.fromkeys(entry_ids))
    loaded = _load_vouchers(conn, firm_id, taxpayer_id, "id = ANY(:ids)", {"ids": requested}, lock=True)
    found = {v.source_id: (v, status) for v, status in loaded}

    pre_issues: list[Issue] = []
    for eid in requested:
        if eid not in found:
            pre_issues.append(Issue("error", "NOT_FOUND", "journal entry not found for this taxpayer", eid))
        elif found[eid][1] == "exported":
            pre_issues.append(Issue("error", "ALREADY_EXPORTED", "journal entry was already exported", eid))
        elif found[eid][1] != "approved":
            pre_issues.append(Issue("error", "NOT_APPROVED",
                                    "only approved journal entries can be exported", eid))
    if len(entry_ids) != len(requested):
        pre_issues.append(Issue("error", "DUPLICATE_IN_BATCH", "the same journal entry was selected twice"))

    chart = {
        r[0] for r in conn.execute(
            text("""SELECT code FROM public.chart_of_accounts
                     WHERE firm_id = :f AND taxpayer_id = :t AND is_active AND is_postable"""),
            {"f": firm_id, "t": taxpayer_id})
    }
    # Fingerprints of everything already exported for this taxpayer.
    previous = _load_vouchers(conn, firm_id, taxpayer_id, "status = 'exported'", {}, lock=False)
    exported_fps = {v.content_fingerprint(): v.source_id for v, _ in previous}

    vouchers = [found[e][0] for e in requested if e in found and found[e][1] == "approved"]
    if not vouchers:
        result = ExportResult(service.template.version, service.template.verified, issues=pre_issues
                              or [Issue("error", "EMPTY_BATCH", "no vouchers selected")])
        return None, result
    result = service.export(
        vouchers,
        chart_of_accounts=chart or None,  # empty chart → codes not checked (warned below)
        exported_fingerprints=exported_fps,
        allowed_duplicate_ids=allowed_duplicate_ids or (),
    )
    result.issues[:0] = pre_issues
    if not chart:
        result.issues.append(Issue("warning", "CHART_EMPTY",
                                   "taxpayer has no chart of accounts in MaliScan; account codes "
                                   "could not be checked against Luca"))
    if not result.ok:
        result.files = []
        result.exported_source_ids = []
        return None, result

    total = len(result.exported_source_ids)
    job_id = conn.execute(
        text("""INSERT INTO public.export_jobs (firm_id, taxpayer_id, template_version, status,
                       voucher_count, file_count, created_by)
                VALUES (:f, :t, :v, 'running', :n, :fc, auth.uid()) RETURNING id"""),
        {"f": firm_id, "t": taxpayer_id, "v": result.template_version, "n": total, "fc": len(result.files)},
    ).scalar_one()

    now = datetime.now(timezone.utc)
    ext = service.options.fmt
    for f in result.files:
        path = f"{firm_id}/{taxpayer_id}/{job_id}/{f.filename}"
        storage.put(path, f.content, _CONTENT_TYPES[ext])
        file_id = conn.execute(
            text("""INSERT INTO public.export_files (firm_id, export_job_id, file_index, storage_bucket,
                           storage_path, voucher_count, sha256)
                    VALUES (:f, :j, :i, :b, :p, :n, :h) RETURNING id"""),
            {"f": firm_id, "j": job_id, "i": f.file_index, "b": storage.bucket, "p": path,
             "n": f.voucher_count, "h": f.sha256},
        ).scalar_one()
        conn.execute(
            text("""UPDATE public.journal_entries
                       SET status = 'exported', export_job_id = :j, export_file_id = :fid, exported_at = :now
                     WHERE firm_id = :f AND id = ANY(:ids) AND status = 'approved'"""),
            {"j": job_id, "fid": file_id, "now": now, "f": firm_id,
             "ids": [uuid.UUID(s) for s in f.voucher_map.values()]},
        )

    conn.execute(text("UPDATE public.export_jobs SET status = 'completed', completed_at = now() WHERE id = :j"),
                 {"j": job_id})
    conn.execute(
        text("SELECT app.write_audit_log(:f, 'export.luca_created', 'export_job', :j, CAST(:m AS jsonb))"),
        {"f": firm_id, "j": job_id,
         "m": f'{{"vouchers": {total}, "files": {len(result.files)}, '
              f'"template": "{result.template_version}", "verified": {str(result.template_verified).lower()}}}'},
    )
    return job_id, result
