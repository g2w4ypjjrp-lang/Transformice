"use client";

import { Building2, Camera, ChevronDown, FileSpreadsheet, FileText, LayoutDashboard, LogOut, Menu, Settings, Shield, Users, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ReactNode, useState } from "react";
import { ROLE_LABELS } from "@/lib/format";
import { useSession } from "@/lib/session";
import { cn, Spinner } from "./ui";

const NAV = [
  { href: "/dashboard", label: "Genel bakış", icon: LayoutDashboard },
  { href: "/scan", label: "Belge tara", icon: Camera },
  { href: "/documents", label: "Belgeler", icon: FileText },
  { href: "/taxpayers", label: "Mükellefler", icon: Users },
  { href: "/exports", label: "Luca aktarımları", icon: FileSpreadsheet },
  { href: "/settings", label: "Ofis ayarları", icon: Settings },
];

export function Shell({ children }: { children: ReactNode }) {
  const { me, firm, setFirmId, signOut, loading } = useSession();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  if (loading || !me) return <Spinner />;

  const nav = (
    <nav className="flex flex-1 flex-col gap-0.5 px-3">
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = pathname === href || pathname?.startsWith(href + "/");
        return (
          <Link key={href} href={href} onClick={() => setOpen(false)}
            className={cn("flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition",
              active ? "bg-brand-50 text-brand-700" : "text-ink-700 hover:bg-slate-100")}>
            <Icon className="h-4 w-4" />{label}
          </Link>
        );
      })}
      {me.is_super_admin && (
        <Link href="/admin" onClick={() => setOpen(false)}
          className={cn("mt-4 flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium",
            pathname?.startsWith("/admin") ? "bg-brand-50 text-brand-700" : "text-ink-700 hover:bg-slate-100")}>
          <Shield className="h-4 w-4" />Süper yönetici
        </Link>
      )}
    </nav>
  );

  const sidebar = (
    <div className="flex h-full flex-col gap-4 py-5">
      <div className="flex items-center gap-2 px-6">
        <div className="grid h-8 w-8 place-items-center rounded-lg bg-brand-600 text-white"><FileText className="h-4 w-4" /></div>
        <span className="text-base font-semibold tracking-tight">MaliScan <span className="text-brand-600">AI</span></span>
      </div>
      {me.memberships.length > 0 && (
        <div className="px-3">
          <div className="relative">
            <Building2 className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-ink-400" />
            <select value={firm?.firm_id ?? ""} onChange={(e) => setFirmId(e.target.value)}
              className="w-full appearance-none rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-8 text-sm font-medium text-ink-900">
              {me.memberships.map((m) => <option key={m.firm_id} value={m.firm_id}>{m.firm_name}</option>)}
            </select>
            <ChevronDown className="pointer-events-none absolute right-2.5 top-2.5 h-4 w-4 text-ink-400" />
          </div>
          {firm && <p className="mt-1 px-1 text-xs text-ink-500">Rolünüz: {ROLE_LABELS[firm.role]}</p>}
        </div>
      )}
      {nav}
      <div className="border-t border-slate-100 px-3 pt-3">
        <div className="px-3 pb-2">
          <p className="truncate text-sm font-medium text-ink-900">{me.full_name || me.email}</p>
          <p className="truncate text-xs text-ink-500">{me.email}</p>
        </div>
        <button onClick={signOut} className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm text-ink-700 hover:bg-slate-100">
          <LogOut className="h-4 w-4" />Çıkış yap
        </button>
      </div>
    </div>
  );

  return (
    <div className="min-h-screen bg-canvas">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 border-r border-slate-200 bg-white lg:block">{sidebar}</aside>
      <div className="sticky top-0 z-20 flex items-center justify-between border-b border-slate-200 bg-white px-4 py-3 lg:hidden">
        <button onClick={() => setOpen(true)} aria-label="Menü" className="rounded-md p-1.5 hover:bg-slate-100"><Menu className="h-5 w-5" /></button>
        <span className="text-sm font-semibold">MaliScan <span className="text-brand-600">AI</span></span>
        <Link href="/scan" className="rounded-md bg-brand-600 p-1.5 text-white" aria-label="Tara"><Camera className="h-5 w-5" /></Link>
      </div>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-ink-900/30" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-72 bg-white shadow-xl">
            <button onClick={() => setOpen(false)} className="absolute right-3 top-4 rounded-md p-1 hover:bg-slate-100" aria-label="Kapat"><X className="h-4 w-4" /></button>
            {sidebar}
          </aside>
        </div>
      )}
      <main className="lg:pl-64">
        <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8 lg:py-8">{children}</div>
      </main>
    </div>
  );
}
