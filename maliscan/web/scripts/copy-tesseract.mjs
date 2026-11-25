// Self-host the on-device OCR engine (tesseract.js worker + LSTM wasm cores + Turkish model)
// under /tesseract so the scanner can read receipts on the phone — no third-party CDN.
import fs from "node:fs";
import path from "node:path";

const out = "public/tesseract";
fs.rmSync(out, { recursive: true, force: true });
fs.mkdirSync(`${out}/core`, { recursive: true });
fs.mkdirSync(`${out}/lang`, { recursive: true });
const copy = (from, to) => fs.copyFileSync(path.join("node_modules", from), path.join(out, to));
copy("tesseract.js/dist/worker.min.js", "worker.min.js");
for (const f of ["tesseract-core-lstm.wasm.js", "tesseract-core-simd-lstm.wasm.js"]) copy(`tesseract.js-core/${f}`, `core/${f}`);
copy("@tesseract.js-data/tur/4.0.0_best_int/tur.traineddata.gz", "lang/tur.traineddata.gz");
console.log("public/tesseract written");
