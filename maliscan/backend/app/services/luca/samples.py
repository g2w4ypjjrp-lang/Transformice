"""Synthetic sample vouchers (no real taxpayer data) for development and the
pilot validation pack. Account codes follow Tek Düzen Hesap Planı; the
importing Luca company must have every sub-account opened beforehand."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal as D

from app.services.luca.models import Voucher, VoucherLine as L

SAMPLE_TAXPAYER = uuid.UUID("00000000-0000-4000-8000-000000000001")

# Sub-accounts the pilot Luca company must have open before importing samples.
SAMPLE_ACCOUNTS = {
    "100.01": "Merkez Kasa",
    "102.01": "Banka - Vadesiz TL",
    "120.01": "Alıcılar",
    "153.01": "Ticari Mallar",
    "191.01": "İndirilecek KDV %1",
    "191.10": "İndirilecek KDV %10",
    "191.20": "İndirilecek KDV %20",
    "300.01": "Kredi Kartı Borçları",
    "320.01": "Satıcılar",
    "360.02": "Ödenecek KDV Tevkifatı",
    "391.20": "Hesaplanan KDV %20",
    "600.01": "Yurt İçi Satışlar",
    "689.01": "KKEG",
    "770.01": "Akaryakıt Giderleri",
    "770.02": "Kırtasiye Giderleri",
    "770.03": "Gıda / Mutfak Giderleri",
    "770.04": "Yazılım ve Abonelik Giderleri",
    "770.06": "Temizlik Hizmeti Giderleri",
}


def _id(n: int) -> uuid.UUID:
    return uuid.UUID(int=0x5A3B1E << 96 | n)


def scenario_vouchers() -> dict[str, Voucher]:
    d = date(2026, 11, 3)
    return {
        # 1. Fuel receipt, VAT 20 %, paid in cash.
        "kdv20_nakit_akaryakit": Voucher(
            _id(1), SAMPLE_TAXPAYER, d, document_number="FIS-0001", description="Akaryakıt fişi",
            lines=(L("770.01", D("1000.00"), description="Akaryakıt"),
                   L("191.20", D("200.00"), description="KDV %20"),
                   L("100.01", credit=D("1200.00"), description="Nakit ödeme"))),
        # 2. Grocery receipt with three VAT rates (1 %, 10 %, 20 %), cash.
        "coklu_kdv_market": Voucher(
            _id(2), SAMPLE_TAXPAYER, d, document_number="FIS-0002", description="Market fişi",
            lines=(L("770.03", D("100.00"), description="Ekmek (KDV %1)"),
                   L("191.01", D("1.00"), description="KDV %1"),
                   L("770.03", D("200.00"), description="Gıda (KDV %10)"),
                   L("191.10", D("20.00"), description="KDV %10"),
                   L("770.02", D("300.00"), description="Temizlik/kırtasiye (KDV %20)"),
                   L("191.20", D("60.00"), description="KDV %20"),
                   L("100.01", credit=D("681.00"), description="Nakit ödeme"))),
        # 3. Purchase invoice of goods, on supplier credit.
        "alis_faturasi_veresiye": Voucher(
            _id(3), SAMPLE_TAXPAYER, d + timedelta(days=1), document_number="ABC2026000000123",
            description="Ticari mal alışı",
            lines=(L("153.01", D("10000.00"), description="Ticari mal", quantity=D("40")),
                   L("191.20", D("2000.00"), description="KDV %20"),
                   L("320.01", credit=D("12000.00"), description="Satıcı borcu"))),
        # 4. Stationery paid by bank transfer.
        "banka_odemesi": Voucher(
            _id(4), SAMPLE_TAXPAYER, d + timedelta(days=1), document_number="GIB2026000000045",
            description="Kırtasiye alımı",
            lines=(L("770.02", D("500.00")), L("191.20", D("100.00")),
                   L("102.01", credit=D("600.00"), description="Banka havalesi"))),
        # 5. Software subscription paid by company credit card.
        "kredi_karti": Voucher(
            _id(5), SAMPLE_TAXPAYER, d + timedelta(days=2), document_number="EAR2026000000777",
            description="Yazılım aboneliği",
            lines=(L("770.04", D("250.00")), L("191.20", D("50.00")),
                   L("300.01", credit=D("300.00"), description="Kredi kartı"))),
        # 6. Cleaning service with VAT withholding 7/10 (KDV tevkifatı).
        "kdv_tevkifat_7_10": Voucher(
            _id(6), SAMPLE_TAXPAYER, d + timedelta(days=2), document_number="TMZ2026000000010",
            description="Temizlik hizmeti - KDV tevkifatı 7/10",
            lines=(L("770.06", D("10000.00"), description="Temizlik hizmeti"),
                   L("191.20", D("2000.00"), description="KDV %20"),
                   L("360.02", credit=D("1400.00"), description="KDV tevkifatı 7/10"),
                   L("320.01", credit=D("10600.00"), description="Satıcı borcu"))),
        # 7. Non-deductible expense (KKEG): VAT not deducted, booked to 689.
        "kkeg_nakit": Voucher(
            _id(7), SAMPLE_TAXPAYER, d + timedelta(days=3), document_number="FIS-0007",
            description="KKEG - kanunen kabul edilmeyen gider",
            lines=(L("689.01", D("1200.00"), description="KKEG (KDV dahil)"),
                   L("100.01", credit=D("1200.00"), description="Nakit ödeme"))),
        # 8. Sales invoice to a customer.
        "satis_faturasi": Voucher(
            _id(8), SAMPLE_TAXPAYER, d + timedelta(days=3), document_number="SAT2026000000001",
            description="Satış faturası",
            lines=(L("120.01", D("12000.00"), description="Alıcı"),
                   L("600.01", credit=D("10000.00"), description="Yurt içi satış"),
                   L("391.20", credit=D("2000.00"), description="Hesaplanan KDV %20"))),
    }


def bulk_vouchers(count: int, start: int = 1000) -> list[Voucher]:
    """`count` distinct balanced fuel vouchers for split testing."""
    out = []
    for i in range(count):
        net = D(100 + i) + D("0.25")
        vat = (net * D("0.20")).quantize(D("0.01"))
        out.append(Voucher(
            _id(start + i), SAMPLE_TAXPAYER, date(2026, 11, 1) + timedelta(days=i % 28),
            document_number=f"TOPLU-{i + 1:04d}", description="Toplu test fişi",
            lines=(L("770.01", net), L("191.20", vat), L("100.01", credit=net + vat))))
    return out
