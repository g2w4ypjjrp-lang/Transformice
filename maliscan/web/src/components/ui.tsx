"use client";

import { Loader2, X } from "lucide-react";
import { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, useEffect } from "react";
import { STATUS_LABELS } from "@/lib/format";

export function cn(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(" ");
}

type Variant = "primary" | "secondary" | "ghost" | "danger" | "success";
const VARIANTS: Record<Variant, string> = {
  primary: "bg-brand-600 text-white hover:bg-brand-700 shadow-sm",
  secondary: "bg-white text-ink-900 border border-slate-200 hover:bg-slate-50 shadow-sm",
  ghost: "text-ink-700 hover:bg-slate-100",
  danger: "bg-white text-red-600 border border-red-200 hover:bg-red-50",
  success: "bg-accent-600 text-white hover:bg-accent-700 shadow-sm",
};

export function Button({ variant = "primary", loading, className, children, size = "md", ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; loading?: boolean; size?: "sm" | "md" }) {
  return (
    <button
      {...rest}
      disabled={rest.disabled || loading}
      className={cn("inline-flex items-center justify-center gap-2 rounded-lg font-medium transition disabled:opacity-50 disabled:cursor-not-allowed focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500",
        size === "sm" ? "px-3 py-1.5 text-sm" : "px-4 py-2 text-sm", VARIANTS[variant], className)}>
      {loading && <Loader2 className="h-4 w-4 animate-spin" />}
      {children}
    </button>
  );
}

export function Card({ children, className, title, actions }: { children: ReactNode; className?: string; title?: ReactNode; actions?: ReactNode }) {
  return (
    <section className={cn("rounded-xl border border-slate-200 bg-white shadow-card", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-slate-100 px-5 py-3.5">
          <h2 className="text-sm font-semibold text-ink-900">{title}</h2>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  );
}

export function Field({ label, hint, error, children }: { label: string; hint?: string; error?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-ink-700">{label}</span>
      {children}
      {hint && !error && <span className="mt-1 block text-xs text-ink-500">{hint}</span>}
      {error && <span className="mt-1 block text-xs text-red-600">{error}</span>}
    </label>
  );
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cn("w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-ink-900 placeholder:text-ink-400 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-100", props.className)} />;
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={cn("w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-ink-900 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-100", props.className)} />;
}

const STATUS_STYLES: Record<string, string> = {
  needs_review: "bg-amber-50 text-amber-700 ring-amber-200",
  approved: "bg-accent-50 text-accent-700 ring-accent-100",
  exported: "bg-brand-50 text-brand-700 ring-brand-100",
  completed: "bg-accent-50 text-accent-700 ring-accent-100",
  succeeded: "bg-accent-50 text-accent-700 ring-accent-100",
  failed: "bg-red-50 text-red-700 ring-red-200",
  dead: "bg-red-50 text-red-700 ring-red-200",
  rejected: "bg-slate-100 text-ink-500 ring-slate-200",
  queued: "bg-slate-50 text-ink-700 ring-slate-200",
  processing: "bg-indigo-50 text-indigo-700 ring-indigo-200",
  running: "bg-indigo-50 text-indigo-700 ring-indigo-200",
  draft: "bg-slate-50 text-ink-700 ring-slate-200",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset", STATUS_STYLES[status] ?? "bg-slate-50 text-ink-700 ring-slate-200")}>
      {(status === "processing" || status === "running") && <Loader2 className="h-3 w-3 animate-spin" />}
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}

export function Badge({ children, tone = "slate" }: { children: ReactNode; tone?: "slate" | "amber" | "red" | "green" | "blue" }) {
  const t = { slate: "bg-slate-100 text-ink-700", amber: "bg-amber-50 text-amber-700", red: "bg-red-50 text-red-700", green: "bg-accent-50 text-accent-700", blue: "bg-brand-50 text-brand-700" }[tone];
  return <span className={cn("inline-flex rounded-md px-1.5 py-0.5 text-xs font-medium", t)}>{children}</span>;
}

export function Confidence({ value, threshold = 0.8 }: { value: string | number | null | undefined; threshold?: number }) {
  if (value === null || value === undefined) return <span className="text-ink-400">—</span>;
  const v = Number(value);
  const tone = v >= threshold ? "bg-accent-500" : v >= 0.6 ? "bg-amber-500" : "bg-red-500";
  return (
    <span className="inline-flex items-center gap-2" title={`Güven: %${Math.round(v * 100)}`}>
      <span className="h-1.5 w-14 overflow-hidden rounded-full bg-slate-100"><span className={cn("block h-full", tone)} style={{ width: `${Math.round(v * 100)}%` }} /></span>
      <span className="text-xs tabular-nums text-ink-500">%{Math.round(v * 100)}</span>
    </span>
  );
}

export function Spinner({ label = "Yükleniyor" }: { label?: string }) {
  return <div className="flex items-center justify-center gap-2 py-10 text-sm text-ink-500"><Loader2 className="h-4 w-4 animate-spin" />{label}</div>;
}

export function Empty({ icon, title, text, action }: { icon?: ReactNode; title: string; text?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-12 text-center">
      {icon && <div className="mb-1 rounded-full bg-slate-100 p-3 text-ink-500">{icon}</div>}
      <p className="text-sm font-medium text-ink-900">{title}</p>
      {text && <p className="max-w-sm text-sm text-ink-500">{text}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function Alert({ tone = "red", children }: { tone?: "red" | "amber" | "green" | "blue"; children: ReactNode }) {
  const t = { red: "border-red-200 bg-red-50 text-red-800", amber: "border-amber-200 bg-amber-50 text-amber-900", green: "border-accent-100 bg-accent-50 text-accent-700", blue: "border-brand-100 bg-brand-50 text-brand-900" }[tone];
  return <div className={cn("rounded-lg border px-3 py-2 text-sm", t)}>{children}</div>;
}

export function Modal({ open, onClose, title, children, footer, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-ink-900/40 p-0 sm:items-center sm:p-4" onClick={onClose}>
      <div role="dialog" aria-modal="true" className={cn("max-h-[92vh] w-full overflow-auto rounded-t-2xl bg-white shadow-xl sm:rounded-2xl", wide ? "sm:max-w-3xl" : "sm:max-w-lg")} onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3.5">
          <h3 className="text-sm font-semibold">{title}</h3>
          <button onClick={onClose} className="rounded-md p-1 text-ink-500 hover:bg-slate-100" aria-label="Kapat"><X className="h-4 w-4" /></button>
        </div>
        <div className="p-5">{children}</div>
        {footer && <div className="flex justify-end gap-2 border-t border-slate-100 px-5 py-3">{footer}</div>}
      </div>
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink-900">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-ink-500">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Table({ head, children, empty }: { head: ReactNode; children: ReactNode; empty?: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-sm">
        <thead><tr className="border-b border-slate-200 text-left text-xs font-medium uppercase tracking-wide text-ink-500">{head}</tr></thead>
        <tbody className="divide-y divide-slate-100">{children}</tbody>
      </table>
      {empty}
    </div>
  );
}

export const Th = ({ children, className }: { children?: ReactNode; className?: string }) => <th className={cn("whitespace-nowrap px-3 py-2.5 font-medium", className)}>{children}</th>;
export const Td = ({ children, className }: { children?: ReactNode; className?: string }) => <td className={cn("px-3 py-2.5 align-middle text-ink-700", className)}>{children}</td>;
