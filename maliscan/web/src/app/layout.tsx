import type { Metadata, Viewport } from "next";
import { SessionProvider } from "@/lib/session";
import { ServiceWorker } from "@/components/ServiceWorker";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "MaliScan AI", template: "%s · MaliScan AI" },
  description: "Mali müşavirler için fiş/fatura tarama, muhasebeleştirme ve Luca aktarımı",
  manifest: "/manifest.webmanifest",
  appleWebApp: { capable: true, title: "MaliScan", statusBarStyle: "default" },
  icons: { apple: "/icons/apple-touch-icon.png" },
};

export const viewport: Viewport = { themeColor: "#2563eb", width: "device-width", initialScale: 1, viewportFit: "cover" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="tr">
      <body>
        <SessionProvider>{children}</SessionProvider>
        <ServiceWorker />
      </body>
    </html>
  );
}
