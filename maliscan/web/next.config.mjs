/** @type {import('next').NextConfig} */
const api = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const supabase = process.env.NEXT_PUBLIC_SUPABASE_URL || "";
const csp = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
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
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  async headers() {
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
  },
};
export default nextConfig;
