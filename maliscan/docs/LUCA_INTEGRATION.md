# Luca Entegrasyonu — Teknik Araştırma Raporu ve Uygulama

**Durum:** ⚠️ **DOĞRULANMAMIŞ.** MaliScan'in ürettiği Luca aktarım dosyaları
henüz gerçek bir Luca ortamına yüklenmedi. Resmî "Şablon İndir" dosyasına da
erişilemedi. Pilot ofiste aşağıdaki §6 testleri başarıyla tamamlanmadan bu
entegrasyonu "çalışıyor" diye sunmayın.

Araştırma tarihi: 24.11.2026

---

## 1. Kaynaklar ve güvenilirlik

| # | Kaynak | Tür | Ne söylüyor |
|---|---|---|---|
| S1 | [Luca Destek — 01.02.09 Excel Veri Aktarımı](https://lucayazilim.freshdesk.com/support/solutions/articles/67000267580-01-02-09-excel-veri-aktar-m-) (son değişiklik 05.04.2021) | **Resmî** (Luca'nın destek portalı) | Zorunlu alanlar, 50 fiş sınırı, gruplama kuralı, 400 satır sınırı, hesap kodlarının önceden açılmış olması |
| S2 | S1'e gömülü ekran görüntüsü — örnek dosya Excel'de açık ([kopya](luca_evidence/02_ornek_aktarim_dosyasi.png)) | **Resmî** (2020-10) | A–K kolonları ve sırası |
| S3 | S1'e gömülü ekran görüntüleri — Fiş Aktarımı ekranı ([1](luca_evidence/01_fis_aktarimi_ekrani.png), [3](luca_evidence/03_fis_kes.png), [4](luca_evidence/04_fis_detay.png)) | **Resmî** (2020-10) | "Ayraç: ; (Türkçe)" seçimi, "Şablon İndir" linki, CAPTCHA, Fiş Kes penceresi |
| S4 | [Luca Destek — 01.02.11 Excel Fatura Aktarımı](https://lucayazilim.freshdesk.com/support/solutions/articles/67000271379-01-02-11-excel-fatura-aktar-m-) (2021) | Resmî | *Farklı bir ekran* (kurala dayalı fatura aktarımı, CSV, 400 satır, 15 sütun) — fiş şablonu değil |
| S5 | [Luca — Kayıt Aktarımı sayfası](https://www.luca.com.tr/Sayfa/luca-kayit-aktarimi/64) | Resmî | Program geçişleri için LUCA Asistan; iletişim: aktarim@luca.com.tr. İndirilebilir iki .xlsx yalnızca *müşteri transfer* formları (ETA, Datasoft) — fiş şablonu değil (OpenPyXL ile doğrulandı) |
| S6 | luca.com.tr tanıtım PDF'leri | Resmî (pazarlama) | "Luca Net Web Servis Aktarımları: … Muhasebe Fişi Aktarımları" — Luca **Net** (ticari yazılım) için; herkese açık teknik doküman yok |
| S7 | [LucaNetOne–Luca Entegrasyonu](https://lucayazilim.freshdesk.com/support/solutions/articles/67000754561) | Resmî | Luca'nın kendi ürünü LucaNetOne ile kapalı entegrasyon; üçüncü taraf API'si değil |
| — | fisliyo.com, dcloudsoftware.com, fisle.co vb. | Üçüncü taraf (ticari) | Kullanılmadı. Örn. "14 sütun" iddiası resmî kaynakla çelişiyor; doğrulanamadı |

## 2. Resmî şablona erişim sonucu

**Resmî şablon herkese açık olarak indirilemiyor.** S1 ve S3'e göre şablon,
oturum açılmış Luca içinde *Muhasebe → Fiş İşlemleri → Excel Veri Aktarımı*
ekranındaki **"Şablon İndir"** bağlantısıyla iniyor. luca.com.tr ve destek
portalında doğrudan bir dosya bağlantısı bulunamadı. Luca kullanıcı adı veya
şifresi istenmedi, otomasyonla giriş denenmedi.

Bu nedenle MaliScan'deki şablon (`luca-fis-aktarim-2020-evidence`) **yalnızca
resmî dokümandan yeniden kurulmuştur** ve kodda `verified=False` olarak
işaretlidir. Her dışa aktarımda `TEMPLATE_UNVERIFIED` uyarısı üretilir.

## 3. Şablon analizi (resmî kaynaklara göre)

| Sıra | Kolon başlığı | Zorunlu | Tip | Biçim (MaliScan çıktısı) | Kanıt | Kesinlik |
|---|---|---|---|---|---|---|
| A | Fiş No | **Evet** | metin/sayı | `1`, `2`, … (dosya içinde benzersiz) | S1, S2 | Kolon ✔ |
| B | Fiş Tarihi | **Evet** | tarih | `GG.AA.YYYY` (ör. `03.11.2026`) | S1, S2 | Kolon ✔, biçim ekranda görünen |
| C | Hesap Kodu | **Evet** | metin | `770.01`, `191.108.01` | S1, S2 | ✔ |
| D | Evrak No | Hayır | metin | belge numarası | S2 | Kolon ✔ |
| E | Evrak Tarihi | Hayır | tarih | `GG.AA.YYYY` | S2 | Kolon ✔ |
| F | Açıklama | Hayır | metin | tek satır, ≤250 karakter (Luca sınırı belgelenmemiş) | S2 | Kolon ✔, uzunluk ✘ |
| G | Borç | **Evet** | tutar | `1000,00`; karşı taraf `0` | S1, S2 | Kolon ✔, ayraçlar Türkçe ✔, binlik ayırıcı ✘ |
| H | Alacak | **Evet** | tutar | aynı | S1, S2 | aynı |
| I | Miktar | Hayır | sayı | `0` veya `40` | S2 | Kolon ✔ |
| J | Döviz | Hayır | metin | boş (TL dışı fişler engelli) | S2 | Biçim ✘ |
| K | Kur | Hayır | sayı | boş | S2 | Biçim ✘ |

**Resmî kurallar (S1):**
- Aynı **Fiş No + Fiş Tarihi** satırları tek fişe gruplanır; ikisinden biri farklıysa ayrı fiş olur.
- Bir yüklemede **en fazla 50 fiş**.
- **Mahsup fişi en fazla 400 satır** (açılış/kapanış fişlerinde sınır yok).
- Dosyadaki hesap kodları Luca'da **önceden açılmış** olmalı; yoksa "hesap kodu bulunamadı".
- Yükleme: Dosya Seç → doğrulama kodu (CAPTCHA) → Yükle → kontrol → **Fiş Kes**
  (Fiş Açıklaması, Fiş Tipi: Mahsup/Açılış/Kapanış, Fiş Kodu, Belge Türü kullanıcı tarafından girilir).
- Muhasebeleşmeyen kayıtlar 24 saat içinde silinir (S3).

**Belirsiz / doğrulanması gereken noktalar:**
1. Dosya türü: Ekranda "Ayraç" seçimi olduğu için yüklenen dosya büyük olasılıkla
   ayraçlı metin (CSV). `.xlsx`'in doğrudan kabul edilip edilmediği bilinmiyor.
2. Karakter kodlaması: Türkçe Excel "CSV (Ayraçlarla ayrılmış)" Windows-1254 yazar;
   varsayılan bu seçildi. UTF-8 varyantı da pakette var.
3. Binlik ayırıcı: Ekran görüntüsünde `6.101,00` Excel biçimlendirmesi olarak görünüyor;
   ham dosyada kullanılıp kullanılmadığı bilinmiyor. MaliScan binlik ayırıcı **kullanmaz**.
4. Başlık satırının zorunlu olup olmadığı (ekranda var; MaliScan yazar).
5. 2020 sonrası Luca sürümlerinde kolon eklenip eklenmediği (ör. Masraf Merkezi 2023'te eklendi).
6. Döviz/Kur biçimi.

## 4. Doğrudan API entegrasyonu araştırması

- Luca Mali Müşavir Paketi için **herkese açık resmî bir API dokümantasyonu bulunamadı.**
- Resmî tanıtım materyali "Luca Net Web Servis Aktarımları" ifadesini içeriyor (S6), ancak
  teknik doküman yayımlanmamış. Üçüncü taraf entegratörler erişimin Luca'ya başvuru ile
  verildiğini belirtiyor (doğrulanmadı).
- **Öneri:** aktarim@luca.com.tr ve Luca destek hattı (0312 987 58 22, S1 sayfa altbilgisi)
  üzerinden yazılı olarak (a) güncel fiş aktarım şablonu ve kodlama/biçim kuralları,
  (b) üçüncü taraf yazılımlar için web servis/API erişim koşulları, (c) sözleşme ve
  KVKK veri işleme koşulları talep edilmeli. Resmî yetki ve doküman olmadan doğrudan
  entegrasyon **yapılmayacak**; tarayıcı otomasyonu ve Luca kimlik bilgisi saklama yasak.

## 5. Uygulama (MaliScan)

```
backend/app/services/luca/
├── template.py     # kanıta dayalı şablon + resmî dosyayı OpenPyXL/CSV ile analiz eden from_official_file()
├── models.py       # Voucher, VoucherLine (Decimal), Issue, ExportFile, ExportResult
├── validation.py   # deterministik kontroller
├── service.py      # LucaExportService: doğrulama → sıralama → 50'lik bölme → CSV/XLSX
├── repository.py   # onaylı yevmiyeleri DB'den yükle, kilitle, dışa aktar, durum = exported
└── samples.py      # sentetik senaryolar
backend/scripts/generate_luca_samples.py   # pilot doğrulama paketi üretimi
samples/luca/                               # üretilmiş paket
```

**Kontroller** (hata → hiçbir dosya üretilmez; kısmi paket asla oluşmaz):

| Kod | Kural |
|---|---|
| `UNBALANCED`, `ZERO_TOTAL` | Σborç = Σalacak > 0 (Decimal, kuruş hassasiyetinde) |
| `LINE_BOTH_SIDES`, `LINE_ZERO`, `NEGATIVE_AMOUNT`, `AMOUNT_PRECISION` | Satır tek taraflı, pozitif, en fazla 2 ondalık |
| `TOO_FEW_LINES`, `TOO_MANY_LINES` | ≥2 satır; mahsupta ≤400 satır |
| `ACCOUNT_CODE_FORMAT`, `ACCOUNT_NOT_IN_CHART` | Kod biçimi; mükellefin MaliScan hesap planında aktif ve alt hesap olmalı |
| `DUPLICATE_IN_BATCH` | Aynı fiş iki kez seçilmiş |
| `ALREADY_EXPORTED` | Daha önce aktarılmış (DB durumu + satır kilidi) |
| `DUPLICATE_CONTENT`, `DUPLICATE_OF_EXPORTED` | Aynı tarih + evrak no + satır kümesi (parmak izi). Kullanıcı açıkça onaylarsa (`allowed_duplicate_ids`) geçer |
| `MIXED_TAXPAYERS` | Bir dosya = bir mükellef |
| `CURRENCY_UNSUPPORTED` | Döviz biçimi doğrulanana kadar yalnızca TL |
| `NOT_APPROVED`, `NOT_FOUND` | Yalnızca onaylı, kendi ofisine ait kayıtlar |

**Dosya bölme:** fişler tarih sırasına göre dizilir, en fazla 50'li parçalara bölünür,
bir fiş asla iki dosyaya bölünmez; Fiş No her dosyada 1'den başlar ve `voucher_map`
ile MaliScan kaydına eşlenir. Aynı girdi her zaman aynı dosyayı (SHA-256) üretir.

**Güvenlik:** formül enjeksiyonu (`=`, `+`, `-`, `@`) temizlenir, XLSX hücreleri açıkça
metin tipindedir, satır sonları/ayraçlar tek kolonda kalır; Windows-1254'te olmayan
karakterler harf karşılığına çevrilir ve uyarılır. Yetki: yalnızca owner/admin/accountant;
RLS ile ofisler arası erişim imkânsız. Denetim kaydına yalnızca sayılar yazılır.

**Resmî şablon gelince:** `LucaTemplate.from_official_file("sablon.xlsx", version="…")`
başlık satırını analiz eder, bilinen alanlara eşler (ör. `Borç(TL)`), bilinmeyen kolonları
korur ve boş yazar, zorunlu kolon eksikse reddeder. Böylece kod değişmeden yeni şablon
kullanılabilir.

## 6. Pilot doğrulama protokolü (gerçek Luca'da, ofis personeli tarafından)

Test, **deneme amaçlı bir Luca firmasında** (gerçek mükellef değil), ofisin kendi
yetkili kullanıcısı tarafından Luca arayüzünden elle yapılır. MaliScan Luca'ya bağlanmaz.

1. Excel Veri Aktarımı ekranından **"Şablon İndir"** ile resmî dosyayı indirip MaliScan ekibine iletin
   (yalnızca başlıklı boş şablon; müşteri verisi içermemeli).
2. `samples/luca/acilmasi_gereken_hesaplar.csv` içindeki alt hesapları deneme firmasında açın.
3. `02_tum_senaryolar_tek_dosya_csv/tum_senaryolar_001.csv` dosyasını **Ayraç: ; (Türkçe)** ile yükleyin.
   Beklenen: 8 fiş, hepsi "Dengeli", Türkçe karakterler bozulmamış.
4. Bozuk karakter görülürse `03_kodlama_varyanti_utf8` dosyasını deneyin; sonucu bildirin.
5. `04_bolme_120_fis` içindeki 3 dosyayı sırayla yükleyin (50 + 50 + 20).
6. Tutarları kontrol edin: ör. `kdv_tevkifat_7_10` → 12.000,00 / 12.000,00.
7. Bir fişte Fiş Kes yapın ve muavin/mizan etkisini kontrol edin; sonra deneme kaydını silin.
8. `.xlsx` dosyalarının doğrudan kabul edilip edilmediğini deneyin.
9. Sonuçları (ekran görüntüleri, hata mesajları, Luca sürüm bilgisi) kaydedin.

Ancak 3–7. adımlar başarılı olduğunda ve resmî şablon `from_official_file` ile
yüklendiğinde şablon `verified=True` olarak işaretlenebilir.

## Fatura Listesi CSV (Luca "Fatura Aktarım")

Format `fatura_listesi` writes one row per approved invoice with the header taken
byte-for-byte from a user-supplied Luca sample (`EvrakTarihi;EvrakNo;Açıklama;GenelToplam;Matrah;KDV`,
cp1254, `;`, CRLF). Values: date dd.mm.yyyy, decimal comma, no thousands separator;
KDV = debits on 191.*, Matrah = other debits, GenelToplam = Matrah + KDV; Açıklama =
counterparty name. Max 400 rows per file. The user loads it via Muhasebe → Fiş İşlemleri →
Fatura Aktarım with their own rule definition (kural tanımlama), which assigns the account codes.
**UNVERIFIED**: the sample had a header only, so date/number formatting is an assumption until a
real import succeeds. Direct automatic transfer is not offered: Luca has no public API and
MaliScan never stores Luca credentials or automates the Luca web UI.
