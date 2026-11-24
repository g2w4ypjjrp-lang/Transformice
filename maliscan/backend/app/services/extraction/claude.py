"""Claude-based extraction for images and PDFs (structured JSON output).

Claude reads the document image/PDF directly; an optional OCR pre-pass adds
plain text as a hint. Output is constrained by a JSON schema and then parsed
into ExtractedDocument and validated deterministically — AI output is never
trusted blindly and never posted without human approval.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass

import anthropic

from app.core.config import Settings
from app.services.extraction.schema import CLAUDE_OUTPUT_SCHEMA, SCHEMA_VERSION, ExtractedDocument

log = logging.getLogger("maliscan.extraction")

SYSTEM_PROMPT = """Sen Türkiye'de çalışan bir mali müşavirlik ofisi için belge verisi çıkaran bir asistansın.
Görevin: verilen fiş/fatura görüntüsünden veya PDF'ten muhasebe verisini JSON şemasına uygun biçimde çıkarmak.

Kesin kurallar:
- Yalnızca belgede açıkça okunan değerleri yaz. Okunamayan, belirsiz veya belgede olmayan her alan için null kullan.
  Asla tahmin etme, tamamlama yapma veya hesaplayarak alan uydurma.
- Tutarları nokta ondalık ayırıcılı, binlik ayırıcısız metin olarak yaz: "1234.56". Türk biçimindeki "1.234,56"
  değerini "1234.56" olarak dönüştür. Para birimi simgesi ekleme.
- Tarihleri YYYY-MM-DD biçiminde yaz (belgede GG.AA.YYYY yazar).
- KDV oranlarını yüzde sayı olarak yaz: "1", "10", "20" (eski belgelerde "8", "18" olabilir).
- vat_breakdown: belgedeki her KDV oranı için matrah (base) ve KDV tutarı (amount). Yazar kasa fişlerinde
  "TOPKDV" toplam KDV, "%20" / "KDV %20" satırları oran bazlı KDV'dir. Matrah belgede yazmıyorsa base null kalsın.
- total_amount: belgedeki genel toplam (KDV dahil, "TOPLAM" / "GENEL TOPLAM" / "ÖDENECEK TUTAR").
- payment_method: "NAKİT" → cash, "KREDİ KARTI"/"K.KARTI"/"BANKA KARTI" → credit_card, havale/EFT → bank;
  belirtilmemişse null.
- document_type: yazar kasa / ÖKC fişi → thermal_receipt; perakende satış fişi → retail_receipt;
  e-Arşiv Fatura → e_archive_invoice; e-Fatura → e_invoice; kağıt alış faturası → purchase_invoice;
  gider pusulası → expense_slip.
- supplier: belgeyi düzenleyen satıcı (fişin üst kısmındaki unvan, VKN/TCKN ve vergi dairesi). buyer: alıcı.
- Fiş/fatura numarası: "FİŞ NO", "BELGE NO", "FATURA NO". Z raporu numarasını belge numarası olarak kullanma.
- KDV tevkifatı varsa withholding alanına oranı ondalık (7/10 → "0.7") ve tutarı yaz.
- lines: belgede kalem satırları okunuyorsa her kalem için açıklama ve tutarları; okunmuyorsa boş liste.
- confidence: her alan grubu için 0 ile 1 arasında, o alanı belgeden ne kadar net okuduğunu gösteren değer.
  Okunamayan (null) alanlar için 0 yaz.
- Belge bir fiş veya fatura değilse document_type "other" olsun ve diğer alanlar null kalsın."""


class ExtractionError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class ClaudeResult:
    document: ExtractedDocument
    model: str
    raw_text: str | None
    usage: dict


class ClaudeExtractor:
    def __init__(self, settings: Settings, client: anthropic.Anthropic | None = None):
        if client is None:
            if not settings.anthropic_api_key:
                raise ExtractionError("ai_not_configured", "ANTHROPIC_API_KEY is not configured")
            client = anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value(),
                                         max_retries=3, timeout=300)
        self.client = client
        self.model = settings.anthropic_model
        self.effort = settings.anthropic_effort

    def extract(self, content: bytes, mime_type: str, *, ocr_text: str | None = None) -> ClaudeResult:
        data = base64.standard_b64encode(content).decode()
        if mime_type == "application/pdf":
            source_block = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                           "data": data}}
        elif mime_type in ("image/jpeg", "image/png", "image/webp", "image/gif"):
            source_block = {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": data}}
        else:
            raise ExtractionError("unsupported_type", f"cannot send {mime_type} to the model")

        user_content = [source_block]
        if ocr_text:
            user_content.append({"type": "text", "text": "OCR ön-okuma metni (hatalı olabilir, belgeyle doğrula):\n"
                                                         + ocr_text[:20000]})
        user_content.append({"type": "text", "text": "Bu belgedeki muhasebe verisini şemaya göre çıkar."})

        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
                output_config={"effort": self.effort,
                               "format": {"type": "json_schema", "schema": CLAUDE_OUTPUT_SCHEMA}},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.RateLimitError as exc:
            raise ExtractionError("ai_rate_limited", "AI provider rate limit") from exc
        except anthropic.APIConnectionError as exc:
            raise ExtractionError("ai_unavailable", "AI provider unreachable") from exc
        except anthropic.APIStatusError as exc:
            code = "ai_unavailable" if exc.status_code >= 500 else "ai_request_rejected"
            raise ExtractionError(code, f"AI provider error {exc.status_code}") from exc

        if response.stop_reason == "refusal":
            raise ExtractionError("ai_refused", "the model declined to process this document")
        if response.stop_reason == "max_tokens":
            raise ExtractionError("ai_truncated", "model output was truncated")
        text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), None)
        if not text:
            raise ExtractionError("ai_empty", "model returned no data")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ExtractionError("ai_invalid_json", "model output was not valid JSON") from exc
        doc = parse_model_payload(payload)
        usage = {"input_tokens": getattr(response.usage, "input_tokens", None),
                 "output_tokens": getattr(response.usage, "output_tokens", None)}
        return ClaudeResult(document=doc, model=getattr(response, "model", self.model), raw_text=ocr_text, usage=usage)


def parse_model_payload(payload: dict) -> ExtractedDocument:
    """Convert the model's string-typed JSON into the canonical model, dropping
    values that are not valid numbers/dates rather than guessing."""
    from datetime import date
    from decimal import Decimal, InvalidOperation

    def dec(v):
        if v is None:
            return None
        s = str(v).strip().replace(" ", "")
        if not s:
            return None
        if "," in s and "." in s:          # defensive: model echoed Turkish format
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            s = s.replace(",", ".")
        try:
            d = Decimal(s)
        except InvalidOperation:
            return None
        return d if d.is_finite() else None

    def dt(v):
        try:
            return date.fromisoformat(v) if v else None
        except ValueError:
            return None

    lines = [{k: (dec(v) if k not in ("description", "unit", "withholding_code") else v) for k, v in ln.items()}
             for ln in payload.get("lines") or []]
    for ln in lines:
        r = ln.get("withholding_ratio")
        if r is not None and not (0 <= r <= 1):
            ln["withholding_ratio"] = None
    vat = [{"rate": dec(g.get("rate")), "base": dec(g.get("base")), "amount": dec(g.get("amount"))}
           for g in payload.get("vat_breakdown") or []]
    vat = [g for g in vat if g["rate"] is not None]
    wh = payload.get("withholding")
    if wh:
        ratio = dec(wh.get("ratio"))
        wh = {"code": wh.get("code"), "ratio": ratio if ratio is not None and 0 <= ratio <= 1 else None,
              "amount": dec(wh.get("amount"))}
        if wh["amount"] is None and wh["ratio"] is None:
            wh = None
    return ExtractedDocument.model_validate({
        "document_type": payload.get("document_type"),
        "supplier": payload.get("supplier") or {},
        "buyer": payload.get("buyer") or {},
        "document_number": payload.get("document_number"),
        "ettn": payload.get("ettn"),
        "document_date": dt(payload.get("document_date")),
        "currency": (payload.get("currency") or "TRY").upper()[:3],
        "payment_method": payload.get("payment_method"),
        "lines": lines,
        "vat_breakdown": vat,
        "withholding": wh,
        "total_net": dec(payload.get("total_net")),
        "total_vat": dec(payload.get("total_vat")),
        "total_amount": dec(payload.get("total_amount")),
        "notes": payload.get("notes"),
        "confidence": {k: float(v) for k, v in (payload.get("confidence") or {}).items()
                       if isinstance(v, (int, float))},
    })


__all__ = ["ClaudeExtractor", "ClaudeResult", "ExtractionError", "SCHEMA_VERSION", "parse_model_payload"]
