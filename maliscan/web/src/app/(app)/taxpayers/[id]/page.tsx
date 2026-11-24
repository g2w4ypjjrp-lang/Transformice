"use client";

import { ArrowLeft, Plus, Trash2, Upload } from "lucide-react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { ChangeEvent, Suspense, useCallback, useEffect, useState } from "react";
import { Alert, Badge, Button, Card, Field, Input, Modal, PageHeader, Select, Spinner, Table, Td, Th, cn } from "@/components/ui";
import { api, ApiError, upload } from "@/lib/api";
import { BOOKERS, MANAGERS, useSession } from "@/lib/session";
import type { Account, Taxpayer } from "@/lib/types";

interface Mapping { id: string; match_field: string; match_value: string; account_code: string; vat_account_code: string | null; is_non_deductible: boolean; vat_deductible: boolean; priority: number; source: string }
interface Rule { id: string; name: string; priority: number; conditions: { all?: Cond[]; any?: Cond[] }; actions: Record<string, unknown>; is_active: boolean }
interface Cond { field: string; op: string; value: string | number }

const FIELDS: Record<string, string> = { supplier_tax_number: "Satıcı VKN", supplier_name: "Satıcı adı içerir", item_keyword: "Kalem açıklaması içerir", document_type: "Belge türü" };
const DEFAULTS: [keyof Taxpayer, string][] = [
  ["default_cash_account", "Kasa"], ["default_bank_account", "Banka"], ["default_credit_card_account", "Kredi kartı"],
  ["default_supplier_account", "Satıcılar"], ["default_customer_account", "Alıcılar"], ["default_expense_account", "Varsayılan gider"],
  ["default_input_vat_account", "İndirilecek KDV (yedek)"], ["default_output_vat_account", "Hesaplanan KDV (yedek)"],
];

function TaxpayerInner() {
  const { id } = useParams<{ id: string }>();
  const params = useSearchParams();
  const { firm, can } = useSession();
  const [tp, setTp] = useState<Taxpayer | null>(null);
  const [tab, setTab] = useState<"settings" | "accounts" | "mappings" | "rules">("settings");
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [mappings, setMappings] = useState<Mapping[]>([]);
  const [rules, setRules] = useState<Rule[]>([]);
  const [msg, setMsg] = useState<{ tone: "green" | "red" | "amber"; text: string } | null>(
    params.get("warn") === "vkn" ? { tone: "amber", text: "VKN kontrol hanesi doğrulanamadı. Numarayı vergi levhasından kontrol edin." } : null);
  const [newAcc, setNewAcc] = useState({ code: "", name: "" });
  const [newMap, setNewMap] = useState({ match_field: "supplier_tax_number", match_value: "", account_code: "", is_non_deductible: false });
  const [ruleOpen, setRuleOpen] = useState(false);
  const [rule, setRule] = useState({ name: "", field: "supplier_name", op: "contains", value: "", expense_account: "", payment_method: "", non_deductible: false, priority: 100 });
  const base = firm ? `/firms/${firm.firm_id}/taxpayers/${id}` : "";
  const editable = can(...BOOKERS);

  const load = useCallback(async () => {
    if (!firm) return;
    const [t, a, m, r] = await Promise.all([api<Taxpayer>(base), api<Account[]>(`${base}/accounts`), api<Mapping[]>(`${base}/mappings`), api<Rule[]>(`${base}/rules`)]);
    setTp(t); setAccounts(a); setMappings(m); setRules(r);
  }, [firm, base]);
  useEffect(() => { load(); }, [load]);

  async function act(fn: () => Promise<unknown>, ok: string) {
    setMsg(null);
    try { await fn(); setMsg({ tone: "green", text: ok }); await load(); }
    catch (e) { setMsg({ tone: "red", text: e instanceof ApiError ? e.message : "İşlem başarısız" }); }
  }

  async function onImport(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    const form = new FormData();
    form.append("file", f);
    await act(async () => {
      const r = await upload<{ accepted: number; rejected: unknown[] }>(`${base}/accounts/import`, form);
      if (r.rejected.length) throw new ApiError(422, "partial", `${r.accepted} hesap alındı, ${r.rejected.length} satır geçersiz.`);
    }, "Hesap planı içe aktarıldı.");
  }

  if (!firm || !tp) return <Spinner />;
  const postable = accounts.filter((a) => a.is_postable && a.is_active);

  return (
    <>
      <Link href="/taxpayers" className="mb-3 inline-flex items-center gap-1 text-sm text-ink-500 hover:text-ink-900"><ArrowLeft className="h-4 w-4" />Mükellefler</Link>
      <PageHeader title={tp.title} subtitle={`${tp.taxpayer_type === "company" ? "VKN" : "TCKN"} ${tp.tax_number}${tp.tax_office ? " · " + tp.tax_office : ""}`}
        actions={<><Link href={`/documents?taxpayer=${tp.id}`}><Button variant="secondary">Belgeler</Button></Link><Link href="/scan"><Button>Belge tara</Button></Link></>} />
      {msg && <div className="mb-4"><Alert tone={msg.tone}>{msg.text}</Alert></div>}
      <div className="mb-4 flex gap-1 overflow-x-auto rounded-lg bg-slate-100 p-1 text-sm">
        {([["settings", "Ayarlar ve dönem"], ["accounts", `Hesap planı (${accounts.length})`], ["mappings", `Hesap eşleştirmeleri (${mappings.length})`], ["rules", `Kurallar (${rules.length})`]] as const).map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)} className={cn("whitespace-nowrap rounded-md px-3 py-1.5 font-medium", tab === k ? "bg-white shadow-sm" : "text-ink-500")}>{l}</button>))}
      </div>

      {tab === "settings" && (
        <Card title="Muhasebe ayarları" actions={editable && <Button size="sm" onClick={() => act(() => api(base, { method: "PATCH", json: {
          title: tp.title, tax_office: tp.tax_office, bookkeeping_basis: tp.bookkeeping_basis, locked_until: tp.locked_until || null,
          default_payment_method: tp.default_payment_method, fiscal_year_start_month: tp.fiscal_year_start_month,
          ...Object.fromEntries(DEFAULTS.map(([k]) => [k, tp[k] || null])) } }), "Ayarlar kaydedildi.")}>Kaydet</Button>}>
          <fieldset disabled={!editable} className="grid gap-4 md:grid-cols-3">
            <Field label="Unvan"><Input value={tp.title} onChange={(e) => setTp({ ...tp, title: e.target.value })} /></Field>
            <Field label="Vergi dairesi"><Input value={tp.tax_office ?? ""} onChange={(e) => setTp({ ...tp, tax_office: e.target.value })} /></Field>
            <Field label="Defter türü"><Select value={tp.bookkeeping_basis} onChange={(e) => setTp({ ...tp, bookkeeping_basis: e.target.value })}><option value="balance_sheet">Bilanço esası</option><option value="operating_account">İşletme hesabı esası</option></Select></Field>
            <Field label="Mali yıl başlangıç ayı"><Select value={tp.fiscal_year_start_month} onChange={(e) => setTp({ ...tp, fiscal_year_start_month: Number(e.target.value) })}>{Array.from({ length: 12 }, (_, i) => <option key={i} value={i + 1}>{i + 1}</option>)}</Select></Field>
            <Field label="Dönem kilidi (bu tarih dahil önceki kayıtlar onaylanamaz)"><Input type="date" value={tp.locked_until ?? ""} onChange={(e) => setTp({ ...tp, locked_until: e.target.value || null })} /></Field>
            <Field label="Fişlerde varsayılan ödeme"><Select value={tp.default_payment_method} onChange={(e) => setTp({ ...tp, default_payment_method: e.target.value })}><option value="cash">Nakit</option><option value="bank">Banka</option><option value="credit_card">Kredi kartı</option><option value="supplier_credit">Cari</option></Select></Field>
          </fieldset>
          <h3 className="mb-3 mt-6 text-xs font-semibold uppercase tracking-wide text-ink-500">Varsayılan hesaplar</h3>
          <datalist id="acc">{postable.map((a) => <option key={a.id} value={a.code}>{a.name}</option>)}</datalist>
          <fieldset disabled={!editable} className="grid gap-4 md:grid-cols-4">
            {DEFAULTS.map(([k, label]) => (
              <Field key={k} label={label} error={tp[k] && !postable.some((a) => a.code === tp[k]) ? "Hesap planında yok" : undefined}>
                <Input list="acc" className="font-mono" value={(tp[k] as string) ?? ""} onChange={(e) => setTp({ ...tp, [k]: e.target.value.trim() })} />
              </Field>))}
          </fieldset>
          <p className="mt-4 text-xs text-ink-500">KDV hesapları oran bazında otomatik seçilir (191.01 / 191.10 / 191.20, 391.xx). Hesap planında yoksa yukarıdaki yedek hesap kullanılır.</p>
        </Card>
      )}

      {tab === "accounts" && (
        <Card title="Hesap planı" actions={editable && <>
          <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium hover:bg-slate-50"><Upload className="h-4 w-4" />Luca/Excel içe aktar<input type="file" accept=".csv,.xlsx,.txt" className="hidden" onChange={onImport} /></label>
          <Button size="sm" variant="secondary" onClick={() => act(() => api(`${base}/accounts/seed-default`, { method: "POST" }), "Standart hesaplar eklendi.")}>TDHP ekle</Button></>}>
          {editable && (
            <form className="mb-4 flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); act(() => api(`${base}/accounts`, { method: "POST", json: newAcc }), "Hesap eklendi.").then(() => setNewAcc({ code: "", name: "" })); }}>
              <Input required placeholder="770.01.005" value={newAcc.code} onChange={(e) => setNewAcc({ ...newAcc, code: e.target.value.trim() })} className="w-40 font-mono" />
              <Input required placeholder="Hesap adı" value={newAcc.name} onChange={(e) => setNewAcc({ ...newAcc, name: e.target.value })} className="min-w-48 flex-1" />
              <Button type="submit" size="sm"><Plus className="h-4 w-4" />Ekle</Button>
            </form>)}
          <p className="mb-3 text-xs text-ink-500">İçe aktarım dosyası: ilk iki sütun &quot;Hesap Kodu; Hesap Adı&quot; (Luca hesap planı dökümü CSV/XLSX). Luca&apos;da açık olmayan hesap kodları aktarımda reddedilir.</p>
          <Table head={<><Th>Kod</Th><Th>Ad</Th><Th>Sınıf</Th><Th>Kayıt</Th></>}>
            {accounts.map((a) => (
              <tr key={a.id} className={cn(!a.is_active && "opacity-50")}>
                <Td className={cn("font-mono", a.code.includes(".") ? "pl-6" : "font-semibold text-ink-900")}>{a.code}</Td><Td>{a.name}</Td>
                <Td className="text-xs">{a.account_class}</Td><Td>{a.is_postable ? <Badge tone="green">Alt hesap</Badge> : <Badge>Ana/ara</Badge>}</Td>
              </tr>))}
          </Table>
        </Card>
      )}

      {tab === "mappings" && (
        <Card title="Hesap eşleştirmeleri">
          <p className="mb-3 text-sm text-ink-500">Belge belirli bir satıcıdan geliyorsa gideri hangi hesaba atacağınızı tanımlayın. Onaylanan belgelerden öğrenilen eşleştirmeler de burada listelenir.</p>
          {editable && (
            <form className="mb-4 grid gap-2 md:grid-cols-[180px_1fr_150px_auto_auto]" onSubmit={(e) => { e.preventDefault(); act(() => api(`${base}/mappings`, { method: "POST", json: newMap }), "Eşleştirme eklendi.").then(() => setNewMap({ ...newMap, match_value: "" })); }}>
              <Select value={newMap.match_field} onChange={(e) => setNewMap({ ...newMap, match_field: e.target.value })}>{Object.entries(FIELDS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Select>
              <Input required placeholder="Değer (örn. VKN veya 'shell')" value={newMap.match_value} onChange={(e) => setNewMap({ ...newMap, match_value: e.target.value })} />
              <Input required list="acc2" placeholder="Hesap" className="font-mono" value={newMap.account_code} onChange={(e) => setNewMap({ ...newMap, account_code: e.target.value.trim() })} />
              <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={newMap.is_non_deductible} onChange={(e) => setNewMap({ ...newMap, is_non_deductible: e.target.checked })} />KKEG</label>
              <Button type="submit" size="sm"><Plus className="h-4 w-4" />Ekle</Button>
              <datalist id="acc2">{postable.map((a) => <option key={a.id} value={a.code}>{a.name}</option>)}</datalist>
            </form>)}
          <Table head={<><Th>Koşul</Th><Th>Değer</Th><Th>Hesap</Th><Th>Özellik</Th><Th>Kaynak</Th><Th /></>}>
            {mappings.map((m) => (
              <tr key={m.id}><Td>{FIELDS[m.match_field] ?? m.match_field}</Td><Td>{m.match_value}</Td><Td className="font-mono">{m.account_code}</Td>
                <Td>{m.is_non_deductible ? <Badge tone="amber">KKEG</Badge> : !m.vat_deductible ? <Badge>KDV indirilmez</Badge> : null}</Td>
                <Td>{m.source === "learned" ? <Badge tone="blue">Öğrenildi</Badge> : "Elle"}</Td>
                <Td>{can(...MANAGERS) && <button onClick={() => act(() => api(`${base}/mappings/${m.id}`, { method: "DELETE" }), "Silindi.")} className="text-ink-400 hover:text-red-600" aria-label="Sil"><Trash2 className="h-4 w-4" /></button>}</Td></tr>))}
          </Table>
        </Card>
      )}

      {tab === "rules" && (
        <Card title="Muhasebe kuralları" actions={editable && <Button size="sm" onClick={() => setRuleOpen(true)}><Plus className="h-4 w-4" />Kural ekle</Button>}>
          <p className="mb-3 text-sm text-ink-500">Kurallar eşleştirmelerden önce, öncelik sırasıyla uygulanır. Yapay zekâ önerisi kuralları geçersiz kılamaz.</p>
          <Table head={<><Th>Öncelik</Th><Th>Ad</Th><Th>Koşul</Th><Th>Sonuç</Th><Th /></>}>
            {rules.map((r) => (
              <tr key={r.id}><Td className="tabular-nums">{r.priority}</Td><Td className="font-medium">{r.name}</Td>
                <Td className="text-xs">{(r.conditions.all ?? r.conditions.any ?? []).map((c, i) => <div key={i}>{c.field} {c.op} &quot;{String(c.value)}&quot;</div>)}</Td>
                <Td className="text-xs">{Object.entries(r.actions).map(([k, v]) => <div key={k}>{k}: {String(v)}</div>)}</Td>
                <Td>{can(...MANAGERS) && <button onClick={() => act(() => api(`${base}/rules/${r.id}`, { method: "DELETE" }), "Kural silindi.")} className="text-ink-400 hover:text-red-600" aria-label="Sil"><Trash2 className="h-4 w-4" /></button>}</Td></tr>))}
          </Table>
        </Card>
      )}

      <Modal open={ruleOpen} onClose={() => setRuleOpen(false)} title="Yeni kural"
        footer={<><Button variant="secondary" onClick={() => setRuleOpen(false)}>Vazgeç</Button><Button onClick={() => act(() => api(`${base}/rules`, { method: "POST", json: {
          name: rule.name, priority: rule.priority,
          conditions: { all: [{ field: rule.field, op: rule.op, value: rule.op.match(/^(gt|gte|lt|lte)$/) ? Number(rule.value) : rule.value }] },
          actions: Object.fromEntries(Object.entries({ expense_account: rule.expense_account || undefined, payment_method: rule.payment_method || undefined, non_deductible: rule.non_deductible || undefined }).filter(([, v]) => v !== undefined)),
        } }), "Kural eklendi.").then(() => setRuleOpen(false))}>Kaydet</Button></>}>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="sm:col-span-2"><Field label="Kural adı"><Input value={rule.name} onChange={(e) => setRule({ ...rule, name: e.target.value })} placeholder="Akaryakıt fişleri" /></Field></div>
          <Field label="Alan"><Select value={rule.field} onChange={(e) => setRule({ ...rule, field: e.target.value })}>
            <option value="supplier_name">Satıcı adı</option><option value="supplier_tax_number">Satıcı VKN</option><option value="item_description">Kalem açıklaması</option><option value="total_amount">Toplam tutar</option><option value="document_type">Belge türü</option><option value="payment_method">Ödeme şekli</option></Select></Field>
          <Field label="Koşul"><Select value={rule.op} onChange={(e) => setRule({ ...rule, op: e.target.value })}>
            <option value="contains">içerir</option><option value="eq">eşittir</option><option value="starts_with">ile başlar</option><option value="gt">büyüktür</option><option value="lte">küçük eşit</option></Select></Field>
          <div className="sm:col-span-2"><Field label="Değer"><Input value={rule.value} onChange={(e) => setRule({ ...rule, value: e.target.value })} /></Field></div>
          <Field label="Gider hesabı"><Input list="acc" className="font-mono" value={rule.expense_account} onChange={(e) => setRule({ ...rule, expense_account: e.target.value.trim() })} /></Field>
          <Field label="Ödeme şekli"><Select value={rule.payment_method} onChange={(e) => setRule({ ...rule, payment_method: e.target.value })}><option value="">Değiştirme</option><option value="cash">Nakit</option><option value="bank">Banka</option><option value="credit_card">Kredi kartı</option><option value="supplier_credit">Cari</option></Select></Field>
          <Field label="Öncelik" hint="Küçük sayı önce uygulanır"><Input type="number" value={rule.priority} onChange={(e) => setRule({ ...rule, priority: Number(e.target.value) })} /></Field>
          <label className="flex items-center gap-2 self-end pb-2 text-sm"><input type="checkbox" checked={rule.non_deductible} onChange={(e) => setRule({ ...rule, non_deductible: e.target.checked })} />KKEG (KDV indirilmez)</label>
        </div>
      </Modal>
    </>
  );
}

export default function TaxpayerPage() {
  return <Suspense fallback={<Spinner />}><TaxpayerInner /></Suspense>;
}
