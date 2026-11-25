// End-to-end smoke test against a running stack (web :3000, API :8000, auth :9999, worker).
//   FAKE_VIDEO=/path/receipt.y4m SHOTS=/tmp/shots node e2e/smoke.mjs
// Uses Chromium's fake camera so the live scanner (detection → auto-capture →
// crop → upload) runs exactly as on a phone.
import { createRequire } from "node:module";
const { chromium } = createRequire(import.meta.url)("playwright"); // pre-installed globally in CI image
import fs from "node:fs";

const BASE = process.env.BASE_URL || "http://localhost:3000";
const SHOTS = process.env.SHOTS || "./e2e-shots";
const VIDEO = process.env.FAKE_VIDEO;
fs.mkdirSync(SHOTS, { recursive: true });

const browser = await chromium.launch({
  args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
    ...(VIDEO ? [`--use-file-for-fake-video-capture=${VIDEO}`] : [])],
});
const ctx = await browser.newContext({ viewport: { width: 1360, height: 900 }, permissions: ["camera"] });
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("console", (m) => m.type() === "error" && !m.text().includes("favicon") && errors.push(m.text()));
const shot = (n) => page.screenshot({ path: `${SHOTS}/${n}.png`, fullPage: true });
const step = (s) => console.log("✓", s);

await page.goto(`${BASE}/`);
await page.waitForSelector("text=Uygulamayı indirin");
await shot("00-landing");
await page.goto(`${BASE}/indir`);
await page.getByRole("button", { name: "iPhone / iPad" }).click();
await page.waitForSelector("text=Ana Ekrana Ekle");
await shot("00-download");
step("landing + download page");

const email = `e2e-${Date.now()}@example.com`;
await page.goto(`${BASE}/register`);
await page.getByLabel("Ad soyad").fill("Ayşe Yılmaz");
await page.getByLabel("E-posta").fill(email);
await page.getByLabel("Şifre").fill("CokGizliSifre123");
await page.locator("input[type=checkbox]").check();
await page.getByRole("button", { name: "Kayıt ol" }).click();
await page.waitForSelector("text=E-postanızı doğrulayın");
step("register");

await page.goto(`${BASE}/login`);
await page.getByLabel("E-posta").fill(email);
await page.getByLabel("Şifre").fill("CokGizliSifre123");
await page.getByRole("button", { name: "Giriş yap" }).click();
await page.waitForURL(/\/onboarding\/?$/);
await shot("01-onboarding");
await page.getByLabel("Ofis adı").fill("Yıldız Mali Müşavirlik");
await page.getByRole("button", { name: /Ofisi oluştur/ }).click();
await page.waitForURL(/\/taxpayers/);
step("login + firm created");

await page.getByLabel("VKN", { exact: true }).fill("1231231233");
await page.getByLabel("Unvan / ad soyad").fill("Deneme Mükellef Ltd. Şti.");
await shot("02-new-taxpayer");
await page.getByRole("button", { name: "Kaydet" }).click();
await page.waitForURL(/\/taxpayers\/view/);
const taxpayerUrl = page.url();
await page.waitForSelector("text=Varsayılan hesaplar");
await shot("03-taxpayer-settings");
await page.getByRole("button", { name: /Hesap planı/ }).click();
await page.waitForSelector("text=Genel Yönetim Giderleri");
step("taxpayer + default chart");

await page.goto(`${BASE}/scan`);
const tpSelect = page.locator("select").filter({ hasText: "Mükellef seçin" });
await tpSelect.selectOption({ index: 1 });
await page.getByRole("button", { name: "Kamerayı başlat" }).click();
await page.waitForSelector("text=belge algılandı", { timeout: 20000 });
await shot("04-scanner-detecting");
const t0 = Date.now();
await page.waitForSelector("text=Fiş okundu", { timeout: 60000 });  // read on the device → ✓
await shot("05-scanner-read");
await page.waitForSelector("text=Okundu ✓", { timeout: 10000 });
step(`live camera: detected, auto-captured, read on device + processed (${Date.now() - t0} ms after detection)`);

await page.goto(`${BASE}/documents`);
await page.locator("tbody").getByText("İnceleme bekliyor").first().waitFor({ timeout: 30000 });
await shot("06-documents");
await page.getByText("ÖRNEK AKARYAKIT LTD.ŞTİ.").first().click();
await page.waitForSelector("text=Yevmiye kaydı");
await page.waitForSelector("text=Borç ve alacak dengeli.");
await shot("07-review");
step("review screen with balanced draft journal");

await page.getByRole("button", { name: "Belge verisi" }).click();
await shot("08-review-data");
await page.getByRole("button", { name: "Yevmiye kaydı" }).click();
// type an account that is not in the chart: it is defined and learned on approval
await page.getByLabel("Gider hesabı").fill("770.20");
await page.waitForSelector("text=Yeni hesap — onaylayınca hesap planına eklenir");
await page.getByRole("button", { name: /Onayla/ }).click();
await page.waitForURL(/documents/);
await page.waitForTimeout(800);
step("approved with new account 770.20");
await page.goto(taxpayerUrl);
await page.getByRole("button", { name: /Hesap planı/ }).click();
await page.waitForSelector("text=770.20");
await page.getByRole("button", { name: /Hesap eşleştirmeleri/ }).click();
await page.locator("td", { hasText: "770.20" }).first().waitFor();
await shot("08b-learned");
step("770.20 added to chart and learned for the supplier");

await page.goto(`${BASE}/exports`);
await page.locator("select").filter({ hasText: "Seçin…" }).selectOption({ index: 1 });
await page.getByRole("button", { name: /Onaylı fişleri aktar/ }).click();
await page.waitForSelector("text=Aktarım dosyaları", { timeout: 15000 });
await shot("09-export");
const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "İndir" }).first().click()]);
const file = `${SHOTS}/${download.suggestedFilename()}`;
await download.saveAs(file);
console.log("downloaded", file, fs.statSync(file).size, "bytes");
step("Luca export downloaded");

await page.goto(`${BASE}/dashboard`);
await page.waitForSelector("text=Mükellef durumu");
await shot("10-dashboard");
await page.setViewportSize({ width: 390, height: 844 });
await page.goto(`${BASE}/scan`);
await page.waitForTimeout(1000);
await shot("11-mobile-scan");
step("dashboard + mobile layout");

await browser.close();
if (errors.length) {
  console.log("BROWSER ERRORS:\n" + errors.join("\n"));
  process.exit(1);
}
console.log("E2E OK");
