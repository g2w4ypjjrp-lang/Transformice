"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Alert, Button, Spinner } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { useSession } from "@/lib/session";

export default function InvitePage() {
  return <Suspense fallback={<Spinner />}><Invite /></Suspense>;
}

function Invite() {
  const token = useSearchParams().get("token") ?? "";
  const { session, loading, refreshMe, setFirmId } = useSession();
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (loading) return <Spinner />;
  async function accept() {
    setBusy(true);
    try {
      const r = await api<{ firm_id: string }>("/invitations/accept", { method: "POST", json: { token } });
      await refreshMe();
      setFirmId(r.firm_id);
      router.replace("/dashboard");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Davet kabul edilemedi.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas p-6">
      <div className="w-full max-w-sm space-y-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-card">
        <h1 className="text-lg font-semibold">Ofis daveti</h1>
        {error && <Alert>{error}</Alert>}
        {session ? (
          <Button onClick={accept} loading={busy} className="w-full">Daveti kabul et</Button>
        ) : (
          <p className="text-sm text-ink-700">Daveti kabul etmek için davet edilen e-posta ile{" "}
            <Link className="text-brand-600 hover:underline" href={`/login?next=${encodeURIComponent(`/invite?token=${token}`)}`}>giriş yapın</Link> veya{" "}
            <Link className="text-brand-600 hover:underline" href="/register">hesap oluşturun</Link>.</p>
        )}
      </div>
    </div>
  );
}
