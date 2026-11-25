"""Free, on-server OCR with Tesseract (Turkish model) + PDF text extraction.

No document leaves the server on this path (no third-party processor → simpler
KVKK position than cloud OCR/AI). Requires the `tesseract-ocr`,
`tesseract-ocr-tur` and `poppler-utils` system packages (installed in the
backend Docker image).
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

GOOD_ENOUGH = 10.0  # total+date+VAT consistent (9) + supplier name or VKN; max score is 12
TARGET_WIDTH = 1700  # enough for receipt fonts; keeps OCR fast on small free-tier CPUs


class LocalOcrError(Exception):
    pass


def available() -> bool:
    return shutil.which("tesseract") is not None


def preprocess(content: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(content))
    img = ImageOps.exif_transpose(img).convert("L")
    img = ImageOps.autocontrast(img, cutoff=1)
    if img.width < TARGET_WIDTH:
        scale = TARGET_WIDTH / img.width
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.Resampling.LANCZOS)
    # global threshold after contrast stretch works well for thermal paper; median removes speckle
    img = img.point(lambda v: 255 if v > 140 else 0).filter(ImageFilter.MedianFilter(3))
    return img


def _prep(img: Image.Image) -> Image.Image:
    img = ImageOps.autocontrast(img.convert("L"), cutoff=1)
    if img.width < TARGET_WIDTH:
        scale = TARGET_WIDTH / img.width
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.Resampling.LANCZOS)
    return img


def gray(content: bytes) -> Image.Image:
    return _prep(ImageOps.exif_transpose(Image.open(io.BytesIO(content))))


def _tesseract(img: Image.Image, psm: str, timeout: int) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "in.png"
        img.save(p)
        r = subprocess.run(["tesseract", str(p), "-", "-l", "tur", "--psm", psm,
                            "-c", "preserve_interword_spaces=1"],
                           capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "OMP_THREAD_LIMIT": "1"})
    if r.returncode != 0:
        raise LocalOcrError("tesseract failed")
    return r.stdout


def ocr_variants(content: bytes, *, timeout: int = 90):
    """Yield OCR texts under several geometry/preprocessing/layout settings, most promising first.

    Real phone photos differ: receipts lie crooked (→ deskew by text angle), are shot at an
    angle (→ perspective warp of the detected paper), or sit on printed paper / uneven light
    (→ plain gray vs. binarised). The parser's score picks the most consistent result.
    """
    from app.services.extraction.document_geometry import deskew, straighten

    if not available():
        raise LocalOcrError("tesseract is not installed")
    original = ImageOps.exif_transpose(Image.open(io.BytesIO(content))).convert("L")
    candidates: list[tuple[str, Image.Image, str]] = []
    try:
        straight = deskew(original)
    except Exception:  # noqa: BLE001 — geometry is best-effort
        straight = None
    if straight is not None:
        d = _prep(straight)
        candidates += [("deskew", d, "6"), ("deskew", d, "4")]
    try:
        warped = straighten(original)
    except Exception:  # noqa: BLE001
        warped = None
    if warped is not None:
        candidates += [("warp", _prep(warped), "6")]
    g = _prep(original)
    candidates += [("gray", g, "4"), ("gray", g, "6"), ("binary", preprocess(content), "6")]
    for name, img, psm in candidates:
        yield f"{name}-psm{psm}", _tesseract(img, psm, timeout)


def ocr_image(content: bytes, *, timeout: int = 90) -> str:
    """OCR the image under every variant and return the text whose parse is the most
    complete and arithmetically consistent. Stops at the first variant that already has total,
    date, a VAT breakdown matching the total and the supplier — usually the first one, so a
    receipt costs one tesseract run instead of six."""
    from app.services.extraction.receipt_parser import parse_text, score

    best: tuple[float, str] | None = None
    for _name, text in ocr_variants(content, timeout=timeout):
        doc, notes = parse_text(text, source="ocr")
        sc = score(doc, notes)
        if best is None or sc > best[0]:
            best = (sc, text)
        if sc >= GOOD_ENOUGH:
            break
    return best[1] if best else ""


def pdf_text(content: bytes, max_pages: int = 5) -> str:
    """Embedded text layer of a PDF (e-Arşiv/e-Fatura PDFs usually have one)."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(content))
        return "\n".join((page.extract_text() or "") for page in reader.pages[:max_pages])
    except Exception:  # noqa: BLE001 — unreadable text layer → fall back to OCR
        return ""


def ocr_pdf(content: bytes, max_pages: int = 3, *, timeout: int = 120) -> str:
    """Rasterise a scanned PDF (no text layer) and OCR its first pages."""
    if not shutil.which("pdftoppm"):
        raise LocalOcrError("pdftoppm is not installed")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.pdf"
        src.write_bytes(content)
        subprocess.run(["pdftoppm", "-r", "200", "-l", str(max_pages), "-png", str(src), str(Path(tmp) / "p")],
                       check=True, capture_output=True, timeout=timeout)
        return "\n".join(ocr_image(p.read_bytes()) for p in sorted(Path(tmp).glob("p*.png")))
