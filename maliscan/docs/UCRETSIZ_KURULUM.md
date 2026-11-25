# Ücretsiz Kurulum — luca.canarslan.net.tr (VPS gerekmez)

Bu rehber MaliScan'i **sunucu kiralamadan**, ücretsiz katmanlarla yayına alır:

| Parça | Servis (ücretsiz katman) | Adres |
|---|---|---|
| Web sitesi + uygulama (PWA) | Cloudflare Pages | `https://luca.canarslan.net.tr` |
| API + belge işleme | Render (Free web service, Docker) | `https://maliscan-api.onrender.com` |
| Giriş, veritabanı, dosyalar | Supabase (Free) | `https://<proje>.supabase.co` |
| Uyanık tutma (isteğe bağlı) | cron-job.org | — |

Telefona kurulum mağazasız ve ücretsizdir: **luca.canarslan.net.tr/indir**
(iPhone: Safari → Paylaş → Ana Ekrana Ekle · Android: Chrome → Uygulamayı yükle).

## Ücretsiz olmayanlar / sınırlar (bilerek seçin)

| Konu | Durum |
|---|---|
| App Store'da yayın | Apple Developer Program **99 $/yıl**. Bu kurulum App Store yerine PWA kullanır (ücretsiz). |
| Google Play'de yayın | 25 $ tek sefer. PWA ile gerek yok. |
| Claude ile otomatik fiş okuma | **Ücretli** (kullanım başına). Anahtar girilmezse sistem **manuel giriş modunda** çalışır: e-Fatura/e-Arşiv XML yine otomatik okunur, fiş verisi görüntüye bakılarak elle girilir. |
| Render Free | 15 dk istek gelmezse uyur, ilk istek ~1 dk sürer; ayda 750 saat; her an yeniden başlatılabilir; disk kalıcı değil (dosyalar bu yüzden Supabase'de). |
| Supabase Free | 500 MB veritabanı, 1 GB dosya; 1 hafta hareketsizlikte proje duraklatılır; **otomatik yedek yok** (aşağıda haftalık yedek adımı var). |
| E-posta | Supabase'in yerleşik e-postası saatlik birkaç gönderimle sınırlıdır; pilot için yeterli, sonra ücretsiz bir SMTP (ör. Brevo) bağlanabilir. |
| Ticari kullanım | Ücretsiz katman koşullarını (özellikle Cloudflare ve Render) kendi kullanımınız için okuyun; müşteri sayısı arttığında ücretli plana geçmek gerekir. |

---

## 1. Supabase (yaklaşık 10 dk)

1. <https://supabase.com> → **New project** → bölge **Central EU (Frankfurt)** → güçlü bir veritabanı şifresi belirleyin ve saklayın.
2. **Authentication → URL Configuration**
   * Site URL: `https://luca.canarslan.net.tr`
   * Redirect URLs: `https://luca.canarslan.net.tr/onboarding/` ve `https://luca.canarslan.net.tr/reset-password/`
3. **Authentication → Sign In / Providers → Email**: "Confirm email" açık kalsın.
4. **Project Settings → API Keys**: şu değerleri not alın
   * Project URL → `SUPABASE_URL` ve `NEXT_PUBLIC_SUPABASE_URL`
   * `anon` / publishable key → `NEXT_PUBLIC_SUPABASE_ANON_KEY` (herkese açık olabilir)
   * `service_role` / secret key → `SUPABASE_SERVICE_ROLE_KEY` (**gizli**, yalnızca Render'a)
   * (Varsa) Legacy JWT secret → `SUPABASE_JWT_SECRET`. Yoksa boş bırakın; API imzayı otomatik doğrular (JWKS).
5. **Connect** (üstteki düğme) → **Session pooler** bağlantı dizesini kopyalayın (Render IPv4 kullandığı için "Direct connection" değil, **Session pooler**). Başını `postgresql+psycopg://` yapıp sonuna `?sslmode=require` ekleyin:
   ```
   postgresql+psycopg://postgres.<proje-ref>:<ŞİFRE>@aws-0-eu-central-1.pooler.supabase.com:5432/postgres?sslmode=require
   ```
   Bu `DATABASE_URL` olacak.
6. Dosya kovaları (`documents`, `exports`) API ilk açıldığında **gizli** olarak otomatik oluşturulur; elle bir şey yapmayın.

## 2. Render — API (yaklaşık 10 dk)

1. <https://render.com> → GitHub ile giriş → **New → Blueprint** → bu depoyu seçin
   (dal: `claude/quirky-tesla-s1xcqu` veya birleştirdiğiniz ana dal). Depo kökündeki `render.yaml` okunur.
2. Sorulan gizli değerleri girin: `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
   `SUPABASE_JWT_SECRET` (varsa), `ANTHROPIC_API_KEY` (**boş bırakırsanız manuel mod**).
3. **Apply**. İlk derleme birkaç dakika sürer. Veritabanı tabloları açılışta otomatik kurulur.
4. Kontrol: tarayıcıda `https://maliscan-api.onrender.com/api/v1/health/ready` →
   `{"status":"ready","schema_version":"0006"}` görmelisiniz. (Servis adı alınmışsa Render farklı bir adres verir; aşağıda o adresi kullanın.)

## 3. Cloudflare Pages — web sitesi (yaklaşık 10 dk)

1. <https://dash.cloudflare.com> → **Workers & Pages → Create → Pages → Connect to Git** → bu depo.
2. Derleme ayarları:
   * Production branch: Render'da seçtiğiniz dal
   * Root directory: `maliscan/web`
   * Build command: `npm ci && npm run build:static`
   * Build output directory: `out`
3. **Environment variables** (Production):
   | Ad | Değer |
   |---|---|
   | `NODE_VERSION` | `22` |
   | `NEXT_PUBLIC_API_URL` | `https://maliscan-api.onrender.com` |
   | `NEXT_PUBLIC_SUPABASE_URL` | Supabase Project URL |
   | `NEXT_PUBLIC_SUPABASE_ANON_KEY` | anon / publishable key |
4. **Save and Deploy**. Site önce `https://maliscan.pages.dev` adresinde açılır.

## 4. Alan adı: luca.canarslan.net.tr

1. Cloudflare Pages projesi → **Custom domains → Set up a domain** → `luca.canarslan.net.tr`.
2. `canarslan.net.tr` alan adınızın DNS'ini nerede yönetiyorsanız (alan adı firmanızın paneli veya Cloudflare) şu kaydı ekleyin:
   | Tür | Ad | Değer |
   |---|---|---|
   | CNAME | `luca` | `maliscan.pages.dev` |
3. Doğrulama ve SSL sertifikası otomatik tamamlanır (birkaç dakika – birkaç saat).
4. Render'da `CORS_ORIGINS` zaten `["https://luca.canarslan.net.tr"]` olarak ayarlı. İlk denemeyi
   `pages.dev` adresinden yapacaksanız geçici olarak `["https://luca.canarslan.net.tr","https://maliscan.pages.dev"]` yapın.

## 5. İlk kullanıcı ve süper yönetici

1. `https://luca.canarslan.net.tr` → **Ücretsiz başla** → kayıt olun → e-postayı doğrulayın → ofisi oluşturun.
2. Supabase → **SQL Editor**:
   ```sql
   UPDATE public.users SET is_super_admin = true WHERE email = 'sizin@epostaniz.com';
   ```

## 6. Uyanık tutma (önerilir, ücretsiz)

Render 15 dakikada uyur, Supabase 1 hafta hareketsizlikte duraklar. <https://cron-job.org>'da ücretsiz
bir iş oluşturun:

* URL: `https://maliscan-api.onrender.com/api/v1/health/ready`
* Sıklık: **her 10 dakikada bir**

Bu istek veritabanına da dokunduğu için iki servisi de aktif tutar. Tek servis 24 saat açık kalsa
ayda ~744 saat eder; Render'ın 750 saatlik ücretsiz kotasına sığar.

## 7. Haftalık yedek (Supabase Free'de otomatik yedek yok)

Kendi bilgisayarınızda (PostgreSQL istemcisi kurulu) haftada bir:

```bash
export DATABASE_URL='postgresql://postgres.<ref>:<ŞİFRE>@aws-0-eu-central-1.pooler.supabase.com:5432/postgres'
export BACKUP_PASSPHRASE='uzun-gizli-bir-parola' BACKUP_DIR=./yedekler STORAGE_DIR=/yok
sh maliscan/deploy/scripts/backup.sh
```

Dosya yedeği için Supabase → Storage'daki kovaları ayrıca indirin. Geri yükleme:
[BACKUP_RECOVERY.md](BACKUP_RECOVERY.md).

## 8. Kontrol listesi

- [ ] `…/api/v1/health/ready` → `ready`
- [ ] `luca.canarslan.net.tr` açılıyor, kilit simgesi (HTTPS) var
- [ ] Kayıt → e-posta doğrulama → ofis → mükellef ekleme çalışıyor
- [ ] Telefonda `/indir` → uygulama ana ekrana eklendi, kamera izni verildi
- [ ] Bir e-Fatura XML yüklendi → "İnceleme bekliyor" → onay → Luca dosyası indi
- [ ] Bir fiş fotoğrafı yüklendi → (manuel modda) veriler girildi → yevmiye dengeli → onay
- [ ] cron-job.org işi çalışıyor

Sorun olursa: Render → Logs (kayıtlarda yalnızca kimlikler bulunur, müşteri verisi yazılmaz).
