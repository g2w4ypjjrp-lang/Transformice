/** @type {import('next').NextConfig} */
const api = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const supabase = process.env.NEXT_PUBLIC_SUPABASE_URL || "";
const staticExport = process.env.STATIC_EXPORT === "1";
export const csp = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'",
  "style-src 'self' 'unsafe-inline'",
  `img-src 'self' data: blob: ${api} ${supabase}`,
  `media-src 'self' blob:`,
  `connect-src 'self' ${api} ${supabase} ${supabase.replace("https://", "wss://")}`,
  `frame-src 'self' ${api} ${supabase}`,
  "object-src 'none'",
  "base-uri 'self'",
  "frame-ancestors 'none'",
].join("; ");

const nextConfig = {
  // STATIC_EXPORT=1 → plain files in out/ (Cloudflare Pages, free); otherwise a Node server (Docker).
  output: staticExport ? "export" : "standalone",
  trailingSlash: staticExport,
  images: { unoptimized: true },
  reactStrictMode: true,
  poweredByHeader: false,
  // Static hosting serves headers from public/_headers (scripts/write-headers.mjs).
  ...(staticExport ? {} : { async headers() {
    return [{
      source: "/:path*",
      headers: [
        { key: "Content-Security-Policy", value: csp },
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        { key: "Permissions-Policy", value: "camera=(self), microphone=(), geolocation=()" },
        { key: "X-Frame-Options", value: "DENY" },
      ],
    }];
  } }),
};
export default nextConfig;
