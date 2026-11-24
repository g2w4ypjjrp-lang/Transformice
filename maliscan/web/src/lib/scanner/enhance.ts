// Image enhancement for scanned receipts.
//  - "color": white balance + contrast stretch
//  - "gray":  luminance + contrast stretch (default; small files, good OCR)
//  - "bw":    adaptive threshold (faded thermal receipts)

import { Img } from "./geometry";

export type EnhanceMode = "color" | "gray" | "bw";

function percentile(hist: number[], total: number, p: number): number {
  let acc = 0;
  for (let i = 0; i < 256; i++) {
    acc += hist[i];
    if (acc >= total * p) return i;
  }
  return 255;
}

export function enhance(img: Img, mode: EnhanceMode = "gray"): Img {
  const { width, height, data } = img;
  const n = width * height;
  const out = new Uint8ClampedArray(data.length);
  const lum = new Uint8Array(n);
  const hist = new Array(256).fill(0);
  for (let i = 0; i < n; i++) {
    const l = (data[i * 4] * 299 + data[i * 4 + 1] * 587 + data[i * 4 + 2] * 114) / 1000;
    lum[i] = l;
    hist[lum[i]]++;
  }
  const lo = percentile(hist, n, 0.01);
  const hi = Math.max(lo + 1, percentile(hist, n, 0.99));
  const stretch = (v: number) => ((v - lo) * 255) / (hi - lo);

  if (mode === "bw") {
    // Bradley adaptive threshold using an integral image.
    const integral = new Float64Array((width + 1) * (height + 1));
    for (let y = 0; y < height; y++) {
      let row = 0;
      for (let x = 0; x < width; x++) {
        row += lum[y * width + x];
        integral[(y + 1) * (width + 1) + x + 1] = integral[y * (width + 1) + x + 1] + row;
      }
    }
    const r = Math.max(4, Math.round(Math.min(width, height) / 16));
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const x0 = Math.max(0, x - r), x1 = Math.min(width, x + r + 1);
        const y0 = Math.max(0, y - r), y1 = Math.min(height, y + r + 1);
        const sum = integral[y1 * (width + 1) + x1] - integral[y0 * (width + 1) + x1]
          - integral[y1 * (width + 1) + x0] + integral[y0 * (width + 1) + x0];
        const mean = sum / ((x1 - x0) * (y1 - y0));
        const v = lum[y * width + x] < mean * 0.88 ? 0 : 255;
        const o = (y * width + x) * 4;
        out[o] = out[o + 1] = out[o + 2] = v;
        out[o + 3] = 255;
      }
    }
    return { data: out, width, height };
  }

  for (let i = 0; i < n; i++) {
    const o = i * 4;
    if (mode === "gray") {
      const v = stretch(lum[i]);
      out[o] = out[o + 1] = out[o + 2] = v;
    } else {
      out[o] = stretch(data[o]);
      out[o + 1] = stretch(data[o + 1]);
      out[o + 2] = stretch(data[o + 2]);
    }
    out[o + 3] = 255;
  }
  return { data: out, width, height };
}

/** Simple sharpness metric (variance of a Laplacian on luminance) to warn about blur. */
export function sharpness(img: Img, step = 2): number {
  const { width, height, data } = img;
  const L = (x: number, y: number) => {
    const i = (y * width + x) * 4;
    return (data[i] * 299 + data[i + 1] * 587 + data[i + 2] * 114) / 1000;
  };
  let sum = 0, sum2 = 0, n = 0;
  for (let y = 1; y < height - 1; y += step) {
    for (let x = 1; x < width - 1; x += step) {
      const v = 4 * L(x, y) - L(x - 1, y) - L(x + 1, y) - L(x, y - 1) - L(x, y + 1);
      sum += v;
      sum2 += v * v;
      n++;
    }
  }
  if (!n) return 0;
  const mean = sum / n;
  return sum2 / n - mean * mean;
}
