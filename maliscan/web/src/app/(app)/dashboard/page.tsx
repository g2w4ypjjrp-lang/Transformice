"use client";

import { AlertTriangle, Camera, CheckCircle2, Clock, Copy, FileSpreadsheet, FileText, XCircle } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Badge, Button, Card, Empty, PageHeader, Spinner, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { useSession } from "@/lib/session";

interface Dash {
  totals: Record<string, number>;
  daily: { day: string; uploaded: number; approved: number }[];
  monthly: { month: string; uploaded: number; approved: number; exported: number }[];
  taxpayers: { id: string; title: string; documents: number; pending: number; failed: number; ready_to_export: number; last_upload: string | null }[];
  failures: { error_code: string; count: number }[];
  average_confidence: number | null;
}

function Stat({ label, value, icon, tone, href }: { label: string; value: number; icon: React.ReactNode; tone: string; href?: string }) {
  const body = (
    <div className="flex items-center gap-4 rounded-xl border border-slate-200 bg-white p-4 shadow-card transition hover:border-slate-300">
      <div className={`rounded-lg p-2.5 ${tone}`}>{icon}</div>
      <div><p className="text-2xl font-semibold tabular-nums">{value}</p><p className="text-xs text-ink-500">{label}</p></div>
    </div>
  );
  return href ? <Link href={href}>{body}</Link> : body;
}

function Bars({ data }: { data: Dash["daily"] }) {
  const max = Math.max(1, ...data.map((d) => Math.max(d.uploaded, d.approved)));
  return (
    <div>
      <div className="flex h-40 items-end gap-1">
        {data.map((d) => (
          <div key={d.day} className="group relative flex h-full flex-1 items-end gap-px" title={`${new Date(d.day).toLocaleDateString("tr-TR")}: ${d.uploaded} yüklendi, ${d.approved} onaylandı`}>
            <div className="w-1/2 rounded-t bg-brand-500/80" style={{ height: `${(d.uploaded / max) * 100}%`, minHeight: d.uploaded ? 2 : 0 }} />
            <div className="w-1/2 rounded-t bg-accent-500/80" style={{ height: `${(d.approved / max) * 100}%`, minHeight: d.approved ? 2 : 0 }} />
          </div>
        ))}
      </div>
      <div className="mt-2 flex justify-between text-[11px] text-ink-400">
        <span>{data[0] && new Date(data[0].day).toLocaleDateString("tr-TR")}</span>
        <span className="flex gap-3"><span className="flex items-center gap-1"><i className="h-2 w-2 rounded-sm bg-brand-500" />Yüklenen</span><span className="flex items-center gap-1"><i className="h-2 w-2 rounded-sm bg-accent-500" />Onaylanan</span></span>
        <span>Bugün</span>
      </div>
    </div>
  );
}

const ERRORS: Record<string, string> = {
  ai_refused: "Yapay zekâ belgeyi işlemeyi reddetti", ai_unavailable: "Yapay zekâ hizmetine ulaşılamadı",
  ai_not_configured: "Yapay zekâ anahtarı tanımlı değil", invalid_ubl: "Geçersiz e-Fatura XML", ai_truncated: "Yanıt kesildi",
  ai_rate_limited: "Hız sınırı", document_missing: "Belge silinmiş",
};

export default function DashboardPage() {
  const { firm } = useSession();
  const [data, setData] = useState<Dash | null>(null);
  useEffect(() => {
    if (firm) api<Dash>(`/firms/${firm.firm_id}/dashboard`).then(setData);
  }, [firm]);
  if (!firm || !data) return <Spinner />;
  const t = data.totals;
  const monthTotals = data.monthly.at(-1);

  return (
    <>
      <PageHeader title="Genel bakış" subtitle={firm.firm_name}
        actions={<Link href="/scan"><Button><Camera className="h-4 w-4" />Belge tara</Button></Link>} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Toplam belge" value={t.total} icon={<FileText className="h-5 w-5 text-brand-700" />} tone="bg-brand-50" href="/documents" />
        <Stat label="İnceleme bekleyen" value={t.pending} icon={<Clock className="h-5 w-5 text-amber-700" />} tone="bg-amber-50" href="/documents?status=needs_review" />
        <Stat label="Onaylanan" value={t.approved} icon={<CheckCircle2 className="h-5 w-5 text-accent-700" />} tone="bg-accent-50" href="/documents?status=approved" />
        <Stat label="Luca'ya aktarılan" value={t.exported} icon={<FileSpreadsheet className="h-5 w-5 text-brand-700" />} tone="bg-brand-50" href="/exports" />
        <Stat label="İşleme hatası" value={t.failed} icon={<XCircle className="h-5 w-5 text-red-700" />} tone="bg-red-50" href="/documents?status=failed" />
        <Stat label="Olası mükerrer" value={t.duplicates_flagged} icon={<Copy className="h-5 w-5 text-ink-700" />} tone="bg-slate-100" href="/documents?duplicates=1" />
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-3">
        <Card title="Son 30 gün" className="xl:col-span-2"><Bars data={data.daily} /></Card>
        <Card title="Bu ay">
          <dl className="space-y-3 text-sm">
            <div className="flex justify-between"><dt className="text-ink-500">Yüklenen</dt><dd className="font-semibold tabular-nums">{monthTotals?.uploaded ?? 0}</dd></div>
            <div className="flex justify-between"><dt className="text-ink-500">Onaylanan</dt><dd className="font-semibold tabular-nums">{monthTotals?.approved ?? 0}</dd></div>
            <div className="flex justify-between"><dt className="text-ink-500">Aktarılan</dt><dd className="font-semibold tabular-nums">{monthTotals?.exported ?? 0}</dd></div>
            <div className="flex justify-between"><dt className="text-ink-500">Düşük güvenli (inceleme)</dt><dd className="font-semibold tabular-nums">{t.low_confidence}</dd></div>
            <div className="flex justify-between"><dt className="text-ink-500">Ortalama okuma güveni</dt><dd className="font-semibold tabular-nums">{data.average_confidence !== null ? `%${Math.round(data.average_confidence * 100)}` : "—"}</dd></div>
          </dl>
          {data.failures.length > 0 && (
            <div className="mt-5 border-t border-slate-100 pt-4">
              <p className="mb-2 flex items-center gap-1 text-xs font-medium text-ink-700"><AlertTriangle className="h-3.5 w-3.5 text-red-500" />Hata nedenleri</p>
              <ul className="space-y-1 text-xs">{data.failures.map((f) => <li key={f.error_code} className="flex justify-between"><span>{ERRORS[f.error_code] ?? f.error_code}</span><span className="tabular-nums">{f.count}</span></li>)}</ul>
            </div>
          )}
        </Card>
      </div>

      <Card title="Mükellef durumu" className="mt-6" actions={<Link href="/taxpayers" className="text-sm text-brand-600 hover:underline">Tümü</Link>}>
        {data.taxpayers.length === 0 ? <Empty title="Henüz mükellef yok" action={<Link href="/taxpayers?new=1"><Button size="sm">Mükellef ekle</Button></Link>} /> : (
          <Table head={<><Th>Mükellef</Th><Th className="text-right">Belge</Th><Th className="text-right">İnceleme</Th><Th className="text-right">Aktarıma hazır</Th><Th className="text-right">Hata</Th><Th>Son yükleme</Th></>}>
            {data.taxpayers.map((tp) => (
              <tr key={tp.id} className="hover:bg-slate-50">
                <Td><Link href={`/taxpayers/view?id=${tp.id}`} className="font-medium text-ink-900 hover:text-brand-700">{tp.title}</Link></Td>
                <Td className="text-right tabular-nums">{tp.documents}</Td>
                <Td className="text-right">{tp.pending ? <Link href={`/documents?taxpayer=${tp.id}&status=needs_review`}><Badge tone="amber">{tp.pending}</Badge></Link> : 0}</Td>
                <Td className="text-right">{tp.ready_to_export ? <Badge tone="green">{tp.ready_to_export}</Badge> : 0}</Td>
                <Td className="text-right">{tp.failed ? <Badge tone="red">{tp.failed}</Badge> : 0}</Td>
                <Td>{fmtDateTime(tp.last_upload)}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </>
  );
}
