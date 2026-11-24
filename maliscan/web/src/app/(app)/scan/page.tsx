"use client";

import { useEffect, useState } from "react";
import { Scanner } from "@/components/Scanner";
import { Alert, PageHeader, Select, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { useSession, WRITERS } from "@/lib/session";
import type { Taxpayer } from "@/lib/types";

export default function ScanPage() {
  const { firm, can } = useSession();
  const [taxpayers, setTaxpayers] = useState<Taxpayer[] | null>(null);
  const [tp, setTp] = useState<string | null>(null);

  useEffect(() => {
    if (!firm) return;
    api<Taxpayer[]>(`/firms/${firm.firm_id}/taxpayers`).then((list) => {
      setTaxpayers(list);
      const saved = typeof window !== "undefined" ? localStorage.getItem(`maliscan.scan.tp.${firm.firm_id}`) : null;
      setTp(list.find((t) => t.id === saved)?.id ?? (list.length === 1 ? list[0].id : null));
    });
  }, [firm]);

  if (!firm || !taxpayers) return <Spinner />;
  if (!can(...WRITERS)) return <Alert>Belge yüklemek için yetkiniz yok.</Alert>;

  return (
    <>
      <PageHeader title="Belge tara" subtitle="Fiş ve faturaları canlı kamera ile tarayın; belgeler otomatik algılanır, kırpılır ve yüklenir."
        actions={
          <Select value={tp ?? ""} onChange={(e) => { setTp(e.target.value || null); localStorage.setItem(`maliscan.scan.tp.${firm.firm_id}`, e.target.value); }} className="min-w-64">
            <option value="">Mükellef seçin…</option>
            {taxpayers.map((t) => <option key={t.id} value={t.id}>{t.title} · {t.tax_number}</option>)}
          </Select>} />
      {taxpayers.length === 0 && <div className="mb-4"><Alert tone="amber">Önce bir mükellef ekleyin (Mükellefler sayfası).</Alert></div>}
      <Scanner firmId={firm.firm_id} taxpayerId={tp} />
    </>
  );
}
