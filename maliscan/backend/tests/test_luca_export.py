"""LucaExportService: format, validation, duplicate checks, 50-voucher split.

These tests verify MaliScan's behaviour against the *documented* Luca rules.
They do not (and cannot) prove that Luca accepts the files.
"""

import csv
import dataclasses
import io
import uuid
from datetime import date
from decimal import Decimal as D

import openpyxl
import pytest

from app.services.luca import (
    LUCA_FIS_AKTARIM_2020_EVIDENCE, ExportOptions, LucaExportService, LucaTemplate, TemplateError,
    Voucher, VoucherLine,
)
from app.services.luca.samples import SAMPLE_ACCOUNTS, SAMPLE_TAXPAYER, bulk_vouchers, scenario_vouchers

EXPECTED_HEADERS = ["Fiş No", "Fiş Tarihi", "Hesap Kodu", "Evrak No", "Evrak Tarihi", "Açıklama",
                    "Borç", "Alacak", "Miktar", "Döviz", "Kur"]


def _rows(content: bytes, enc="cp1254"):
    return list(csv.reader(io.StringIO(content.decode(enc)), delimiter=";"))


def _codes(result):
    return {i.code for i in result.issues}


def _v(lines, **kw):
    base = dict(source_id=uuid.uuid4(), taxpayer_id=SAMPLE_TAXPAYER, voucher_date=date(2026, 11, 3),
                document_number=f"D-{uuid.uuid4().hex[:6]}")
    base.update(kw)
    return Voucher(lines=tuple(lines), **base)


class TestTemplate:
    def test_evidence_template_matches_official_screenshot_order(self):
        assert LUCA_FIS_AKTARIM_2020_EVIDENCE.headers == EXPECTED_HEADERS
        required = {c.header for c in LUCA_FIS_AKTARIM_2020_EVIDENCE.columns if c.required}
        assert required == {"Fiş No", "Fiş Tarihi", "Hesap Kodu", "Borç", "Alacak"}
        assert LUCA_FIS_AKTARIM_2020_EVIDENCE.verified is False
        assert LUCA_FIS_AKTARIM_2020_EVIDENCE.max_vouchers_per_file == 50

    def test_analyse_official_xlsx_with_extra_column(self, tmp_path):
        wb = openpyxl.Workbook()
        wb.active.append(["FİŞ NO", "Fiş Tarihi", "Hesap Kodu", "Evrak No", "Evrak Tarihi", "Açıklama",
                          "Borç(TL)", "Alacak(TL)", "Miktar", "Döviz", "Kur", "Masraf Merkezi"])
        p = tmp_path / "sablon.xlsx"
        wb.save(p)
        t = LucaTemplate.from_official_file(p, version="pilot-2026-11")
        assert t.verified is True
        assert [c.field for c in t.columns][:8] == ["voucher_no", "voucher_date", "account_code",
                                                     "document_number", "document_date", "description",
                                                     "debit", "credit"]
        assert t.headers[6] == "Borç(TL)"  # exact official spelling is preserved in output
        assert t.unknown_headers == ("Masraf Merkezi",)

    def test_analyse_official_csv_cp1254(self, tmp_path):
        p = tmp_path / "sablon.csv"
        p.write_bytes(";".join(EXPECTED_HEADERS).encode("cp1254") + b"\r\n")
        t = LucaTemplate.from_official_file(p, version="pilot")
        assert t.headers == EXPECTED_HEADERS and t.unknown_headers == ()

    def test_official_file_missing_mandatory_column_is_rejected(self, tmp_path):
        p = tmp_path / "bad.csv"
        p.write_text("Fiş No;Fiş Tarihi;Hesap Kodu;Borç\n", encoding="utf-8")
        with pytest.raises(TemplateError, match="Alacak"):
            LucaTemplate.from_official_file(p, version="x")

    def test_custom_template_cannot_exceed_50(self):
        t = dataclasses.replace(LUCA_FIS_AKTARIM_2020_EVIDENCE, max_vouchers_per_file=60)
        with pytest.raises(ValueError):
            LucaExportService(t)


class TestCsvFormat:
    def test_scenario_file_structure(self):
        vouchers = list(scenario_vouchers().values())
        res = LucaExportService().export(vouchers, chart_of_accounts=set(SAMPLE_ACCOUNTS))
        assert res.ok, [i.as_dict() for i in res.errors]
        assert len(res.files) == 1
        f = res.files[0]
        rows = _rows(f.content)
        assert rows[0] == EXPECTED_HEADERS
        assert len(rows) - 1 == f.row_count == sum(len(v.lines) for v in vouchers)
        assert f.voucher_count == 8
        assert f.total_debit == f.total_credit
        assert f.content.endswith(b"\r\n")

    def test_value_formatting(self):
        v = scenario_vouchers()["kdv20_nakit_akaryakit"]
        rows = _rows(LucaExportService().export([v]).files[0].content)
        assert rows[1] == ["1", "03.11.2026", "770.01", "FIS-0001", "03.11.2026", "Akaryakıt",
                           "1000,00", "0", "0", "", ""]
        assert rows[3][6:8] == ["0", "1200,00"]

    def test_turkish_characters_survive_cp1254(self):
        v = _v([VoucherLine("770.01", D("10"), description="Şoför öğle yemeği ÇİĞ ığü"),
                VoucherLine("100.01", credit=D("10"))])
        res = LucaExportService().export([v])
        assert _rows(res.files[0].content)[1][5] == "Şoför öğle yemeği ÇİĞ ığü"

    def test_unencodable_characters_are_transliterated_with_warning(self):
        v = _v([VoucherLine("770.01", D("10"), description="Café ☕ ō"), VoucherLine("100.01", credit=D("10"))])
        res = LucaExportService().export([v])
        assert res.ok and "CHARS_TRANSLITERATED" in _codes(res)
        assert _rows(res.files[0].content)[1][5] == "Café ? o"

    def test_utf8_option(self):
        v = scenario_vouchers()["kdv20_nakit_akaryakit"]
        res = LucaExportService(options=ExportOptions(encoding="utf-8-sig")).export([v])
        assert res.files[0].content.startswith(b"\xef\xbb\xbf")

    def test_formula_injection_and_delimiters_are_neutralised(self):
        v = _v([VoucherLine("770.01", D("10"), description="=HYPERLINK(\"x\");kötü\nsatır"),
                VoucherLine("100.01", credit=D("10"))])
        res = LucaExportService().export([v])
        row = _rows(res.files[0].content)[1]
        assert len(row) == 11                       # the ';' did not create an extra column
        assert not row[5].startswith("=") and "\n" not in row[5]
        assert "FORMULA_PREFIX_REMOVED" in _codes(res)

    def test_lines_of_a_voucher_share_fis_no_and_date(self):
        vs = list(scenario_vouchers().values())
        res = LucaExportService().export(vs)
        rows = _rows(res.files[0].content)[1:]
        groups = {}
        for r in rows:
            groups.setdefault((r[0], r[1]), []).append(r)
        assert len(groups) == len(vs)  # Luca's grouping key yields exactly one voucher per source
        for (fis_no, _), lines in groups.items():
            debit = sum(D(r[6].replace(",", ".")) for r in lines)
            credit = sum(D(r[7].replace(",", ".")) for r in lines)
            assert debit == credit > 0, fis_no
        assert set(res.files[0].voucher_map) == {r[0] for r in rows}


class TestXlsxFormat:
    def test_xlsx_types_and_formats(self):
        v = scenario_vouchers()["alis_faturasi_veresiye"]
        res = LucaExportService(options=ExportOptions(fmt="xlsx")).export([v])
        ws = openpyxl.load_workbook(io.BytesIO(res.files[0].content)).active
        assert [c.value for c in ws[1]] == EXPECTED_HEADERS
        assert ws["B2"].is_date and ws["B2"].number_format == "DD.MM.YYYY"
        assert ws["C2"].data_type == "s" and ws["C2"].value == "153.01"
        assert ws["G2"].value == 10000 and ws["I2"].value == 40
        assert ws.max_row == 4

    def test_xlsx_text_is_never_a_formula(self):
        v = _v([VoucherLine("770.01", D("1"), description="@SUM(A1)"), VoucherLine("100.01", credit=D("1"))],
               document_number="=1+1")
        ws = openpyxl.load_workbook(io.BytesIO(
            LucaExportService(options=ExportOptions(fmt="xlsx")).export([v]).files[0].content)).active
        for cell in ws[2]:
            assert cell.data_type != "f"


class TestValidation:
    @pytest.mark.parametrize("lines,code", [
        ([VoucherLine("770.01", D("100")), VoucherLine("100.01", credit=D("99.99"))], "UNBALANCED"),
        ([VoucherLine("770.01", D("100"), D("100")), VoucherLine("100.01", credit=D("0"))], "LINE_BOTH_SIDES"),
        ([VoucherLine("770.01", D("0")), VoucherLine("100.01", credit=D("0"))], "LINE_ZERO"),
        ([VoucherLine("770.01", D("-5")), VoucherLine("100.01", credit=D("-5"))], "NEGATIVE_AMOUNT"),
        ([VoucherLine("770.01", D("1.005")), VoucherLine("100.01", credit=D("1.005"))], "AMOUNT_PRECISION"),
        ([VoucherLine("77A", D("1")), VoucherLine("100.01", credit=D("1"))], "ACCOUNT_CODE_FORMAT"),
        ([VoucherLine("770.01", D("1"))], "TOO_FEW_LINES"),
    ])
    def test_rejections(self, lines, code):
        res = LucaExportService().export([_v(lines)])
        assert not res.ok and code in _codes(res) and res.files == []

    def test_unknown_account_rejected_when_chart_known(self):
        v = _v([VoucherLine("770.99", D("1")), VoucherLine("100.01", credit=D("1"))])
        res = LucaExportService().export([v], chart_of_accounts={"770.01", "100.01"})
        assert "ACCOUNT_NOT_IN_CHART" in _codes(res)

    def test_line_limit_400_for_mahsup(self):
        lines = [VoucherLine("770.01", D("1"))] * 400 + [VoucherLine("100.01", credit=D("400"))]
        assert "TOO_MANY_LINES" in _codes(LucaExportService().export([_v(lines)]))
        ok = [VoucherLine("770.01", D("1"))] * 399 + [VoucherLine("100.01", credit=D("399"))]
        assert LucaExportService().export([_v(ok)]).ok

    def test_foreign_currency_blocked(self):
        v = _v([VoucherLine("770.01", D("1")), VoucherLine("100.01", credit=D("1"))], currency="EUR")
        assert "CURRENCY_UNSUPPORTED" in _codes(LucaExportService().export([v]))

    def test_mixed_taxpayers_blocked(self):
        a = _v([VoucherLine("770.01", D("1")), VoucherLine("100.01", credit=D("1"))])
        b = dataclasses.replace(a, source_id=uuid.uuid4(), taxpayer_id=uuid.uuid4(), document_number="X")
        assert "MIXED_TAXPAYERS" in _codes(LucaExportService().export([a, b]))

    def test_one_bad_voucher_blocks_whole_batch(self):
        good = list(scenario_vouchers().values())
        bad = _v([VoucherLine("770.01", D("1")), VoucherLine("100.01", credit=D("2"))])
        res = LucaExportService().export(good + [bad])
        assert not res.ok and res.files == [] and res.exported_source_ids == []
        assert [i.source_id for i in res.errors] == [bad.source_id]

    def test_unverified_template_always_warns(self):
        res = LucaExportService().export([scenario_vouchers()["banka_odemesi"]])
        assert res.ok and "TEMPLATE_UNVERIFIED" in _codes(res)


class TestDuplicates:
    def test_same_voucher_twice_in_batch(self):
        v = scenario_vouchers()["banka_odemesi"]
        assert "DUPLICATE_IN_BATCH" in _codes(LucaExportService().export([v, v]))

    def test_same_content_different_source(self):
        v = scenario_vouchers()["banka_odemesi"]
        twin = dataclasses.replace(v, source_id=uuid.uuid4())
        res = LucaExportService().export([v, twin])
        assert "DUPLICATE_CONTENT" in _codes(res)
        # A user can explicitly confirm a genuine second identical receipt.
        assert LucaExportService().export([v, twin], allowed_duplicate_ids=[twin.source_id]).ok

    def test_already_exported_id_and_content(self):
        v = scenario_vouchers()["banka_odemesi"]
        assert "ALREADY_EXPORTED" in _codes(LucaExportService().export([v], already_exported_ids={v.source_id}))
        twin = dataclasses.replace(v, source_id=uuid.uuid4())
        res = LucaExportService().export([twin], exported_fingerprints={v.content_fingerprint(): v.source_id})
        assert "DUPLICATE_OF_EXPORTED" in _codes(res)

    def test_fingerprint_ignores_line_order_but_not_amounts(self):
        v = scenario_vouchers()["kdv20_nakit_akaryakit"]
        assert dataclasses.replace(v, lines=tuple(reversed(v.lines))).content_fingerprint() == v.content_fingerprint()
        changed = (VoucherLine("770.01", D("1000.01")),) + v.lines[1:]
        assert dataclasses.replace(v, lines=changed).content_fingerprint() != v.content_fingerprint()


class TestSplitting:
    @pytest.mark.parametrize("count,expected", [(1, [1]), (50, [50]), (51, [50, 1]), (120, [50, 50, 20])])
    def test_split_sizes(self, count, expected):
        res = LucaExportService().export(bulk_vouchers(count))
        assert res.ok
        assert [f.voucher_count for f in res.files] == expected
        assert [f.file_index for f in res.files] == list(range(1, len(expected) + 1))
        for f in res.files:
            rows = _rows(f.content)[1:]
            assert len({(r[0], r[1]) for r in rows}) == f.voucher_count <= 50
            assert f.total_debit == f.total_credit

    def test_every_voucher_exported_exactly_once_and_never_split(self):
        vs = bulk_vouchers(137)
        res = LucaExportService().export(vs)
        mapped = [sid for f in res.files for sid in f.voucher_map.values()]
        assert sorted(mapped) == sorted(str(v.source_id) for v in vs)
        for f in res.files:
            rows = _rows(f.content)[1:]
            per_no = {}
            for r in rows:
                per_no.setdefault(r[0], 0)
                per_no[r[0]] += 1
            assert all(n == 3 for n in per_no.values())  # all 3 lines stayed together

    def test_files_are_deterministic(self):
        a = LucaExportService().export(bulk_vouchers(60))
        b = LucaExportService().export(list(reversed(bulk_vouchers(60))))
        assert [f.sha256 for f in a.files] == [f.sha256 for f in b.files]
