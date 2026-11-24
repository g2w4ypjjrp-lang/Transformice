"""Generate the Luca pilot validation pack (synthetic data only).

    python -m scripts.generate_luca_samples ../samples/luca

All files are UNVERIFIED: produced from the documented Luca rules, never
imported into a real Luca environment by MaliScan.
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.services.luca import ExportOptions, LucaExportService, Voucher, VoucherLine
from app.services.luca.samples import SAMPLE_ACCOUNTS, SAMPLE_TAXPAYER, bulk_vouchers, scenario_vouchers


def _write(result, folder: Path, manifest: list, label: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for f in result.files:
        (folder / f.filename).write_bytes(f.content)
        manifest.append({
            "scenario": label, "file": str(folder.name + "/" + f.filename), "sha256": f.sha256,
            "vouchers": f.voucher_count, "rows": f.row_count,
            "total_debit": str(f.total_debit), "total_credit": str(f.total_credit),
            "fis_no_to_source": f.voucher_map,
        })


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    scen = scenario_vouchers()
    chart = set(SAMPLE_ACCOUNTS)

    for fmt, enc in (("csv", "cp1254"), ("xlsx", "cp1254")):
        svc = LucaExportService(options=ExportOptions(fmt=fmt, encoding=enc, filename_prefix="tek"))
        for name, v in scen.items():
            res = svc.export([v], chart_of_accounts=chart)
            assert res.ok, res.errors
            for f in res.files:
                f.filename = f"{name}.{fmt}"
            _write(res, out / f"01_senaryolar_{fmt}", manifest, name)

        res = LucaExportService(options=ExportOptions(fmt=fmt, filename_prefix="tum_senaryolar")).export(
            list(scen.values()), chart_of_accounts=chart)
        _write(res, out / f"02_tum_senaryolar_tek_dosya_{fmt}", manifest, "all_scenarios")

    # Encoding variant: the correct encoding is not documented by Luca.
    res = LucaExportService(options=ExportOptions(encoding="utf-8-sig", filename_prefix="tum_senaryolar_utf8")) \
        .export(list(scen.values()), chart_of_accounts=chart)
    _write(res, out / "03_kodlama_varyanti_utf8", manifest, "all_scenarios_utf8")

    # 120 vouchers → 3 files (50 + 50 + 20).
    res = LucaExportService(options=ExportOptions(filename_prefix="toplu_120_fis")).export(bulk_vouchers(120))
    _write(res, out / "04_bolme_120_fis", manifest, "split_120")

    # Rejected batch: validation report only, no file is produced.
    bad = [
        Voucher(uuid.UUID(int=901), SAMPLE_TAXPAYER, date(2026, 11, 5), document_number="HATALI-1",
                lines=(VoucherLine("770.01", Decimal("100.00")), VoucherLine("100.01", credit=Decimal("99.00")))),
        scen["banka_odemesi"],
        Voucher(uuid.UUID(int=902), SAMPLE_TAXPAYER, date(2026, 11, 6), document_number="GIB2026000000045",
                lines=scen["banka_odemesi"].lines),   # same lines, different date → legitimately distinct
        Voucher(uuid.UUID(int=903), SAMPLE_TAXPAYER, scen["banka_odemesi"].voucher_date,
                document_number="GIB2026000000045", lines=scen["banka_odemesi"].lines),  # true duplicate
        Voucher(uuid.UUID(int=904), SAMPLE_TAXPAYER, date(2026, 11, 7), document_number="X",
                lines=(VoucherLine("770.99", Decimal("5.00")), VoucherLine("100.01", credit=Decimal("5.00")))),
    ]
    res = LucaExportService().export(bad, chart_of_accounts=chart)
    assert not res.ok and not res.files
    (out / "05_reddedilen_ornek_dogrulama_raporu.json").write_text(
        json.dumps([i.as_dict() for i in res.issues], ensure_ascii=False, indent=2), encoding="utf-8")

    (out / "acilmasi_gereken_hesaplar.csv").write_bytes(
        ("Hesap Kodu;Hesap Adı\r\n" + "".join(f"{k};{v}\r\n" for k, v in sorted(SAMPLE_ACCOUNTS.items())))
        .encode("cp1254"))
    (out / "manifest.json").write_text(json.dumps({
        "status": "UNVERIFIED - NOT IMPORTED INTO LUCA",
        "template": LucaExportService().template.version,
        "provenance": LucaExportService().template.provenance,
        "files": manifest,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(manifest)} files written to {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "../samples/luca"))
