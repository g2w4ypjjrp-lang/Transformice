// Writes public/_headers (Cloudflare Pages / Netlify format) with the same
// security headers the Node server sends, using the build-time public URLs.
import fs from "node:fs";

const api = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const supabase = process.env.NEXT_PUBLIC_SUPABASE_URL || "";
const csp = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'",
  "style-src 'self' 'unsafe-inline'",
  `img-src 'self' data: blob: ${api} ${supabase}`,
  "media-src 'self' blob:",
  `connect-src 'self' ${api} ${supabase} ${supabase.replace("https://", "wss://")}`,
  `frame-src 'self' ${api} ${supabase}`,
  "object-src 'none'",
  "base-uri 'self'",
  "frame-ancestors 'none'",
].join("; ");

fs.writeFileSync("public/_headers", `/*
  Content-Security-Policy: ${csp}
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: camera=(self), microphone=(), geolocation=()
  X-Frame-Options: DENY
  Strict-Transport-Security: max-age=63072000; includeSubDomains

/sw.js
  Cache-Control: no-cache

/_next/static/*
  Cache-Control: public, max-age=31536000, immutable
`);
console.log("public/_headers written");
