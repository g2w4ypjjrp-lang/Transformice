"""Deterministic checks of extracted data + confidence scoring.

Nothing here invents data. The only derivations are arithmetic identities
(e.g. a VAT breakdown summed from line items that carry explicit rates), and
each derivation is reported as an issue so a reviewer sees it.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from app.domain.tax_ids import is_valid_tckn, is_valid_vkn
from app.services.extraction.schema import ExtractedDocument, VatGroup

VALID_VAT_RATES = {Decimal(x) for x in ("0", "1", "8", "10", "18", "20")}
CURRENT_VAT_RATES = {Decimal(x) for x in ("0", "1", "10", "20")}  # since 10.07.2023
RATE_CHANGE_DATE = date(2023, 7, 10)
TOL = Decimal("0.05")


def _close(a: Decimal | None, b: Decimal | None, tol: Decimal = TOL) -> bool:
    return a is not None and b is not None and abs(a - b) <= max(tol, abs(b) * Decimal("0.001"))


def validate_extraction(doc: ExtractedDocument, *, source: str, today: date | None = None) -> tuple[
        ExtractedDocument, list[dict], float]:
    """Return (possibly completed doc, issues, overall_confidence 0..1)."""
    today = today or date.today()
    issues: list[dict] = []

    def add(sev: str, code: str, msg: str, field: str | None = None) -> None:
        issues.append({"severity": sev, "code": code, "message": msg, "field": field})

    # --- mandatory fields ---------------------------------------------------------
    if doc.document_date is None:
        add("error", "date_missing", "document date could not be read", "document_date")
    else:
        if doc.document_date > today + timedelta(days=1):
            add("error", "date_in_future", "document date is in the future", "document_date")
        if doc.document_date < date(2000, 1, 1):
            add("error", "date_too_old", "document date is implausible", "document_date")
    if doc.total_amount is None:
        add("error", "total_missing", "total amount could not be read", "total_amount")
    elif doc.total_amount <= 0:
        add("error", "total_not_positive", "total amount must be positive", "total_amount")
    if not doc.supplier.name and not doc.supplier.tax_number:
        add("warning", "supplier_missing", "supplier could not be identified", "supplier")
    if doc.document_number is None:
        add("warning", "number_missing", "document number could not be read", "document_number")

    # --- tax numbers ----------------------------------------------------------------
    for role, party in (("supplier", doc.supplier), ("buyer", doc.buyer)):
        tn = party.tax_number
        if tn:
            ok = (len(tn) == 10 and is_valid_vkn(tn)) or (len(tn) == 11 and is_valid_tckn(tn))
            if not ok:
                add("warning", f"{role}_tax_number_invalid", f"{role} tax number fails checksum",
                    f"{role}.tax_number")

    # --- VAT breakdown ------------------------------------------------------------------
    if not doc.vat_breakdown and doc.lines and all(l.vat_rate is not None and l.net_amount is not None
                                                   for l in doc.lines):
        groups: dict[Decimal, VatGroup] = defaultdict(lambda: VatGroup(rate=Decimal(0), base=Decimal(0),
                                                                       amount=Decimal(0)))
        for l in doc.lines:
            g = groups[l.vat_rate]
            g.rate = l.vat_rate
            g.base += l.net_amount
            g.amount += l.vat_amount if l.vat_amount is not None else (l.net_amount * l.vat_rate / 100).quantize(
                Decimal("0.01"))
        doc.vat_breakdown = sorted(groups.values(), key=lambda g: g.rate)
        add("info", "vat_breakdown_derived", "VAT breakdown summed from line items", "vat_breakdown")

    for g in doc.vat_breakdown:
        if g.rate not in VALID_VAT_RATES:
            add("error", "vat_rate_invalid", f"unexpected VAT rate %{g.rate}", "vat_breakdown")
        elif doc.document_date and doc.document_date >= RATE_CHANGE_DATE and g.rate not in CURRENT_VAT_RATES:
            add("warning", "vat_rate_outdated", f"VAT rate %{g.rate} is not valid after 10.07.2023", "vat_breakdown")
        if g.base is not None and g.amount is not None:
            expected = (g.base * g.rate / 100).quantize(Decimal("0.01"))
            if not _close(g.amount, expected, Decimal("0.10")):
                add("error", "vat_amount_mismatch", f"VAT %{g.rate}: {g.amount} ≠ base × rate ({expected})",
                    "vat_breakdown")
        if g.base is None or g.amount is None:
            add("warning", "vat_group_incomplete", f"VAT %{g.rate} group lacks base or amount", "vat_breakdown")

    if doc.vat_breakdown:
        sum_base = sum((g.base or Decimal(0)) for g in doc.vat_breakdown)
        sum_vat = sum((g.amount or Decimal(0)) for g in doc.vat_breakdown)
        if doc.total_vat is None:
            doc.total_vat = sum_vat
        elif not _close(doc.total_vat, sum_vat):
            add("error", "total_vat_mismatch", f"total VAT {doc.total_vat} ≠ sum of groups {sum_vat}", "total_vat")
        if doc.total_net is None:
            doc.total_net = sum_base
        if doc.total_amount is not None and not _close(doc.total_amount, sum_base + sum_vat):
            add("error", "total_mismatch", f"total {doc.total_amount} ≠ net + VAT ({sum_base + sum_vat})",
                "total_amount")
    elif doc.total_amount is not None:
        add("warning", "vat_unknown", "no VAT information; VAT will not be deducted", "vat_breakdown")

    if doc.lines:
        nets = [l.net_amount for l in doc.lines if l.net_amount is not None]
        if len(nets) == len(doc.lines) and doc.total_net is not None:
            if not _close(sum(nets), doc.total_net, Decimal("0.10")):
                add("warning", "lines_net_mismatch", "sum of line nets differs from total net", "lines")

    if doc.withholding and doc.withholding.amount:
        if doc.total_vat is not None and doc.withholding.amount > doc.total_vat + TOL:
            add("error", "withholding_exceeds_vat", "withholding amount exceeds VAT", "withholding")
        if doc.withholding.ratio and doc.total_vat:
            expected = (doc.total_vat * doc.withholding.ratio).quantize(Decimal("0.01"))
            if not _close(doc.withholding.amount, expected, Decimal("0.10")):
                add("warning", "withholding_mismatch", f"withholding {doc.withholding.amount} ≠ VAT × ratio ({expected})",
                    "withholding")

    if doc.currency and doc.currency != "TRY" and not doc.exchange_rate:
        add("warning", "fx_rate_missing", "foreign-currency document without exchange rate", "currency")

    # --- confidence --------------------------------------------------------------------
    if source == "ubl_xml":
        base_conf = 1.0
    elif doc.confidence:
        vals = [max(0.0, min(1.0, float(v))) for v in doc.confidence.values()]
        # weakest critical field dominates
        critical = [doc.confidence.get(k) for k in ("total_amount", "document_date", "vat_breakdown")]
        crit = [max(0.0, min(1.0, float(v))) for v in critical if v is not None]
        base_conf = min(sum(vals) / len(vals), min(crit) if crit else 1.0)
    else:
        base_conf = 0.7
    penalty = sum({"error": 0.25, "warning": 0.07, "info": 0.0}[i["severity"]] for i in issues)
    overall = round(max(0.0, min(1.0, base_conf - penalty)), 3)
    return doc, issues, overall


def duplicate_fingerprint(doc: ExtractedDocument) -> str | None:
    """Business key for duplicate detection: supplier + number + date + total."""
    if not (doc.document_date and doc.total_amount and (doc.supplier.tax_number or doc.supplier.name)):
        return None
    who = doc.supplier.tax_number or "".join(ch for ch in (doc.supplier.name or "").upper() if ch.isalnum())[:40]
    number = "".join(ch for ch in (doc.document_number or "").upper() if ch.isalnum())
    return f"{who}|{number}|{doc.document_date.isoformat()}|{doc.total_amount:.2f}"
