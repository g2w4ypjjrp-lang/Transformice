import { describe, expect, it } from "vitest";
import { applyH, homography, Img, isConvex, Quad, quadArea, targetSize, warp } from "./geometry";
import { detectDocuments, otsu, StabilityTracker } from "./detect";
import { enhance, sharpness } from "./enhance";

function canvas(w: number, h: number, bg: number): Img {
  const data = new Uint8ClampedArray(w * h * 4);
  for (let i = 0; i < w * h; i++) {
    data[i * 4] = data[i * 4 + 1] = data[i * 4 + 2] = bg;
    data[i * 4 + 3] = 255;
  }
  return { data, width: w, height: h };
}

/** Fill a (possibly rotated) convex quad with a colour. */
function fillQuad(img: Img, q: Quad, v: number) {
  for (let y = 0; y < img.height; y++) {
    for (let x = 0; x < img.width; x++) {
      let inside = true;
      for (let i = 0; i < 4; i++) {
        const a = q[i], b = q[(i + 1) % 4];
        if ((b.x - a.x) * (y - a.y) - (b.y - a.y) * (x - a.x) < 0) { inside = false; break; }
      }
      if (inside) {
        const o = (y * img.width + x) * 4;
        img.data[o] = img.data[o + 1] = img.data[o + 2] = v;
      }
    }
  }
}

const rotated = (cx: number, cy: number, w: number, h: number, deg: number): Quad => {
  const r = (deg * Math.PI) / 180;
  const pts = [[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]];
  return pts.map(([x, y]) => ({ x: cx + x * Math.cos(r) - y * Math.sin(r), y: cy + x * Math.sin(r) + y * Math.cos(r) })) as Quad;
};

describe("geometry", () => {
  it("homography maps the four corners exactly", () => {
    const src: Quad = [{ x: 10, y: 20 }, { x: 300, y: 35 }, { x: 290, y: 410 }, { x: 5, y: 400 }];
    const dst: Quad = [{ x: 0, y: 0 }, { x: 299, y: 0 }, { x: 299, y: 399 }, { x: 0, y: 399 }];
    const h = homography(src, dst);
    src.forEach((p, i) => {
      const m = applyH(h, p);
      expect(m.x).toBeCloseTo(dst[i].x, 6);
      expect(m.y).toBeCloseTo(dst[i].y, 6);
    });
  });

  it("area, convexity and target size", () => {
    const q: Quad = [{ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 100, y: 50 }, { x: 0, y: 50 }];
    expect(quadArea(q)).toBe(5000);
    expect(isConvex(q)).toBe(true);
    expect(isConvex([q[0], q[2], q[1], q[3]])).toBe(false);
    expect(targetSize(q)).toEqual({ width: 100, height: 50 });
    expect(targetSize(q, 50)).toEqual({ width: 50, height: 25 });
  });

  it("warp straightens a rotated white page", () => {
    const img = canvas(400, 400, 30);
    const q = rotated(200, 200, 200, 280, 12);
    fillQuad(img, q, 240);
    const out = warp(img, q, 100, 140);
    let white = 0;
    for (let i = 0; i < out.width * out.height; i++) if (out.data[i * 4] > 200) white++;
    expect(white / (out.width * out.height)).toBeGreaterThan(0.95);
  });
});

describe("detection", () => {
  it("otsu separates two clusters", () => {
    const g = new Uint8Array([...Array(500).fill(40), ...Array(500).fill(220)]);
    const t = otsu(g);
    expect(t).toBeGreaterThanOrEqual(40);
    expect(t).toBeLessThan(220);
  });

  it("finds a tilted receipt and its corners", () => {
    const img = canvas(640, 480, 45);
    const q = rotated(320, 240, 220, 360, -8);
    fillQuad(img, q, 235);
    const [d] = detectDocuments(img);
    expect(d).toBeDefined();
    expect(d.fill).toBeGreaterThan(0.85);
    const expectedArea = quadArea(q) / (640 * 480);
    expect(d.areaRatio).toBeGreaterThan(expectedArea * 0.85);
    expect(d.areaRatio).toBeLessThan(expectedArea * 1.1);
    // corner order TL,TR,BR,BL and within a few px (downscale tolerance)
    const sorted = [...q].sort((a, b) => a.x + a.y - (b.x + b.y));
    expect(Math.hypot(d.quad[0].x - sorted[0].x, d.quad[0].y - sorted[0].y)).toBeLessThan(12);
  });

  it("detects several receipts in one frame", () => {
    const img = canvas(800, 500, 50);
    fillQuad(img, rotated(200, 250, 160, 300, 4), 230);
    fillQuad(img, rotated(580, 240, 180, 320, -6), 240);
    const ds = detectDocuments(img);
    expect(ds.length).toBe(2);
    expect(ds.every((d) => d.fill > 0.8)).toBe(true);
  });

  it("ignores frames without a document and tiny specks", () => {
    expect(detectDocuments(canvas(320, 240, 128))).toEqual([]);
    const img = canvas(320, 240, 40);
    fillQuad(img, rotated(100, 100, 20, 20, 0), 250);
    expect(detectDocuments(img)).toEqual([]);
  });
});

describe("detection on a light background (no dark surface)", () => {
  /** White receipt on a white/light table: almost no brightness difference, only the text stands out. */
  function receiptOnLightTable(angle: number, bg = 214) {
    const img = canvas(640, 480, bg);
    const cx = 320, cy = 240, pw = 200, ph = 380;
    fillQuad(img, rotated(cx, cy, pw, ph, angle), 228); // paper barely brighter than the table
    const rad = (angle * Math.PI) / 180;
    const at = (u: number, v: number) => ({ x: cx + u * Math.cos(rad) - v * Math.sin(rad), y: cy + u * Math.sin(rad) + v * Math.cos(rad) });
    for (let v = -ph / 2 + 18; v < ph / 2 - 18; v += 12) {           // text lines
      for (let u = -pw / 2 + 14; u < pw / 2 - 14; u += 22) {          // words
        const wlen = 12 + ((u * 7 + v * 3) & 7);
        const c = at(u + wlen / 2, v);
        fillQuad(img, rotated(c.x, c.y, wlen, 5, angle), 50);
      }
    }
    return { img, paper: rotated(cx, cy, pw, ph, angle) };
  }

  it.each([0, 9, -14])("finds a receipt by its text (tilt %i°)", (angle) => {
    const { img, paper } = receiptOnLightTable(angle);
    const ds = detectDocuments(img, { workWidth: 200 });
    expect(ds.length).toBe(1);
    expect(ds[0].method).toBe("text");
    const ratio = ds[0].areaRatio / (quadArea(paper) / (640 * 480));
    expect(ratio).toBeGreaterThan(0.75);  // covers the text block …
    expect(ratio).toBeLessThan(1.25);     // … without swallowing the table
    const c = ds[0].quad.reduce((m, p) => ({ x: m.x + p.x / 4, y: m.y + p.y / 4 }), { x: 0, y: 0 });
    expect(Math.hypot(c.x - 320, c.y - 240)).toBeLessThan(20);
  });

  it("still prefers the paper outline on a dark surface", () => {
    const img = canvas(640, 480, 45);
    fillQuad(img, rotated(320, 240, 220, 360, -8), 235);
    expect(detectDocuments(img)[0].method).toBe("paper");
  });

  it("finds nothing on an empty light table or a busy texture", () => {
    expect(detectDocuments(canvas(640, 480, 214))).toEqual([]);
    const busy = canvas(320, 240, 200);
    for (let i = 0; i < busy.width * busy.height; i++) if ((i * 2654435761) % 7 === 0) busy.data[i * 4] = busy.data[i * 4 + 1] = busy.data[i * 4 + 2] = 40;
    expect(detectDocuments(busy)).toEqual([]);
  });
});

describe("stability tracker", () => {
  const q: Quad = [{ x: 10, y: 10 }, { x: 110, y: 10 }, { x: 110, y: 210 }, { x: 10, y: 210 }];
  it("fires once after enough still frames and re-arms when the document is removed", () => {
    const t = new StabilityTracker(1000, 5);
    const fired = Array.from({ length: 12 }, () => t.update(q));
    expect(fired.filter(Boolean).length).toBe(1);
    for (let i = 0; i < 8; i++) t.update(null);
    expect(Array.from({ length: 8 }, () => t.update(q)).filter(Boolean).length).toBe(1);
  });
  it("a flickering detection or a big hand movement does not take a second photo", () => {
    const t = new StabilityTracker(1000, 5);
    let shots = 0;
    for (let round = 0; round < 10; round++) {
      for (let i = 0; i < 8; i++) shots += Number(t.update(q));
      shots += Number(t.update(null)); // one dropped frame
      shots += Number(t.update(q.map((p) => ({ x: p.x + 400, y: p.y })) as Quad)); // jump
    }
    expect(shots).toBe(1);
  });
  it("captures a shaky hand-held document after the max wait", () => {
    const t = new StabilityTracker(1000, 5, 0.015, 5, 18);
    const shots = Array.from({ length: 30 }, (_, i) => t.update(q.map((p) => ({ x: p.x + (i % 2) * 25, y: p.y })) as Quad));
    expect(shots.filter(Boolean).length).toBe(1);
    expect(shots.indexOf(true)).toBe(17); // 18th frame ≈ 1.5 s at 12 fps
  });
  it("manual capture disarms auto capture for that document", () => {
    const t = new StabilityTracker(1000, 5);
    t.disarm();
    expect(Array.from({ length: 20 }, () => t.update(q)).some(Boolean)).toBe(false);
  });
  it("does not fire while moving", () => {
    const t = new StabilityTracker(1000, 5);
    const moving = Array.from({ length: 20 }, (_, i) =>
      t.update(q.map((p) => ({ x: p.x + i * 30, y: p.y })) as Quad));
    expect(moving.some(Boolean)).toBe(false);
  });
});

describe("enhancement", () => {
  it("stretches contrast and binarises", () => {
    const img = canvas(60, 60, 150);
    fillQuad(img, [{ x: 10, y: 10 }, { x: 30, y: 10 }, { x: 30, y: 20 }, { x: 10, y: 20 }], 110);
    const g = enhance(img, "gray");
    const vals = new Set<number>();
    for (let i = 0; i < 3600; i++) vals.add(g.data[i * 4]);
    expect(Math.max(...vals) - Math.min(...vals)).toBeGreaterThan(200);
    const bw = enhance(img, "bw");
    for (let i = 0; i < 3600; i++) expect([0, 255]).toContain(bw.data[i * 4]);
  });
  it("sharpness is higher for edges than flat images", () => {
    const flat = canvas(50, 50, 128);
    const edges = canvas(50, 50, 0);
    for (let i = 0; i < 2500; i++) if ((i % 50) % 3 === 0) edges.data[i * 4] = edges.data[i * 4 + 1] = edges.data[i * 4 + 2] = 255;
    expect(sharpness(edges)).toBeGreaterThan(sharpness(flat) + 100);
  });
});
