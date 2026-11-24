"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useState } from "react";
import { Alert, Button, Field, Input } from "@/components/ui";
import { supabase } from "@/lib/supabase";

function LoginForm() {
  const router = useRouter();
  const next = useSearchParams().get("next") || "/dashboard";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    const { error } = await supabase().auth.signInWithPassword({ email, password });
    setLoading(false);
    if (error) setError(error.message.includes("Email not confirmed") ? "E-posta adresiniz henüz doğrulanmadı." : "E-posta veya şifre hatalı.");
    else router.replace(next.startsWith("/") ? next : "/dashboard");
  }

  return (
    <form onSubmit={submit} className="space-y-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-card">
      <div>
        <h1 className="text-lg font-semibold">Giriş yap</h1>
        <p className="text-sm text-ink-500">MaliScan hesabınızla devam edin.</p>
      </div>
      {error && <Alert>{error}</Alert>}
      <Field label="E-posta"><Input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
      <Field label="Şifre"><Input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
      <Button type="submit" loading={loading} className="w-full">Giriş yap</Button>
      <div className="flex justify-between text-sm">
        <Link href="/forgot-password" className="text-brand-600 hover:underline">Şifremi unuttum</Link>
        <Link href="/register" className="text-brand-600 hover:underline">Hesap oluştur</Link>
      </div>
    </form>
  );
}

export default function LoginPage() {
  return <Suspense><LoginForm /></Suspense>;
}
