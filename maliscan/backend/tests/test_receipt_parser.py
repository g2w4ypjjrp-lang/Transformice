"""Rule-based Turkish receipt/invoice parsing (free OCR path)."""

import shutil
from datetime import date
from decimal import Decimal as D

import pytest

from app.services.extraction.receipt_parser import parse_amount, parse_text
from app.services.samples_docs import thermal_receipt_image

# Typical Tesseract output with OCR noise: “ instead of %, spaces inside amounts, comma thousands
OCR_MARKET = """ÖRNEK GIDA MARKET A.Ş.
KIZILAY MAH. ATATÜRK BLV. NO:5
KIZILAY V.D. 1231231231
TARİH: 05/11/2026 SAAT: 09:12
FİŞ NO: 1187
EKMEK            1      *10,00
SÜT 1L           1      *32,50
KİTAP            “10     *110,00
DETERJAN         “20     *120,00
TOPKDV                  *30,72
TOPLAM                 *272 ,50
KREDİ KARTI            *272 ,50
Z NO: 0456 EKÜ NO: 0001"""

EARSIV_PDF_TEXT = """e-Arşiv Fatura
ÖRNEK BİLİŞİM HİZMETLERİ A.Ş.
Kavaklıdere V.D. VKN: 9876543217
Fatura No: BLS2026000000007
Fatura Tarihi: 05-11-2026
Mal Hizmet Toplam Tutarı 1.000,00
Hesaplanan KDV(%20) 200,00
Vergiler Dahil Toplam Tutar 1.200,00
Ödenecek Tutar 1.200,00"""


def test_amount_parsing_tolerates_ocr_noise():
    assert parse_amount("*1.200,00") == D("1200.00")
    assert parse_amount("1,200,00") == D("1200.00")
    assert parse_amount("272 ,50") == D("272.50")
    assert parse_amount("1O,00") == D("10.00")
    assert parse_amount("abc") is None


def test_ocr_market_receipt_multi_rate():
    doc, notes = parse_text(OCR_MARKET, source="ocr")
    assert doc.document_type == "thermal_receipt"
    assert doc.supplier.name == "ÖRNEK GIDA MARKET A.Ş." and doc.supplier.tax_number == "1231231231"
    assert doc.document_number == "1187" and doc.document_date == date(2026, 11, 5)
    assert doc.payment_method == "credit_card" and doc.total_amount == D("272.50")
    assert {int(g.rate): (g.base, g.amount) for g in doc.vat_breakdown} == {
        1: (D("42.08"), D("0.42")), 10: (D("100.00"), D("10.00")), 20: (D("100.00"), D("20.00"))}
    codes = {n["code"] for n in notes}
    assert "ocr_vat_corrected" in codes and "vat_from_items" in codes   # printed TOPKDV disagrees with items
    assert max(doc.confidence.values()) < 0.85


def test_earsiv_pdf_text():
    doc, notes = parse_text(EARSIV_PDF_TEXT, source="pdf_text")
    assert doc.document_type == "e_archive_invoice"
    assert doc.supplier.tax_number == "9876543217" and doc.document_number == "BLS2026000000007"
    assert doc.document_date == date(2026, 11, 5) and doc.total_amount == D("1200.00")
    assert [(int(g.rate), g.base, g.amount) for g in doc.vat_breakdown] == [(20, D("1000.00"), D("200.00"))]


def test_nothing_invented_from_unrelated_text():
    doc, _ = parse_text("MERHABA DÜNYA\nBU BİR FİŞ DEĞİL", source="ocr")
    assert doc.total_amount is None and doc.document_date is None and doc.vat_breakdown == []
    assert doc.supplier.tax_number is None


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract not installed")
def test_real_tesseract_on_synthetic_receipt_photo():
    from app.services.extraction.local_ocr import ocr_image

    doc, _ = parse_text(ocr_image(thermal_receipt_image(tilt=2)), source="ocr")
    assert doc.total_amount == D("1200.00") and doc.document_date == date(2026, 11, 3)
    assert doc.payment_method == "cash" and doc.supplier.tax_number == "9876543217"
    assert [(int(g.rate), g.base, g.amount) for g in doc.vat_breakdown] == [(20, D("1000.00"), D("200.00"))]


# Noise patterns observed on a real phone photo (receipt lying on printed paper);
# company name / VKN replaced with fictitious values.
OCR_REAL_PHOTO_PSM4 = """AKYOL KUTU               —
SAN.VE DIŞ TİC.LTD.ŞTİ.
İKİTELLİ 3. ADA NO:2-4            —
GÜNEŞLİ VD 1231231231           —
TİC.SİC. NO:77618?
Es 08-10-2026            FİŞ NO:20
—i AMBALAJ “520          “5.750,00
© TOPKDV               *958,33
© TOPLAM              *5.750,00  #
<Öİ NAKİT                 5.750,00
Nİ EKÜNOSİ               Z NO:B20"""

OCR_REAL_PHOTO_PSM6 = """NE          AKYOL KUTU          —
SAN.VE DİŞ TİC.LTD.ŞTİ.
AN2         GÜNEŞLİ VD 1231231231           —
>:  UR-10-20206              FİŞ NO:20    mi
ef  AMBALAJ  20 v7 0,00    >
*-) TOPKDV                   *95B,33
#&İ TOPLAM                 5.750,00
İİ Nakir                     “5.7500U"""


def test_real_photo_noise_patterns():
    doc, notes = parse_text(OCR_REAL_PHOTO_PSM4, source="ocr")
    assert doc.supplier.name == "AKYOL KUTU SAN.VE DIŞ TİC.LTD.ŞTİ."   # two wrapped lines merged
    assert doc.supplier.tax_number == "1231231231" and doc.document_number == "20"
    assert doc.document_date == date(2026, 10, 8) and doc.payment_method == "cash"
    assert doc.total_amount == D("5750.00")
    assert [(int(g.rate), g.base, g.amount) for g in doc.vat_breakdown] == [(20, D("4791.67"), D("958.33"))]


def test_rate_derived_from_topkdv_and_total_when_items_unreadable():
    doc, notes = parse_text(OCR_REAL_PHOTO_PSM6, source="ocr")
    assert doc.total_amount == D("5750.00")                       # "5.750,00"
    assert [(int(g.rate), g.amount) for g in doc.vat_breakdown] == [(20, D("958.33"))]   # "95B,33" → 958,33
    assert "vat_rate_from_totals" in {n["code"] for n in notes}


def test_score_prefers_complete_consistent_parse():
    from app.services.extraction.receipt_parser import score

    a = score(*parse_text(OCR_REAL_PHOTO_PSM4, source="ocr"))
    b = score(*parse_text(OCR_REAL_PHOTO_PSM6, source="ocr"))
    assert a > b
