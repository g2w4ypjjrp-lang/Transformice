"""Deterministic pre-export validation of vouchers.

Every rule here mirrors either an official Luca constraint ([S1] in
template.py) or a basic double-entry invariant. Errors block the export of the
affected voucher; the whole batch is refused if any error exists, so a file
never silently omits a voucher the user selected.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from decimal import Decimal

from app.services.luca.models import Issue, Voucher
from app.services.luca.template import LucaTemplate

ACCOUNT_CODE_RE = re.compile(r"^[0-9]{3}(\.[0-9A-Za-z]{1,10}){0,5}$")
_CENT = Decimal("0.01")


def validate_vouchers(
    vouchers: list[Voucher],
    template: LucaTemplate,
    *,
    chart_of_accounts: set[str] | None = None,
    already_exported_ids: set[uuid.UUID] | None = None,
    exported_fingerprints: dict[str, uuid.UUID] | None = None,
    allowed_duplicate_ids: Iterable[uuid.UUID] = (),
) -> list[Issue]:
    issues: list[Issue] = []
    already_exported_ids = already_exported_ids or set()
    exported_fingerprints = exported_fingerprints or {}
    allowed_dupes = set(allowed_duplicate_ids)

    taxpayers = {v.taxpayer_id for v in vouchers}
    if len(taxpayers) > 1:
        issues.append(Issue("error", "MIXED_TAXPAYERS",
                            "a Luca import file belongs to one company; export taxpayers separately"))

    seen_ids: set[uuid.UUID] = set()
    seen_fp: dict[str, uuid.UUID] = {}
    for v in vouchers:
        sid = v.source_id
        if sid in seen_ids:
            issues.append(Issue("error", "DUPLICATE_IN_BATCH", "voucher selected more than once", sid))
            continue
        seen_ids.add(sid)
        if sid in already_exported_ids:
            issues.append(Issue("error", "ALREADY_EXPORTED", "voucher was already exported to Luca", sid))

        fp = v.content_fingerprint()
        if sid not in allowed_dupes:
            if fp in seen_fp:
                issues.append(Issue("error", "DUPLICATE_CONTENT",
                                    f"same date, document number and lines as voucher {seen_fp[fp]}", sid))
            elif fp in exported_fingerprints and exported_fingerprints[fp] != sid:
                issues.append(Issue("error", "DUPLICATE_OF_EXPORTED",
                                    f"identical to previously exported voucher {exported_fingerprints[fp]}", sid))
        seen_fp.setdefault(fp, sid)

        issues.extend(_validate_one(v, template, chart_of_accounts))
    return issues


def _validate_one(v: Voucher, template: LucaTemplate, chart: set[str] | None) -> list[Issue]:
    out: list[Issue] = []
    sid = v.source_id

    if v.voucher_date is None:
        out.append(Issue("error", "DATE_MISSING", "Fiş Tarihi is mandatory", sid))
    if v.currency != "TRY":
        out.append(Issue("error", "CURRENCY_UNSUPPORTED",
                         "foreign-currency vouchers are blocked until the Döviz/Kur format is verified", sid))
    if len(v.lines) < 2:
        out.append(Issue("error", "TOO_FEW_LINES", "a voucher needs at least two lines", sid))
    if v.voucher_type == "mahsup" and len(v.lines) > template.max_lines_per_mahsup_voucher:
        out.append(Issue("error", "TOO_MANY_LINES",
                         f"Luca mahsup vouchers allow at most {template.max_lines_per_mahsup_voucher} lines", sid))

    for no, ln in enumerate(v.lines, start=1):
        if not ln.account_code or not ACCOUNT_CODE_RE.match(ln.account_code):
            out.append(Issue("error", "ACCOUNT_CODE_FORMAT", "invalid account code format", sid, no))
        elif chart is not None and ln.account_code not in chart:
            out.append(Issue("error", "ACCOUNT_NOT_IN_CHART",
                             f"account {ln.account_code} is not in the taxpayer's chart "
                             "(Luca rejects unknown codes: 'hesap kodu bulunamadı')", sid, no))
        for name, amount in (("debit", ln.debit), ("credit", ln.credit)):
            if amount < 0:
                out.append(Issue("error", "NEGATIVE_AMOUNT", f"{name} is negative", sid, no))
            if amount != amount.quantize(_CENT):
                out.append(Issue("error", "AMOUNT_PRECISION", f"{name} has more than 2 decimals", sid, no))
        if ln.debit > 0 and ln.credit > 0:
            out.append(Issue("error", "LINE_BOTH_SIDES", "a line cannot have both debit and credit", sid, no))
        if ln.debit == 0 and ln.credit == 0:
            out.append(Issue("error", "LINE_ZERO", "a line must have a debit or a credit amount", sid, no))

    if v.total_debit != v.total_credit:
        out.append(Issue("error", "UNBALANCED",
                         f"debit {v.total_debit:.2f} ≠ credit {v.total_credit:.2f}", sid))
    elif v.total_debit == 0:
        out.append(Issue("error", "ZERO_TOTAL", "voucher total is zero", sid))
    return out
