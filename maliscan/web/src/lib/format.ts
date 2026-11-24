const money = new Intl.NumberFormat("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const fmtMoney = (v: string | number | null | undefined) =>
  v === null || v === undefined || v === "" ? "—" : money.format(Number(v));

export const fmtDate = (v: string | null | undefined) => {
  if (!v) return "—";
  const d = new Date(v.length === 10 ? v + "T00:00:00" : v);
  return d.toLocaleDateString("tr-TR");
};

export const fmtDateTime = (v: string | null | undefined) =>
  v ? new Date(v).toLocaleString("tr-TR", { dateStyle: "short", timeStyle: "short" }) : "—";

export const pct = (v: string | number | null | undefined) =>
  v === null || v === undefined ? "—" : `%${Math.round(Number(v) * 100)}`;

/** Parse Turkish-formatted user input ("1.234,56") into a dot-decimal string. */
export function parseAmount(input: string): string {
  const s = input.trim().replace(/\s/g, "");
  if (!s) return "0";
  const normalised = s.includes(",") ? s.replace(/\./g, "").replace(",", ".") : s;
  const n = Number(normalised);
  return Number.isFinite(n) ? n.toFixed(2) : "0";
}

export const STATUS_LABELS: Record<string, string> = {
  uploaded: "Yüklendi", queued: "Sırada", processing: "İşleniyor", needs_review: "İnceleme bekliyor",
  approved: "Onaylandı", rejected: "Reddedildi", exported: "Luca'ya aktarıldı", failed: "Hata",
  draft: "Taslak", completed: "Tamamlandı", running: "Çalışıyor", dead: "Başarısız", succeeded: "Başarılı",
};

export const DOC_TYPE_LABELS: Record<string, string> = {
  thermal_receipt: "Yazar kasa fişi", retail_receipt: "Perakende satış fişi", purchase_invoice: "Alış faturası",
  sales_invoice: "Satış faturası", e_invoice: "e-Fatura", e_archive_invoice: "e-Arşiv fatura",
  expense_slip: "Gider pusulası", other: "Diğer",
};

export const ROLE_LABELS: Record<string, string> = {
  owner: "Ofis sahibi", admin: "Yönetici", accountant: "Muhasebeci", clerk: "Veri giriş", viewer: "Görüntüleyici",
};
