"""Synthetic (fictitious) Turkish documents for tests and demos.

No real company or person data: tax numbers are generated to pass checksums,
names are invented.
"""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

from PIL import Image, ImageDraw, ImageFont

from app.domain.tax_ids import make_valid_vkn

SUPPLIER_VKN = make_valid_vkn("987654321")     # fictitious supplier
BUYER_VKN = make_valid_vkn("123123123")        # fictitious taxpayer (our customer)

_UBL_HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
 xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
 xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
<cbc:UBLVersionID>2.1</cbc:UBLVersionID><cbc:CustomizationID>TR1.2</cbc:CustomizationID>
<cbc:ProfileID>{profile}</cbc:ProfileID><cbc:ID>{number}</cbc:ID><cbc:CopyIndicator>false</cbc:CopyIndicator>
<cbc:UUID>{uuid}</cbc:UUID><cbc:IssueDate>{date}</cbc:IssueDate><cbc:InvoiceTypeCode>{type_code}</cbc:InvoiceTypeCode>
<cbc:Note>Sentetik test faturasıdır.</cbc:Note><cbc:DocumentCurrencyCode>TRY</cbc:DocumentCurrencyCode>
<cac:AccountingSupplierParty><cac:Party>
 <cac:PartyIdentification><cbc:ID schemeID="VKN">{supplier_vkn}</cbc:ID></cac:PartyIdentification>
 <cac:PartyName><cbc:Name>{supplier_name}</cbc:Name></cac:PartyName>
 <cac:PostalAddress><cbc:StreetName>Örnek Cad.</cbc:StreetName><cbc:BuildingNumber>1</cbc:BuildingNumber>
 <cbc:CitySubdivisionName>Çankaya</cbc:CitySubdivisionName><cbc:CityName>Ankara</cbc:CityName></cac:PostalAddress>
 <cac:PartyTaxScheme><cac:TaxScheme><cbc:Name>Kavaklıdere</cbc:Name></cac:TaxScheme></cac:PartyTaxScheme>
</cac:Party></cac:AccountingSupplierParty>
<cac:AccountingCustomerParty><cac:Party>
 <cac:PartyIdentification><cbc:ID schemeID="VKN">{buyer_vkn}</cbc:ID></cac:PartyIdentification>
 <cac:PartyName><cbc:Name>Deneme Mükellef Ltd. Şti.</cbc:Name></cac:PartyName>
</cac:Party></cac:AccountingCustomerParty>
<cac:PaymentMeans><cbc:PaymentMeansCode>{means}</cbc:PaymentMeansCode></cac:PaymentMeans>
"""


def _money(v: Decimal) -> str:
    return f"{v:.2f}"


def ubl_invoice(*, number: str = "ABC2026000000123", issue: date = date(2026, 11, 3), profile: str = "TICARIFATURA",
                lines: list[tuple[str, Decimal, Decimal, Decimal]] | None = None, withholding: tuple[str, Decimal] | None
                = None, supplier_vkn: str = SUPPLIER_VKN, buyer_vkn: str = BUYER_VKN,
                supplier_name: str = "Örnek Kırtasiye Ticaret A.Ş.", means: str = "42",
                uuid: str = "5f1c2b9e-1111-4a2b-9c3d-000000000001") -> bytes:
    """lines: (name, qty, unit_price, vat_rate). withholding: (code, ratio) e.g. ("624", Decimal("0.7"))."""
    lines = lines or [("A4 Fotokopi Kağıdı", Decimal(10), Decimal("150.00"), Decimal(20)),
                      ("Toner", Decimal(2), Decimal("500.00"), Decimal(20))]
    xml = [_UBL_HEAD.format(profile=profile, number=number, uuid=uuid, date=issue.isoformat(),
                            type_code="TEVKIFAT" if withholding else "SATIS", supplier_vkn=supplier_vkn,
                            supplier_name=supplier_name, buyer_vkn=buyer_vkn, means=means)]
    groups: dict[Decimal, list[Decimal]] = {}
    line_xml = []
    for i, (name, qty, price, rate) in enumerate(lines, 1):
        net = (qty * price).quantize(Decimal("0.01"))
        vat = (net * rate / 100).quantize(Decimal("0.01"))
        g = groups.setdefault(rate, [Decimal(0), Decimal(0)])
        g[0] += net
        g[1] += vat
        wh_xml = ""
        if withholding:
            wh = (vat * withholding[1]).quantize(Decimal("0.01"))
            wh_xml = (f"<cac:WithholdingTaxTotal><cbc:TaxAmount currencyID=\"TRY\">{_money(wh)}</cbc:TaxAmount>"
                      f"<cac:TaxSubtotal><cbc:TaxableAmount currencyID=\"TRY\">{_money(vat)}</cbc:TaxableAmount>"
                      f"<cbc:TaxAmount currencyID=\"TRY\">{_money(wh)}</cbc:TaxAmount>"
                      f"<cbc:Percent>{withholding[1] * 100:.0f}</cbc:Percent><cac:TaxCategory><cac:TaxScheme>"
                      f"<cbc:Name>KDV TEVKIFAT</cbc:Name><cbc:TaxTypeCode>{withholding[0]}</cbc:TaxTypeCode>"
                      f"</cac:TaxScheme></cac:TaxCategory></cac:TaxSubtotal></cac:WithholdingTaxTotal>")
        line_xml.append(
            f"<cac:InvoiceLine><cbc:ID>{i}</cbc:ID><cbc:InvoicedQuantity unitCode=\"C62\">{qty}</cbc:InvoicedQuantity>"
            f"<cbc:LineExtensionAmount currencyID=\"TRY\">{_money(net)}</cbc:LineExtensionAmount>"
            f"<cac:TaxTotal><cbc:TaxAmount currencyID=\"TRY\">{_money(vat)}</cbc:TaxAmount><cac:TaxSubtotal>"
            f"<cbc:TaxableAmount currencyID=\"TRY\">{_money(net)}</cbc:TaxableAmount>"
            f"<cbc:TaxAmount currencyID=\"TRY\">{_money(vat)}</cbc:TaxAmount><cbc:Percent>{rate}</cbc:Percent>"
            f"<cac:TaxCategory><cac:TaxScheme><cbc:Name>KDV</cbc:Name><cbc:TaxTypeCode>0015</cbc:TaxTypeCode>"
            f"</cac:TaxScheme></cac:TaxCategory></cac:TaxSubtotal></cac:TaxTotal>{wh_xml}"
            f"<cac:Item><cbc:Name>{name}</cbc:Name></cac:Item><cac:Price><cbc:PriceAmount currencyID=\"TRY\">"
            f"{_money(price)}</cbc:PriceAmount></cac:Price></cac:InvoiceLine>")
    total_vat = sum(v for _, v in groups.values())
    total_net = sum(n for n, _ in groups.values())
    xml.append(f"<cac:TaxTotal><cbc:TaxAmount currencyID=\"TRY\">{_money(total_vat)}</cbc:TaxAmount>")
    for rate, (net, vat) in sorted(groups.items()):
        xml.append(f"<cac:TaxSubtotal><cbc:TaxableAmount currencyID=\"TRY\">{_money(net)}</cbc:TaxableAmount>"
                   f"<cbc:TaxAmount currencyID=\"TRY\">{_money(vat)}</cbc:TaxAmount><cbc:Percent>{rate}</cbc:Percent>"
                   f"<cac:TaxCategory><cac:TaxScheme><cbc:Name>KDV</cbc:Name><cbc:TaxTypeCode>0015</cbc:TaxTypeCode>"
                   f"</cac:TaxScheme></cac:TaxCategory></cac:TaxSubtotal>")
    xml.append("</cac:TaxTotal>")
    wh_total = Decimal(0)
    if withholding:
        wh_total = (total_vat * withholding[1]).quantize(Decimal("0.01"))
        xml.append(f"<cac:WithholdingTaxTotal><cbc:TaxAmount currencyID=\"TRY\">{_money(wh_total)}</cbc:TaxAmount>"
                   f"<cac:TaxSubtotal><cbc:TaxableAmount currencyID=\"TRY\">{_money(total_vat)}</cbc:TaxableAmount>"
                   f"<cbc:TaxAmount currencyID=\"TRY\">{_money(wh_total)}</cbc:TaxAmount>"
                   f"<cbc:Percent>{withholding[1] * 100:.0f}</cbc:Percent><cac:TaxCategory><cac:TaxScheme>"
                   f"<cbc:Name>KDV TEVKIFAT</cbc:Name><cbc:TaxTypeCode>{withholding[0]}</cbc:TaxTypeCode>"
                   f"</cac:TaxScheme></cac:TaxCategory></cac:TaxSubtotal></cac:WithholdingTaxTotal>")
    xml.append(f"<cac:LegalMonetaryTotal><cbc:LineExtensionAmount currencyID=\"TRY\">{_money(total_net)}"
               f"</cbc:LineExtensionAmount><cbc:TaxExclusiveAmount currencyID=\"TRY\">{_money(total_net)}"
               f"</cbc:TaxExclusiveAmount><cbc:TaxInclusiveAmount currencyID=\"TRY\">{_money(total_net + total_vat)}"
               f"</cbc:TaxInclusiveAmount><cbc:PayableAmount currencyID=\"TRY\">"
               f"{_money(total_net + total_vat - wh_total)}</cbc:PayableAmount></cac:LegalMonetaryTotal>")
    xml.extend(line_xml)
    xml.append("</Invoice>")
    return "".join(xml).encode("utf-8")


def _font(size: int):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
                 "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def thermal_receipt_image(lines: list[str] | None = None, *, background=(40, 44, 52), tilt: float = 0.0) -> bytes:
    """A synthetic thermal-receipt photo: white paper on a dark table (for scanner/OCR tests)."""
    lines = lines or [
        "ÖRNEK AKARYAKIT LTD.ŞTİ.", "ANKARA YOLU NO:12 ÇANKAYA", f"V.D. KAVAKLIDERE  VKN {SUPPLIER_VKN}",
        "", "TARİH: 03.11.2026   SAAT: 14:25", "FİŞ NO: 0042", "", "KURŞUNSUZ 95      %20     *1.200,00",
        "", "TOPKDV                   *200,00", "TOPLAM                 *1.200,00", "NAKİT                  *1.200,00",
        "", "Z NO: 0123  EKÜ NO: 0001", "MF ABC 12345678",
    ]
    font = _font(28)
    paper_w, line_h = 640, 40
    paper = Image.new("RGB", (paper_w, 80 + line_h * len(lines)), (250, 250, 245))
    d = ImageDraw.Draw(paper)
    for i, line in enumerate(lines):
        d.text((30, 40 + i * line_h), line, fill=(20, 20, 20), font=font)
    canvas = Image.new("RGB", (paper.width + 300, paper.height + 300), background)
    if tilt:
        paper = paper.rotate(tilt, expand=True, fillcolor=background)
    canvas.paste(paper, (150, 150))
    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=92)
    return out.getvalue()


def invoice_pdf() -> bytes:
    """A one-page image-based PDF invoice (no text layer → exercises the vision path)."""
    img = Image.open(io.BytesIO(thermal_receipt_image([
        "SATIŞ FATURASI", "Örnek Bilişim Hizmetleri A.Ş.", f"VKN: {SUPPLIER_VKN}", "Fatura No: BLS2026000000007",
        "Tarih: 05.11.2026", "", "Yazılım bakım hizmeti     1.000,00", "KDV %20                    200,00",
        "GENEL TOPLAM             1.200,00"]))).convert("RGB")
    out = io.BytesIO()
    img.save(out, "PDF", resolution=150)
    return out.getvalue()
