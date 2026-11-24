"""Luca "Excel Veri Aktarımı" (Fiş Aktarımı) template definition.

PROVENANCE — read before changing anything here
------------------------------------------------
The official template is only downloadable from *inside* an authenticated Luca
session (Muhasebe → Fiş İşlemleri → Excel Veri Aktarımı → "Şablon İndir").
It is not published on luca.com.tr. MaliScan has NOT obtained that file.

`LUCA_FIS_AKTARIM_2020_EVIDENCE` is reconstructed exclusively from official
Luca sources (see docs/LUCA_INTEGRATION.md):

  [S1] Luca Destek, "01.02.09 Excel Veri Aktarımı" (modified 2021-04-05)
       https://lucayazilim.freshdesk.com/support/solutions/articles/67000267580
       - mandatory: Fiş No, Fiş Tarihi, Hesap Kodu, Borç, Alacak
       - rows with identical Fiş No + Fiş Tarihi form one voucher
       - max 50 vouchers per upload; mahsup vouchers max 400 lines
       - account codes must already exist in Luca
  [S2] Screenshot embedded in [S1] (uploaded 2020-10-15) showing the sample
       file opened in Excel, columns A–K in this order:
       Fiş No | Fiş Tarihi | Hesap Kodu | Evrak No | Evrak Tarihi | Açıklama |
       Borç | Alacak | Miktar | Döviz | Kur
  [S3] Screenshot of the upload screen: "Ayraç: ; (Türkçe)" selector → the
       upload is a delimiter-separated text file; Turkish separator ';'.

It is therefore marked `verified=False`. Once a pilot office supplies the real
file from "Şablon İndir", load it with `LucaTemplate.from_official_file()`,
which analyses the header with openpyxl/csv and maps it onto known fields.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class LucaColumn:
    header: str          # exact header text as it must appear in the file
    field: str           # semantic MaliScan field name
    required: bool
    kind: str            # text | date | amount | quantity | code
    source: str          # evidence reference


@dataclass(frozen=True)
class LucaTemplate:
    version: str
    columns: tuple[LucaColumn, ...]
    verified: bool
    provenance: str
    delimiter: str = ";"
    date_format: str = "%d.%m.%Y"
    decimal_separator: str = ","
    thousands_separator: str = ""        # unverified: none used to stay unambiguous
    max_vouchers_per_file: int = 50      # [S1]
    max_lines_per_mahsup_voucher: int = 400  # [S1]
    unknown_headers: tuple[str, ...] = field(default=())

    @property
    def headers(self) -> list[str]:
        return [c.header for c in self.columns]

    def column_for(self, field_name: str) -> LucaColumn | None:
        return next((c for c in self.columns if c.field == field_name), None)

    # ------------------------------------------------------------------ analysis
    @classmethod
    def from_headers(cls, headers: list[str], *, version: str, provenance: str) -> "LucaTemplate":
        """Build a template from a real header row, mapping known Luca labels.

        Unknown headers are kept (written empty) and reported, so a newer Luca
        template with extra columns still produces a structurally identical file.
        """
        cols: list[LucaColumn] = []
        unknown: list[str] = []
        seen: set[str] = set()
        for raw in headers:
            header = (raw or "").strip()
            if not header:
                continue
            key = _normalise(header)
            known = _KNOWN_BY_KEY.get(key)
            if known and known.field not in seen:
                seen.add(known.field)
                cols.append(LucaColumn(header, known.field, known.required, known.kind, "official file"))
            else:
                unknown.append(header)
                cols.append(LucaColumn(header, f"unmapped:{header}", False, "text", "official file"))
        missing = [c.header for c in LUCA_FIS_AKTARIM_2020_EVIDENCE.columns if c.required and c.field not in seen]
        if missing:
            raise TemplateError(f"official file lacks mandatory columns: {', '.join(missing)}")
        return cls(version=version, columns=tuple(cols), verified=True, provenance=provenance,
                   unknown_headers=tuple(unknown))

    @classmethod
    def from_official_file(cls, path: str | Path, *, version: str) -> "LucaTemplate":
        """Analyse the file downloaded via Luca's "Şablon İndir" (xlsx/xls-as-xlsx/csv)."""
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix in (".xlsx", ".xlsm"):
            import openpyxl

            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            ws = wb.worksheets[0]
            first = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
            headers = [str(v) if v is not None else "" for v in first]
            wb.close()
        elif suffix in (".csv", ".txt"):
            raw = path.read_bytes()
            text = _decode(raw)
            dialect_delim = ";" if text.split("\n", 1)[0].count(";") >= text.split("\n", 1)[0].count(",") else ","
            headers = next(csv.reader(io.StringIO(text), delimiter=dialect_delim))
        else:
            raise TemplateError(f"unsupported template file type: {suffix}")
        return cls.from_headers(headers, version=version, provenance=f"official file {path.name}")


class TemplateError(ValueError):
    pass


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1254"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _normalise(label: str) -> str:
    """Case/diacritic-insensitive key: 'Borç(TL)' → 'borctl', 'Fiş No' → 'fisno'."""
    s = label.replace("İ", "i").replace("I", "ı").lower()
    s = s.translate(str.maketrans("çğıöşü", "cgiosu"))
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", s)


LUCA_FIS_AKTARIM_2020_EVIDENCE = LucaTemplate(
    version="luca-fis-aktarim-2020-evidence",
    verified=False,
    provenance="Reconstructed from Luca Destek article 01.02.09 and its screenshots (2020-10/2021-04); "
               "official 'Şablon İndir' file not obtained. NOT TESTED IN A REAL LUCA ENVIRONMENT.",
    columns=(
        LucaColumn("Fiş No", "voucher_no", True, "text", "S1,S2"),
        LucaColumn("Fiş Tarihi", "voucher_date", True, "date", "S1,S2"),
        LucaColumn("Hesap Kodu", "account_code", True, "code", "S1,S2"),
        LucaColumn("Evrak No", "document_number", False, "text", "S2"),
        LucaColumn("Evrak Tarihi", "document_date", False, "date", "S2"),
        LucaColumn("Açıklama", "description", False, "text", "S2"),
        LucaColumn("Borç", "debit", True, "amount", "S1,S2"),
        LucaColumn("Alacak", "credit", True, "amount", "S1,S2"),
        LucaColumn("Miktar", "quantity", False, "quantity", "S2"),
        LucaColumn("Döviz", "currency", False, "text", "S2"),
        LucaColumn("Kur", "exchange_rate", False, "quantity", "S2"),
    ),
)

# Label aliases accepted when analysing an official file (screen labels use "Borç(TL)").
_ALIASES = {
    "voucher_no": ["Fiş No", "Fiş Numarası", "FisNo"],
    "voucher_date": ["Fiş Tarihi", "FisTarihi"],
    "account_code": ["Hesap Kodu", "HesapKodu"],
    "document_number": ["Evrak No", "Belge No"],
    "document_date": ["Evrak Tarihi", "Belge Tarihi"],
    "description": ["Açıklama", "Aciklama"],
    "debit": ["Borç", "Borç(TL)", "Borç TL"],
    "credit": ["Alacak", "Alacak(TL)", "Alacak TL"],
    "quantity": ["Miktar"],
    "currency": ["Döviz", "Döviz Cinsi"],
    "exchange_rate": ["Kur", "Döviz Kuru"],
}
_BASE = {c.field: c for c in LUCA_FIS_AKTARIM_2020_EVIDENCE.columns}
_KNOWN_BY_KEY = {_normalise(a): _BASE[f] for f, names in _ALIASES.items() for a in names}
