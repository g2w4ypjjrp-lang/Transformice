"use client";
/**
 * On-device OCR (tesseract.js, self-hosted under /tesseract). The phone reads each receipt
 * right after it is captured, in parallel with the camera, and sends the text with the upload;
 * the server only has to parse it, so the result is there in about a second instead of waiting
 * for the (slow, shared) server OCR. If anything fails the image is still uploaded and the
 * server reads it as before.
 */
import type { Scheduler } from "tesseract.js";

let schedulerPromise: Promise<Scheduler> | null = null;
let failed = false;

/** Phones with ≥4 cores read two receipts at once (series scanning); others use one worker. */
export const OCR_PARALLEL = typeof navigator !== "undefined" && (navigator.hardwareConcurrency ?? 2) >= 4 ? 2 : 1;

function getScheduler(): Promise<Scheduler> {
  if (!schedulerPromise) {
    schedulerPromise = (async () => {
      const { createScheduler, createWorker, OEM, PSM } = await import("tesseract.js");
      const scheduler = createScheduler();
      const make = async () => {
        const w = await createWorker("tur", OEM.LSTM_ONLY, {
          workerPath: "/tesseract/worker.min.js",
          corePath: "/tesseract/core",
          langPath: "/tesseract/lang",
          workerBlobURL: false,
          gzip: true,
          cacheMethod: "write", // model cached in IndexedDB after the first download (~2 MB)
        });
        await w.setParameters({ tessedit_pageseg_mode: PSM.SINGLE_BLOCK, preserve_interword_spaces: "1" });
        scheduler.addWorker(w);
      };
      await make(); // first worker ready → reading can start
      if (OCR_PARALLEL > 1) make().catch(() => undefined); // second one joins in the background
      return scheduler;
    })();
    schedulerPromise.catch(() => { failed = true; schedulerPromise = null; });
  }
  return schedulerPromise;
}

/** Start loading the engine early (e.g. when the camera starts) so the first receipt is fast. */
export function warmOcr(): void {
  if (typeof window === "undefined" || failed) return;
  getScheduler().catch(() => undefined);
}

/** Text of the receipt, or null when on-device OCR is unavailable / too slow. Never throws. */
export async function readOnDevice(image: Blob, timeoutMs = 25000): Promise<string | null> {
  if (typeof window === "undefined" || failed || !image.type.startsWith("image/")) return null;
  try {
    const run = (async () => {
      const s = await getScheduler();
      // text only: no hOCR/TSV/blocks output → less work per receipt
      const { data } = await s.addJob("recognize", image, {}, { text: true, blocks: false, hocr: false, tsv: false });
      return data.text?.trim() || null;
    })();
    const timeout = new Promise<null>((r) => setTimeout(() => r(null), timeoutMs));
    return await Promise.race([run, timeout]);
  } catch {
    return null;
  }
}
