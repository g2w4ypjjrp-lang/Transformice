"""Rule-based extraction from OCR / PDF text of Turkish receipts and invoices.

Free alternative to AI extraction. Works on the text layout of ÖKC (yazar kasa)
receipts ("TOPKDV", "TOPLAM", "%20", "NAKİT", "FİŞ NO", "Z NO") and common
e-Arşiv/e-Fatura PDF wording ("Hesaplanan KDV(%20)", "Ödenecek Tutar").

Principles (same as the AI path): a value is only filled when it is found in
the text; arithmetic is used to cross-check OCR digits, and any correction or
derivation is reported to the reviewer as an issue. Confidence is capped below
the bulk-approval threshold, so every OCR-read document is reviewed one by one.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

from app.domain.tax_ids import is_valid_tckn, is_valid_vkn
from app.services.extraction.schema import ExtractedDocument

VALID_RATES = {0, 1, 8, 10, 18, 20}
CENT = Decimal("0.01")
MAX_CONFIDENCE = 0.8  # always below review_confidence_threshold (0.85) → individual review

# amount with 2 decimals; tolerates OCR spaces ("272 ,50") and either separator
_D = r"[\dBOoSUD?]"
_AMOUNT = re.compile(rf"\*?\s*((?:\d{_D}{{0,2}}(?:[.,\s]{_D}{{3}})+|\d{_D}*)\s?[.,]\s?{_D}{{2}})(?![\dA-Za-z])")
_DATE = re.compile(r"(?<!\d)(\d{1,2})\s?[./-]\s?(\d{1,2})\s?[./-]\s?(\d{4}|\d{2})(?!\d)")
_RATE = re.compile(r"(?:^|\s)[%“”\"'°*#x]?5?\s?(1|8|10|18|20|0)\s*(?=\s{1,}[*“\"']?\s*\d)")


def fold(s: str) -> str:
    """Upper-case ASCII fold for keyword matching: 'Fİş No' → 'FIS NO'."""
    t = s.replace("ı", "i").replace("İ", "I").upper()
    t = unicodedata.normalize("NFKD", t)
    return "".join(c for c in t if not unicodedata.combining(c)).replace("Ş", "S")


_CONFUSABLE = str.maketrans({"O": "0", "o": "0", "D": "0", "U": "0", "Q": "0", "B": "8", "S": "5", "s": "5",
                              "I": "1", "l": "1", "|": "1", "İ": "1", "Z": "2", "z": "2", "G": "6", "?": "7"})


def fix_digits(token: str) -> str:
    """Map OCR letter/digit confusions inside a mostly-numeric token ('95B,33' → '958,33')."""
    digits = sum(c.isdigit() for c in token)
    return token.translate(_CONFUSABLE) if digits >= max(1, len(token.strip()) // 2) else token


def parse_amount(token: str) -> Decimal | None:
    t = fix_digits(token.replace("*", "").replace(" ", ""))
    m = re.fullmatch(r"(\d[\d.,]*)[.,](\d{2})", t)
    if not m:
        return None
    whole = re.sub(r"[.,]", "", m.group(1))
    try:
        return Decimal(f"{int(whole)}.{m.group(2)}")
    except (InvalidOperation, ValueError):
        return None


def last_amount(line: str) -> Decimal | None:
    found = _AMOUNT.findall(line)
    return parse_amount(found[-1]) if found else None


def _labelled(lines: list[str], *labels: str, exclude: tuple[str, ...] = ()) -> Decimal | None:
    for ln in lines:
        f = fold(ln)
        if any(lab in f for lab in labels) and not any(x in f for x in exclude):
            v = last_amount(ln)
            if v is not None:
                return v
    return None


_DATE_FUZZY = re.compile(r"(?<![\dA-Za-z])([0-9OUoIlB]{1,2})\s?[./\-—–]\s?([0-9OUoIlB]{1,2})\s?[./\-—–]\s?(20\d{2})")


def _date(text: str) -> date | None:
    found = _DATE.findall(text) + [(fix_digits(d + "0")[:-1] if len(d) else d, fix_digits(m + "0")[:-1], y)
                                   for d, m, y in _DATE_FUZZY.findall(text)]
    for d, m, y in found:
        if not (d.isdigit() and m.isdigit()):
            continue
        year = int(y) + (2000 if len(y) == 2 else 0)
        try:
            dt = date(year, int(m), int(d))
        except ValueError:
            continue
        if 2000 <= dt.year <= 2100:
            return dt
    return None


def _tax_number(lines: list[str]) -> str | None:
    candidates: list[tuple[int, str]] = []
    for i, ln in enumerate(lines):
        f = fold(ln)
        for num in re.findall(r"(?<!\d)(\d{10,11})(?!\d)", ln.replace(" ", "")):
            score = 2 if (any(k in f for k in ("VKN", "V.D", "VD.", "VERGI", "TCKN", "T.C"))
                          or re.search(r"\bVD\b", f)) else 0
            if (len(num) == 10 and is_valid_vkn(num)) or (len(num) == 11 and is_valid_tckn(num)):
                score += 3
            candidates.append((score - i * 0.01, num))  # earlier lines = seller
    if not candidates:
        return None
    best = max(candidates)
    return best[1] if best[0] >= 2 else None


def _clean_line(ln: str) -> str:
    """Drop OCR edge noise: short leading tokens ('NE', 'AN8', 'pM') and trailing dashes/symbols."""
    words = ln.strip().strip("—-–_|>'\"“”").split()
    while words and (len(words[0]) <= 3 and not re.search(r"A\.?S|LTD|STI", fold(words[0]))):
        words = words[1:]
    return " ".join(w for w in words if w not in ("—", "-", "–", "|")).strip(" —-–")


def _supplier(lines: list[str]) -> str | None:
    head = [_clean_line(ln) for ln in lines[:10]]
    company = re.compile(r"\b(LTD|STI|A\.S|AS\b|TIC|SAN|LIMITED|ANONIM|SIRKETI|PETROL|MARKET)")
    for i, ln in enumerate(head):
        if len(ln) >= 3 and company.search(fold(ln)):
            prev = head[i - 1] if i > 0 else ""
            # company names often wrap: "AKYILDIZ AMBALAJ" / "SAN.VE DIŞ TİC.LTD.ŞTİ."
            if prev and len(prev) >= 3 and not re.search(r"\d", prev) and prev.isupper() and not company.search(fold(prev)):
                return f"{prev} {ln}"
            return ln
    for ln in head:
        f = fold(ln)
        if len(ln) >= 3 and not re.search(r"\d", ln) and "FATURA" not in f and "FIS" not in f:
            return ln
    return None


def _tax_office(lines: list[str]) -> str | None:
    for ln in lines[:10]:
        m = re.search(r"([A-Za-zÇĞİÖŞÜçğıöşü .]{3,40}?)\s*V\.?\s?D\.?", ln)
        if m and m.group(1).strip():
            return m.group(1).strip().title()
        m = re.search(r"V\.?\s?D\.?\s*[:\-]?\s*([A-Za-zÇĞİÖŞÜçğıöşü ]{3,40})", ln)
        if m:
            words = [w for w in m.group(1).split() if not re.search(r"VKN|TCKN|^NO$|VERG", fold(w))]
            if words:
                return " ".join(words).title()
    return None


def _doc_number(lines: list[str]) -> str | None:
    for ln in lines:
        f = fold(ln)
        m = re.search(r"(FIS|BELGE|FATURA)\s*(NO|NUMARASI)\s*[:.]?\s*([A-Z0-9][A-Z0-9\-/]{0,24})", f)
        if m:
            return m.group(3)
    return None


def _payment(text_f: str) -> str | None:
    if re.search(r"KREDI\s*KART|K\.\s?KART|BANKA\s*KART|KARTLA|\bPOS\b", text_f):
        return "credit_card"
    if "NAKIT" in text_f:
        return "cash"
    if re.search(r"HAVALE|EFT\b", text_f):
        return "bank"
    return None


def parse_text(text: str, *, source: str) -> tuple[ExtractedDocument, list[dict]]:
    """Return (document, notes). `source` is 'ocr' or 'pdf_text' (affects wording only)."""
    notes: list[dict] = []
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    text_f = fold(text)

    doc_type = None
    if re.search(r"\bZ\s?NO\b|\bEKU\s?NO\b|\bMF\s?[A-Z]{2}", text_f):
        doc_type = "thermal_receipt"
    elif "E-ARSIV" in text_f or "EARSIV" in text_f:
        doc_type = "e_archive_invoice"
    elif "E-FATURA" in text_f:
        doc_type = "e_invoice"
    elif "FATURA" in text_f:
        doc_type = "purchase_invoice"
    elif "FIS" in text_f:
        doc_type = "retail_receipt"

    # ---- item lines with VAT rate (ÖKC receipts print VAT-inclusive prices per rate)
    gross_by_rate: dict[int, Decimal] = {}
    items = []
    for ln in lines:
        f = fold(ln)
        if any(k in f for k in ("TOPKDV", "TOPLAM", "KDV", "NAKIT", "KART", "PARA USTU", "ARA TOP")):
            continue
        amount = last_amount(ln)
        if amount is None:
            continue
        before = _AMOUNT.split(ln)[0] if _AMOUNT.search(ln) else ln
        rm = list(_RATE.finditer(before + " 0"))
        rate = None
        for r in rm[::-1]:
            if int(r.group(1)) in VALID_RATES:
                rate = int(r.group(1))
                desc = before[: r.start()].strip(" *%“\"")
                break
        if rate is None:
            continue
        gross_by_rate[rate] = gross_by_rate.get(rate, Decimal(0)) + amount
        items.append({"description": desc or None, "vat_rate": Decimal(rate), "total_amount": amount})

    total = _labelled(lines, "GENEL TOPLAM", "ODENECEK TUTAR", "VERGILER DAHIL TOPLAM", exclude=("ARA",)) \
        or _labelled(lines, "TOPLAM", exclude=("TOPKDV", "ARA TOPLAM", "KDV TOPLAM", "MAL HIZMET", "ISKONTO"))
    total_vat_read = _labelled(lines, "TOPKDV", "TOP KDV", "TOPLAM KDV", "KDV TOPLAM", "HESAPLANAN KDV")

    # e-Arşiv style per-rate VAT: "Hesaplanan KDV(%20) 200,00"
    vat_by_rate_read: dict[int, Decimal] = {}
    for ln in lines:
        f = fold(ln)
        m = re.search(r"KDV\s*\(?\s*%?\s*(\d{1,2})\s*\)?", f)
        if m and "TOPKDV" not in f and int(m.group(1)) in VALID_RATES:
            v = last_amount(ln)
            if v is not None:
                vat_by_rate_read[int(m.group(1))] = v

    vat_breakdown = []
    total_vat = None
    if gross_by_rate:
        items_total = sum(gross_by_rate.values())
        if total is not None and abs(items_total - total) > CENT and len(items) >= 1:
            notes.append({"severity": "warning", "code": "ocr_items_total_mismatch",
                          "message": f"Okunan kalemlerin toplamı ({items_total}) fiş toplamından ({total}) farklı; "
                                     "kalemleri ve KDV'yi kontrol edin."})
        if total is None:
            total = items_total
            notes.append({"severity": "info", "code": "ocr_total_from_items",
                          "message": "Genel toplam okunamadı; kalem tutarlarının toplamı kullanıldı."})
        for rate, gross in sorted(gross_by_rate.items()):
            vat = (gross * rate / (100 + rate)).quantize(CENT)
            vat_breakdown.append({"rate": Decimal(rate), "base": gross - vat, "amount": vat})
        total_vat = sum((g["amount"] for g in vat_breakdown), Decimal(0))
        if total_vat_read is not None and abs(total_vat_read - total_vat) > Decimal("0.05"):
            notes.append({"severity": "info", "code": "ocr_vat_corrected",
                          "message": f"TOPKDV {total_vat_read} okundu; kalemlerden hesaplanan KDV {total_vat} "
                                     "kullanıldı (OCR rakam hatası olabilir)."})
        notes.append({"severity": "info", "code": "vat_from_items",
                      "message": "KDV matrahı, fişteki KDV dahil kalem tutarlarından orana göre hesaplandı."})
    elif vat_by_rate_read and total is not None:
        for rate, vat in sorted(vat_by_rate_read.items()):
            base = (vat * 100 / rate).quantize(CENT) if rate else None
            vat_breakdown.append({"rate": Decimal(rate), "base": base, "amount": vat})
        total_vat = sum(vat_by_rate_read.values())
    elif total_vat_read is not None and total is not None and total > total_vat_read > 0:
        base = total - total_vat_read
        implied = total_vat_read * 100 / base
        rate = min(VALID_RATES - {0}, key=lambda r: abs(r - implied))
        if abs((base * rate / 100).quantize(CENT) - total_vat_read) <= Decimal("0.05"):
            vat_breakdown.append({"rate": Decimal(rate), "base": base, "amount": total_vat_read})
            total_vat = total_vat_read
            notes.append({"severity": "info", "code": "vat_rate_from_totals",
                          "message": f"KDV oranı TOPKDV ve TOPLAM'dan hesaplandı (%{rate})."})
        else:
            total_vat = total_vat_read
            notes.append({"severity": "warning", "code": "vat_rate_unknown",
                          "message": "Toplam KDV okundu ama oranı belirlenemedi; KDV dağılımını girin."})

    supplier_tax = _tax_number(lines)
    doc = ExtractedDocument.model_validate({
        "document_type": doc_type,
        "direction": "purchase",
        "supplier": {"name": _supplier(lines), "tax_number": supplier_tax, "tax_office": _tax_office(lines)},
        "document_number": _doc_number(lines),
        "document_date": _date(text),
        "currency": "TRY",
        "payment_method": _payment(text_f),
        "lines": items,
        "vat_breakdown": vat_breakdown,
        "total_vat": total_vat,
        "total_amount": total,
    })

    found = {k: v is not None and v != [] for k, v in {
        "total_amount": doc.total_amount, "document_date": doc.document_date, "vat_breakdown": doc.vat_breakdown,
        "supplier_name": doc.supplier.name, "supplier_tax_number": doc.supplier.tax_number,
        "document_number": doc.document_number, "payment_method": doc.payment_method}.items()}
    penalty = sum(1 for n in notes if n["severity"] == "warning") * 0.1
    doc.confidence = {k: (MAX_CONFIDENCE - penalty if ok else 0.0) for k, ok in found.items()}
    return doc, notes


def score(doc: ExtractedDocument, notes: list[dict]) -> float:
    """Higher = more complete and arithmetically consistent extraction."""
    pts = 0.0
    pts += 3 if doc.total_amount else 0
    pts += 2 if doc.document_date else 0
    pts += 2 if doc.vat_breakdown else 0
    pts += 1 if doc.supplier.tax_number else 0
    pts += 1 if doc.supplier.name else 0
    pts += 0.5 if doc.document_number else 0
    pts += 0.5 if doc.payment_method else 0
    if doc.total_amount and doc.vat_breakdown:
        s = sum((g.base or 0) + (g.amount or 0) for g in doc.vat_breakdown)
        pts += 2 if abs(s - doc.total_amount) <= CENT else 0
    pts -= sum(1.5 for n in notes if n["severity"] == "warning")
    return pts
