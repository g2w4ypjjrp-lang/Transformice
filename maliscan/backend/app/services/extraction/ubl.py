"""UBL-TR (GİB e-Fatura / e-Arşiv Fatura) XML parser.

Electronic invoices carry the authoritative data, so they are parsed directly
— no OCR, no AI. Parsing uses defusedxml (no XXE / entity expansion).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as SafeET

from app.services.extraction.schema import ExtractedDocument, Line, Party, VatGroup, Withholding

NS = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}
VAT_CODE = "0015"  # KDV tax type code in UBL-TR


class UblError(ValueError):
    pass


def _t(el: Element | None, path: str) -> str | None:
    if el is None:
        return None
    found = el.find(path, NS)
    if found is None or found.text is None:
        return None
    v = found.text.strip()
    return v or None


def _d(el: Element | None, path: str) -> Decimal | None:
    v = _t(el, path)
    if v is None:
        return None
    try:
        return Decimal(v)
    except InvalidOperation as exc:
        raise UblError(f"invalid number at {path}") from exc


def _party(p: Element | None) -> Party:
    if p is None:
        return Party()
    tax_no = None
    for pid in p.findall("cac:PartyIdentification/cbc:ID", NS):
        if (pid.get("schemeID") or "").upper() in ("VKN", "TCKN") and pid.text:
            tax_no = pid.text.strip()
            break
    name = _t(p, "cac:PartyName/cbc:Name")
    if not name:
        first, family = _t(p, "cac:Person/cbc:FirstName"), _t(p, "cac:Person/cbc:FamilyName")
        name = " ".join(x for x in (first, family) if x) or None
    addr_parts = [_t(p, f"cac:PostalAddress/cbc:{k}") for k in ("StreetName", "BuildingNumber", "CitySubdivisionName",
                                                                   "CityName")]
    return Party(name=name, tax_number=tax_no, tax_office=_t(p, "cac:PartyTaxScheme/cac:TaxScheme/cbc:Name"),
                 address=", ".join(a for a in addr_parts if a) or None)


def _withholding(el: Element | None) -> Withholding | None:
    if el is None:
        return None
    sub = el.find("cac:TaxSubtotal", NS)
    amount = _d(el, "cbc:TaxAmount")
    percent = _d(sub, "cbc:Percent") if sub is not None else None
    code = _t(sub, "cac:TaxCategory/cac:TaxScheme/cbc:TaxTypeCode") if sub is not None else None
    return Withholding(code=code, ratio=(percent / 100) if percent is not None else None, amount=amount)


def parse_ubl(content: bytes, *, taxpayer_tax_number: str | None = None) -> ExtractedDocument:
    try:
        root = SafeET.fromstring(content)
    except Exception as exc:  # noqa: BLE001
        raise UblError("XML could not be parsed") from exc
    if not root.tag.endswith("}Invoice"):
        raise UblError("not a UBL Invoice document")

    profile = (_t(root, "cbc:ProfileID") or "").upper()
    issue = _t(root, "cbc:IssueDate")
    try:
        issue_date = date.fromisoformat(issue) if issue else None
    except ValueError as exc:
        raise UblError("invalid IssueDate") from exc

    supplier = _party(root.find("cac:AccountingSupplierParty/cac:Party", NS))
    buyer = _party(root.find("cac:AccountingCustomerParty/cac:Party", NS))

    direction = None
    if taxpayer_tax_number:
        if supplier.tax_number == taxpayer_tax_number:
            direction = "sales"
        elif buyer.tax_number == taxpayer_tax_number:
            direction = "purchase"

    vat_groups: list[VatGroup] = []
    for tt in root.findall("cac:TaxTotal", NS):
        for sub in tt.findall("cac:TaxSubtotal", NS):
            if _t(sub, "cac:TaxCategory/cac:TaxScheme/cbc:TaxTypeCode") not in (VAT_CODE, None):
                continue  # ÖTV etc. are kept in lines/notes, not as VAT
            rate = _d(sub, "cbc:Percent")
            if rate is None:
                continue
            vat_groups.append(VatGroup(rate=rate, base=_d(sub, "cbc:TaxableAmount"), amount=_d(sub, "cbc:TaxAmount")))
    merged: dict[Decimal, VatGroup] = {}
    for g in vat_groups:
        m = merged.setdefault(g.rate, VatGroup(rate=g.rate, base=Decimal("0"), amount=Decimal("0")))
        m.base = (m.base or 0) + (g.base or 0)
        m.amount = (m.amount or 0) + (g.amount or 0)

    lines = []
    for ln in root.findall("cac:InvoiceLine", NS):
        qty_el = ln.find("cbc:InvoicedQuantity", NS)
        vat_sub = None
        for sub in ln.findall("cac:TaxTotal/cac:TaxSubtotal", NS):
            if _t(sub, "cac:TaxCategory/cac:TaxScheme/cbc:TaxTypeCode") in (VAT_CODE, None):
                vat_sub = sub
                break
        wh = _withholding(ln.find("cac:WithholdingTaxTotal", NS))
        net = _d(ln, "cbc:LineExtensionAmount")
        vat_amount = _d(vat_sub, "cbc:TaxAmount") if vat_sub is not None else None
        lines.append(Line(
            description=_t(ln, "cac:Item/cbc:Name") or _t(ln, "cac:Item/cbc:Description"),
            quantity=Decimal(qty_el.text.strip()) if qty_el is not None and qty_el.text else None,
            unit=qty_el.get("unitCode") if qty_el is not None else None,
            unit_price=_d(ln, "cac:Price/cbc:PriceAmount"),
            discount_amount=_d(ln, "cac:AllowanceCharge/cbc:Amount"),
            net_amount=net,
            vat_rate=_d(vat_sub, "cbc:Percent") if vat_sub is not None else None,
            vat_amount=vat_amount,
            total_amount=(net + vat_amount) if net is not None and vat_amount is not None else None,
            withholding_code=wh.code if wh else None,
            withholding_ratio=wh.ratio if wh else None,
            withholding_amount=wh.amount if wh else None,
        ))

    doc_wh = _withholding(root.find("cac:WithholdingTaxTotal", NS))
    if doc_wh is None and any(l.withholding_amount for l in lines):
        doc_wh = Withholding(code=next((l.withholding_code for l in lines if l.withholding_code), None),
                             ratio=next((l.withholding_ratio for l in lines if l.withholding_ratio), None),
                             amount=sum((l.withholding_amount or Decimal(0)) for l in lines))

    totals = root.find("cac:LegalMonetaryTotal", NS)
    total_net = _d(totals, "cbc:TaxExclusiveAmount") or _d(totals, "cbc:LineExtensionAmount")
    total_incl = _d(totals, "cbc:TaxInclusiveAmount")
    payable = _d(totals, "cbc:PayableAmount")
    total_vat = sum((g.amount or Decimal(0)) for g in merged.values()) if merged else None

    doc_type = "e_archive_invoice" if "EARSIV" in profile else "e_invoice"
    payment = None
    means = _t(root, "cac:PaymentMeans/cbc:PaymentMeansCode")
    if means in ("10",):
        payment = "cash"
    elif means in ("42", "31", "1"):
        payment = "bank"
    elif means in ("48", "ZZZ") and "KART" in (_t(root, "cac:PaymentMeans/cbc:InstructionNote") or "").upper():
        payment = "credit_card"

    return ExtractedDocument(
        document_type=doc_type,
        direction=direction,
        supplier=supplier,
        buyer=buyer,
        document_number=_t(root, "cbc:ID"),
        ettn=_t(root, "cbc:UUID"),
        document_date=issue_date,
        currency=_t(root, "cbc:DocumentCurrencyCode") or "TRY",
        exchange_rate=_d(root, "cac:PricingExchangeRate/cbc:CalculationRate"),
        payment_method=payment,
        lines=lines,
        vat_breakdown=sorted(merged.values(), key=lambda g: g.rate),
        withholding=doc_wh,
        total_net=total_net,
        total_vat=total_vat,
        # Gross invoice amount (incl. VAT); withholding is accounted separately.
        total_amount=total_incl if total_incl is not None else payable,
        notes="; ".join(n.text.strip() for n in root.findall("cbc:Note", NS) if n.text and n.text.strip())[:1000] or None,
    )
