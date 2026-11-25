"use client";

import { CheckCheck, FileText, Search, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { Alert, Badge, Button, Card, Confidence, Empty, Input, PageHeader, Select, Spinner, StatusBadge, Table, Td, Th } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { DOC_TYPE_LABELS, fmtDate, fmtMoney, STATUS_LABELS } from "@/lib/format";
import { BOOKERS, MANAGERS, useSession } from "@/lib/session";
import type { DocumentSummary, Taxpayer } from "@/lib/types";

const PAGE = 50;

function DocumentsInner() {
  const { firm, can } = useSession();
  const params = useSearchParams();
  const router = useRouter();
  const [taxpayers, setTaxpayers] = useState<Taxpayer[]>([]);
  const [data, setData] = useState<{ total: number; items: DocumentSummary[] } | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [q, setQ] = useState(params.get("q") ?? "");
  const status = params.get("status") ?? "";
  const taxpayer = params.get("taxpayer") ?? "";
  const duplicates = params.get("duplicates") === "1";
  const page = Number(params.get("page") ?? "0");

  const setParam = (k: string, v: string) => {
    const p = new URLSearchParams(params.toString());
    if (v) p.set(k, v); else p.delete(k);
    if (k !== "page") p.delete("page");
    router.replace(`/documents?${p.toString()}`);
  };

  const load = useCallback(() => {
    if (!firm) return;
    const p = new URLSearchParams({ limit: String(PAGE), offset: String(page * PAGE) });
    if (status) p.set("status", status);
    if (taxpayer) p.set("taxpayer_id", taxpayer);
    if (duplicates) p.set("duplicates_only", "true");
    if (params.get("q")) p.set("q", params.get("q")!);
    api<{ total: number; items: DocumentSummary[] }>(`/firms/${firm.firm_id}/documents?${p}`).then((d) => { setData(d); setSelected(new Set()); });
  }, [firm, status, taxpayer, duplicates, page, params]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (firm) api<Taxpayer[]>(`/firms/${firm.firm_id}/taxpayers`).then(setTaxpayers); }, [firm]);
  // live refresh while documents are being processed
  useEffect(() => {
    if (!data?.items.some((d) => ["queued", "processing", "uploaded"].includes(d.status))) return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [data, load]);

  async function bulkApprove() {
    if (!firm) return;
    setBusy(true);
    try {
      const r = await api<{ approved: string[]; skipped: { reason: string }[] }>(`/firms/${firm.firm_id}/documents/bulk-approve`,
        { method: "POST", json: { document_ids: Array.from(selected) } });
      setMessage(`${r.approved.length} belge onaylandı${r.skipped.length ? `, ${r.skipped.length} belge atlandı (mükerrer şüphesi, hata veya düşük güven — tek tek inceleyin)` : ""}.`);
      load();
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : "İşlem başarısız");
    } finally {
      setBusy(false);
    }
  }

  async function deleteAll() {
    if (!firm) return;
    const tp = taxpayers.find((t) => t.id === taxpayer);
    const scope = tp ? `"${tp.title}" mükellefinin` : "TÜM mükelleflerin";
    if (!window.confirm(`${scope} bütün belgeleri silinsin mi?\n\nOnaylanmış ve Luca'ya aktarılmış belgeler de silinir. Luca'ya yüklediğiniz fişleri Luca'dan ayrıca silin. Bu işlem geri alınamaz.`)) return;
    setBusy(true);
    try {
      const r = await api<{ deleted: number; kept_locked: number }>(`/firms/${firm.firm_id}/documents/bulk-delete`,
        { method: "POST", json: { taxpayer_id: taxpayer || null } });
      setMessage(`${r.deleted} belge silindi.${r.kept_locked ? ` ${r.kept_locked} Luca'ya aktarılmış belge korundu.` : ""}`);
      load();
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : "Silme başarısız");
    } finally {
      setBusy(false);
    }
  }

  if (!firm) return <Spinner />;
  const reviewable = data?.items.filter((d) => d.status === "needs_review") ?? [];

  return (
    <>
      <PageHeader title="Belgeler" subtitle={data ? `${data.total} belge` : undefined}
        actions={<div className="flex flex-wrap gap-2">
          {can(...BOOKERS) && selected.size > 0 && <Button variant="success" loading={busy} onClick={bulkApprove}><CheckCheck className="h-4 w-4" />Seçilenleri onayla ({selected.size})</Button>}
          {can(...MANAGERS) && !!data?.total && <Button variant="danger" loading={busy} onClick={deleteAll}><Trash2 className="h-4 w-4" />Tüm belgeleri sil</Button>}
        </div>} />
      {message && <div className="mb-4"><Alert tone="blue">{message}</Alert></div>}
      <Card>
        <div className="mb-4 grid gap-3 md:grid-cols-4">
          <form className="relative md:col-span-2" onSubmit={(e) => { e.preventDefault(); setParam("q", q); }}>
            <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-ink-400" />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Satıcı, VKN veya belge no ara" className="pl-9" />
          </form>
          <Select value={taxpayer} onChange={(e) => setParam("taxpayer", e.target.value)}>
            <option value="">Tüm mükellefler</option>
            {taxpayers.map((t) => <option key={t.id} value={t.id}>{t.title}</option>)}
          </Select>
          <Select value={duplicates ? "dup" : status} onChange={(e) => e.target.value === "dup" ? setParam("duplicates", "1") : (setParam("duplicates", ""), setParam("status", e.target.value))}>
            <option value="">Tüm durumlar</option>
            {["needs_review", "queued,processing", "approved", "exported", "failed", "rejected"].map((s) => <option key={s} value={s}>{s.split(",").map((x) => STATUS_LABELS[x]).join(" / ")}</option>)}
            <option value="dup">Olası mükerrerler</option>
          </Select>
        </div>
        {!data ? <Spinner /> : data.items.length === 0 ? (
          <Empty icon={<FileText className="h-5 w-5" />} title="Belge bulunamadı" text="Filtreleri değiştirin veya yeni belge tarayın." action={<Link href="/scan"><Button size="sm">Belge tara</Button></Link>} />
        ) : (
          <Table head={<>
            {can(...BOOKERS) && <Th><input type="checkbox" aria-label="Tümünü seç" checked={reviewable.length > 0 && reviewable.every((d) => selected.has(d.id))}
              onChange={(e) => setSelected(e.target.checked ? new Set(reviewable.map((d) => d.id)) : new Set())} /></Th>}
            <Th>Tarih</Th><Th>Satıcı / Alıcı</Th><Th>Belge</Th><Th>Mükellef</Th><Th className="text-right">Tutar</Th><Th>Güven</Th><Th>Durum</Th></>}>
            {data.items.map((d) => (
              <tr key={d.id} className="cursor-pointer hover:bg-slate-50" onClick={() => router.push(`/documents/view?id=${d.id}`)}>
                {can(...BOOKERS) && <Td><input type="checkbox" disabled={d.status !== "needs_review"} checked={selected.has(d.id)} onClick={(e) => e.stopPropagation()}
                  onChange={(e) => { const s = new Set(selected); e.target.checked ? s.add(d.id) : s.delete(d.id); setSelected(s); }} /></Td>}
                <Td className="whitespace-nowrap tabular-nums">{fmtDate(d.document_date)}</Td>
                <Td><div className="max-w-[220px] truncate font-medium text-ink-900">{d.supplier_name || d.original_filename || "—"}</div><div className="text-xs text-ink-500">{d.supplier_tax_number}</div></Td>
                <Td><div className="text-ink-900">{d.document_number || "—"}</div><div className="text-xs text-ink-500">{d.document_type ? DOC_TYPE_LABELS[d.document_type] : d.file_kind.toUpperCase()}</div></Td>
                <Td className="max-w-[180px] truncate">{d.taxpayer_title}</Td>
                <Td className="whitespace-nowrap text-right font-medium tabular-nums text-ink-900">{fmtMoney(d.total_amount)}</Td>
                <Td><Confidence value={d.overall_confidence} /></Td>
                <Td><div className="flex flex-wrap gap-1"><StatusBadge status={d.status} />{d.duplicate_of && <Badge tone="amber">Mükerrer?</Badge>}</div></Td>
              </tr>
            ))}
          </Table>
        )}
        {data && data.total > PAGE && (
          <div className="mt-4 flex items-center justify-between text-sm">
            <span className="text-ink-500">{page * PAGE + 1}–{Math.min(data.total, (page + 1) * PAGE)} / {data.total}</span>
            <div className="flex gap-2">
              <Button variant="secondary" size="sm" disabled={page === 0} onClick={() => setParam("page", String(page - 1))}>Önceki</Button>
              <Button variant="secondary" size="sm" disabled={(page + 1) * PAGE >= data.total} onClick={() => setParam("page", String(page + 1))}>Sonraki</Button>
            </div>
          </div>
        )}
      </Card>
    </>
  );
}

export default function DocumentsPage() {
  return <Suspense fallback={<Spinner />}><DocumentsInner /></Suspense>;
}
