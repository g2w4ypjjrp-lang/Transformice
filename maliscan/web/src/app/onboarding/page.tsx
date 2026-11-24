"use client";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { Alert, Button, Field, Input, Spinner } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { useSession } from "@/lib/session";

export default function OnboardingPage() {
  const { me, loading, refreshMe, setFirmId, signOut } = useSession();
  const router = useRouter();
  const [name, setName] = useState("");
  const [tax, setTax] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (loading || !me) return <Spinner />;
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const firm = await api<{ id: string }>("/firms", { method: "POST", json: { name, tax_number: tax || null } });
      await refreshMe();
      setFirmId(firm.id);
      router.replace("/taxpayers?new=1");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Ofis oluşturulamadı.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas p-6">
      <form onSubmit={submit} className="w-full max-w-md space-y-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-card">
        <div>
          <h1 className="text-lg font-semibold">Muhasebe ofisinizi oluşturun</h1>
          <p className="text-sm text-ink-500">Bir ofise davet edildiyseniz e-postanızdaki davet bağlantısını kullanın.</p>
        </div>
        {error && <Alert>{error}</Alert>}
        <Field label="Ofis adı"><Input required minLength={2} value={name} onChange={(e) => setName(e.target.value)} placeholder="Örn. Yıldız Mali Müşavirlik" /></Field>
        <Field label="VKN / TCKN (isteğe bağlı)"><Input inputMode="numeric" pattern="[0-9]{10,11}" value={tax} onChange={(e) => setTax(e.target.value)} /></Field>
        <Button type="submit" loading={busy} className="w-full">Ofisi oluştur ve denemeyi başlat</Button>
        <button type="button" onClick={signOut} className="w-full text-center text-sm text-ink-500 hover:underline">Çıkış yap</button>
      </form>
    </div>
  );
}
