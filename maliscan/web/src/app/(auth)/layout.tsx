import { FileText } from "lucide-react";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <div className="hidden flex-col justify-between bg-white p-12 lg:flex">
        <div className="flex items-center gap-2">
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-brand-600 text-white"><FileText className="h-5 w-5" /></div>
          <span className="text-lg font-semibold">MaliScan <span className="text-brand-600">AI</span></span>
        </div>
        <div className="max-w-md">
          <h2 className="text-3xl font-semibold leading-tight tracking-tight text-ink-900">Fişleri tarayın, kontrol edin, Luca&apos;ya aktarın.</h2>
          <ul className="mt-6 space-y-3 text-sm text-ink-700">
            <li>• Canlı kamera ile otomatik belge yakalama</li>
            <li>• e-Fatura XML&apos;i doğrudan, fişleri yapay zekâ ile okuma</li>
            <li>• Mükellefe özel hesap planı ve kurallarla deterministik yevmiye</li>
            <li>• Yetkili onayı olmadan hiçbir kayıt kesinleşmez</li>
          </ul>
        </div>
        <p className="text-xs text-ink-500">Verileriniz ofisinize özel olarak ayrılır ve şifreli bağlantı ile iletilir.</p>
      </div>
      <div className="flex items-center justify-center bg-canvas p-6">
        <div className="w-full max-w-sm">{children}</div>
      </div>
    </div>
  );
}
