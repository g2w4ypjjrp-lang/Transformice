"use client";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { Alert, Button, Field, Input } from "@/components/ui";
import { supabase } from "@/lib/supabase";

export default function ResetPage() {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (password.length < 10) return setError("Şifre en az 10 karakter olmalı.");
    setLoading(true);
    const { error } = await supabase().auth.updateUser({ password });
    setLoading(false);
    if (error) setError("Bağlantının süresi dolmuş olabilir. Yeniden sıfırlama isteyin.");
    else router.replace("/dashboard");
  }
  return (
    <form onSubmit={submit} className="space-y-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-card">
      <h1 className="text-lg font-semibold">Yeni şifre belirleyin</h1>
      {error && <Alert>{error}</Alert>}
      <Field label="Yeni şifre"><Input type="password" required autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
      <Button type="submit" loading={loading} className="w-full">Şifreyi kaydet</Button>
    </form>
  );
}
