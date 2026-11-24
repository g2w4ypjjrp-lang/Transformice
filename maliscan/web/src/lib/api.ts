"use client";
import { supabase } from "./supabase";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "") + "/api/v1";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details?: unknown) {
    super(message);
  }
}

async function token(): Promise<string | null> {
  const { data } = await supabase().auth.getSession();
  return data.session?.access_token ?? null;
}

export async function api<T = unknown>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const t = await token();
  if (t) headers.set("Authorization", `Bearer ${t}`);
  let body = init.body;
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }
  const res = await fetch(API_URL + path, { ...init, headers, body, cache: "no-store" });
  if (res.status === 204) return undefined as T;
  const payload = await res.json().catch(() => null);
  if (!res.ok) {
    const err = payload?.error ?? {};
    throw new ApiError(res.status, err.code ?? "error", trError(err.code, err.message ?? res.statusText), err);
  }
  return payload as T;
}

const MESSAGES: Record<string, string> = {
  unauthenticated: "Oturumunuzun süresi doldu, lütfen tekrar giriş yapın.",
  forbidden: "Bu işlem için yetkiniz yok.",
  not_found: "Kayıt bulunamadı.",
  conflict: "Aynı bilgilerle bir kayıt zaten var.",
  duplicate_file: "Bu dosya daha önce yüklenmiş.",
  plan_limit_exceeded: "Abonelik paketinizin limitine ulaştınız.",
  subscription_inactive: "Aboneliğiniz aktif değil (deneme süresi bitmiş olabilir).",
  unsupported_type: "Desteklenmeyen dosya türü. JPEG, PNG, WEBP, PDF veya e-Fatura XML yükleyin.",
  file_too_large: "Dosya çok büyük.",
  unbalanced: "Borç ve alacak toplamları eşit değil.",
  possible_duplicate: "Bu belge başka bir belgenin mükerrer kaydı olabilir.",
  export_validation_failed: "Luca aktarımı doğrulama hataları nedeniyle oluşturulamadı.",
  nothing_to_export: "Aktarılacak onaylı fiş yok.",
  invitation_invalid: "Davet geçersiz, süresi dolmuş veya kullanılmış.",
  invitation_email_mismatch: "Davet farklı bir e-posta adresine gönderilmiş.",
  profile_missing: "Kullanıcı profiliniz henüz oluşturulmadı. Birkaç saniye sonra tekrar deneyin.",
  validation_error: "Girilen bilgileri kontrol edin.",
};

export function trError(code: string | undefined, fallback: string): string {
  return (code && MESSAGES[code]) || fallback;
}

export async function upload<T>(path: string, form: FormData, onProgress?: (p: number) => void): Promise<T> {
  const t = await token();
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", API_URL + path);
    if (t) xhr.setRequestHeader("Authorization", `Bearer ${t}`);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total);
    xhr.onload = () => {
      let payload: any = null;
      try { payload = JSON.parse(xhr.responseText); } catch { /* ignore */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(payload as T);
      else {
        const err = payload?.error ?? {};
        reject(new ApiError(xhr.status, err.code ?? "error", trError(err.code, err.message ?? "Yükleme başarısız")));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "network", "Ağ hatası — bağlantınızı kontrol edin."));
    xhr.send(form);
  });
}
