# Luca pilot doğrulama paketi (sentetik veri)

> ⚠️ **DOĞRULANMAMIŞ — GERÇEK LUCA'YA YÜKLENMEDİ.** Bu dosyalar Luca'nın resmî
> destek dokümanındaki kurallardan üretilmiştir; resmî "Şablon İndir" dosyası
> ile karşılaştırılmamıştır. Test protokolü: `docs/LUCA_INTEGRATION.md` §6.
> Tüm veriler kurgusaldır; gerçek mükellef bilgisi içermez.

| Klasör / dosya | İçerik |
|---|---|
| `01_senaryolar_csv/`, `01_senaryolar_xlsx/` | Her senaryo ayrı dosya (CSV: `;`, Windows-1254) |
| `02_tum_senaryolar_tek_dosya_*` | 8 senaryo tek dosyada (8 fiş, 27 satır) |
| `03_kodlama_varyanti_utf8/` | Aynı dosya UTF-8 (BOM) — kodlama testi için |
| `04_bolme_120_fis/` | 120 fiş → 50 + 50 + 20 (otomatik bölme) |
| `05_reddedilen_ornek_dogrulama_raporu.json` | Dengesiz, mükerrer ve hesap planında olmayan kod içeren paketin ret raporu (dosya üretilmez) |
| `acilmasi_gereken_hesaplar.csv` | Yüklemeden önce deneme firmasında açılması gereken alt hesaplar |
| `manifest.json` | SHA-256, fiş/satır sayıları, borç-alacak toplamları, Fiş No → kaynak eşlemesi |

| Senaryo | KDV | Ödeme | Hesaplar |
|---|---|---|---|
| `kdv20_nakit_akaryakit` | %20 | Nakit | 770.01 / 191.20 / 100.01 |
| `coklu_kdv_market` | %1 + %10 + %20 | Nakit | 770.03, 770.02 / 191.01, 191.10, 191.20 / 100.01 |
| `alis_faturasi_veresiye` | %20 | Satıcı (vadeli) | 153.01 (miktar 40) / 191.20 / 320.01 |
| `banka_odemesi` | %20 | Banka | 770.02 / 191.20 / 102.01 |
| `kredi_karti` | %20 | Kredi kartı | 770.04 / 191.20 / 300.01 |
| `kdv_tevkifat_7_10` | %20, 7/10 tevkifat | Satıcı | 770.06 / 191.20 / 360.02 / 320.01 |
| `kkeg_nakit` | indirilmeyen KDV | Nakit | 689.01 / 100.01 |
| `satis_faturasi` | %20 hesaplanan | Alıcı | 120.01 / 600.01 / 391.20 |

Yeniden üretmek için: `cd backend && python -m scripts.generate_luca_samples ../samples/luca`
