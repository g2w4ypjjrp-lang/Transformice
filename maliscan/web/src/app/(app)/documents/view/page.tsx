"use client";

import { AlertTriangle, ArrowLeft, Check, ChevronRight, Info, Plus, RefreshCw, RotateCcw, Save, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Badge, Button, Card, Confidence, Field, Input, Modal, Select, Spinner, StatusBadge, cn } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { DOC_TYPE_LABELS, fmtDate, fmtDateTime, fmtMoney, parseAmount } from "@/lib/format";
import { BOOKERS, MANAGERS, useSession, WRITERS } from "@/lib/session";
import type { Account, Issue, Journal, JournalLine } from "@/lib/types";

interface Extracted {
  document_type: string | null; direction: string | null;
  supplier: { name: string | null; tax_number: string | null; tax_office: string | null; address: string | null };
  buyer: { name: string | null; tax_number: string | null; tax_office: string | null; address: string | null };
  document_number: string | null; ettn: string | null; document_date: string | null; currency: string | null;
  payment_method: string | null; lines: Record<string, unknown>[];
  vat_breakdown: { rate: string; base: string | null; amount: string | null }[];
  withholding: { code: string | null; ratio: string | null; amount: string | null } | null;
  total_net: string | null; total_vat: string | null; total_amount: string | null; notes: string | null;
}
interface Detail {
  document: { id: string; taxpayer_id: string; status: string; file_kind: string; mime_type: string; document_type: string | null;
    duplicate_of: string | null; overall_confidence: string | null; error_code: string | null; original_filename: string | null; created_at: string };
  file_url: string | null;
  extraction: { version: number; source: string; ai_model: string | null; data: Extracted; overall_confidence: string | null; validation_issues: Issue[]; created_at: string } | null;
  journal: Journal | null;
  history: { action: string; comment: string | null; created_at: string; full_name: string | null; email: string }[];
  job: { status: string; last_error_code: string | null } | null;
  review_threshold: number;
}

const SOURCE: Record<string, string> = { ubl_xml: "e-Fatura XML (kaynak veri)", ocr_ai: "Yapay zekâ ile okundu", ocr_rules: "Ücretsiz OCR ile okundu", manual: "Elle düzeltildi", pdf_text: "PDF metni" };
const ACTIONS: Record<string, string> = { approve: "Onayladı", reject: "Reddetti", reopen: "Yeniden açtı", correct: "Veriyi düzeltti" };

export default function DocumentReviewPage() {
  return <Suspense fallback={<Spinner />}><DocumentReview /></Suspense>;
}

function DocumentReview() {
  const id = useSearchParams().get("id") ?? "";
  const { firm, can } = useSession();
  const router = useRouter();
  const [d, setD] = useState<Detail | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [form, setForm] = useState<Extracted | null>(null);
  const [lines, setLines] = useState<JournalLine[]>([]);
  const [tab, setTab] = useState<"data" | "journal">("journal");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [comment, setComment] = useState("");
  const [ackDup, setAckDup] = useState(false);
  const base = firm ? `/firms/${firm.firm_id}/documents/${id}` : "";

  const load = useCallback(async () => {
    if (!firm) return;
    const det = await api<Detail>(base);
    setD(det);
    setForm(det.extraction ? structuredClone(det.extraction.data) : null);
    setLines(det.journal?.lines.map((l) => ({ ...l, debit: String(l.debit), credit: String(l.credit) })) ?? []);
    if (!accounts.length) api<Account[]>(`/firms/${firm.firm_id}/taxpayers/${det.document.taxpayer_id}/accounts?postable_only=true`).then(setAccounts);
  }, [firm, base, accounts.length]);

  useEffect(() => { load().catch((e) => setError(e instanceof ApiError ? e.message : "Belge yüklenemedi")); }, [load]);
  useEffect(() => {
    if (!d || !["queued", "processing", "uploaded"].includes(d.document.status)) return;
    const t = setInterval(() => load(), 3000);
    return () => clearInterval(t);
  }, [d, load]);

  const totals = useMemo(() => {
    const dr = lines.reduce((s, l) => s + Number(l.debit || 0), 0);
    const cr = lines.reduce((s, l) => s + Number(l.credit || 0), 0);
    return { dr, cr, balanced: Math.abs(dr - cr) < 0.005 && dr > 0 };
  }, [lines]);

  async function run(key: string, fn: () => Promise<unknown>, after?: () => void) {
    setBusy(key);
    setError(null);
    try {
      await fn();
      after?.();
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "İşlem başarısız");
    } finally {
      setBusy(null);
    }
  }

  async function goNext() {
    if (!firm || !d) return;
    const r = await api<{ items: { id: string }[] }>(`/firms/${firm.firm_id}/documents?status=needs_review&taxpayer_id=${d.document.taxpayer_id}&limit=5`);
    const next = r.items.find((x) => x.id !== id);
    router.push(next ? `/documents/view?id=${next.id}` : "/documents?status=needs_review");
  }

  if (error && !d) return <Alert>{error}</Alert>;
  if (!firm || !d) return <Spinner />;
  const doc = d.document;
  const ex = d.extraction;
  const editable = doc.status === "needs_review" || doc.status === "failed";
  const journalEditable = editable && can(...BOOKERS) && (!d.journal || d.journal.status === "draft");
  const issues = ex?.validation_issues ?? [];
  const errors = issues.filter((i) => i.severity === "error");
  const setF = (patch: Partial<Extracted>) => setForm((f) => (f ? { ...f, ...patch } : f));

  return (
    <>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Link href="/documents" className="rounded-lg p-2 hover:bg-white" aria-label="Geri"><ArrowLeft className="h-4 w-4" /></Link>
          <div>
            <h1 className="flex flex-wrap items-center gap-2 text-lg font-semibold">
              {form?.supplier.name || doc.original_filename || "Belge"} <StatusBadge status={doc.status} />
              {doc.duplicate_of && <Link href={`/documents/view?id=${doc.duplicate_of}`}><Badge tone="amber">Olası mükerrer →</Badge></Link>}
            </h1>
            <p className="text-sm text-ink-500">{doc.document_type ? DOC_TYPE_LABELS[doc.document_type] : doc.file_kind.toUpperCase()} · yüklendi {fmtDateTime(doc.created_at)}{ex && <> · {SOURCE[ex.source] ?? ex.source}</>}</p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {can(...WRITERS) && ["needs_review", "failed", "rejected"].includes(doc.status) && (
            <Button variant="ghost" size="sm" loading={busy === "re"} onClick={() => run("re", () => api(`${base}/reprocess`, { method: "POST" }))}><RefreshCw className="h-4 w-4" />Yeniden oku</Button>)}
          {can(...MANAGERS) && doc.status !== "exported" && (
            <Button variant="ghost" size="sm" onClick={() => confirm(doc.status === "approved" ? "Onaylı belge silinsin mi? Onayı geri alınır ve fiş Luca'ya aktarılmaz." : "Belge silinsin mi?") && run("del", () => api(base, { method: "DELETE" }), () => router.replace("/documents"))}><Trash2 className="h-4 w-4" />Sil</Button>)}
          {can(...BOOKERS) && ["approved", "rejected"].includes(doc.status) && (
            <Button variant="secondary" size="sm" loading={busy === "reopen"} onClick={() => run("reopen", () => api(`${base}/reopen`, { method: "POST", json: {} }))}><RotateCcw className="h-4 w-4" />Yeniden aç</Button>)}
          {doc.status === "needs_review" && <Button variant="ghost" size="sm" onClick={goNext}>Sonraki<ChevronRight className="h-4 w-4" /></Button>}
        </div>
      </div>

      {error && <div className="mb-4"><Alert>{error}</Alert></div>}
      {["queued", "processing"].includes(doc.status) && <div className="mb-4"><Alert tone="blue">Belge işleniyor… Sayfa otomatik yenilenecek.</Alert></div>}
      {doc.status === "failed" && <div className="mb-4"><Alert>İşleme hatası: {doc.error_code}. Yeniden okutabilir veya yevmiyeyi elle girebilirsiniz.</Alert></div>}

      <div className="grid gap-6 xl:grid-cols-2">
        {/* ------------------------------------------------ viewer */}
        <Card className="xl:sticky xl:top-6 xl:self-start">
          {!d.file_url ? <p className="text-sm text-ink-500">Dosya önizlemesi kullanılamıyor.</p>
            : doc.file_kind === "image" ? <img src={d.file_url} alt="Belge" className="mx-auto max-h-[75vh] w-auto rounded-lg border border-slate-100" />
            : doc.file_kind === "pdf" ? <iframe src={d.file_url} title="PDF" className="h-[75vh] w-full rounded-lg border border-slate-100" />
            : <div className="space-y-2 text-sm"><p>e-Fatura / e-Arşiv XML belgesi. Veriler doğrudan elektronik belgeden okundu (OCR kullanılmadı).</p><a href={d.file_url} className="text-brand-600 hover:underline">XML dosyasını indir</a></div>}
        </Card>

        {/* ------------------------------------------------ review */}
        <div className="space-y-4">
          {ex && (
            <Card>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-3 text-sm"><span className="text-ink-500">Okuma güveni</span><Confidence value={ex.overall_confidence} threshold={d.review_threshold} /></div>
                <span className="text-xs text-ink-500">Sürüm {ex.version}{ex.ai_model ? ` · ${ex.ai_model}` : ""}</span>
              </div>
              {issues.length > 0 && (
                <ul className="mt-3 space-y-1.5">
                  {issues.map((i, k) => (
                    <li key={k} className={cn("flex items-start gap-2 rounded-md px-2 py-1.5 text-xs", i.severity === "error" ? "bg-red-50 text-red-800" : i.severity === "warning" ? "bg-amber-50 text-amber-900" : "bg-slate-50 text-ink-700")}>
                      {i.severity === "info" ? <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" /> : <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />}{i.message}
                    </li>))}
                </ul>
              )}
            </Card>
          )}

          <div className="flex gap-1 rounded-lg bg-slate-100 p-1 text-sm">
            {(["journal", "data"] as const).map((t) => (
              <button key={t} onClick={() => setTab(t)} className={cn("flex-1 rounded-md px-3 py-1.5 font-medium", tab === t ? "bg-white shadow-sm" : "text-ink-500")}>
                {t === "journal" ? "Yevmiye kaydı" : "Belge verisi"}</button>))}
          </div>

          {tab === "data" && form && (
            <Card title="Belge verisi" actions={editable && can(...WRITERS) && (
              <Button size="sm" loading={busy === "save"} onClick={() => run("save", () => api(`${base}/extraction`, { method: "PUT", json: form }))}><Save className="h-4 w-4" />Düzeltmeyi kaydet</Button>)}>
              <fieldset disabled={!editable || !can(...WRITERS)} className="grid gap-3 sm:grid-cols-2">
                <Field label="Belge türü"><Select value={form.document_type ?? ""} onChange={(e) => setF({ document_type: e.target.value || null })}>
                  <option value="">—</option>{Object.entries(DOC_TYPE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Select></Field>
                <Field label="Yön"><Select value={form.direction ?? "purchase"} onChange={(e) => setF({ direction: e.target.value })}>
                  <option value="purchase">Alış / gider</option><option value="sales">Satış</option></Select></Field>
                <Field label="Satıcı unvanı"><Input value={form.supplier.name ?? ""} onChange={(e) => setF({ supplier: { ...form.supplier, name: e.target.value || null } })} /></Field>
                <Field label="Satıcı VKN/TCKN"><Input inputMode="numeric" value={form.supplier.tax_number ?? ""} onChange={(e) => setF({ supplier: { ...form.supplier, tax_number: e.target.value || null } })} /></Field>
                <Field label="Belge no"><Input value={form.document_number ?? ""} onChange={(e) => setF({ document_number: e.target.value || null })} /></Field>
                <Field label="Belge tarihi"><Input type="date" value={form.document_date ?? ""} onChange={(e) => setF({ document_date: e.target.value || null })} /></Field>
                <Field label="Ödeme şekli"><Select value={form.payment_method ?? ""} onChange={(e) => setF({ payment_method: e.target.value || null })}>
                  <option value="">Belirtilmemiş</option><option value="cash">Nakit</option><option value="credit_card">Kredi kartı</option><option value="bank">Banka</option><option value="supplier_credit">Vadeli (cari)</option></Select></Field>
                <Field label="Genel toplam (KDV dahil)"><Input inputMode="decimal" value={form.total_amount ?? ""} onChange={(e) => setF({ total_amount: e.target.value ? parseAmount(e.target.value) : null })} /></Field>
              </fieldset>
              <div className="mt-5">
                <p className="mb-2 text-xs font-medium text-ink-700">KDV dağılımı</p>
                <div className="space-y-2">
                  {form.vat_breakdown.map((g, i) => (
                    <div key={i} className="grid grid-cols-[80px_1fr_1fr_32px] items-center gap-2">
                      <Select disabled={!editable} value={String(Number(g.rate))} onChange={(e) => { const v = [...form.vat_breakdown]; v[i] = { ...g, rate: e.target.value }; setF({ vat_breakdown: v }); }}>
                        {["0", "1", "10", "20", "8", "18"].map((r) => <option key={r} value={r}>%{r}</option>)}</Select>
                      <Input disabled={!editable} placeholder="Matrah" inputMode="decimal" value={g.base ?? ""} onChange={(e) => { const v = [...form.vat_breakdown]; v[i] = { ...g, base: parseAmount(e.target.value) }; setF({ vat_breakdown: v }); }} />
                      <Input disabled={!editable} placeholder="KDV" inputMode="decimal" value={g.amount ?? ""} onChange={(e) => { const v = [...form.vat_breakdown]; v[i] = { ...g, amount: parseAmount(e.target.value) }; setF({ vat_breakdown: v }); }} />
                      {editable && <button onClick={() => setF({ vat_breakdown: form.vat_breakdown.filter((_, k) => k !== i) })} className="rounded p-1 text-ink-400 hover:text-red-600" aria-label="Sil"><X className="h-4 w-4" /></button>}
                    </div>))}
                  {editable && <Button size="sm" variant="ghost" onClick={() => setF({ vat_breakdown: [...form.vat_breakdown, { rate: "20", base: null, amount: null }] })}><Plus className="h-4 w-4" />Oran ekle</Button>}
                </div>
              </div>
              <div className="mt-5 grid gap-3 sm:grid-cols-3">
                <Field label="Tevkifat oranı" hint="7/10 → 0.7"><Input disabled={!editable} inputMode="decimal" value={form.withholding?.ratio ?? ""} onChange={(e) => setF({ withholding: { code: form.withholding?.code ?? null, amount: form.withholding?.amount ?? null, ratio: e.target.value || null } })} /></Field>
                <Field label="Tevkifat tutarı"><Input disabled={!editable} inputMode="decimal" value={form.withholding?.amount ?? ""} onChange={(e) => setF({ withholding: e.target.value ? { code: form.withholding?.code ?? null, ratio: form.withholding?.ratio ?? null, amount: parseAmount(e.target.value) } : null })} /></Field>
                <Field label="Tevkifat kodu"><Input disabled={!editable} value={form.withholding?.code ?? ""} onChange={(e) => setF({ withholding: { ratio: form.withholding?.ratio ?? null, amount: form.withholding?.amount ?? null, code: e.target.value || null } })} /></Field>
              </div>
              {form.lines.length > 0 && <p className="mt-4 text-xs text-ink-500">{form.lines.length} kalem satırı okundu.</p>}
              {can(...WRITERS) && !can(...BOOKERS) && editable && <p className="mt-3 text-xs text-ink-500">Düzeltmeniz kaydedildiğinde yevmiye taslağı otomatik yeniden oluşturulur; onay muhasebeci tarafından verilir.</p>}
            </Card>
          )}

          {tab === "journal" && (
            <Card title={<span className="flex items-center gap-2">Yevmiye kaydı {d.journal && <StatusBadge status={d.journal.status} />}</span>}
              actions={journalEditable && (
                <Button size="sm" variant="secondary" disabled={!totals.balanced} loading={busy === "journal"}
                  onClick={() => run("journal", () => api(`${base}/journal`, { method: "PUT", json: {
                    entry_date: d.journal?.entry_date ?? form?.document_date, description: d.journal?.description ?? form?.supplier.name,
                    document_number: d.journal?.document_number ?? form?.document_number, voucher_type: d.journal?.voucher_type ?? "mahsup",
                    lines: lines.filter((l) => l.account_code).map((l) => ({ account_code: l.account_code, description: l.description || null,
                      debit: Number(l.debit || 0).toFixed(2), credit: Number(l.credit || 0).toFixed(2) })) } }))}>
                  <Save className="h-4 w-4" />Yevmiyeyi kaydet</Button>)}>
              {!d.journal && lines.length === 0 && !journalEditable ? <p className="text-sm text-ink-500">Yevmiye taslağı henüz oluşmadı.</p> : (
                <>
                  {d.journal?.warnings?.length ? <div className="mb-3 space-y-1">{d.journal.warnings.map((w, i) => <Alert key={i} tone="amber">{w.message}</Alert>)}</div> : null}
                  <datalist id="accounts">{accounts.map((a) => <option key={a.id} value={a.code}>{a.name}</option>)}</datalist>
                  <div className="overflow-x-auto">
                    <table className="min-w-full text-sm">
                      <thead><tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-ink-500"><th className="py-2 pr-2">Hesap</th><th className="py-2 pr-2">Açıklama</th><th className="py-2 pr-2 text-right">Borç</th><th className="py-2 pr-2 text-right">Alacak</th><th /></tr></thead>
                      <tbody className="divide-y divide-slate-100">
                        {lines.map((l, i) => {
                          const name = accounts.find((a) => a.code === l.account_code)?.name ?? l.account_name;
                          const upd = (p: Partial<JournalLine>) => setLines((ls) => ls.map((x, k) => (k === i ? { ...x, ...p } : x)));
                          return (
                            <tr key={i}>
                              <td className="py-1.5 pr-2 align-top">
                                {journalEditable ? <Input list="accounts" value={l.account_code} onChange={(e) => upd({ account_code: e.target.value.trim() })} className="w-32 py-1.5 font-mono" />
                                  : <span className="font-mono">{l.account_code}</span>}
                                <div className={cn("mt-0.5 max-w-[160px] truncate text-[11px]", name ? "text-ink-500" : "text-red-600")}>{name ?? "Hesap planında yok"}</div>
                              </td>
                              <td className="py-1.5 pr-2 align-top">{journalEditable ? <Input value={l.description ?? ""} onChange={(e) => upd({ description: e.target.value })} className="min-w-[160px] py-1.5" /> : l.description}</td>
                              <td className="py-1.5 pr-2 text-right align-top tabular-nums">{journalEditable ? <Input inputMode="decimal" value={Number(l.debit) ? l.debit : ""} onChange={(e) => upd({ debit: parseAmount(e.target.value), credit: "0" })} className="w-28 py-1.5 text-right" /> : (Number(l.debit) ? fmtMoney(l.debit) : "")}</td>
                              <td className="py-1.5 pr-2 text-right align-top tabular-nums">{journalEditable ? <Input inputMode="decimal" value={Number(l.credit) ? l.credit : ""} onChange={(e) => upd({ credit: parseAmount(e.target.value), debit: "0" })} className="w-28 py-1.5 text-right" /> : (Number(l.credit) ? fmtMoney(l.credit) : "")}</td>
                              <td className="py-1.5 align-top">{journalEditable && <button onClick={() => setLines(lines.filter((_, k) => k !== i))} className="rounded p-1.5 text-ink-400 hover:text-red-600" aria-label="Satırı sil"><X className="h-4 w-4" /></button>}</td>
                            </tr>);
                        })}
                      </tbody>
                      <tfoot><tr className="border-t-2 border-slate-200 font-semibold"><td colSpan={2} className="py-2">
                        {journalEditable && <Button size="sm" variant="ghost" onClick={() => setLines([...lines, { account_code: "", description: "", debit: "0", credit: "0" }])}><Plus className="h-4 w-4" />Satır ekle</Button>}</td>
                        <td className="py-2 pr-2 text-right tabular-nums">{fmtMoney(totals.dr)}</td><td className="py-2 pr-2 text-right tabular-nums">{fmtMoney(totals.cr)}</td><td /></tr></tfoot>
                    </table>
                  </div>
                  <p className={cn("mt-2 text-xs font-medium", totals.balanced ? "text-accent-700" : "text-red-600")}>{totals.balanced ? "Borç ve alacak dengeli." : `Fark: ${fmtMoney(totals.dr - totals.cr)} — kayıt dengeli olmadan onaylanamaz.`}</p>
                </>
              )}
            </Card>
          )}

          {doc.status === "needs_review" && can(...BOOKERS) && (
            <Card>
              {doc.duplicate_of && <label className="mb-3 flex items-center gap-2 text-sm"><input type="checkbox" checked={ackDup} onChange={(e) => setAckDup(e.target.checked)} />Bu belgenin mükerrer olmadığını kontrol ettim.</label>}
              {errors.length > 0 && <p className="mb-3 text-xs text-red-700">Veride {errors.length} hata var. Onaylamadan önce belge ile karşılaştırın.</p>}
              <div className="flex flex-wrap gap-2">
                <Button variant="success" loading={busy === "approve"} disabled={!d.journal || !totals.balanced || (!!doc.duplicate_of && !ackDup)}
                  onClick={() => run("approve", () => api(`${base}/approve`, { method: "POST", json: { acknowledge_duplicate: ackDup } }), goNext)}>
                  <Check className="h-4 w-4" />Onayla ve sonrakine geç</Button>
                <Button variant="danger" onClick={() => setRejectOpen(true)}><X className="h-4 w-4" />Reddet</Button>
              </div>
              <p className="mt-2 text-xs text-ink-500">Onay, yevmiye kaydını kesinleştirir ve Luca aktarımına hazırlar. Kayıt dengeli olmalı ve dönem kilitli olmamalıdır.</p>
            </Card>
          )}

          {d.history.length > 0 && (
            <Card title="Onay geçmişi">
              <ul className="space-y-2 text-sm">{d.history.map((h, i) => (
                <li key={i} className="flex justify-between gap-3"><span><b className="font-medium">{h.full_name || h.email}</b> {ACTIONS[h.action] ?? h.action}{h.comment && <span className="text-ink-500"> — {h.comment}</span>}</span><span className="whitespace-nowrap text-xs text-ink-500">{fmtDateTime(h.created_at)}</span></li>))}</ul>
            </Card>
          )}
          {d.journal?.status === "approved" && <p className="text-xs text-ink-500">Fiş tarihi {fmtDate(d.journal.entry_date)} · Luca aktarımı için &quot;Luca aktarımları&quot; sayfasını kullanın.</p>}
        </div>
      </div>

      <Modal open={rejectOpen} onClose={() => setRejectOpen(false)} title="Belgeyi reddet"
        footer={<><Button variant="secondary" onClick={() => setRejectOpen(false)}>Vazgeç</Button>
          <Button variant="danger" loading={busy === "reject"} onClick={() => run("reject", () => api(`${base}/reject`, { method: "POST", json: { comment } }), () => setRejectOpen(false))}>Reddet</Button></>}>
        <Field label="Gerekçe"><Input value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Örn. mükerrer, okunaksız, şahsi harcama" /></Field>
      </Modal>
    </>
  );
}
