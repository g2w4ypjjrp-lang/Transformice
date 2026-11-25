"use client";
import { FileText } from "lucide-react";
import Link from "next/link";
import { InstallApp } from "@/components/InstallApp";

export default function DownloadPage() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas p-4">
      <div className="w-full max-w-md space-y-5">
        <div className="flex items-center justify-center gap-2">
          <div className="grid h-10 w-10 place-items-center rounded-xl bg-brand-600 text-white"><FileText className="h-5 w-5" /></div>
          <span className="text-xl font-semibold">MaliScan <span className="text-brand-600">AI</span></span>
        </div>
        <h1 className="text-center text-lg font-semibold">Uygulamayı ücretsiz yükleyin</h1>
        <InstallApp />
        <p className="text-center text-sm"><Link href="/login" className="text-brand-600 hover:underline">Giriş yap</Link> · <Link href="/register" className="text-brand-600 hover:underline">Hesap oluştur</Link></p>
      </div>
    </div>
  );
}
