"""Server-side document detection + perspective correction for receipt photos.

Same idea as the web scanner (web/src/lib/scanner): receipts are brighter than
the table they lie on. Otsu threshold → largest bright connected region →
quadrilateral from extreme points → perspective warp to an upright rectangle.
Lets OCR read receipts photographed at an angle or lying crooked.
"""

from __future__ import annotations

from collections import deque

import numpy as np
from PIL import Image

Quad = tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]  # TL TR BR BL


def _otsu(gray: np.ndarray) -> int:
    hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    total = gray.size
    cum = np.cumsum(hist)
    cum_mean = np.cumsum(hist * np.arange(256))
    mean_total = cum_mean[-1]
    w_b = cum
    w_f = total - cum
    valid = (w_b > 0) & (w_f > 0)
    between = np.zeros(256)
    between[valid] = (mean_total * w_b[valid] / total - cum_mean[valid]) ** 2 * total / (w_b[valid] * w_f[valid])
    return int(np.argmax(between))


def _largest_component(mask: np.ndarray) -> tuple[np.ndarray, int] | None:
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    best: list[tuple[int, int]] = []
    best_border = 0
    for y0, x0 in zip(*np.nonzero(mask)):
        if seen[y0, x0]:
            continue
        comp, border = [], 0
        q = deque([(y0, x0)])
        seen[y0, x0] = True
        while q:
            y, x = q.popleft()
            comp.append((y, x))
            if y in (0, h - 1) or x in (0, w - 1):
                border += 1
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    q.append((ny, nx))
        if len(comp) > len(best):
            best, best_border = comp, border
    if not best:
        return None
    return np.array(best), best_border


def _area(q: np.ndarray) -> float:
    x, y = q[:, 0], q[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def detect_quad(img: Image.Image, work_width: int = 320) -> tuple[Quad, float, float] | None:
    """Return (quad in source coordinates, area ratio, fill ratio) or None."""
    gray = img.convert("L")
    scale = work_width / gray.width
    small = gray.resize((work_width, max(1, int(gray.height * scale))), Image.Resampling.BILINEAR)
    g = np.asarray(small, dtype=np.uint8)
    t = _otsu(g)
    mask = g > t
    if not mask.any() or mask.all():
        return None
    found = _largest_component(mask)
    if found is None:
        return None
    pts, border = found
    ys, xs = pts[:, 0].astype(float), pts[:, 1].astype(float)
    s, d = xs + ys, xs - ys
    quad = np.array([[xs[s.argmin()], ys[s.argmin()]], [xs[d.argmax()], ys[d.argmax()]],
                     [xs[s.argmax()], ys[s.argmax()]], [xs[d.argmin()], ys[d.argmin()]]])
    frame = g.shape[0] * g.shape[1]
    area = _area(quad)
    if area <= 0:
        return None
    area_ratio, fill = area / frame, min(1.0, len(pts) / area)
    if area_ratio < 0.1 or area_ratio > 0.97 or fill < 0.7 or border > (g.shape[0] + g.shape[1]) * 0.6:
        return None
    src = tuple((float((x + 0.5) / scale), float((y + 0.5) / scale)) for x, y in quad)
    return src, float(area_ratio), float(fill)  # type: ignore[return-value]


def warp(img: Image.Image, quad: Quad, max_side: int = 2400) -> Image.Image:
    (tlx, tly), (trx, try_), (brx, bry), (blx, bly) = quad
    w = max(np.hypot(trx - tlx, try_ - tly), np.hypot(brx - blx, bry - bly))
    h = max(np.hypot(blx - tlx, bly - tly), np.hypot(brx - trx, bry - try_))
    k = min(1.0, max_side / max(w, h))
    size = (max(1, int(w * k)), max(1, int(h * k)))
    # PIL QUAD data order: upper-left, lower-left, lower-right, upper-right
    return img.transform(size, Image.Transform.QUAD, (tlx, tly, blx, bly, brx, bry, trx, try_),
                         resample=Image.Resampling.BICUBIC)


def straighten(img: Image.Image) -> Image.Image | None:
    """Perspective-corrected crop of the receipt, or None when no document is found."""
    found = detect_quad(img)
    if found is None:
        return None
    return warp(img, found[0])


def skew_angle(img: Image.Image, max_angle: float = 35.0) -> float:
    """Text skew in degrees (counter-clockwise positive) via horizontal projection profiles:
    when the image is rotated so text lines are horizontal, row sums become maximally 'peaky'."""
    gray = img.convert("L")
    k = 700 / max(gray.size)
    small = gray.resize((max(1, int(gray.width * k)), max(1, int(gray.height * k))), Image.Resampling.BILINEAR)
    g = np.asarray(small, dtype=np.uint8)
    t = _otsu(g)
    # Only dark pixels *on the paper* are text: close the bright paper mask over the
    # letters, so a dark table/background does not dominate the profile.
    from PIL import ImageFilter

    paper = Image.fromarray(((g > t) * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(9)).filter(
        ImageFilter.MinFilter(15))
    on_paper = np.asarray(paper) > 0
    ink = Image.fromarray((((g < t - 15) & on_paper) * 255).astype(np.uint8))

    def peakiness(angle: float) -> float:
        rows = np.asarray(ink.rotate(angle, resample=Image.Resampling.NEAREST, expand=True), dtype=np.float32).sum(axis=1)
        return float(np.square(np.diff(rows)).sum())

    coarse = max(np.arange(-max_angle, max_angle + 0.1, 1.5), key=peakiness)
    fine = max(np.arange(coarse - 1.5, coarse + 1.6, 0.25), key=peakiness)
    return float(fine)


def deskew(img: Image.Image, min_angle: float = 1.5) -> Image.Image | None:
    """Rotated copy with horizontal text lines, or None when already straight."""
    angle = skew_angle(img)
    if abs(angle) < min_angle:
        return None
    return img.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255 if img.mode == "L" else "white")
