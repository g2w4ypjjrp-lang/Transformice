// Document boundary detection on a downscaled frame.
//
// Receipts/invoices are brighter than the surface they lie on. We threshold the
// luminance with Otsu's method, label connected bright regions, and fit a
// quadrilateral to each large, rectangular-looking region via extreme points.
// Works for several receipts in one frame (multi-receipt capture).

import { Img, Point, Quad, isConvex, quadArea } from "./geometry";

export interface Detection {
  quad: Quad; // in source-image coordinates
  areaRatio: number; // quad area / frame area
  fill: number; // region pixels / quad area (1 = perfect rectangle)
}

export interface DetectOptions {
  workWidth?: number;
  minAreaRatio?: number;
  maxAreaRatio?: number;
  minFill?: number;
  maxDocuments?: number;
}

export function toGray(img: Img, workWidth: number): { gray: Uint8Array; w: number; h: number; scale: number } {
  const scale = Math.min(1, workWidth / img.width);
  const w = Math.max(1, Math.round(img.width * scale));
  const h = Math.max(1, Math.round(img.height * scale));
  const gray = new Uint8Array(w * h);
  const inv = 1 / scale;
  for (let y = 0; y < h; y++) {
    const sy = Math.min(img.height - 1, Math.floor((y + 0.5) * inv));
    for (let x = 0; x < w; x++) {
      const sx = Math.min(img.width - 1, Math.floor((x + 0.5) * inv));
      const i = (sy * img.width + sx) * 4;
      gray[y * w + x] = (img.data[i] * 299 + img.data[i + 1] * 587 + img.data[i + 2] * 114) / 1000;
    }
  }
  return { gray, w, h, scale };
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
  const { gray, w, h, scale } = toGray(img, workWidth);
  const t = otsu(gray);
  // Require real contrast between paper and background.
  let bright = 0;
  for (let i = 0; i < gray.length; i++) if (gray[i] > t) bright++;
  if (bright === 0 || bright === gray.length) return [];

  const labels = new Int32Array(w * h).fill(-1);
  const queue = new Int32Array(w * h);
  const results: Detection[] = [];
  const frameArea = w * h;

  for (let start = 0; start < w * h; start++) {
    if (gray[start] <= t || labels[start] !== -1) continue;
    // BFS over the bright component
    let head = 0, tail = 0, count = 0, touchesBorder = 0;
    let tl: Point = { x: 0, y: 0 }, tr = tl, br = tl, bl = tl;
    let minS = Infinity, maxS = -Infinity, minD = Infinity, maxD = -Infinity;
    queue[tail++] = start;
    labels[start] = start;
    while (head < tail) {
      const p = queue[head++];
      const x = p % w, y = (p - x) / w;
      count++;
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
    const quad: Quad = [tl, tr, br, bl];
    if (!isConvex(quad)) continue;
    const area = quadArea(quad);
    const areaRatio = area / frameArea;
    const fill = Math.min(1, count / Math.max(area, 1));
    // A region hugging the frame border on a large part is the background, not paper.
    if (areaRatio > maxAreaRatio || fill < minFill || touchesBorder > (w + h) * 0.5) continue;
    const inv = 1 / scale;
    const toSrc = (pt: Point): Point => ({ x: (pt.x + 0.5) * inv, y: (pt.y + 0.5) * inv });
    results.push({ quad: [toSrc(tl), toSrc(tr), toSrc(br), toSrc(bl)] as Quad, areaRatio, fill });
  }
  return results.sort((a, b) => b.areaRatio - a.areaRatio).slice(0, maxDocuments);
}

/** Track detection stability across frames to trigger automatic capture. */
export class StabilityTracker {
  private last: Quad | null = null;
  private stableFrames = 0;
  private armed = true;

  constructor(private diag: number, private needFrames = 12, private tolerance = 0.015) {}

  /** Returns true exactly once when the document has been still for `needFrames` frames. */
  update(quad: Quad | null): boolean {
    if (!quad) {
      this.last = null;
      this.stableFrames = 0;
      this.armed = true; // document removed → ready for the next one
      return false;
    }
    if (this.last) {
      const move = Math.max(...quad.map((p, i) => Math.hypot(p.x - this.last![i].x, p.y - this.last![i].y))) / this.diag;
      if (move < this.tolerance) this.stableFrames++;
      else {
        this.stableFrames = 0;
        if (move > 0.15) this.armed = true; // a different document was placed
      }
    }
    this.last = quad;
    if (this.armed && this.stableFrames >= this.needFrames) {
      this.armed = false;
      this.stableFrames = 0;
      return true;
    }
    return false;
  }

  get progress(): number {
    return this.armed ? Math.min(1, this.stableFrames / this.needFrames) : 0;
  }

  reset(): void {
    this.last = null;
    this.stableFrames = 0;
    this.armed = true;
  }
}
