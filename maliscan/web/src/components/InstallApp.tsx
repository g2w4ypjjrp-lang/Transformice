"use client";

import { CheckCircle2, Download, MonitorSmartphone, PlusSquare, Share, Smartphone } from "lucide-react";
import { useEffect, useState } from "react";
import { Button, cn } from "./ui";

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

type Platform = "ios" | "android" | "desktop";

function detect(): { platform: Platform; standalone: boolean; iosNonSafari: boolean } {
  if (typeof window === "undefined") return { platform: "desktop", standalone: false, iosNonSafari: false };
  const ua = navigator.userAgent;
  const ios = /iPhone|iPad|iPod/i.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const android = /Android/i.test(ua);
  const standalone = window.matchMedia("(display-mode: standalone)").matches || (navigator as { standalone?: boolean }).standalone === true;
  const iosNonSafari = ios && /CriOS|FxiOS|EdgiOS|OPiOS/i.test(ua);
  return { platform: ios ? "ios" : android ? "android" : "desktop", standalone, iosNonSafari };
}

/** "Download the app": PWA installation for iPhone (Safari → Add to Home Screen),
 *  Android/desktop Chrome/Edge (native install prompt). Free — no app store needed. */
export function InstallApp({ compact = false }: { compact?: boolean }) {
  const [env, setEnv] = useState<ReturnType<typeof detect> | null>(null);
  const [prompt, setPrompt] = useState<BeforeInstallPromptEvent | null>(null);
  const [installed, setInstalled] = useState(false);
  const [tab, setTab] = useState<Platform>("ios");

  useEffect(() => {
    const e = detect();
    setEnv(e);
    setTab(e.platform);
    const onPrompt = (ev: Event) => { ev.preventDefault(); setPrompt(ev as BeforeInstallPromptEvent); };
    const onInstalled = () => setInstalled(true);
    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => { window.removeEventListener("beforeinstallprompt", onPrompt); window.removeEventListener("appinstalled", onInstalled); };
  }, []);

  async function install() {
    if (!prompt) return;
    await prompt.prompt();
    const { outcome } = await prompt.userChoice;
    if (outcome === "accepted") setInstalled(true);
    setPrompt(null);
  }

  if (env?.standalone || installed) {
    return <div className="flex items-center gap-2 rounded-xl border border-accent-100 bg-accent-50 p-4 text-sm text-accent-700"><CheckCircle2 className="h-5 w-5" />MaliScan bu cihazda uygulama olarak kurulu.</div>;
  }

  const tabs: [Platform, string, typeof Smartphone][] = [["ios", "iPhone / iPad", Smartphone], ["android", "Android", Smartphone], ["desktop", "Bilgisayar", MonitorSmartphone]];

  return (
    <div className={cn("rounded-2xl border border-slate-200 bg-white shadow-card", compact ? "p-4" : "p-6")}>
      <div className="mb-4 flex gap-1 rounded-lg bg-slate-100 p-1 text-sm">
        {tabs.map(([k, label, Icon]) => (
          <button key={k} onClick={() => setTab(k)} className={cn("flex flex-1 items-center justify-center gap-1.5 rounded-md px-2 py-1.5 font-medium", tab === k ? "bg-white shadow-sm" : "text-ink-500")}>
            <Icon className="h-4 w-4" />{label}
          </button>))}
      </div>

      {tab === "ios" && (
        <div className="space-y-3 text-sm text-ink-700">
          {env?.iosNonSafari && <p className="rounded-lg bg-amber-50 p-2 text-amber-900">Bu sayfayı <b>Safari</b> ile açın — iPhone&apos;da uygulama yalnızca Safari&apos;den eklenebilir.</p>}
          <ol className="space-y-3">
            <li className="flex gap-3"><span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-brand-600 text-xs font-semibold text-white">1</span><span>Safari&apos;de alttaki <Share className="inline h-4 w-4 align-text-bottom text-brand-600" /> <b>Paylaş</b> düğmesine dokunun.</span></li>
            <li className="flex gap-3"><span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-brand-600 text-xs font-semibold text-white">2</span><span>Listeden <PlusSquare className="inline h-4 w-4 align-text-bottom text-brand-600" /> <b>Ana Ekrana Ekle</b>&apos;yi seçin.</span></li>
            <li className="flex gap-3"><span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-brand-600 text-xs font-semibold text-white">3</span><span><b>Ekle</b>&apos;ye dokunun. MaliScan simgesi ana ekranınıza gelir ve uygulama gibi tam ekran açılır.</span></li>
          </ol>
          <p className="text-xs text-ink-500">App Store gerekmez, ücretsizdir. Kamera izni istendiğinde &quot;İzin ver&quot;i seçin.</p>
        </div>
      )}

      {tab === "android" && (
        <div className="space-y-3 text-sm text-ink-700">
          {prompt ? <Button onClick={install} className="w-full"><Download className="h-4 w-4" />Uygulamayı yükle</Button>
            : <ol className="space-y-3">
                <li className="flex gap-3"><span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-brand-600 text-xs font-semibold text-white">1</span><span>Bu sayfayı <b>Chrome</b> ile açın.</span></li>
                <li className="flex gap-3"><span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-brand-600 text-xs font-semibold text-white">2</span><span>Sağ üstteki <b>⋮</b> menüsünden <b>Uygulamayı yükle</b> (veya &quot;Ana ekrana ekle&quot;) seçin.</span></li>
              </ol>}
          <p className="text-xs text-ink-500">Google Play gerekmez, ücretsizdir. Yüklenen uygulama otomatik güncellenir.</p>
        </div>
      )}

      {tab === "desktop" && (
        <div className="space-y-3 text-sm text-ink-700">
          {prompt ? <Button onClick={install} className="w-full"><Download className="h-4 w-4" />Bilgisayara yükle</Button>
            : <p>Chrome veya Edge adres çubuğunun sağındaki <b>yükle</b> simgesine tıklayın. Belge taramak için telefonunuzdan bu adresi açmanız önerilir.</p>}
          <p className="text-xs text-ink-500">Bilgisayarda kamera yerine PDF, e-Fatura XML veya fotoğraf yükleyebilirsiniz.</p>
        </div>
      )}
    </div>
  );
}
