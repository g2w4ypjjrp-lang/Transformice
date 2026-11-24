"""Upload validation: real type detection by content (magic bytes), size
limits, safe XML parsing, image normalisation (EXIF/GPS stripped)."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

from defusedxml import ElementTree as SafeET
from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = 60_000_000  # decompression-bomb guard

MAX_IMAGE_SIDE = 3200


class FileRejected(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class ValidatedFile:
    content: bytes
    mime_type: str
    file_kind: str          # image | pdf | xml
    extension: str
    sha256: str
    page_count: int | None = None


def sniff(content: bytes) -> tuple[str, str, str] | None:
    """Return (mime, kind, ext) from leading bytes, or None if unsupported."""
    head = content[:64]
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "image", "jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "image", "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp", "image", "webp"
    if head.startswith(b"%PDF-"):
        return "application/pdf", "pdf", "pdf"
    stripped = content[:512].lstrip(b"\xef\xbb\xbf \t\r\n")
    if stripped.startswith(b"<?xml") or stripped.startswith(b"<"):
        return "application/xml", "xml", "xml"
    if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"mif1", b"msf1"):
        return "image/heic", "heic", "heic"
    return None


def validate_upload(content: bytes, max_bytes: int) -> ValidatedFile:
    if not content:
        raise FileRejected("empty_file", "file is empty")
    if len(content) > max_bytes:
        raise FileRejected("file_too_large", f"file exceeds {max_bytes // (1024 * 1024)} MB")
    detected = sniff(content)
    if detected is None:
        raise FileRejected("unsupported_type", "only JPEG, PNG, WEBP, PDF and UBL-TR XML files are accepted")
    mime, kind, ext = detected
    if kind == "heic":
        raise FileRejected("unsupported_type", "HEIC is not supported; use the in-app scanner or JPEG")

    page_count = None
    if kind == "image":
        content, mime, ext = normalise_image(content)
    elif kind == "pdf":
        page_count = _pdf_pages(content)
    elif kind == "xml":
        _check_xml(content)
    return ValidatedFile(content, mime, kind, ext, hashlib.sha256(content).hexdigest(), page_count)


def normalise_image(content: bytes) -> tuple[bytes, str, str]:
    """Decode, apply EXIF orientation, drop all metadata (incl. GPS), bound size, re-encode JPEG."""
    try:
        img = Image.open(io.BytesIO(content))
        img.load()
    except Exception as exc:  # noqa: BLE001 — any decoder failure means a bad upload
        raise FileRejected("corrupt_image", "image could not be decoded") from exc
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    if max(img.size) > MAX_IMAGE_SIDE:
        img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
    if min(img.size) < 200:
        raise FileRejected("image_too_small", "image resolution is too low to read")
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90, optimize=True)  # no exif= → metadata removed
    return out.getvalue(), "image/jpeg", "jpg"


def _pdf_pages(content: bytes) -> int:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise FileRejected("encrypted_pdf", "password-protected PDFs are not supported")
        n = len(reader.pages)
    except FileRejected:
        raise
    except Exception as exc:  # noqa: BLE001
        raise FileRejected("corrupt_pdf", "PDF could not be read") from exc
    if n == 0 or n > 50:
        raise FileRejected("pdf_pages", "PDF must have between 1 and 50 pages")
    return n


def _check_xml(content: bytes) -> None:
    try:
        root = SafeET.fromstring(content)  # blocks XXE, entity expansion, external DTDs
    except Exception as exc:  # noqa: BLE001
        raise FileRejected("invalid_xml", "XML could not be parsed safely") from exc
    if not root.tag.endswith("Invoice"):
        raise FileRejected("unsupported_xml", "only UBL-TR e-Fatura / e-Arşiv invoice XML is accepted")
