"use client";

import { Download, FileSpreadsheet, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Card, Empty, Field, Input, PageHeader, Select, Spinner, StatusBadge, Table, Td, Th } from "@/components/ui";
import { api, ApiError, downloadFile } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { BOOKERS, MANAGERS, useSession } from "@/lib/session";
import type { Taxpayer } from "@/lib/types";

interface ExportRow { id: string; taxpayer_title: string; template_version: string; status: string; voucher_count: number; file_count: number; created_at: string; created_by_name: string | null }
interface ExportDetail { id: string; voucher_count: number; files: { id: string; filename: string; voucher_count: number; download_url: string | null; sha256: string }[]; issues?: { severity: string; code: string; message: string }[] }

export default function ExportsPage() {
  const { firm, can } = useSession();
  const [taxpayers, setTaxpayers] = useState<Taxpayer[]>([]);
  const [rows, setRows] = useState<ExportRow[] | null>(null);
  const [form, setForm] = useState({ taxpayer: "", date_from: "", date_to: "", format: "fatura_listesi_hesapli", encoding: "cp1254" });
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ExportDetail | null>(null);
  const [errors, setErrors] = useState<{ message: string; source_id?: string | null }[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!firm) return;
    api<ExportRow[]>(`/firms/${firm.firm_id}/exports`).then(setRows);
  }, [firm]);
  useEffect(() => {
    load();
    if (firm) api<Taxpayer[]>(`/firms/${firm.firm_id}/taxpayers`).then(setTaxpayers);
  }, [firm, load]);

  async function create() {
    if (!firm || !form.taxpayer) return;
    setBusy(true); setError(null); setErrors(null); setResult(null);
    try {
      const r = await api<ExportDetail>(`/firms/${firm.firm_id}/taxpayers/${form.taxpayer}/exports`, { method: "POST", json: {
        all_approved: true, date_from: form.date_from || null, date_to: form.date_to || null, format: form.format, encoding: form.encoding } });
      setResult(r);
      load();
    } catch (e) {
      if (e instanceof ApiError) {
        setError(e.message);
        const issues = (e.details as { issues?: { severity: string; message: string; source_id: string | null }[] })?.issues;
        if (issues) setErrors(issues.filter((i) => i.severity === "error"));
      } else setError("Aktarım oluşturulamadı");
    } finally {
      setBusy(false);
    }
  }

  async function openExport(id: string) {
    if (!firm) return;
    setResult(await api<ExportDetail>(`/firms/${firm.firm_id}/exports/${id}`));
  }

  async function downloadAsInvoiceList(id: string) {
    if (!firm) return;
    setError(null);
    try { await downloadFile(`/firms/${firm.firm_id}/exports/${id}/download?format=${form.format.startsWith("fatura_listesi") ? form.format : "fatura_listesi_hesapli"}`); }
    catch (e) { setError(e instanceof ApiError ? e.message : "İndirme başarısız"); }
  }

  async function deleteExport(id: string) {
    if (!firm) return;
    if (!window.confirm("Bu aktarım ve indirme dosyaları silinsin mi?\n\nİçindeki fişler tekrar 'Onaylandı' durumuna döner; yeniden aktarabilir veya silebilirsiniz.\nDosyayı Luca'ya yüklediyseniz o fişleri Luca'dan da silin.")) return;
    setError(null);
    try {
      await api(`/firms/${firm.firm_id}/exports/${id}`, { method: "DELETE" });
      if (result?.id === id) setResult(null);
      load();
    } catch (e) { setError(e instanceof ApiError ? e.message : "Silinemedi"); }
  }

  if (!firm || !rows) return <Spinner />;
  return (
    <>
      <PageHeader title="Luca aktarımları" subtitle="Onaylı yevmiye kayıtlarını Luca 'Excel Veri Aktarımı' dosyasına dönüştürün (dosya başına en fazla 50 fiş)." />
      <div className="mb-6"><Alert tone="amber">
        <b>Önemli:</b> Aktarım şablonu Luca&apos;nın resmî destek dokümanından oluşturulmuştur ve henüz gerçek bir Luca ortamında doğrulanmamıştır.
        İlk kullanımda deneme firmasında test edin. Dosyadaki tüm hesap kodlarının Luca&apos;da açık olması gerekir. Luca şifreniz istenmez; dosyayı Luca&apos;ya kendiniz yüklersiniz.
      </Alert></div>
      {can(...BOOKERS) && (
        <Card title="Yeni aktarım" className="mb-6">
          <div className="grid gap-3 md:grid-cols-5">
            <Field label="Mükellef"><Select value={form.taxpayer} onChange={(e) => setForm({ ...form, taxpayer: e.target.value })}><option value="">Seçin…</option>{taxpayers.map((t) => <option key={t.id} value={t.id}>{t.title}</option>)}</Select></Field>
            <Field label="Başlangıç tarihi"><Input type="date" value={form.date_from} onChange={(e) => setForm({ ...form, date_from: e.target.value })} /></Field>
            <Field label="Bitiş tarihi"><Input type="date" value={form.date_to} onChange={(e) => setForm({ ...form, date_to: e.target.value })} /></Field>
            <Field label="Biçim"><Select value={form.format} onChange={(e) => setForm({ ...form, format: e.target.value })}><option value="fatura_listesi_hesapli">Luca Fatura Aktarım + hesap kodları (…;KDV;HesapKodu;KDVHesapKodu)</option><option value="fatura_listesi">Luca Fatura Aktarım (EvrakTarihi;EvrakNo;Açıklama;GenelToplam;Matrah;KDV)</option><option value="csv">Luca Fiş Aktarımı CSV (Fiş No;Fiş Tarihi;Hesap Kodu…)</option><option value="xlsx">Luca Fiş Aktarımı Excel (.xlsx)</option></Select></Field>
            <Field label="Karakter kodlaması"><Select value={form.encoding} disabled={form.format !== "csv"} onChange={(e) => setForm({ ...form, encoding: e.target.value })}><option value="cp1254">Windows-1254 (Türkçe)</option><option value="utf-8-sig">UTF-8</option></Select></Field>
          </div>
          <div className="mt-4 flex items-center gap-3">
            <Button onClick={create} loading={busy} disabled={!form.taxpayer}><FileSpreadsheet className="h-4 w-4" />Onaylı fişleri aktar</Button>
            <span className="text-xs text-ink-500">Aynı fiş ikinci kez aktarılamaz; aktarılan belgeler kilitlenir.</span>
          </div>
          {error && <div className="mt-4"><Alert>{error}</Alert></div>}
          {errors && errors.length > 0 && <ul className="mt-2 space-y-1 text-xs text-red-700">{errors.slice(0, 20).map((e, i) => <li key={i}>• {e.message}{e.source_id ? ` (kayıt ${e.source_id.slice(0, 8)})` : ""}</li>)}</ul>}
        </Card>
      )}
      {result && (
        <Card title={`Aktarım dosyaları — ${result.voucher_count} fiş`} className="mb-6">
          <ul className="divide-y divide-slate-100">
            {result.files.map((f) => (
              <li key={f.id} className="flex items-center justify-between py-2.5 text-sm">
                <span><b className="font-medium">{f.filename}</b> <span className="text-ink-500">· {f.voucher_count} fiş</span></span>
                {f.download_url && <a href={f.download_url}><Button size="sm" variant="secondary"><Download className="h-4 w-4" />İndir</Button></a>}
              </li>))}
          </ul>
          <p className="mt-3 text-xs text-ink-500">{result.files.some((f) => f.filename.startsWith("luca_fatura_listesi")) ? <>Luca: Muhasebe → Fiş İşlemleri → Fatura Aktarım → şablonunuzu seçin → CSV dosyasını yükleyin (dosya başına en fazla 400 satır). Hesap kodlu dosyada Matrah kuralını &quot;Hesap Kodunu Sütundan Al → HesapKodu&quot;, KDV kuralını &quot;→ KDVHesapKodu&quot; yapın; gider hesapları MaliScan&apos;deki gibi gelir.</> : <>Luca: Muhasebe → Fiş İşlemleri → Excel Veri Aktarımı → Ayraç &quot;; (Türkçe)&quot; → Dosya Seç → Yükle → Fiş Kes.</>} İndirme bağlantıları birkaç dakika geçerlidir.</p>
        </Card>
      )}
      <Card title="Aktarım geçmişi">
        {rows.length === 0 ? <Empty icon={<FileSpreadsheet className="h-5 w-5" />} title="Henüz aktarım yok" /> : (
          <Table head={<><Th>Tarih</Th><Th>Mükellef</Th><Th className="text-right">Fiş</Th><Th className="text-right">Dosya</Th><Th>Oluşturan</Th><Th>Durum</Th><Th /></>}>
            {rows.map((r) => (
              <tr key={r.id} className="hover:bg-slate-50">
                <Td>{fmtDateTime(r.created_at)}</Td><Td>{r.taxpayer_title}</Td><Td className="text-right tabular-nums">{r.voucher_count}</Td>
                <Td className="text-right tabular-nums">{r.file_count}</Td><Td>{r.created_by_name ?? "—"}</Td><Td><StatusBadge status={r.status} /></Td>
                <Td><div className="flex gap-1"><Button size="sm" variant="ghost" onClick={() => openExport(r.id)}>Dosyalar</Button>
                  <Button size="sm" variant="ghost" onClick={() => downloadAsInvoiceList(r.id)} title="Aynı fişleri yeniden okutmadan, yukarıda seçili Fatura Aktarım biçiminde (varsayılan: hesap kodlu) tekrar indir"><Download className="h-4 w-4" />Tekrar indir (hesap kodlu)</Button>
                  {can(...MANAGERS) && <Button size="sm" variant="danger" onClick={() => deleteExport(r.id)}><Trash2 className="h-4 w-4" />Sil</Button>}</div></Td>
              </tr>))}
          </Table>
        )}
      </Card>
    </>
  );
}
