"use client";

import { Camera, CheckCircle2, FileSpreadsheet, FileText, ShieldCheck, Sparkles } from "lucide-react";
import Link from "next/link";
import { InstallApp } from "@/components/InstallApp";
import { Button } from "@/components/ui";
import { useSession } from "@/lib/session";

const FEATURES = [
  { icon: Camera, title: "Canlı kamera ile tarama", text: "Fişi masaya koyun; belge otomatik algılanır, düzeltilir ve yüklenir. Aynı karede birden fazla fiş desteklenir." },
  { icon: FileText, title: "e-Fatura ve e-Arşiv", text: "UBL-TR XML dosyaları doğrudan okunur — yazım hatası yok, yapay zekâ gerekmez." },
  { icon: Sparkles, title: "Akıllı muhasebeleştirme", text: "Mükellefe özel hesap planı, eşleştirme ve kurallarla dengeli yevmiye taslağı; KDV, tevkifat ve KKEG desteği." },
  { icon: CheckCircle2, title: "Onaysız kayıt yok", text: "Her kayıt yetkili muhasebeci onayından geçer; kilitli dönemler ve dengesiz fişler engellenir." },
  { icon: FileSpreadsheet, title: "Luca'ya aktarım", text: "Onaylı fişler Luca Excel Veri Aktarımı dosyasına dönüştürülür (dosya başına en fazla 50 fiş)." },
  { icon: ShieldCheck, title: "Ofise özel ve güvenli", text: "Her ofisin verisi veritabanı seviyesinde ayrılır; dosyalar yalnızca kısa süreli bağlantılarla açılır." },
];

export default function Landing() {
  const { session, loading } = useSession();
  return (
    <div className="min-h-screen bg-canvas">
      <header className="mx-auto flex max-w-6xl items-center justify-between px-4 py-5 sm:px-6">
        <div className="flex items-center gap-2">
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-brand-600 text-white"><FileText className="h-5 w-5" /></div>
          <span className="text-lg font-semibold">MaliScan <span className="text-brand-600">AI</span></span>
        </div>
        <nav className="flex items-center gap-2">
          <a href="#indir" className="hidden px-3 py-2 text-sm font-medium text-ink-700 hover:text-ink-900 sm:inline">Uygulamayı indir</a>
          {!loading && (session
            ? <Link href="/dashboard"><Button>Panele git</Button></Link>
            : <><Link href="/login"><Button variant="ghost">Giriş</Button></Link><Link href="/register"><Button>Ücretsiz başla</Button></Link></>)}
        </nav>
      </header>

      <main>
        <section className="mx-auto grid max-w-6xl items-center gap-10 px-4 pb-16 pt-8 sm:px-6 lg:grid-cols-2 lg:pt-16">
          <div>
            <p className="mb-3 inline-flex rounded-full bg-brand-50 px-3 py-1 text-xs font-medium text-brand-700">Mali müşavirler için</p>
            <h1 className="text-4xl font-semibold leading-tight tracking-tight text-ink-900 sm:text-5xl">Fişleri tarayın, kontrol edin, Luca&apos;ya aktarın.</h1>
            <p className="mt-5 max-w-xl text-lg text-ink-500">MaliScan; fiş ve faturaları telefon kamerasıyla okur, mükellefinize özel kurallarla yevmiye taslağı hazırlar ve onayınızdan sonra Luca aktarım dosyasını üretir.</p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link href={session ? "/scan" : "/register"}><Button className="px-6 py-3 text-base"><Camera className="h-5 w-5" />{session ? "Belge tara" : "Ücretsiz hesap oluştur"}</Button></Link>
              <a href="#indir"><Button variant="secondary" className="px-6 py-3 text-base">Telefona yükle</Button></a>
            </div>
          </div>
          <div className="relative mx-auto w-full max-w-md">
            <img src="/screens/review.png" alt="MaliScan inceleme ekranı" className="rounded-2xl border border-slate-200 shadow-xl" />
            <img src="/screens/mobile-scan.png" alt="MaliScan mobil tarama" className="absolute -bottom-8 -left-6 hidden w-36 rounded-2xl border border-slate-200 shadow-xl sm:block" />
          </div>
        </section>

        <section className="border-y border-slate-200 bg-white py-16">
          <div className="mx-auto grid max-w-6xl gap-6 px-4 sm:grid-cols-2 sm:px-6 lg:grid-cols-3">
            {FEATURES.map(({ icon: Icon, title, text }) => (
              <div key={title} className="rounded-xl border border-slate-100 p-5">
                <Icon className="mb-3 h-6 w-6 text-brand-600" />
                <h3 className="font-semibold text-ink-900">{title}</h3>
                <p className="mt-1 text-sm text-ink-500">{text}</p>
              </div>))}
          </div>
        </section>

        <section id="indir" className="mx-auto grid max-w-6xl gap-10 px-4 py-16 sm:px-6 lg:grid-cols-2">
          <div>
            <h2 className="text-2xl font-semibold tracking-tight">Uygulamayı indirin</h2>
            <p className="mt-3 text-ink-500">MaliScan; iPhone, Android ve bilgisayara mağaza gerekmeden, ücretsiz olarak kurulur. Simgesi ana ekranınıza gelir, tam ekran açılır ve güncellemeler otomatik yüklenir.</p>
            <ul className="mt-5 space-y-2 text-sm text-ink-700">
              <li>• iPhone: Safari → Paylaş → Ana Ekrana Ekle</li>
              <li>• Android: Chrome → Uygulamayı yükle</li>
              <li>• Bilgisayar: Chrome/Edge → adres çubuğundaki yükle simgesi</li>
            </ul>
            <p className="mt-5 text-xs text-ink-400">Not: Uygulama App Store / Google Play üzerinden değil, doğrudan bu siteden kurulur (PWA).</p>
          </div>
          <InstallApp />
        </section>
      </main>

      <footer className="border-t border-slate-200 bg-white py-8 text-center text-xs text-ink-500">
        MaliScan AI · luca.canarslan.net.tr · Luca, TÜRMOB&apos;un tescilli ürünüdür; MaliScan bağımsız bir yazılımdır ve Luca ile resmî bir bağı yoktur.
      </footer>
    </div>
  );
}
