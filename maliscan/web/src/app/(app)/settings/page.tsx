"use client";

import { Copy, Trash2, UserPlus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Alert, Badge, Button, Card, Field, Input, PageHeader, Select, Spinner, Table, Td, Th } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { fmtDate, fmtDateTime, ROLE_LABELS } from "@/lib/format";
import { MANAGERS, useSession } from "@/lib/session";

interface Firm { id: string; name: string; tax_number: string | null; tax_office: string | null; email: string | null; phone: string | null; address: string | null; subscription: { plan_name: string; status: string; current_period_end: string | null } | null }
interface Member { user_id: string; email: string; full_name: string | null; role: string; status: string }
interface Invitation { id: string; email: string; role: string; expires_at: string; accepted_at: string | null; revoked_at: string | null }
interface Usage { usage: { users: number; taxpayers: number; documents_this_month: number }; limits: { users: number | null; taxpayers: number | null; documents_per_month: number | null } }
interface AuditLog { id: number; action: string; entity_type: string | null; created_at: string; actor_user_id: string | null }

function Meter({ label, used, limit }: { label: string; used: number; limit: number | null }) {
  const pct = limit ? Math.min(100, (used / limit) * 100) : 0;
  return (
    <div>
      <div className="mb-1 flex justify-between text-sm"><span className="text-ink-700">{label}</span><span className="tabular-nums text-ink-500">{used} / {limit ?? "∞"}</span></div>
      <div className="h-2 overflow-hidden rounded-full bg-slate-100"><div className={pct > 90 ? "h-full bg-red-500" : "h-full bg-brand-500"} style={{ width: `${pct}%` }} /></div>
    </div>
  );
}

export default function SettingsPage() {
  const { firm, can, me, refreshMe } = useSession();
  const [f, setF] = useState<Firm | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [invites, setInvites] = useState<Invitation[]>([]);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [invite, setInvite] = useState({ email: "", role: "accountant" });
  const [link, setLink] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ tone: "green" | "red"; text: string } | null>(null);
  const manager = can(...MANAGERS);

  const load = useCallback(async () => {
    if (!firm) return;
    const id = firm.firm_id;
    const [ff, mm, uu] = await Promise.all([api<Firm>(`/firms/${id}`), api<Member[]>(`/firms/${id}/members`), api<Usage>(`/firms/${id}/usage`)]);
    setF(ff); setMembers(mm); setUsage(uu);
    if (can(...MANAGERS)) {
      setInvites(await api<Invitation[]>(`/firms/${id}/invitations`));
      setLogs(await api<AuditLog[]>(`/firms/${id}/audit-logs?limit=30`));
    }
  }, [firm, can]);
  useEffect(() => { load(); }, [load]);

  async function act(fn: () => Promise<unknown>, ok: string) {
    setMsg(null);
    try { await fn(); setMsg({ tone: "green", text: ok }); await load(); }
    catch (e) { setMsg({ tone: "red", text: e instanceof ApiError ? e.message : "İşlem başarısız" }); }
  }

  if (!firm || !f || !usage) return <Spinner />;
  return (
    <>
      <PageHeader title="Ofis ayarları" subtitle={f.name} />
      {msg && <div className="mb-4"><Alert tone={msg.tone}>{msg.text}</Alert></div>}
      <div className="grid gap-6 lg:grid-cols-3">
        <Card title="Ofis bilgileri" className="lg:col-span-2" actions={manager && <Button size="sm" onClick={() => act(() => api(`/firms/${f.id}`, { method: "PATCH", json: { name: f.name, tax_number: f.tax_number || null, tax_office: f.tax_office, email: f.email, phone: f.phone, address: f.address } }).then(refreshMe), "Kaydedildi.")}>Kaydet</Button>}>
          <fieldset disabled={!manager} className="grid gap-3 sm:grid-cols-2">
            <Field label="Ofis adı"><Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
            <Field label="VKN/TCKN"><Input value={f.tax_number ?? ""} onChange={(e) => setF({ ...f, tax_number: e.target.value })} /></Field>
            <Field label="Vergi dairesi"><Input value={f.tax_office ?? ""} onChange={(e) => setF({ ...f, tax_office: e.target.value })} /></Field>
            <Field label="E-posta"><Input value={f.email ?? ""} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
            <Field label="Telefon"><Input value={f.phone ?? ""} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field>
            <Field label="Adres"><Input value={f.address ?? ""} onChange={(e) => setF({ ...f, address: e.target.value })} /></Field>
          </fieldset>
        </Card>
        <Card title="Abonelik">
          {f.subscription ? <p className="mb-4 text-sm"><b>{f.subscription.plan_name}</b> <Badge tone={f.subscription.status === "active" ? "green" : "amber"}>{f.subscription.status === "trialing" ? "Deneme" : f.subscription.status}</Badge>{f.subscription.current_period_end && <span className="block text-xs text-ink-500">Bitiş: {fmtDate(f.subscription.current_period_end)}</span>}</p> : <Alert tone="amber">Aktif abonelik yok.</Alert>}
          <div className="space-y-3">
            <Meter label="Kullanıcı" used={usage.usage.users} limit={usage.limits.users} />
            <Meter label="Mükellef" used={usage.usage.taxpayers} limit={usage.limits.taxpayers} />
            <Meter label="Bu ay belge" used={usage.usage.documents_this_month} limit={usage.limits.documents_per_month} />
          </div>
        </Card>
      </div>

      <Card title="Ekip" className="mt-6">
        {manager && (
          <form className="mb-4 flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); act(async () => {
            const r = await api<{ accept_url: string }>(`/firms/${f.id}/invitations`, { method: "POST", json: invite });
            setLink(r.accept_url); setInvite({ ...invite, email: "" });
          }, "Davet oluşturuldu. Bağlantıyı çalışana iletin."); }}>
            <Input type="email" required placeholder="calisan@ofis.com" value={invite.email} onChange={(e) => setInvite({ ...invite, email: e.target.value })} className="min-w-56 flex-1" />
            <Select value={invite.role} onChange={(e) => setInvite({ ...invite, role: e.target.value })} className="w-48">
              {["admin", "accountant", "clerk", "viewer"].map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}</Select>
            <Button type="submit"><UserPlus className="h-4 w-4" />Davet et</Button>
          </form>)}
        {link && <div className="mb-4 flex items-center gap-2 rounded-lg bg-brand-50 p-3 text-sm"><code className="flex-1 truncate">{link}</code><Button size="sm" variant="secondary" onClick={() => navigator.clipboard.writeText(link)}><Copy className="h-4 w-4" />Kopyala</Button></div>}
        <Table head={<><Th>Kişi</Th><Th>Rol</Th><Th>Durum</Th><Th /></>}>
          {members.map((m) => (
            <tr key={m.user_id}>
              <Td><div className="font-medium text-ink-900">{m.full_name || m.email}</div><div className="text-xs text-ink-500">{m.email}</div></Td>
              <Td>{manager && m.user_id !== me?.id ? (
                <Select value={m.role} className="w-44 py-1" onChange={(e) => act(() => api(`/firms/${f.id}/members/${m.user_id}`, { method: "PATCH", json: { role: e.target.value } }), "Rol güncellendi.")}>
                  {Object.entries(ROLE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Select>) : ROLE_LABELS[m.role]}</Td>
              <Td>{m.status === "active" ? <Badge tone="green">Aktif</Badge> : <Badge>Pasif</Badge>}</Td>
              <Td>{manager && m.user_id !== me?.id && <div className="flex gap-1">
                <Button size="sm" variant="ghost" onClick={() => act(() => api(`/firms/${f.id}/members/${m.user_id}`, { method: "PATCH", json: { status: m.status === "active" ? "disabled" : "active" } }), "Güncellendi.")}>{m.status === "active" ? "Pasifleştir" : "Aktifleştir"}</Button>
                <button className="p-1.5 text-ink-400 hover:text-red-600" aria-label="Çıkar" onClick={() => confirm("Üye ofisten çıkarılsın mı?") && act(() => api(`/firms/${f.id}/members/${m.user_id}`, { method: "DELETE" }), "Üye çıkarıldı.")}><Trash2 className="h-4 w-4" /></button></div>}</Td>
            </tr>))}
        </Table>
        {manager && invites.filter((i) => !i.accepted_at && !i.revoked_at).length > 0 && (
          <div className="mt-5">
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-500">Bekleyen davetler</p>
            <ul className="space-y-1 text-sm">{invites.filter((i) => !i.accepted_at && !i.revoked_at).map((i) => (
              <li key={i.id} className="flex items-center justify-between rounded-md bg-slate-50 px-3 py-1.5"><span>{i.email} · {ROLE_LABELS[i.role]} · son {fmtDate(i.expires_at)}</span>
                <Button size="sm" variant="ghost" onClick={() => act(() => api(`/firms/${f.id}/invitations/${i.id}`, { method: "DELETE" }), "Davet iptal edildi.")}>İptal</Button></li>))}</ul>
          </div>)}
      </Card>

      {manager && logs.length > 0 && (
        <Card title="Denetim kaydı (son 30)" className="mt-6">
          <ul className="divide-y divide-slate-100 text-sm">{logs.map((l) => (
            <li key={l.id} className="flex justify-between py-1.5"><span className="font-mono text-xs">{l.action}</span><span className="text-xs text-ink-500">{fmtDateTime(l.created_at)}</span></li>))}</ul>
        </Card>)}
    </>
  );
}
