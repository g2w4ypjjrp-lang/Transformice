"use client";

import { AlertTriangle, Camera, CheckCircle2, FileUp, Flashlight, Loader2, RefreshCw, Trash2, Zap, ZapOff } from "lucide-react";
import Link from "next/link";
import { ChangeEvent, useCallback, useEffect, useRef, useState } from "react";
import { upload } from "@/lib/api";
import { Detection, detectDocuments, StabilityTracker } from "@/lib/scanner/detect";
import { enhance, EnhanceMode, sharpness } from "@/lib/scanner/enhance";
import { Img, Quad, targetSize, warp } from "@/lib/scanner/geometry";
import { Alert, Button, cn, Select } from "./ui";

type ItemStatus = "pending" | "uploading" | "done" | "error";
interface QueueItem {
  id: string;
  blob: Blob;
  name: string;
  preview: string;
  status: ItemStatus;
  progress: number;
  error?: string;
  documentId?: string;
  blurry?: boolean;
  source: "camera" | "upload";
}

const DETECT_WIDTH = 320;
const BLUR_THRESHOLD = 60;

function imageDataOf(canvas: HTMLCanvasElement): Img {
  const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
  return ctx.getImageData(0, 0, canvas.width, canvas.height);
}

function toBlob(img: Img, quality = 0.9): Promise<{ blob: Blob; url: string }> {
  const c = document.createElement("canvas");
  c.width = img.width;
  c.height = img.height;
  c.getContext("2d")!.putImageData(new ImageData(new Uint8ClampedArray(img.data), img.width, img.height), 0, 0);
  return new Promise((resolve, reject) => c.toBlob((b) => (b ? resolve({ blob: b, url: URL.createObjectURL(b) }) : reject(new Error("encode"))), "image/jpeg", quality));
}

function scaleQuad(q: Quad, s: number): Quad {
  return q.map((p) => ({ x: p.x * s, y: p.y * s })) as Quad;
}

/** Crop + perspective-correct + enhance every detected document in a full-resolution frame. */
async function processFrame(full: Img, quads: Quad[], mode: EnhanceMode) {
  const crops = quads.length ? quads : [[{ x: 0, y: 0 }, { x: full.width - 1, y: 0 }, { x: full.width - 1, y: full.height - 1 }, { x: 0, y: full.height - 1 }] as Quad];
  const results: { blob: Blob; url: string; blurry: boolean }[] = [];
  for (const q of crops) {
    const { width, height } = targetSize(q, 2200);
    const flat = warp(full, q, width, height);
    const blurry = sharpness(flat, 3) < BLUR_THRESHOLD;
    const out = enhance(flat, mode);
    results.push({ ...(await toBlob(out)), blurry });
  }
  return results;
}

export function Scanner({ firmId, taxpayerId }: { firmId: string; taxpayerId: string | null }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const workRef = useRef<HTMLCanvasElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const trackerRef = useRef<StabilityTracker | null>(null);
  const detectionsRef = useRef<Detection[]>([]);
  const busyRef = useRef(false);

  const [camera, setCamera] = useState<"idle" | "starting" | "on" | "unsupported" | "denied">("idle");
  const [auto, setAuto] = useState(true);
  const [mode, setMode] = useState<EnhanceMode>("gray");
  const [torch, setTorch] = useState<boolean | null>(null);
  const [progress, setProgress] = useState(0);
  const [found, setFound] = useState(0);
  const [flash, setFlash] = useState(false);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const autoRef = useRef(auto);
  const modeRef = useRef(mode);
  autoRef.current = auto;
  modeRef.current = mode;

  const addToQueue = useCallback((items: Omit<QueueItem, "id" | "status" | "progress">[]) => {
    setQueue((q) => [...q, ...items.map((it) => ({ ...it, id: crypto.randomUUID(), status: "pending" as const, progress: 0 }))]);
  }, []);

  // ---------------------------------------------------------------- camera lifecycle
  const stop = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }, []);

  const start = useCallback(async () => {
    if (!navigator.mediaDevices?.getUserMedia) return setCamera("unsupported");
    setCamera("starting");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } },
      });
      streamRef.current = stream;
      const v = videoRef.current!;
      v.srcObject = stream;
      await v.play();
      const track = stream.getVideoTracks()[0];
      const caps = (track.getCapabilities?.() ?? {}) as { torch?: boolean };
      setTorch(caps.torch ? false : null);
      trackerRef.current = new StabilityTracker(Math.hypot(DETECT_WIDTH, DETECT_WIDTH * 0.75), 10);
      setCamera("on");
    } catch (e) {
      setCamera((e as DOMException)?.name === "NotAllowedError" ? "denied" : "unsupported");
    }
  }, []);

  useEffect(() => () => stop(), [stop]);

  const toggleTorch = async () => {
    const track = streamRef.current?.getVideoTracks()[0];
    if (!track || torch === null) return;
    await track.applyConstraints({ advanced: [{ torch: !torch } as MediaTrackConstraintSet] }).catch(() => undefined);
    setTorch(!torch);
  };

  // ---------------------------------------------------------------- capture
  const capture = useCallback(async (source: "auto" | "manual") => {
    const v = videoRef.current;
    if (!v || busyRef.current || !v.videoWidth) return;
    busyRef.current = true;
    try {
      const full = document.createElement("canvas");
      full.width = v.videoWidth;
      full.height = v.videoHeight;
      full.getContext("2d")!.drawImage(v, 0, 0);
      const s = v.videoWidth / DETECT_WIDTH;
      const quads = detectionsRef.current.map((d) => scaleQuad(d.quad, s));
      if (source === "auto" && !quads.length) return;
      setFlash(true);
      setTimeout(() => setFlash(false), 150);
      navigator.vibrate?.(40);
      const crops = await processFrame(imageDataOf(full), quads, modeRef.current);
      addToQueue(crops.map((c, i) => ({ blob: c.blob, preview: c.url, blurry: c.blurry, source: "camera" as const,
        name: `tarama-${new Date().toISOString().replace(/[:.]/g, "-")}-${i + 1}.jpg` })));
    } finally {
      busyRef.current = false;
    }
  }, [addToQueue]);

  // ---------------------------------------------------------------- detection loop (~12 fps)
  useEffect(() => {
    if (camera !== "on") return;
    let raf = 0, last = 0;
    const loop = (t: number) => {
      raf = requestAnimationFrame(loop);
      if (t - last < 80) return;
      last = t;
      const v = videoRef.current, overlay = overlayRef.current;
      if (!v || !overlay || !v.videoWidth) return;
      const work = (workRef.current ??= document.createElement("canvas"));
      work.width = DETECT_WIDTH;
      work.height = Math.round((DETECT_WIDTH * v.videoHeight) / v.videoWidth);
      work.getContext("2d", { willReadFrequently: true })!.drawImage(v, 0, 0, work.width, work.height);
      const dets = detectDocuments(imageDataOf(work), { workWidth: 200 });
      detectionsRef.current = dets;
      setFound(dets.length);

      // draw overlay in displayed-video coordinates
      const rect = v.getBoundingClientRect();
      overlay.width = rect.width;
      overlay.height = rect.height;
      const ctx = overlay.getContext("2d")!;
      ctx.clearRect(0, 0, overlay.width, overlay.height);
      // The <video> uses object-fit: cover — replicate its scale and centring offset.
      const cover = Math.max(rect.width / v.videoWidth, rect.height / v.videoHeight);
      const k = (v.videoWidth / work.width) * cover;
      const ox = (rect.width - v.videoWidth * cover) / 2, oy = (rect.height - v.videoHeight * cover) / 2;
      for (const d of dets) {
        ctx.beginPath();
        d.quad.forEach((p, i) => (i ? ctx.lineTo(ox + p.x * k, oy + p.y * k) : ctx.moveTo(ox + p.x * k, oy + p.y * k)));
        ctx.closePath();
        ctx.fillStyle = "rgba(16,185,129,0.15)";
        ctx.strokeStyle = "#10b981";
        ctx.lineWidth = 3;
        ctx.fill();
        ctx.stroke();
      }
      const tracker = trackerRef.current!;
      const fire = tracker.update(dets[0]?.quad ?? null);
      setProgress(tracker.progress);
      if (fire && autoRef.current) void capture("auto");
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [camera, capture]);

  // ---------------------------------------------------------------- file fallback
  const onFiles = async (e: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    for (const f of files) {
      if (!f.type.startsWith("image/") || f.type === "image/heic") {
        addToQueue([{ blob: f, name: f.name, preview: "", source: "upload" }]);
        continue;
      }
      const bmp = await createImageBitmap(f).catch(() => null);
      if (!bmp) {
        addToQueue([{ blob: f, name: f.name, preview: "", source: "upload" }]);
        continue;
      }
      const scale = Math.min(1, 2400 / Math.max(bmp.width, bmp.height));
      const c = document.createElement("canvas");
      c.width = Math.round(bmp.width * scale);
      c.height = Math.round(bmp.height * scale);
      c.getContext("2d")!.drawImage(bmp, 0, 0, c.width, c.height);
      const full = imageDataOf(c);
      const dets = detectDocuments(full, { workWidth: 240 });
      const crops = await processFrame(full, dets.map((d) => d.quad), modeRef.current);
      addToQueue(crops.map((cr, i) => ({ blob: cr.blob, preview: cr.url, blurry: cr.blurry, source: "upload" as const,
        name: f.name.replace(/\.[^.]+$/, "") + (crops.length > 1 ? `-${i + 1}` : "") + ".jpg" })));
    }
  };

  // ---------------------------------------------------------------- upload queue (sequential)
  useEffect(() => {
    if (!taxpayerId) return;
    const next = queue.find((q) => q.status === "pending");
    if (!next || queue.some((q) => q.status === "uploading")) return;
    setQueue((q) => q.map((it) => (it.id === next.id ? { ...it, status: "uploading" } : it)));
    const form = new FormData();
    form.append("file", next.blob, next.name);
    form.append("source", next.source);
    form.append("capture_metadata", JSON.stringify({ scanner: "maliscan-web", mode, blurry: !!next.blurry }));
    upload<{ id: string }>(`/firms/${firmId}/taxpayers/${taxpayerId}/documents`, form, (p) =>
      setQueue((q) => q.map((it) => (it.id === next.id ? { ...it, progress: p } : it))))
      .then((doc) => setQueue((q) => q.map((it) => (it.id === next.id ? { ...it, status: "done", documentId: doc.id } : it))))
      .catch((err) => setQueue((q) => q.map((it) => (it.id === next.id ? { ...it, status: "error", error: err.message } : it))));
  }, [queue, taxpayerId, firmId, mode]);

  const retry = (id: string) => setQueue((q) => q.map((it) => (it.id === id ? { ...it, status: "pending", error: undefined, progress: 0 } : it)));
  const remove = (id: string) => setQueue((q) => q.filter((it) => it.id !== id || it.status === "uploading"));

  const done = queue.filter((q) => q.status === "done").length;

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
      <div className="space-y-3">
        <div className="relative overflow-hidden rounded-2xl bg-ink-900" style={{ aspectRatio: "3 / 4", maxHeight: "72vh" }}>
          <video ref={videoRef} playsInline muted className={cn("h-full w-full object-cover", camera !== "on" && "hidden")} />
          <canvas ref={overlayRef} className="pointer-events-none absolute inset-0 h-full w-full" />
          {flash && <div className="absolute inset-0 bg-white/70" />}
          {camera !== "on" && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 p-6 text-center text-white">
              {camera === "starting" ? <Loader2 className="h-8 w-8 animate-spin" /> : <Camera className="h-10 w-10 opacity-80" />}
              {camera === "idle" && <><p className="text-sm opacity-80">Fişi koyu bir zemine koyun ve kamerayı başlatın. Belge sabit durduğunda otomatik çekilir.</p>
                <Button onClick={start} disabled={!taxpayerId}>Kamerayı başlat</Button>
                {!taxpayerId && <p className="text-xs text-amber-300">Önce mükellef seçin.</p>}</>}
              {camera === "denied" && <p className="text-sm">Kamera izni reddedildi. Tarayıcı ayarlarından izin verin veya aşağıdan dosya yükleyin.</p>}
              {camera === "unsupported" && <p className="text-sm">Bu tarayıcı canlı kamerayı desteklemiyor. Aşağıdan fotoğraf çekerek veya dosya seçerek yükleyin.</p>}
            </div>
          )}
          {camera === "on" && (
            <>
              <div className="absolute left-3 top-3 rounded-full bg-black/50 px-3 py-1 text-xs text-white">
                {found ? `${found} belge algılandı` : "Belge aranıyor…"}
              </div>
              {auto && progress > 0 && (
                <div className="absolute inset-x-6 top-12 h-1 overflow-hidden rounded-full bg-white/20">
                  <div className="h-full bg-accent-500 transition-all" style={{ width: `${progress * 100}%` }} />
                </div>
              )}
              <div className="absolute inset-x-0 bottom-0 flex items-center justify-between gap-3 bg-gradient-to-t from-black/60 to-transparent p-4">
                <button onClick={() => setAuto(!auto)} className="rounded-full bg-white/15 p-3 text-white backdrop-blur" aria-label="Otomatik çekim">
                  {auto ? <Zap className="h-5 w-5" /> : <ZapOff className="h-5 w-5" />}
                </button>
                <button onClick={() => capture("manual")} aria-label="Çek"
                  className="h-16 w-16 rounded-full border-4 border-white bg-white/30 backdrop-blur transition active:scale-95" />
                {torch !== null ? (
                  <button onClick={toggleTorch} className={cn("rounded-full p-3 backdrop-blur", torch ? "bg-amber-400 text-ink-900" : "bg-white/15 text-white")} aria-label="Fener">
                    <Flashlight className="h-5 w-5" />
                  </button>
                ) : <span className="w-11" />}
              </div>
            </>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-medium shadow-sm hover:bg-slate-50">
            <FileUp className="h-4 w-4" />Dosya / fotoğraf yükle
            <input type="file" className="hidden" multiple accept="image/jpeg,image/png,image/webp,application/pdf,.xml,text/xml,application/xml" capture="environment"
              onChange={onFiles} disabled={!taxpayerId} />
          </label>
          <div className="flex items-center gap-2 text-sm">
            <span className="text-ink-500">Görüntü:</span>
            <Select value={mode} onChange={(e) => setMode(e.target.value as EnhanceMode)} className="w-auto py-1.5">
              <option value="gray">Gri tonlama (önerilen)</option>
              <option value="color">Renkli</option>
              <option value="bw">Siyah-beyaz (soluk fişler)</option>
            </Select>
          </div>
          <span className="text-sm text-ink-500">{auto ? "Otomatik çekim açık" : "Otomatik çekim kapalı"}</span>
        </div>
      </div>

      <aside className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">Yükleme kuyruğu</h2>
          <span className="text-xs text-ink-500">{done}/{queue.length} yüklendi</span>
        </div>
        {queue.length === 0 && <p className="rounded-xl border border-dashed border-slate-300 bg-white p-6 text-center text-sm text-ink-500">Çekilen belgeler burada görünür ve otomatik yüklenir. Aynı karede birden fazla fiş varsa her biri ayrı kırpılır.</p>}
        <ul className="space-y-2">
          {queue.map((it) => (
            <li key={it.id} className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white p-2.5 shadow-card">
              {it.preview ? <img src={it.preview} alt="" className="h-14 w-11 rounded-md border border-slate-100 object-cover" />
                : <div className="grid h-14 w-11 place-items-center rounded-md bg-slate-100 text-[10px] text-ink-500">{it.name.split(".").pop()?.toUpperCase()}</div>}
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs font-medium">{it.name}</p>
                {it.status === "uploading" && <div className="mt-1 h-1 overflow-hidden rounded-full bg-slate-100"><div className="h-full bg-brand-500" style={{ width: `${it.progress * 100}%` }} /></div>}
                {it.status === "pending" && <p className="text-xs text-ink-500">{taxpayerId ? "Sırada" : "Mükellef seçin"}</p>}
                {it.status === "done" && <Link href={`/documents/${it.documentId}`} className="inline-flex items-center gap-1 text-xs text-accent-700 hover:underline"><CheckCircle2 className="h-3 w-3" />Yüklendi — işleniyor</Link>}
                {it.status === "error" && <p className="text-xs text-red-600">{it.error}</p>}
                {it.blurry && it.status !== "error" && <p className="inline-flex items-center gap-1 text-xs text-amber-700"><AlertTriangle className="h-3 w-3" />Görüntü bulanık olabilir</p>}
              </div>
              {it.status === "error" && <button onClick={() => retry(it.id)} className="rounded-md p-1.5 text-ink-500 hover:bg-slate-100" aria-label="Tekrar dene"><RefreshCw className="h-4 w-4" /></button>}
              {(it.status === "pending" || it.status === "error") && <button onClick={() => remove(it.id)} className="rounded-md p-1.5 text-ink-500 hover:bg-slate-100" aria-label="Kaldır"><Trash2 className="h-4 w-4" /></button>}
            </li>
          ))}
        </ul>
        {queue.some((q) => q.blurry) && <Alert tone="amber">Bulanık görünen belgeleri tekrar çekmeniz okuma doğruluğunu artırır.</Alert>}
      </aside>
    </div>
  );
}
