"use client";

import { Plus, Users } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useEffect, useState } from "react";
import { Alert, Badge, Button, Card, Empty, Field, Input, Modal, PageHeader, Select, Spinner, Table, Td, Th } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { BOOKERS, useSession } from "@/lib/session";
import type { Taxpayer } from "@/lib/types";

function TaxpayersInner() {
  const { firm, can } = useSession();
  const router = useRouter();
  const params = useSearchParams();
  const [list, setList] = useState<Taxpayer[] | null>(null);
  const [open, setOpen] = useState(params.get("new") === "1");
  const [form, setForm] = useState({ taxpayer_type: "company", title: "", tax_number: "", tax_office: "", bookkeeping_basis: "balance_sheet", default_payment_method: "cash" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () => firm && api<Taxpayer[]>(`/firms/${firm.firm_id}/taxpayers`).then(setList);
  useEffect(() => { load(); }, [firm]); // eslint-disable-line react-hooks/exhaustive-deps

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!firm) return;
    setBusy(true);
    setError(null);
    try {
      const tp = await api<Taxpayer>(`/firms/${firm.firm_id}/taxpayers`, { method: "POST", json: { ...form, tax_office: form.tax_office || null } });
      router.push(`/taxpayers/${tp.id}${tp.warnings?.length ? "?warn=vkn" : ""}`);
    } catch (e) {
      setError(e instanceof ApiError ? (e.code === "validation_error" ? "Vergi numarasını kontrol edin (VKN 10, TCKN 11 hane)." : e.message) : "Kaydedilemedi");
    } finally {
      setBusy(false);
    }
  }

  if (!firm || !list) return <Spinner />;
  return (
    <>
      <PageHeader title="Mükellefler" subtitle={`${list.length} mükellef`}
        actions={can(...BOOKERS) && <Button onClick={() => setOpen(true)}><Plus className="h-4 w-4" />Mükellef ekle</Button>} />
      <Card>
        {list.length === 0 ? <Empty icon={<Users className="h-5 w-5" />} title="Henüz mükellef eklenmedi" text="Belgeler mükellef bazında işlenir. İlk mükellefinizi ekleyin; standart hesap planı otomatik oluşturulur." /> : (
          <Table head={<><Th>Unvan</Th><Th>VKN/TCKN</Th><Th>Defter</Th><Th>Varsayılan ödeme</Th><Th className="text-right">Belge</Th><Th className="text-right">İnceleme</Th></>}>
            {list.map((t) => (
              <tr key={t.id} className="cursor-pointer hover:bg-slate-50" onClick={() => router.push(`/taxpayers/${t.id}`)}>
                <Td><Link href={`/taxpayers/${t.id}`} className="font-medium text-ink-900">{t.title}</Link></Td>
                <Td className="font-mono text-xs">{t.tax_number}</Td>
                <Td>{t.bookkeeping_basis === "balance_sheet" ? "Bilanço" : "İşletme"}</Td>
                <Td>{{ cash: "Nakit", bank: "Banka", credit_card: "Kredi kartı", supplier_credit: "Cari" }[t.default_payment_method]}</Td>
                <Td className="text-right tabular-nums">{t.stats?.documents ?? 0}</Td>
                <Td className="text-right">{t.stats?.pending_review ? <Badge tone="amber">{t.stats.pending_review}</Badge> : 0}</Td>
              </tr>))}
          </Table>
        )}
      </Card>
      <Modal open={open} onClose={() => setOpen(false)} title="Yeni mükellef"
        footer={<><Button variant="secondary" onClick={() => setOpen(false)}>Vazgeç</Button><Button form="tp-form" type="submit" loading={busy}>Kaydet</Button></>}>
        <form id="tp-form" onSubmit={submit} className="grid gap-3 sm:grid-cols-2">
          {error && <div className="sm:col-span-2"><Alert>{error}</Alert></div>}
          <Field label="Tür"><Select value={form.taxpayer_type} onChange={(e) => setForm({ ...form, taxpayer_type: e.target.value })}><option value="company">Şirket (VKN)</option><option value="individual">Şahıs (TCKN)</option></Select></Field>
          <Field label={form.taxpayer_type === "company" ? "VKN" : "TCKN"}><Input required inputMode="numeric" pattern={form.taxpayer_type === "company" ? "[0-9]{10}" : "[1-9][0-9]{10}"} value={form.tax_number} onChange={(e) => setForm({ ...form, tax_number: e.target.value.trim() })} /></Field>
          <div className="sm:col-span-2"><Field label="Unvan / ad soyad"><Input required minLength={2} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field></div>
          <Field label="Vergi dairesi"><Input value={form.tax_office} onChange={(e) => setForm({ ...form, tax_office: e.target.value })} /></Field>
          <Field label="Defter türü"><Select value={form.bookkeeping_basis} onChange={(e) => setForm({ ...form, bookkeeping_basis: e.target.value })}><option value="balance_sheet">Bilanço esası</option><option value="operating_account">İşletme hesabı esası</option></Select></Field>
          <Field label="Fişlerde varsayılan ödeme"><Select value={form.default_payment_method} onChange={(e) => setForm({ ...form, default_payment_method: e.target.value })}><option value="cash">Nakit (100)</option><option value="bank">Banka (102)</option><option value="credit_card">Kredi kartı (300)</option><option value="supplier_credit">Cari (320)</option></Select></Field>
        </form>
      </Modal>
    </>
  );
}

export default function TaxpayersPage() {
  return <Suspense fallback={<Spinner />}><TaxpayersInner /></Suspense>;
}
