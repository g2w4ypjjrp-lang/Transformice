"""OCR provider abstraction.

Claude reads document images directly, so OCR is an optional pre-pass that
adds a text hint (useful for faint thermal receipts). Providers:
  * none           — no pre-pass (default)
  * google_vision  — Google Cloud Vision DOCUMENT_TEXT_DETECTION (REST, API key)

Before enabling a provider, assess KVKK cross-border transfer rules and sign a
data processing agreement (see docs/SECURITY_KVKK.md).
"""

from __future__ import annotations

import base64
from typing import Protocol

import httpx

from app.core.config import Settings


class OcrError(Exception):
    pass


class OcrProvider(Protocol):
    name: str

    def read_text(self, content: bytes, mime_type: str) -> str | None: ...


class NoOcr:
    name = "none"

    def read_text(self, content: bytes, mime_type: str) -> str | None:
        return None


class GoogleVisionOcr:
    name = "google_vision"
    URL = "https://vision.googleapis.com/v1/images:annotate"

    def __init__(self, api_key: str, client: httpx.Client | None = None):
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=60)

    def read_text(self, content: bytes, mime_type: str) -> str | None:
        if not mime_type.startswith("image/"):
            return None  # PDFs go to Claude as documents
        body = {"requests": [{
            "image": {"content": base64.b64encode(content).decode()},
            "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
            "imageContext": {"languageHints": ["tr"]},
        }]}
        r = self.client.post(self.URL, params={"key": self.api_key}, json=body)
        if r.status_code >= 400:
            raise OcrError(f"google vision error {r.status_code}")
        resp = (r.json().get("responses") or [{}])[0]
        if "error" in resp:
            raise OcrError("google vision returned an error")
        return (resp.get("fullTextAnnotation") or {}).get("text") or None


def get_ocr_provider(settings: Settings) -> OcrProvider:
    if settings.ocr_provider == "google_vision":
        if not settings.google_vision_api_key:
            raise OcrError("GOOGLE_VISION_API_KEY is not configured")
        return GoogleVisionOcr(settings.google_vision_api_key.get_secret_value())
    return NoOcr()
