// Document boundary detection on a downscaled frame.
//
// Receipts/invoices are brighter than the surface they lie on. We threshold the
// luminance with Otsu's method, label connected bright regions, and fit a
// quadrilateral to each large, rectangular-looking region via extreme points.
// Works for several receipts in one frame (multi-receipt capture).
//
// On a light surface (white table, paper) the receipt is not brighter than its background, so
// a second method finds it by its text instead: dark strokes relative to their neighbourhood
// (adaptive threshold) are merged into one block, and a rotated rectangle around that block
// plus a small margin is the receipt — independent of the background colour.

import { Img, Point, Quad, isConvex, quadArea } from "./geometry";

export interface Detection {
  quad: Quad; // in source-image coordinates
  areaRatio: number; // quad area / frame area
  fill: number; // region pixels / quad area (1 = perfect rectangle)
  method?: "paper" | "text" | "frame";
}

export interface DetectOptions {
  workWidth?: number;
  minAreaRatio?: number;
  maxAreaRatio?: number;
  minFill?: number;
  maxDocuments?: number;
}

export function toGray(img: Img, workWidth: number): { gray: Uint8Array; white: Uint8Array; w: number; h: number; scale: number } {
  const scale = Math.min(1, workWidth / img.width);
  const w = Math.max(1, Math.round(img.width * scale));
  const h = Math.max(1, Math.round(img.height * scale));
  const gray = new Uint8Array(w * h);
  const white = new Uint8Array(w * h); // min(R,G,B): paper is white, wood/cardboard are not
  const inv = 1 / scale;
  for (let y = 0; y < h; y++) {
    const sy = Math.min(img.height - 1, Math.floor((y + 0.5) * inv));
    for (let x = 0; x < w; x++) {
      const sx = Math.min(img.width - 1, Math.floor((x + 0.5) * inv));
      const i = (sy * img.width + sx) * 4;
      const r = img.data[i], g = img.data[i + 1], b = img.data[i + 2];
      gray[y * w + x] = (r * 299 + g * 587 + b * 114) / 1000;
      white[y * w + x] = Math.min(r, g, b);
    }
  }
  return { gray, white, w, h, scale };
}

export function otsu(gray: Uint8Array): number {
  const hist = new Array(256).fill(0);
  for (let i = 0; i < gray.length; i++) hist[gray[i]]++;
  const total = gray.length;
  let sum = 0;
  for (let t = 0; t < 256; t++) sum += t * hist[t];
  let sumB = 0, wB = 0, best = 0, threshold = 127;
  for (let t = 0; t < 256; t++) {
    wB += hist[t];
    if (wB === 0) continue;
    const wF = total - wB;
    if (wF === 0) break;
    sumB += t * hist[t];
    const mB = sumB / wB, mF = (sum - sumB) / wF;
    const between = wB * wF * (mB - mF) * (mB - mF);
    if (between > best) {
      best = between;
      threshold = t;
    }
  }
  return threshold;
}

export function detectDocuments(img: Img, opts: DetectOptions = {}): Detection[] {
  const { workWidth = 240, minAreaRatio = 0.06, maxAreaRatio = 0.97, minFill = 0.72, maxDocuments = 4 } = opts;
  const { gray, white, w, h, scale } = toGray(img, workWidth);
  const stats = frameStats(gray, white, w, h);
  lastDetectStats = { width: img.width, height: img.height, ...stats };
  // paper outline: on the whiteness channel first (separates white paper from wood/beige), then luminance
  // (whiteness needs a clear white-vs-surface gap, so light wood grain is never taken for paper)
  for (const [ch, minGap] of [[white, 60], [gray, 0]] as const) {
    const byPaper = detectByBrightness(ch, w, h, scale, minAreaRatio, maxAreaRatio, minFill, maxDocuments, minGap, white);
    if (byPaper.length) return byPaper;
  }
  const byText = detectByText(gray, w, h, scale, white);
  if (byText) return [byText];
  // close-up: the receipt fills the frame (its edges touch the border) → the whole frame is the document
  if (stats.centerWhite >= 140 && stats.centerInk >= 0.015 && stats.lineStructure >= 0.6) {
    const inset = 0.01;
    const W = img.width, H = img.height;
    return [{ quad: [{ x: W * inset, y: H * inset }, { x: W * (1 - inset), y: H * inset }, { x: W * (1 - inset), y: H * (1 - inset) },
      { x: W * inset, y: H * (1 - inset) }] as Quad, areaRatio: (1 - 2 * inset) ** 2, fill: 1, method: "frame" }];
  }
  return [];
}

export interface DetectStats { width: number; height: number; centerWhite: number; centerInk: number; lineStructure: number }
/** Last frame's measurements, shown on screen when nothing is found (helps support from a screenshot). */
export let lastDetectStats: DetectStats | null = null;

/** Median whiteness and share of text-like dark pixels in the central 60% of the frame. */
function frameStats(gray: Uint8Array, white: Uint8Array, w: number, h: number): { centerWhite: number; centerInk: number; lineStructure: number } {
  const x0 = Math.floor(w * 0.2), x1 = Math.ceil(w * 0.8), y0 = Math.floor(h * 0.2), y1 = Math.ceil(h * 0.8);
  const hist = new Uint32Array(256);
  let n = 0, ink = 0;
  const rows: number[] = [];
  for (let y = y0; y < y1; y++) {
    let row = 0;
    for (let x = x0; x < x1; x++) {
      const i = y * w + x;
      hist[white[i]]++;
      n++;
      // dark stroke next to brighter neighbours (cheap local contrast test)
      const nb = Math.max(gray[i - 2] ?? 0, gray[i + 2] ?? 0, gray[i - 2 * w] ?? 0, gray[i + 2 * w] ?? 0);
      if (nb - gray[i] > 40) { ink++; row++; }
    }
    rows.push(row);
  }
  // text = rows of ink separated by empty rows → row sums vary a lot; random texture → uniform rows
  const mean = rows.reduce((a, b) => a + b, 0) / Math.max(1, rows.length);
  const sd = Math.sqrt(rows.reduce((a, b) => a + (b - mean) ** 2, 0) / Math.max(1, rows.length));
  let acc = 0, median = 0;
  for (let v = 0; v < 256; v++) { acc += hist[v]; if (acc >= n / 2) { median = v; break; } }
  return { centerWhite: median, centerInk: n ? ink / n : 0, lineStructure: mean ? sd / mean : 0 };
}

function detectByBrightness(gray: Uint8Array, w: number, h: number, scale: number, minAreaRatio: number,
  maxAreaRatio: number, minFill: number, maxDocuments: number, minGap = 0, white?: Uint8Array): Detection[] {
  const t = otsu(gray);
  // Require real contrast between paper and background.
  let bright = 0, sumB = 0, sumD = 0;
  for (let i = 0; i < gray.length; i++) if (gray[i] > t) { bright++; sumB += gray[i]; } else sumD += gray[i];
  if (bright === 0 || bright === gray.length) return [];
  if (minGap && sumB / bright - sumD / (gray.length - bright) < minGap) return [];
  let whiteTotal = 0;
  if (white) for (let i = 0; i < white.length; i++) whiteTotal += white[i];

  const labels = new Int32Array(w * h).fill(-1);
  const queue = new Int32Array(w * h);
  const results: Detection[] = [];
  const frameArea = w * h;

  for (let start = 0; start < w * h; start++) {
    if (gray[start] <= t || labels[start] !== -1) continue;
    // BFS over the bright component
    let head = 0, tail = 0, count = 0, touchesBorder = 0, whiteIn = 0;
    let tl: Point = { x: 0, y: 0 }, tr = tl, br = tl, bl = tl;
    let minS = Infinity, maxS = -Infinity, minD = Infinity, maxD = -Infinity;
    queue[tail++] = start;
    labels[start] = start;
    while (head < tail) {
      const p = queue[head++];
      const x = p % w, y = (p - x) / w;
      count++;
      if (white) whiteIn += white[p];
      if (x === 0 || y === 0 || x === w - 1 || y === h - 1) touchesBorder++;
      const s = x + y, d = x - y;
      if (s < minS) { minS = s; tl = { x, y }; }
      if (s > maxS) { maxS = s; br = { x, y }; }
      if (d > maxD) { maxD = d; tr = { x, y }; }
      if (d < minD) { minD = d; bl = { x, y }; }
      const nb = [x > 0 ? p - 1 : -1, x < w - 1 ? p + 1 : -1, y > 0 ? p - w : -1, y < h - 1 ? p + w : -1];
      for (const q of nb) {
        if (q >= 0 && labels[q] === -1 && gray[q] > t) {
          labels[q] = start;
          queue[tail++] = q;
        }
      }
    }
    if (count / frameArea < minAreaRatio) continue;
    // paper is clearly whiter than what surrounds it (light wood grain / beige stripes are not)
    if (white && count < frameArea) {
      const inside = whiteIn / count, outside = (whiteTotal - whiteIn) / (frameArea - count);
      if (inside < 100 || inside < outside * 1.3) continue;
    }
    const quad: Quad = [tl, tr, br, bl];
    if (!isConvex(quad)) continue;
    const area = quadArea(quad);
    const areaRatio = area / frameArea;
    const fill = Math.min(1, count / Math.max(area, 1));
    // A region hugging the frame border on a large part is the background, not paper.
    if (areaRatio > maxAreaRatio || fill < minFill || touchesBorder > (w + h) * 0.5) continue;
    const inv = 1 / scale;
    const toSrc = (pt: Point): Point => ({ x: (pt.x + 0.5) * inv, y: (pt.y + 0.5) * inv });
    results.push({ quad: [toSrc(tl), toSrc(tr), toSrc(br), toSrc(bl)] as Quad, areaRatio, fill, method: "paper" });
  }
  return results.sort((a, b) => b.areaRatio - a.areaRatio).slice(0, maxDocuments);
}

/** Box sums over an integral image (inclusive box of radius r, clamped to the frame). */
function boxSum(ii: Float64Array, w: number, h: number, x: number, y: number, r: number): [number, number] {
  const x0 = Math.max(0, x - r), y0 = Math.max(0, y - r), x1 = Math.min(w - 1, x + r), y1 = Math.min(h - 1, y + r);
  const W = w + 1;
  const sum = ii[(y1 + 1) * W + x1 + 1] - ii[y0 * W + x1 + 1] - ii[(y1 + 1) * W + x0] + ii[y0 * W + x0];
  return [sum, (x1 - x0 + 1) * (y1 - y0 + 1)];
}

function integral(src: ArrayLike<number>, w: number, h: number): Float64Array {
  const W = w + 1;
  const ii = new Float64Array(W * (h + 1));
  for (let y = 0; y < h; y++) {
    let row = 0;
    for (let x = 0; x < w; x++) {
      row += src[y * w + x];
      ii[(y + 1) * W + x + 1] = ii[y * W + x + 1] + row;
    }
  }
  return ii;
}

function cross(o: Point, a: Point, b: Point): number {
  return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
}

/** Smallest-area enclosing rectangle of a point set: centre, axis u and extents along u / v=⟂u. */
export function minAreaRect(points: Point[]): { cx: number; cy: number; ux: number; uy: number; a0: number; a1: number; b0: number; b1: number } | null {
  if (points.length < 3) return null;
  const p = [...points].sort((a, b) => a.x - b.x || a.y - b.y);
  const lower: Point[] = [], upper: Point[] = [];
  for (const q of p) { while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], q) <= 0) lower.pop(); lower.push(q); }
  for (let i = p.length - 1; i >= 0; i--) { const q = p[i]; while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], q) <= 0) upper.pop(); upper.push(q); }
  const hull = lower.slice(0, -1).concat(upper.slice(0, -1));
  if (hull.length < 3) return null;
  let best: { area: number; ux: number; uy: number; a0: number; a1: number; b0: number; b1: number } | null = null;
  for (let i = 0; i < hull.length; i++) {
    const e = hull[(i + 1) % hull.length], s = hull[i];
    const len = Math.hypot(e.x - s.x, e.y - s.y);
    if (len === 0) continue;
    const ux = (e.x - s.x) / len, uy = (e.y - s.y) / len;
    let a0 = Infinity, a1 = -Infinity, b0 = Infinity, b1 = -Infinity;
    for (const q of hull) {
      const a = q.x * ux + q.y * uy, b = -q.x * uy + q.y * ux;
      if (a < a0) a0 = a; if (a > a1) a1 = a; if (b < b0) b0 = b; if (b > b1) b1 = b;
    }
    const area = (a1 - a0) * (b1 - b0);
    if (!best || area < best.area) best = { area, ux, uy, a0, a1, b0, b1 };
  }
  if (!best) return null;
  // express extents relative to the rectangle centre
  const am = (best.a0 + best.a1) / 2, bm = (best.b0 + best.b1) / 2;
  const cx = am * best.ux - bm * best.uy, cy = am * best.uy + bm * best.ux;
  return { cx, cy, ux: best.ux, uy: best.uy, a0: best.a0 - am, a1: best.a1 - am, b0: best.b0 - bm, b1: best.b1 - bm };
}

/** Receipt found by its text block (works on white/light backgrounds). */
export function detectByText(gray: Uint8Array, w: number, h: number, scale: number, white?: Uint8Array): Detection | null {
  const frameArea = w * h;
  // 1) ink = clearly darker than the local neighbourhood, on a reasonably bright surface
  const r = Math.max(3, Math.round(w / 40));
  const giI = integral(gray, w, h);
  const ink = new Uint8Array(frameArea);
  let inkCount = 0;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const [s, n] = boxSum(giI, w, h, x, y, r);
      const mean = s / n;
      if (mean > 90 && gray[y * w + x] < mean - 14) { ink[y * w + x] = 1; inkCount++; }
    }
  }
  if (inkCount < frameArea * 0.004) return null; // no text in view
  // 2) merge characters and lines into one block (dilation via box sums of the ink mask)
  const R = Math.max(3, Math.round(w / 32));
  const inkI = integral(ink, w, h);
  const block = new Uint8Array(frameArea);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) if (boxSum(inkI, w, h, x, y, R)[0] > 0) block[y * w + x] = 1;
  // 3) largest connected block
  const seen = new Uint8Array(frameArea);
  const queue = new Int32Array(frameArea);
  let best: { pts: Int32Array; n: number; border: number; ink: number } | null = null;
  for (let start = 0; start < frameArea; start++) {
    if (!block[start] || seen[start]) continue;
    let head = 0, tail = 0, border = 0, inkIn = 0;
    queue[tail++] = start;
    seen[start] = 1;
    while (head < tail) {
      const p = queue[head++];
      const x = p % w, y = (p - x) / w;
      if (x === 0 || y === 0 || x === w - 1 || y === h - 1) border++;
      inkIn += ink[p];
      if (x > 0 && block[p - 1] && !seen[p - 1]) { seen[p - 1] = 1; queue[tail++] = p - 1; }
      if (x < w - 1 && block[p + 1] && !seen[p + 1]) { seen[p + 1] = 1; queue[tail++] = p + 1; }
      if (y > 0 && block[p - w] && !seen[p - w]) { seen[p - w] = 1; queue[tail++] = p - w; }
      if (y < h - 1 && block[p + w] && !seen[p + w]) { seen[p + w] = 1; queue[tail++] = p + w; }
    }
    if (!best || tail > best.n) best = { pts: queue.slice(0, tail), n: tail, border, ink: inkIn };
  }
  if (!best) return null;
  const areaRatio0 = best.n / frameArea;
  // too small, the whole frame (textured table / wall of text) or hugging the border → not a receipt
  if (areaRatio0 < 0.04 || areaRatio0 > 0.85 || best.border > (w + h) * 0.35) return null;
  if (best.ink / best.n < 0.04) return null;
  if (white) {
    // the surface behind the "text" must be paper-white (wood grain / patterned tables are not)
    let sw = 0, nw = 0;
    for (let i = 0; i < best.n; i++) { const p = best.pts[i]; if (!ink[p]) { sw += white[p]; nw++; } }
    if (!nw || sw / nw < 140) return null;
  }
  // 4) minimum-area rotated rectangle around the block (convex hull + rotating edges; robust
  //    for square-ish blocks where principal axes are undefined)
  // shrink the dilated block back (closing) so the box hugs the text, not text + dilation halo
  const comp = new Uint8Array(frameArea);
  for (let i = 0; i < best.n; i++) comp[best.pts[i]] = 1;
  const compI = integral(comp, w, h);
  const er = Math.max(1, R - 1);
  const pts: Point[] = [];
  for (let i = 0; i < best.n; i++) {
    const p = best.pts[i], x = p % w, y = (p - x) / w;
    const [sum, n] = boxSum(compI, w, h, x, y, er);
    if (sum === n) pts.push({ x, y });
  }
  const rect = minAreaRect(pts);
  if (!rect) return null;
  let { cx, cy, ux, uy, a0, a1, b0, b1 } = rect;
  const vx = -uy, vy = ux;
  // paper margin around the text block
  const ma = (a1 - a0) * 0.03 + 2, mb = (b1 - b0) * 0.03 + 2;
  a0 -= ma; a1 += ma; b0 -= mb; b1 += mb;
  const corner = (a: number, b: number): Point => ({
    x: Math.min(w - 0.5, Math.max(-0.5, cx + a * ux + b * vx)),
    y: Math.min(h - 0.5, Math.max(-0.5, cy + a * uy + b * vy)),
  });
  const cs = [corner(a0, b0), corner(a1, b0), corner(a1, b1), corner(a0, b1)];
  // order TL, TR, BR, BL by angle around the centre (no duplicates even near 45°)
  const mcx = cs.reduce((t, p) => t + p.x, 0) / 4, mcy = cs.reduce((t, p) => t + p.y, 0) / 4;
  const byAngle = [...cs].sort((p, q) => Math.atan2(p.y - mcy, p.x - mcx) - Math.atan2(q.y - mcy, q.x - mcx));
  let k = 0;
  for (let i = 1; i < 4; i++) if (byAngle[i].x + byAngle[i].y < byAngle[k].x + byAngle[k].y) k = i;
  const [tl, tr, br, bl] = [0, 1, 2, 3].map((i) => byAngle[(k + i) % 4]);
  const quad: Quad = [tl, tr, br, bl];
  if (new Set(quad).size < 4 || !isConvex(quad)) return null;
  const area = quadArea(quad);
  const inv = 1 / scale;
  const toSrc = (pt: Point): Point => ({ x: (pt.x + 0.5) * inv, y: (pt.y + 0.5) * inv });
  return { quad: quad.map(toSrc) as Quad, areaRatio: area / frameArea, fill: Math.min(1, best.n / Math.max(area, 1)), method: "text" };
}

/** Track detection stability across frames to trigger automatic capture. */
export class StabilityTracker {
  private last: Quad | null = null;
  private stableFrames = 0;
  private seenFrames = 0;
  private absentFrames = 0;
  private armed = true;

  /** `rearmFrames`: the document must be out of view this many frames in a row before the next
   *  auto-capture — a single dropped detection (hand shake, glare) must not cause a second photo.
   *  `maxWaitFrames`: a hand-held phone never holds perfectly still (and a stack of receipts has
   *  ragged edges), so once a document has been in view this long it is captured anyway. */
  constructor(private diag: number, private needFrames = 12, private tolerance = 0.015, private rearmFrames = 8,
    private maxWaitFrames = Infinity) {}

  /** Returns true exactly once per document placed in view (still for `needFrames`, or in view
   *  for `maxWaitFrames`). */
  update(quad: Quad | null): boolean {
    if (!quad) {
      this.last = null;
      this.stableFrames = 0;
      this.seenFrames = 0;
      if (++this.absentFrames >= this.rearmFrames) this.armed = true; // document really removed
      return false;
    }
    this.absentFrames = 0;
    this.seenFrames++;
    if (this.last) {
      const move = Math.max(...quad.map((p, i) => Math.hypot(p.x - this.last![i].x, p.y - this.last![i].y))) / this.diag;
      if (move < this.tolerance) this.stableFrames++;
      else this.stableFrames = 0;
    }
    this.last = quad;
    if (this.armed && (this.stableFrames >= this.needFrames || this.seenFrames >= this.maxWaitFrames)) {
      this.armed = false;
      this.stableFrames = 0;
      return true;
    }
    return false;
  }

  /** A manual photo was taken: do not auto-capture the same document again. */
  disarm(): void {
    this.armed = false;
    this.stableFrames = 0;
  }

  get progress(): number {
    if (!this.armed) return 0;
    return Math.min(1, Math.max(this.stableFrames / this.needFrames, this.seenFrames / this.maxWaitFrames));
  }

  reset(): void {
    this.last = null;
    this.stableFrames = 0;
    this.seenFrames = 0;
    this.armed = true;
  }
}
