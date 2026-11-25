"""LucaExportService: validated, split, formatted Luca voucher import files.

Output formats
--------------
* ``csv``  — what the Luca upload screen consumes according to the official
  screenshot ("Ayraç: ; (Türkçe)"): ';'-separated, header row, dd.mm.yyyy,
  decimal comma. Encoding defaults to Windows-1254 (what Turkish Excel writes
  for "CSV (Ayraçlarla ayrılmış)"); this is UNVERIFIED — see LUCA_INTEGRATION.md.
* ``xlsx`` — same columns/order as an Excel workbook, for review or for
  offices whose Luca version accepts Excel directly (UNVERIFIED).

Nothing produced here has been imported into a real Luca environment yet.
"""

from __future__ import annotations

import csv
import hashlib
import io
import unicodedata
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from app.services.luca.models import ExportFile, ExportResult, Issue, Voucher
from app.services.luca.template import LUCA_FIS_AKTARIM_2020_EVIDENCE, LucaTemplate
from app.services.luca.validation import validate_vouchers

Format = Literal["csv", "xlsx", "fatura_listesi", "fatura_listesi_hesapli"]
FATURA_FORMATS = ("fatura_listesi", "fatura_listesi_hesapli")

# Header of the user's own Luca "Fatura Aktarım" sample file (luca_gider_fatura_listesi.csv),
# reproduced byte-for-byte in cp1254. One row per invoice; Luca's screen takes ≤400 rows.
FATURA_LISTESI_HEADERS = ["EvrakTarihi", "EvrakNo", "Açıklama", "GenelToplam", "Matrah", "KDV"]
FATURA_LISTESI_MAX_ROWS = 400
# Same list plus the accounts MaliScan booked (learned per supplier), for Luca rules with
# "HESAP KODUNU SÜTUNDAN AL" (official article 01.02.11): Matrah → HesapKodu, KDV → KDVHesapKodu.
FATURA_LISTESI_HESAPLI_HEADERS = FATURA_LISTESI_HEADERS + ["HesapKodu", "KDVHesapKodu"]
_VAT_PREFIXES = ("191", "391", "360")
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True)
class ExportOptions:
    fmt: Format = "csv"
    encoding: str = "cp1254"
    filename_prefix: str = "luca_fis_aktarim"
    max_description_length: int = 250   # conservative; Luca's limit is not documented
    zero_amount: str = "0"              # opposite side shown as 0 in the official sample


class LucaExportService:
    def __init__(self, template: LucaTemplate = LUCA_FIS_AKTARIM_2020_EVIDENCE,
                 options: ExportOptions | None = None):
        self.template = template
        self.options = options or ExportOptions()
        if self.template.max_vouchers_per_file > 50:
            raise ValueError("Luca accepts at most 50 vouchers per upload")

    # ------------------------------------------------------------------ public
    def export(
        self,
        vouchers: list[Voucher],
        *,
        chart_of_accounts: set[str] | None = None,
        already_exported_ids: set[uuid.UUID] | None = None,
        exported_fingerprints: dict[str, uuid.UUID] | None = None,
        allowed_duplicate_ids: Iterable[uuid.UUID] = (),
    ) -> ExportResult:
        result = ExportResult(self.template.version, self.template.verified)
        if not vouchers:
            result.issues.append(Issue("error", "EMPTY_BATCH", "no vouchers selected"))
            return result
        result.issues.extend(validate_vouchers(
            vouchers, self.template,
            chart_of_accounts=chart_of_accounts,
            already_exported_ids=already_exported_ids,
            exported_fingerprints=exported_fingerprints,
            allowed_duplicate_ids=allowed_duplicate_ids,
        ))
        if not self.template.verified:
            result.issues.append(Issue(
                "warning", "TEMPLATE_UNVERIFIED",
                "template reconstructed from Luca support documentation; not yet confirmed "
                "against the official 'Şablon İndir' file or a real Luca import"))
        if not result.ok:
            return result  # never produce a partial batch

        ordered = sorted(vouchers, key=lambda v: (v.voucher_date, v.document_number or "", str(v.source_id)))
        for index, chunk in enumerate(self.split(ordered), start=1):
            result.files.append(self._build_file(index, chunk, result.issues))
        result.exported_source_ids = [v.source_id for v in ordered]
        return result

    def rebuild_files(self, vouchers: list[Voucher]) -> list[ExportFile]:
        """Re-create files for vouchers that were already validated and exported (e.g. the same
        export again in another format). No validation, no state change."""
        ordered = sorted(vouchers, key=lambda v: (v.voucher_date, v.document_number or "", str(v.source_id)))
        issues: list[Issue] = []
        return [self._build_file(i, chunk, issues) for i, chunk in enumerate(self.split(ordered), start=1)]

    def split(self, vouchers: list[Voucher]) -> list[list[Voucher]]:
        """Chunks of ≤ max_vouchers_per_file; a voucher is never split across files."""
        size = FATURA_LISTESI_MAX_ROWS if self.options.fmt in FATURA_FORMATS else self.template.max_vouchers_per_file
        return [vouchers[i:i + size] for i in range(0, len(vouchers), size)]

    # ------------------------------------------------------------------ building
    def _build_file(self, index: int, chunk: list[Voucher], issues: list[Issue]) -> ExportFile:
        if self.options.fmt in FATURA_FORMATS:
            return self._build_fatura_listesi(index, chunk, issues)
        rows: list[list[object]] = []
        voucher_map: dict[str, str] = {}
        for seq, v in enumerate(chunk, start=1):
            # Fiş No must be unique per file: Luca merges rows with equal Fiş No + Fiş Tarihi.
            fis_no = str(seq)
            voucher_map[fis_no] = str(v.source_id)
            for ln in v.lines:
                values = {
                    "voucher_no": fis_no,
                    "voucher_date": v.voucher_date,
                    "account_code": ln.account_code,
                    "document_number": self._text(v.document_number, v, issues),
                    "document_date": v.document_date or v.voucher_date,
                    "description": self._text(ln.description or v.description, v, issues),
                    "debit": ln.debit,
                    "credit": ln.credit,
                    "quantity": ln.quantity if ln.quantity is not None else Decimal("0"),
                    "currency": None,       # TRY only (foreign currency blocked in validation)
                    "exchange_rate": None,
                }
                rows.append([values.get(c.field) for c in self.template.columns])

        if self.options.fmt == "csv":
            content = self._to_csv(rows, chunk, issues)
            ext = "csv"
        else:
            content = self._to_xlsx(rows)
            ext = "xlsx"
        total_d = sum((v.total_debit for v in chunk), Decimal("0"))
        total_c = sum((v.total_credit for v in chunk), Decimal("0"))
        assert total_d == total_c  # guaranteed by validation; defensive
        return ExportFile(
            file_index=index,
            filename=f"{self.options.filename_prefix}_{index:03d}.{ext}",
            content=content,
            sha256=hashlib.sha256(content).hexdigest(),
            voucher_count=len(chunk),
            row_count=len(rows),
            voucher_map=voucher_map,
            total_debit=total_d,
            total_credit=total_c,
        )

    def _build_fatura_listesi(self, index: int, chunk: list[Voucher], issues: list[Issue]) -> ExportFile:
        """One row per invoice, figures taken from the approved journal:
        KDV = debits on 191 (İndirilecek KDV), Matrah = other debits, GenelToplam = Matrah + KDV."""
        t = self.template
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=";", lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
        with_accounts = self.options.fmt == "fatura_listesi_hesapli"
        writer.writerow(FATURA_LISTESI_HESAPLI_HEADERS if with_accounts else FATURA_LISTESI_HEADERS)
        voucher_map: dict[str, str] = {}
        for seq, v in enumerate(chunk, start=1):
            voucher_map[str(seq)] = str(v.source_id)
            kdv = sum((ln.debit for ln in v.lines if ln.account_code.startswith("191")), Decimal("0"))
            matrah = v.total_debit - kdv
            extra = [_main_debit(v, vat=False), _main_debit(v, vat=True)] if with_accounts else []
            writer.writerow([
                (v.document_date or v.voucher_date).strftime(t.date_format),
                self._text(v.document_number, v, issues) or "",
                self._text(_supplier_label(v), v, issues) or "",
                _fmt_decimal(matrah + kdv, 2, t.decimal_separator, ""),
                _fmt_decimal(matrah, 2, t.decimal_separator, ""),
                _fmt_decimal(kdv, 2, t.decimal_separator, ""),
                *extra,
            ])
        content = self._encode(buf.getvalue(), issues)
        total = sum((v.total_debit for v in chunk), Decimal("0"))
        return ExportFile(
            file_index=index, content=content,
            filename=f"luca_fatura_listesi{'_hesapli' if with_accounts else ''}_{index:03d}.csv",
            sha256=hashlib.sha256(content).hexdigest(), voucher_count=len(chunk), row_count=len(chunk),
            voucher_map=voucher_map, total_debit=total, total_credit=total)

    def _text(self, value: str | None, v: Voucher, issues: list[Issue]) -> str | None:
        if value is None:
            return None
        text = " ".join(str(value).split())  # no newlines / tabs / double spaces
        if text.startswith(_FORMULA_PREFIXES):
            text = text.lstrip("=+-@ ").strip()
            issues.append(Issue("warning", "FORMULA_PREFIX_REMOVED",
                                "leading formula character removed from text", v.source_id))
        limit = self.options.max_description_length
        if len(text) > limit:
            text = text[:limit].rstrip()
            issues.append(Issue("warning", "TEXT_TRUNCATED", f"text truncated to {limit} characters",
                                v.source_id))
        return text or None

    # ------------------------------------------------------------------ formats
    def _format_cell(self, kind: str, value: object) -> str:
        t = self.template
        if value is None:
            return ""
        if kind == "date":
            return value.strftime(t.date_format)  # type: ignore[union-attr]
        if kind == "amount":
            amount = Decimal(value)  # type: ignore[arg-type]
            if amount == 0:
                return self.options.zero_amount
            return _fmt_decimal(amount, 2, t.decimal_separator, t.thousands_separator)
        if kind == "quantity":
            q = Decimal(value)  # type: ignore[arg-type]
            if q == 0:
                return "0"
            return _fmt_decimal(q.normalize(), None, t.decimal_separator, "")
        return str(value)

    def _to_csv(self, rows: list[list[object]], chunk: list[Voucher], issues: list[Issue]) -> bytes:
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=self.template.delimiter, lineterminator="\r\n",
                            quoting=csv.QUOTE_MINIMAL)
        writer.writerow(self.template.headers)
        kinds = [c.kind for c in self.template.columns]
        for row in rows:
            writer.writerow([self._format_cell(k, v) for k, v in zip(kinds, row)])
        return self._encode(buf.getvalue(), issues)

    def _encode(self, text: str, issues: list[Issue]) -> bytes:
        enc = self.options.encoding
        try:
            return text.encode(enc)
        except UnicodeEncodeError:
            issues.append(Issue("warning", "CHARS_TRANSLITERATED",
                                f"characters not representable in {enc} were transliterated"))
            safe = "".join(ch if _encodable(ch, enc) else _ascii_fallback(ch) for ch in text)
            return safe.encode(enc, errors="replace")

    def _to_xlsx(self, rows: list[list[object]]) -> bytes:
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Sayfa1"
        ws.append(self.template.headers)
        kinds = [c.kind for c in self.template.columns]
        for r_idx, row in enumerate(rows, start=2):
            for c_idx, (kind, value) in enumerate(zip(kinds, row), start=1):
                if value is None:
                    continue
                cell = ws.cell(row=r_idx, column=c_idx)
                if kind == "date":
                    cell.value = value
                    cell.number_format = "DD.MM.YYYY"
                elif kind == "amount":
                    cell.value = Decimal(value).quantize(Decimal("0.01"))  # type: ignore[arg-type]
                    cell.number_format = "0.00"
                elif kind == "quantity":
                    cell.value = Decimal(value)  # type: ignore[arg-type]
                else:
                    # Explicit string type: never let text be interpreted as a formula.
                    cell.value = str(value)
                    cell.data_type = "s"
        out = io.BytesIO()
        wb.save(out)
        return out.getvalue()


def _fmt_decimal(value: Decimal, places: int | None, dec_sep: str, thou_sep: str) -> str:
    if places is not None:
        value = value.quantize(Decimal(1).scaleb(-places))
    sign = "-" if value < 0 else ""
    s = f"{abs(value):f}"
    whole, _, frac = s.partition(".")
    if thou_sep:
        groups = []
        while len(whole) > 3:
            groups.insert(0, whole[-3:])
            whole = whole[:-3]
        whole = thou_sep.join([whole, *groups])
    return sign + whole + (dec_sep + frac if frac else "")


def _main_debit(v: Voucher, *, vat: bool) -> str:
    """Account of the largest expense line (vat=False) or VAT line (vat=True). Purchases: debits
    (770…/191…); a sales invoice in the list: credits (600…/391…)."""
    sales = any(ln.credit > 0 and ln.account_code.startswith("391") for ln in v.lines)
    side = (lambda ln: ln.credit) if sales else (lambda ln: ln.debit)
    lines = [ln for ln in v.lines if side(ln) > 0 and ln.account_code.startswith(_VAT_PREFIXES) == vat]
    return max(lines, key=side).account_code if lines else ""


def _supplier_label(v: Voucher) -> str | None:
    """Journal description is 'SUPPLIER - DOCNO'; the invoice list wants just the counterparty."""
    desc = (v.description or "").strip()
    if v.document_number and desc.endswith(f" - {v.document_number}"):
        desc = desc[: -len(f" - {v.document_number}")].strip()
    return desc or None


def _encodable(ch: str, enc: str) -> bool:
    try:
        ch.encode(enc)
        return True
    except UnicodeEncodeError:
        return False


def _ascii_fallback(ch: str) -> str:
    decomposed = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
    return decomposed or "?"
