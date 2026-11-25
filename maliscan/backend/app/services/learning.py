"""Learn the expense account a user chose for a supplier and apply it everywhere at once.

When a reviewer books a receipt to e.g. 770.20, every later receipt of the same supplier
(same VKN, or the same name when the VKN could not be read) gets 770.20 automatically —
including receipts that are already waiting for review. A later correction overwrites the
earlier choice: the user's latest decision always wins.
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import Connection, text

from app.domain.rules import norm
from app.domain.tax_ids import is_valid_tckn, is_valid_vkn

VAT_PREFIXES = ("191", "391", "360")


def supplier_name_key(name: str | None) -> str | None:
    """First three words of the normalised name: stable across OCR noise at the end of the line."""
    words = norm(name).split()[:3]
    key = " ".join(words)
    return key if len(key) >= 6 else None


def _expense_account(conn: Connection, journal_id) -> str | None:
    rows = conn.execute(text("""SELECT account_code, debit FROM public.journal_lines
                                WHERE journal_entry_id = :j AND debit > 0 ORDER BY debit DESC"""),
                        {"j": journal_id}).all()
    for code, _ in rows:
        if not code.startswith(VAT_PREFIXES):
            return code
    return None


def learn_from_journal(conn: Connection, firm_id, document_id, journal_id, *,
                       created_by: uuid.UUID | None = None) -> dict | None:
    """Store (or overwrite) the supplier → expense account mapping and re-book pending receipts
    of that supplier. Returns {"account": ..., "match_field": ..., "rebooked": n} or None."""
    d = conn.execute(text("""SELECT d.taxpayer_id, d.direction, d.supplier_tax_number,
                                    ed.data #>> '{supplier,name}' AS supplier_name
                               FROM public.documents d
                               LEFT JOIN public.extracted_data ed ON ed.document_id = d.id AND ed.is_current
                              WHERE d.firm_id = :f AND d.id = :d"""),
                     {"f": firm_id, "d": document_id}).mappings().one_or_none()
    if d is None or d["direction"] == "sales":
        return None
    account = _expense_account(conn, journal_id)
    if not account:
        return None
    vkn = (d["supplier_tax_number"] or "").strip()
    if vkn and (is_valid_vkn(vkn) or is_valid_tckn(vkn)):
        field, value = "supplier_tax_number", vkn
    else:
        field, value = "supplier_name", supplier_name_key(d["supplier_name"])
        if not value:
            return None
    conn.execute(text("""
        INSERT INTO public.account_mappings (firm_id, taxpayer_id, match_field, match_value, account_code,
                                             source, priority, created_by)
        VALUES (:f, :t, :mf, :v, :a, 'learned', 500, :u)
        ON CONFLICT (firm_id, taxpayer_id, match_field, lower(match_value))
        DO UPDATE SET account_code = EXCLUDED.account_code, is_active = true"""),
        {"f": firm_id, "t": d["taxpayer_id"], "mf": field, "v": value, "a": account,
         "u": created_by})
    rebooked = _rebook_pending(conn, firm_id, d["taxpayer_id"], document_id, field, value)
    return {"account": account, "match_field": field, "rebooked": rebooked}


def _rebook_pending(conn: Connection, firm_id, taxpayer_id, document_id, field: str, value: str) -> int:
    """Regenerate engine-made drafts of other receipts of the same supplier awaiting review."""
    from app.services.pipeline import ProcessingError, build_journal

    by_vkn = field == "supplier_tax_number"
    rows = conn.execute(text(f"""
        SELECT d.id, ed.data #>> '{{supplier,name}}' AS name
          FROM public.documents d
          JOIN public.extracted_data ed ON ed.document_id = d.id AND ed.is_current
          LEFT JOIN public.journal_entries je ON je.document_id = d.id AND je.status <> 'rejected'
         WHERE d.firm_id = :f AND d.taxpayer_id = :t AND d.id <> :d AND d.deleted_at IS NULL
           AND d.status = 'needs_review' AND coalesce(d.direction, 'purchase') <> 'sales'
           AND (je.id IS NULL OR (je.status = 'draft' AND je.generated_by = 'engine'))
           AND (NOT :by_vkn OR d.supplier_tax_number = :v)"""),
        {"f": firm_id, "t": taxpayer_id, "d": document_id, "by_vkn": by_vkn, "v": value}).mappings().all()
    n = 0
    for r in rows:
        if not by_vkn and value not in norm(r["name"]):
            continue
        try:
            with conn.begin_nested():
                if build_journal(conn, firm_id=firm_id, document_id=r["id"], created_by=None):
                    n += 1
        except ProcessingError:
            continue
    return n


def ensure_accounts(conn: Connection, firm_id, taxpayer_id, codes: set[str]) -> list[str]:
    """Create account codes the user typed that are not in the chart yet (e.g. 770.20), so they
    can be used right away and pass the Luca export check. Returns the created codes.
    Existing group accounts (with sub-accounts) are left alone — they cannot be posted to."""
    from app.api.routes.taxpayers import AccountIn, _account_row, _ensure_parents, _insert_accounts

    existing = {r[0]: r[1] for r in conn.execute(
        text("SELECT code, name FROM public.chart_of_accounts WHERE firm_id = :f AND taxpayer_id = :t"),
        {"f": firm_id, "t": taxpayer_id})}
    created = []
    for code in sorted(codes - set(existing), key=lambda c: c.count(".")):
        parent = code.rsplit(".", 1)[0] if "." in code else None
        base = existing.get(parent) if parent else None
        name = f"{base} ({code})" if base and base != parent else code
        _ensure_parents(conn, firm_id, taxpayer_id, code)
        _insert_accounts(conn, firm_id, taxpayer_id, [_account_row(AccountIn(code=code, name=name[:250]))])
        if parent:  # a parent with sub-accounts can no longer be posted to (Luca rule)
            conn.execute(text("""UPDATE public.chart_of_accounts SET is_postable = false
                                 WHERE taxpayer_id = :t AND code = :c"""), {"t": taxpayer_id, "c": parent})
        existing[code] = name
        created.append(code)
    if created:
        conn.execute(text("SELECT app.write_audit_log(:f, 'chart.auto_created', 'taxpayer', :t, CAST(:m AS jsonb))"),
                     {"f": firm_id, "t": taxpayer_id, "m": json.dumps({"accounts": len(created)})})
    return created
