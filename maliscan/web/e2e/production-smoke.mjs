// Production smoke test (no AI key needed): login with an existing confirmed user,
// create an office + taxpayer, upload a synthetic e-invoice XML and a receipt photo,
// approve the XML, export to Luca and download the file.
//   BASE_URL=https://luca.canarslan.net.tr TEST_EMAIL=... TEST_PASSWORD_FILE=... \
//   XML_FILE=... IMG_FILE=... VKN=... SHOTS=... node e2e/production-smoke.mjs
import { createRequire } from "node:module";
import fs from "node:fs";
const { chromium } = createRequire(import.meta.url)("playwright");

const BASE = process.env.BASE_URL;
const SHOTS = process.env.SHOTS || "./prod-shots";
fs.mkdirSync(SHOTS, { recursive: true });
const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1360, height: 900 } })).newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
const shot = (n) => page.screenshot({ path: `${SHOTS}/${n}.png`, fullPage: true });
const step = (s) => console.log("✓", s);

await page.goto(`${BASE}/login`);
await page.getByLabel("E-posta").fill(process.env.TEST_EMAIL);
await page.getByLabel("Şifre").fill(fs.readFileSync(process.env.TEST_PASSWORD_FILE, "utf8").trim());
await page.getByRole("button", { name: "Giriş yap" }).click();
await page.waitForURL(/\/(onboarding|dashboard)\/?/, { timeout: 120000 });   // API may be waking up
await page.waitForTimeout(5000); // the session provider may still redirect to /onboarding
step("login (real Supabase Auth)");

if (page.url().includes("onboarding")) {
  await page.getByLabel("Ofis adı").fill("MaliScan Canlı Test Ofisi");
  await page.getByRole("button", { name: /Ofisi oluştur/ }).click();
  await page.waitForURL(/\/taxpayers/, { timeout: 60000 });
}
await page.goto(`${BASE}/taxpayers`);
await page.waitForSelector("text=Mükellefler");
await page.waitForTimeout(3000);
if (await page.getByText("Henüz mükellef eklenmedi").count()) {
  await page.getByRole("button", { name: "Mükellef ekle" }).click();
  await page.getByLabel("VKN", { exact: true }).fill(process.env.VKN);
  await page.getByLabel("Unvan / ad soyad").fill("Canlı Test Mükellef Ltd. Şti.");
  await page.getByRole("button", { name: "Kaydet" }).click();
  await page.waitForURL(/\/taxpayers\/view/, { timeout: 60000 });
}
step("office + taxpayer");

await page.goto(`${BASE}/scan`);
await page.locator("select").filter({ hasText: "Mükellef seçin" }).selectOption({ index: 1 });
await page.locator("input[type=file]").setInputFiles([process.env.XML_FILE, process.env.IMG_FILE]);
await page.getByText("Yüklendi — işleniyor").nth(1).waitFor({ timeout: 90000 });
await shot("p1-uploaded");
step("uploads (XML + photo) to Supabase Storage");

await page.goto(`${BASE}/documents`);
for (let i = 0; i < 30; i++) {
  const n = await page.locator("tbody").getByText("İnceleme bekliyor").count();
  if (n >= 2) break;
  await page.waitForTimeout(3000);
  await page.reload();
}
await shot("p2-documents");
step("both documents processed by the embedded worker");

await page.locator("tbody tr").filter({ hasText: "Örnek Kırtasiye" }).first().click();
await page.waitForSelector("text=Borç ve alacak dengeli.", { timeout: 30000 });
await shot("p3-review-xml");
await page.getByRole("button", { name: /Onayla/ }).click();
await page.waitForTimeout(2500);
step("e-invoice reviewed and approved");

await page.goto(`${BASE}/documents?status=needs_review`);
await page.locator("tbody tr").first().click();
await page.waitForSelector("text=ücretsiz OCR ile otomatik okundu", { timeout: 30000 });
await shot("p4-manual-mode");
step("receipt photo read automatically by free OCR");

await page.goto(`${BASE}/exports`);
await page.locator("select").filter({ hasText: "Seçin…" }).selectOption({ index: 1 });
await page.getByRole("button", { name: /Onaylı fişleri aktar/ }).click();
await page.waitForSelector("text=Aktarım dosyaları", { timeout: 60000 });
const [dl] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "İndir" }).first().click()]);
const file = `${SHOTS}/${dl.suggestedFilename()}`;
await dl.saveAs(file);
await shot("p5-export");
console.log("downloaded", file, fs.statSync(file).size, "bytes");
step("Luca export via Supabase signed URL");

await browser.close();
if (errors.length) { console.log("PAGE ERRORS:\n" + errors.join("\n")); process.exit(1); }
console.log("PRODUCTION SMOKE OK");
