"""Free, on-server OCR with Tesseract (Turkish model) + PDF text extraction.

No document leaves the server on this path (no third-party processor → simpler
KVKK position than cloud OCR/AI). Requires the `tesseract-ocr`,
`tesseract-ocr-tur` and `poppler-utils` system packages (installed in the
backend Docker image).
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

TARGET_WIDTH = 2000


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


def gray(content: bytes) -> Image.Image:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(content))).convert("L")
    img = ImageOps.autocontrast(img, cutoff=1)
    if img.width < TARGET_WIDTH:
        scale = TARGET_WIDTH / img.width
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.Resampling.LANCZOS)
    return img


def _tesseract(img: Image.Image, psm: str, timeout: int) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "in.png"
        img.save(p)
        r = subprocess.run(["tesseract", str(p), "-", "-l", "tur", "--psm", psm,
                            "-c", "preserve_interword_spaces=1"],
                           capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise LocalOcrError("tesseract failed")
    return r.stdout


# Photos of real receipts differ (lighting, background paper, tilt). Each variant
# suits a different case; the parser's score picks the most consistent result.
VARIANTS = (("gray", "4"), ("gray", "6"), ("binary", "6"))


def ocr_variants(content: bytes, *, timeout: int = 90):
    """Yield OCR texts of the image under several preprocessing/layout settings."""
    if not available():
        raise LocalOcrError("tesseract is not installed")
    g = gray(content)
    images = {"gray": g, "binary": preprocess(content)}
    for kind, psm in VARIANTS:
        yield f"{kind}-psm{psm}", _tesseract(images[kind], psm, timeout)


def ocr_image(content: bytes, *, timeout: int = 90) -> str:
    """OCR the image under every variant and return the text whose parse is the most
    complete and arithmetically consistent (stops early on a perfect result)."""
    from app.services.extraction.receipt_parser import parse_text, score

    best: tuple[float, str] | None = None
    for _name, text in ocr_variants(content, timeout=timeout):
        doc, notes = parse_text(text, source="ocr")
        sc = score(doc, notes)
        if best is None or sc > best[0]:
            best = (sc, text)
        if sc >= 12:
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
