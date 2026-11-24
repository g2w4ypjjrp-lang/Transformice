"use client";
import Link from "next/link";
import { FormEvent, useState } from "react";
import { Alert, Button, Field, Input } from "@/components/ui";
import { supabase } from "@/lib/supabase";

export default function RegisterPage() {
  const [form, setForm] = useState({ name: "", email: "", password: "", kvkk: false });
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [loading, setLoading] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (form.password.length < 10) return setError("Şifre en az 10 karakter olmalı.");
    if (!form.kvkk) return setError("Devam etmek için aydınlatma metnini onaylayın.");
    setLoading(true);
    setError(null);
    const { error } = await supabase().auth.signUp({
      email: form.email,
      password: form.password,
      options: { data: { full_name: form.name }, emailRedirectTo: `${window.location.origin}/onboarding` },
    });
    setLoading(false);
    if (error) setError(error.message);
    else setDone(true);
  }

  if (done) return (
    <div className="space-y-3 rounded-2xl border border-slate-200 bg-white p-6 shadow-card">
      <h1 className="text-lg font-semibold">E-postanızı doğrulayın</h1>
      <p className="text-sm text-ink-700"><b>{form.email}</b> adresine bir doğrulama bağlantısı gönderdik. Bağlantıya tıkladıktan sonra ofisinizi oluşturabilirsiniz.</p>
      <Link href="/login" className="text-sm text-brand-600 hover:underline">Giriş sayfasına dön</Link>
    </div>
  );

  return (
    <form onSubmit={submit} className="space-y-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-card">
      <div><h1 className="text-lg font-semibold">Hesap oluştur</h1><p className="text-sm text-ink-500">14 gün ücretsiz deneme.</p></div>
      {error && <Alert>{error}</Alert>}
      <Field label="Ad soyad"><Input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
      <Field label="E-posta"><Input type="email" required autoComplete="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
      <Field label="Şifre" hint="En az 10 karakter"><Input type="password" required autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></Field>
      <label className="flex items-start gap-2 text-xs text-ink-700">
        <input type="checkbox" className="mt-0.5" checked={form.kvkk} onChange={(e) => setForm({ ...form, kvkk: e.target.checked })} />
        <span>Kişisel verilerin işlenmesine ilişkin aydınlatma metnini okudum. Yüklenen belgelerin yapay zekâ hizmet sağlayıcısı tarafından işlenebileceğini biliyorum.</span>
      </label>
      <Button type="submit" loading={loading} className="w-full">Kayıt ol</Button>
      <p className="text-center text-sm">Hesabınız var mı? <Link href="/login" className="text-brand-600 hover:underline">Giriş yapın</Link></p>
    </form>
  );
}
