"""Luca 'Fatura Aktarım' invoice-list CSV (header copied from the user's own Luca sample file).

Proves the byte format we produce; it cannot prove Luca's rule screen accepts it.
"""

import csv
import io
import uuid
from datetime import date
from decimal import Decimal as D

from app.services.luca import ExportOptions, LucaExportService, Voucher, VoucherLine
from app.services.luca.samples import SAMPLE_TAXPAYER

USER_SAMPLE_HEADER = b"EvrakTarihi;EvrakNo;A\xe7\xfdklama;GenelToplam;Matrah;KDV"  # cp1254 bytes of the upload


def _invoice(total_base, vat, supplier="AKYILDIZ PETROL", doc="ABC2026000000123", day=3):
    lines = [VoucherLine("770.01", debit=D(total_base), description=f"{supplier} - {doc}")]
    if D(vat):
        lines.append(VoucherLine("191.01", debit=D(vat), description="KDV %20"))
    lines.append(VoucherLine("320.01", credit=D(total_base) + D(vat), description=f"{supplier} - {doc}"))
    return Voucher(source_id=uuid.uuid4(), taxpayer_id=SAMPLE_TAXPAYER, voucher_date=date(2026, 11, day),
                   lines=tuple(lines), document_number=doc, description=f"{supplier} - {doc}")


def _svc():
    return LucaExportService(options=ExportOptions(fmt="fatura_listesi"))


def test_header_matches_user_sample_byte_for_byte():
    res = _svc().export([_invoice("1000.00", "200.00")])
    assert res.ok, res.issues
    assert res.files[0].content.split(b"\r\n")[0] == USER_SAMPLE_HEADER


def test_row_values():
    res = _svc().export([_invoice("1000.00", "200.00"), _invoice("85.50", "0", supplier="ŞİŞLİ BÜFE", doc="F-9", day=4)])
    rows = list(csv.reader(io.StringIO(res.files[0].content.decode("cp1254")), delimiter=";"))
    assert rows[1] == ["03.11.2026", "ABC2026000000123", "AKYILDIZ PETROL", "1200,00", "1000,00", "200,00"]
    assert rows[2] == ["04.11.2026", "F-9", "ŞİŞLİ BÜFE", "85,50", "85,50", "0,00"]
    assert res.files[0].filename == "luca_fatura_listesi_001.csv"


def test_split_at_400_rows():
    res = _svc().export([_invoice("10", "2", doc=f"D{i}") for i in range(401)])
    assert res.ok, [i for i in res.issues if i.severity == "error"]
    assert [f.row_count for f in res.files] == [400, 1]


def test_hesapli_variant_carries_expense_and_vat_accounts():
    res = LucaExportService(options=ExportOptions(fmt="fatura_listesi_hesapli")).export(
        [_invoice("1000.00", "200.00"), _invoice("85.50", "0", supplier="ŞİŞLİ BÜFE", doc="F-9", day=4)])
    assert res.ok, res.issues
    rows = list(csv.reader(io.StringIO(res.files[0].content.decode("cp1254")), delimiter=";"))
    assert rows[0] == ["EvrakTarihi", "EvrakNo", "Açıklama", "GenelToplam", "Matrah", "KDV", "HesapKodu", "KDVHesapKodu"]
    assert rows[1] == ["03.11.2026", "ABC2026000000123", "AKYILDIZ PETROL", "1200,00", "1000,00", "200,00", "770.01", "191.01"]
    assert rows[2][-2:] == ["770.01", ""]  # no VAT line → empty VAT account
    assert res.files[0].filename == "luca_fatura_listesi_hesapli_001.csv"
