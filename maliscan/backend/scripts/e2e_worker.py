"""E2E TEST ONLY — runs the real worker loop but with a deterministic fake
Claude client (no API key / network needed). Every image is "read" as the
synthetic fuel receipt; XML files take the real UBL path."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

from app.core.config import get_settings
from app.db.session import get_engine
from app.services.extraction.claude import ClaudeExtractor
from app.services.samples_docs import SUPPLIER_VKN
from app.services.storage import get_storage
from app.worker.main import claim_job, run_job

PAYLOAD = {
    "document_type": "thermal_receipt",
    "supplier": {"name": "ÖRNEK AKARYAKIT LTD.ŞTİ.", "tax_number": SUPPLIER_VKN, "tax_office": "Kavaklıdere", "address": None},
    "buyer": {"name": None, "tax_number": None, "tax_office": None, "address": None},
    "document_number": "0042", "ettn": None, "document_date": "2026-11-03", "currency": "TRY", "payment_method": "cash",
    "lines": [], "vat_breakdown": [{"rate": "20", "base": "1000.00", "amount": "200.00"}], "withholding": None,
    "total_net": None, "total_vat": "200.00", "total_amount": "1200.00", "notes": None,
    "confidence": {k: 0.95 for k in ("document_type", "supplier_name", "supplier_tax_number", "document_number",
                                     "document_date", "vat_breakdown", "total_amount", "lines", "payment_method")},
}


class _Fake:
    def __init__(self):
        n = {"i": 0}

        class M:
            def create(self, **kw):
                n["i"] += 1
                p = dict(PAYLOAD, document_number=f"{42 + n['i']:04d}")
                return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(p))], stop_reason="end_turn",
                                       model=kw["model"], usage=SimpleNamespace(input_tokens=1, output_tokens=1))
        self.beta = SimpleNamespace(messages=M())


def main() -> None:
    s = get_settings()
    extractor = ClaudeExtractor(s.model_copy(update={"anthropic_api_key": "sk-e2e"}), client=_Fake())
    engine, storage = get_engine(), get_storage()
    while True:
        job = claim_job(engine, "e2e-worker")
        if job:
            print("job", job["job_type"], run_job(job, engine=engine, storage=storage, settings=s, claude=extractor),
                  flush=True)
        else:
            time.sleep(1)


if __name__ == "__main__":
    main()
