"use client";

import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Alert, Badge, Button, Card, Input, PageHeader, Select, Spinner, StatusBadge, Table, Td, Th, cn } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { fmtDate, fmtDateTime } from "@/lib/format";
import { useSession } from "@/lib/session";

type Tab = "overview" | "firms" | "users" | "plans" | "jobs" | "config";
interface Plan { id: string; code: string; name: string; price_monthly: string; currency: string; max_users: number | null; max_taxpayers: number | null; max_documents_per_month: number | null; trial_days: number; is_active: boolean; features: Record<string, unknown> }

export default function AdminPage() {
  const { me } = useSession();
  const [tab, setTab] = useState<Tab>("overview");
  const [data, setData] = useState<Record<string, any>>({});
  const [q, setQ] = useState("");
  const [msg, setMsg] = useState<{ tone: "green" | "red"; text: string } | null>(null);
  const [planEdit, setPlanEdit] = useState<Plan | null>(null);

  const load = useCallback(async () => {
    const [overview, errors] = await Promise.all([api("/admin/overview"), api("/admin/errors")]);
    const map: Record<Tab, string> = { overview: "", firms: `/admin/firms${q ? `?q=${encodeURIComponent(q)}` : ""}`, users: `/admin/users${q ? `?q=${encodeURIComponent(q)}` : ""}`, plans: "/admin/plans", jobs: "/admin/jobs?limit=200", config: "/admin/config" };
    const extra = map[tab] ? await api(map[tab]) : null;
    setData({ overview, errors, [tab]: extra, plans: tab === "firms" || tab === "plans" ? await api("/admin/plans") : data.plans });
  }, [tab, q]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (me?.is_super_admin) load().catch(() => setMsg({ tone: "red", text: "Yüklenemedi" })); }, [load, me]);

  async function act(fn: () => Promise<unknown>, ok: string) {
    setMsg(null);
    try { await fn(); setMsg({ tone: "green", text: ok }); await load(); }
    catch (e) { setMsg({ tone: "red", text: e instanceof ApiError ? e.message : "İşlem başarısız" }); }
  }

  if (!me) return <Spinner />;
  if (!me.is_super_admin) return <Alert>Bu sayfa yalnızca platform yöneticileri içindir.</Alert>;
  const o = data.overview;

  return (
    <>
      <PageHeader title="Süper yönetici" subtitle="Platform genelinde ofisler, kullanıcılar, paketler ve işleme kuyruğu" actions={<Button variant="secondary" onClick={load}><RefreshCw className="h-4 w-4" />Yenile</Button>} />
      {msg && <div className="mb-4"><Alert tone={msg.tone}>{msg.text}</Alert></div>}
      <div className="mb-4 flex gap-1 overflow-x-auto rounded-lg bg-slate-100 p-1 text-sm">
        {(["overview", "firms", "users", "plans", "jobs", "config"] as Tab[]).map((t) => (
          <button key={t} onClick={() => setTab(t)} className={cn("whitespace-nowrap rounded-md px-3 py-1.5 font-medium", tab === t ? "bg-white shadow-sm" : "text-ink-500")}>
            {{ overview: "Özet", firms: "Ofisler", users: "Kullanıcılar", plans: "Paketler", jobs: "İşler ve hatalar", config: "Yapılandırma" }[t]}</button>))}
      </div>
      {(tab === "firms" || tab === "users") && <form className="mb-4 max-w-sm" onSubmit={(e) => { e.preventDefault(); load(); }}><Input placeholder="Ara…" value={q} onChange={(e) => setQ(e.target.value)} /></form>}

      {tab === "overview" && o && (
        <div className="grid gap-6 lg:grid-cols-2">
          <Card title="Kullanım">
            <dl className="grid grid-cols-2 gap-4 text-sm">
              {[["Ofis", o.firms], ["Aktif ofis", o.active_firms], ["Kullanıcı", o.users], ["Mükellef", o.taxpayers], ["Bugün belge", o.documents_today], ["Bu ay belge", o.documents_month], ["Toplam belge", o.documents_total], ["Luca aktarımı", o.exports_total]].map(([l, v]) => (
                <div key={l as string} className="rounded-lg bg-slate-50 p-3"><dt className="text-xs text-ink-500">{l}</dt><dd className="text-xl font-semibold tabular-nums">{v as number}</dd></div>))}
            </dl>
          </Card>
          <Card title="İşleme kuyruğu">
            <div className="flex flex-wrap gap-2">{Object.entries(o.jobs as Record<string, number>).map(([s, n]) => <span key={s} className="flex items-center gap-1"><StatusBadge status={s} /><b className="tabular-nums">{n}</b></span>)}</div>
            <p className="mt-3 text-sm">Son 24 saatte başarısız iş: <b>{o.failed_jobs_24h}</b> · yeniden deneniyor: <b>{data.errors?.retrying}</b></p>
            <ul className="mt-3 space-y-1 text-sm">{(data.errors?.by_error_code ?? []).map((e: any) => <li key={e.error_code} className="flex justify-between"><span className="font-mono text-xs">{e.error_code}</span><span>{e.count} · {fmtDateTime(e.last_seen)}</span></li>)}</ul>
          </Card>
        </div>
      )}

      {tab === "firms" && data.firms && (
        <Card><Table head={<><Th>Ofis</Th><Th>Paket</Th><Th className="text-right">Üye</Th><Th className="text-right">Mükellef</Th><Th className="text-right">Bu ay belge</Th><Th>Durum</Th><Th /></>}>
          {data.firms.map((f: any) => (
            <tr key={f.id}>
              <Td><div className="font-medium text-ink-900">{f.name}</div><div className="text-xs text-ink-500">{fmtDate(f.created_at)}</div></Td>
              <Td><Select className="w-40 py-1" value={f.plan_code ?? ""} onChange={(e) => act(() => api(`/admin/firms/${f.id}/subscription`, { method: "PUT", json: { plan_code: e.target.value } }), "Paket atandı.")}>
                <option value="">—</option>{(data.plans ?? []).map((p: Plan) => <option key={p.code} value={p.code}>{p.name}</option>)}</Select>
                <div className="text-xs text-ink-500">{f.subscription_status}{f.current_period_end ? ` · ${fmtDate(f.current_period_end)}` : ""}</div></Td>
              <Td className="text-right tabular-nums">{f.members}</Td><Td className="text-right tabular-nums">{f.taxpayers}</Td><Td className="text-right tabular-nums">{f.documents_month}</Td>
              <Td><Badge tone={f.status === "active" ? "green" : "red"}>{f.status}</Badge></Td>
              <Td><Button size="sm" variant={f.status === "active" ? "danger" : "secondary"} onClick={() => act(() => api(`/admin/firms/${f.id}`, { method: "PATCH", json: { status: f.status === "active" ? "suspended" : "active" } }), "Durum güncellendi.")}>{f.status === "active" ? "Askıya al" : "Etkinleştir"}</Button></Td>
            </tr>))}
        </Table></Card>
      )}

      {tab === "users" && data.users && (
        <Card><Table head={<><Th>Kullanıcı</Th><Th>Ofisler</Th><Th>Son görülme</Th><Th>Durum</Th><Th /></>}>
          {data.users.map((u: any) => (
            <tr key={u.id}>
              <Td><div className="font-medium text-ink-900">{u.full_name || "—"}{u.is_super_admin && <span className="ml-1"><Badge tone="blue">Süper yönetici</Badge></span>}</div><div className="text-xs text-ink-500">{u.email}</div></Td>
              <Td className="text-xs">{(u.memberships ?? []).map((m: any) => `${m.firm} (${m.role})`).join(", ") || "—"}</Td>
              <Td>{fmtDateTime(u.last_seen_at)}</Td>
              <Td>{u.is_active ? <Badge tone="green">Aktif</Badge> : <Badge tone="red">Engelli</Badge>}</Td>
              <Td>{u.id !== me.id && <Button size="sm" variant="ghost" onClick={() => act(() => api(`/admin/users/${u.id}`, { method: "PATCH", json: { is_active: !u.is_active } }), "Güncellendi.")}>{u.is_active ? "Engelle" : "Etkinleştir"}</Button>}</Td>
            </tr>))}
        </Table></Card>
      )}

      {tab === "plans" && data.plans && (
        <Card actions={<Button size="sm" onClick={() => setPlanEdit({ id: "", code: "", name: "", price_monthly: "0", currency: "TRY", max_users: null, max_taxpayers: null, max_documents_per_month: null, trial_days: 0, is_active: true, features: {} })}>Yeni paket</Button>} title="Abonelik paketleri">
          <Table head={<><Th>Kod</Th><Th>Ad</Th><Th className="text-right">Aylık</Th><Th className="text-right">Kullanıcı</Th><Th className="text-right">Mükellef</Th><Th className="text-right">Belge/ay</Th><Th /></>}>
            {data.plans.map((p: Plan) => (
              <tr key={p.code}><Td className="font-mono">{p.code}</Td><Td>{p.name} {!p.is_active && <Badge>pasif</Badge>}</Td><Td className="text-right tabular-nums">{p.price_monthly} {p.currency}</Td>
                <Td className="text-right">{p.max_users ?? "∞"}</Td><Td className="text-right">{p.max_taxpayers ?? "∞"}</Td><Td className="text-right">{p.max_documents_per_month ?? "∞"}</Td>
                <Td><Button size="sm" variant="ghost" onClick={() => setPlanEdit(p)}>Düzenle</Button></Td></tr>))}
          </Table>
          {planEdit && (
            <form className="mt-5 grid gap-3 rounded-lg border border-slate-200 p-4 md:grid-cols-4" onSubmit={(e) => { e.preventDefault(); act(() => api(`/admin/plans/${planEdit.code}`, { method: "PUT", json: {
              code: planEdit.code, name: planEdit.name, price_monthly: planEdit.price_monthly, currency: planEdit.currency, max_users: planEdit.max_users || null,
              max_taxpayers: planEdit.max_taxpayers || null, max_documents_per_month: planEdit.max_documents_per_month || null, trial_days: planEdit.trial_days, features: planEdit.features, is_active: planEdit.is_active } }), "Paket kaydedildi.").then(() => setPlanEdit(null)); }}>
              <Input required placeholder="kod" value={planEdit.code} disabled={!!planEdit.id} onChange={(e) => setPlanEdit({ ...planEdit, code: e.target.value })} />
              <Input required placeholder="Ad" value={planEdit.name} onChange={(e) => setPlanEdit({ ...planEdit, name: e.target.value })} />
              <Input required placeholder="Aylık ücret" value={planEdit.price_monthly} onChange={(e) => setPlanEdit({ ...planEdit, price_monthly: e.target.value })} />
              <Input type="number" placeholder="Deneme günü" value={planEdit.trial_days} onChange={(e) => setPlanEdit({ ...planEdit, trial_days: Number(e.target.value) })} />
              <Input type="number" placeholder="Maks. kullanıcı (boş=∞)" value={planEdit.max_users ?? ""} onChange={(e) => setPlanEdit({ ...planEdit, max_users: e.target.value ? Number(e.target.value) : null })} />
              <Input type="number" placeholder="Maks. mükellef" value={planEdit.max_taxpayers ?? ""} onChange={(e) => setPlanEdit({ ...planEdit, max_taxpayers: e.target.value ? Number(e.target.value) : null })} />
              <Input type="number" placeholder="Maks. belge/ay" value={planEdit.max_documents_per_month ?? ""} onChange={(e) => setPlanEdit({ ...planEdit, max_documents_per_month: e.target.value ? Number(e.target.value) : null })} />
              <div className="flex gap-2"><Button type="submit">Kaydet</Button><Button type="button" variant="ghost" onClick={() => setPlanEdit(null)}>Vazgeç</Button></div>
            </form>)}
        </Card>
      )}

      {tab === "jobs" && data.jobs && (
        <Card title="Son işler"><Table head={<><Th>Zaman</Th><Th>Ofis</Th><Th>Tür</Th><Th>Deneme</Th><Th>Hata</Th><Th>Durum</Th><Th /></>}>
          {data.jobs.map((j: any) => (
            <tr key={j.id}><Td>{fmtDateTime(j.updated_at)}</Td><Td>{j.firm_name}</Td><Td className="font-mono text-xs">{j.job_type}</Td><Td>{j.attempts}/{j.max_attempts}</Td>
              <Td className="font-mono text-xs">{j.last_error_code ?? ""}</Td><Td><StatusBadge status={j.status} /></Td>
              <Td>{["dead", "failed"].includes(j.status) && <Button size="sm" variant="secondary" onClick={() => act(() => api(`/admin/jobs/${j.id}/retry`, { method: "POST" }), "İş yeniden kuyruğa alındı.")}>Tekrar dene</Button>}</Td></tr>))}
        </Table></Card>
      )}

      {tab === "config" && data.config && (
        <Card title="Platform yapılandırması">
          <ul className="space-y-3">{data.config.map((c: any) => (
            <li key={c.key} className="grid gap-2 md:grid-cols-[260px_1fr_auto] md:items-center">
              <span className="font-mono text-sm">{c.key}</span>
              <Input defaultValue={JSON.stringify(c.value)} id={`cfg-${c.key}`} className="font-mono" />
              <Button size="sm" variant="secondary" onClick={() => {
                const raw = (document.getElementById(`cfg-${c.key}`) as HTMLInputElement).value;
                let value: unknown;
                try { value = JSON.parse(raw); } catch { return setMsg({ tone: "red", text: "Geçerli JSON girin" }); }
                act(() => api(`/admin/config/${c.key}`, { method: "PUT", json: { value } }), "Kaydedildi.");
              }}>Kaydet</Button>
            </li>))}</ul>
        </Card>
      )}
    </>
  );
}
