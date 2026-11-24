"use client";
import Link from "next/link";
import { FormEvent, useState } from "react";
import { Alert, Button, Field, Input } from "@/components/ui";
import { supabase } from "@/lib/supabase";

export default function ForgotPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [loading, setLoading] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    await supabase().auth.resetPasswordForEmail(email, { redirectTo: `${window.location.origin}/reset-password` });
    setLoading(false);
    setSent(true); // same message whether or not the account exists
  }
  return (
    <form onSubmit={submit} className="space-y-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-card">
      <div><h1 className="text-lg font-semibold">Şifre sıfırlama</h1><p className="text-sm text-ink-500">Sıfırlama bağlantısı e-posta ile gönderilir.</p></div>
      {sent ? <Alert tone="green">Bu adrese kayıtlı bir hesap varsa sıfırlama bağlantısı gönderildi.</Alert> : (
        <>
          <Field label="E-posta"><Input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
          <Button type="submit" loading={loading} className="w-full">Bağlantı gönder</Button>
        </>
      )}
      <Link href="/login" className="block text-center text-sm text-brand-600 hover:underline">Girişe dön</Link>
    </form>
  );
}
