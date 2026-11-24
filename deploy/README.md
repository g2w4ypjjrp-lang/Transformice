# transformice.canarslan – VPS kurulumu

Bu klasör, sunucuyu bir Ubuntu 24.04 / Debian 12 VPS'e tek komutla kurar.

## Kurulum

```bash
git clone <bu deponun adresi> transformice && cd transformice
sudo bash deploy/install.sh
```

Script şunları yapar:

1. JDK 25'i kurar (apt'te yoksa Eclipse Temurin'i kurar). Docker yoksa onu da kurar.
2. Rastgele şifreleri bir kez üretir ve yalnızca `/etc/transformice.canarslan/` içinde saklar. Bu klasöre sadece root erişebilir; şifreler kaynak kodda ya da `config/` içinde bulunmaz.
3. MongoDB 7'yi Docker'da, kimlik doğrulama açık şekilde başlatır. Veritabanı yalnızca `127.0.0.1`'i dinler.
4. PSFramework 1.0.2 bağımlılığını kaynaktan derler. Bunun için jitpack gerekmez.
5. Sunucuyu derler ve `/opt/transformice.canarslan/` klasörüne kurar.
6. Servisi `transformice.canarslan` adıyla systemd'ye ekler. Servis ayrıcalıksız `tfm` kullanıcısıyla çalışır.
7. Güvenlik duvarında (ufw) yalnızca SSH ve oyun portlarını açar: 11801, 12801, 13801 ve 14801.

## Windows kurulumu (Windows 11 / Windows Server)

PowerShell'i **Yönetici olarak** açın ve şunu çalıştırın:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/g2w4ypjjrp-lang/Transformice/local-build-setup/deploy/install-windows.ps1 -OutFile $env:TEMP\tfm-install.ps1; powershell -ExecutionPolicy Bypass -File $env:TEMP\tfm-install.ps1
```

Betik winget ile Git, Java 25, MongoDB ve mongosh'u kurar. MongoDB'de kimlik doğrulamayı açar ve veritabanı yalnızca 127.0.0.1'i dinler. Şifreleri `C:\ProgramData\transformice.canarslan\` klasöründe tutar. Sunucuyu derleyip `C:\transformice.canarslan\` klasörüne kurar ve açılışta `LOCAL SERVICE` hesabıyla otomatik başlatır. Güvenlik duvarında yalnızca oyun portlarını açar.

- Günlük: `Get-Content C:\transformice.canarslan\logs\console.log -Tail 30 -Wait`
- Yeniden başlatma: `Stop-ScheduledTask transformice.canarslan; Start-ScheduledTask transformice.canarslan`

## Yönetim (Linux)

```bash
sudo systemctl status transformice.canarslan
```
```bash
sudo journalctl -u transformice.canarslan -f
```
```bash
sudo systemctl restart transformice.canarslan
```

Güncellemek için `git pull` yapıp `sudo bash deploy/install.sh` komutunu tekrar çalıştırın. Mevcut şifreler korunur.

## Ortam değişkenleri

Sunucu bu değişkenleri `/etc/transformice.canarslan/server.env` dosyasından okur:

| Değişken | Açıklama |
|---|---|
| `TFM_DB_HOST`, `TFM_DB_PORT`, `TFM_DB_NAME` | MongoDB adresi |
| `TFM_DB_USERNAME`, `TFM_DB_PASSWORD` | MongoDB uygulama kullanıcısı |
| `TFM_ADMIN_USERNAME`, `TFM_ADMIN_PASSWORD` | Yönetim paneli bilgileri (panel henüz uygulanmadı) |

## Alan adı

`transformice.canarslan.net.tr` alt alan adını kullanmak için DNS'e bir **A kaydı** ekleyin: `transformice.canarslan` → VPS'in IP adresi. Oyun bağlantısı HTTP değil, doğrudan TCP olarak yukarıdaki portlardan yapılır.

## Önemli notlar

- **Oyun istemcisi bu paketle dağıtılmaz.** Depodaki `158.swf` ve Atelier 801'e ait diğer istemci dosyaları telifle korunur. Bunları web sitesinde yayınlamayın.
- Sunucu tarafında hile koruması henüz yok. Peynir ve delik konumlarını istemci bildiriyor. Sunucuyu herkese açmadan önce bu düzeltilmeli.
- Veritabanında harita yok. Normal odalar şimdilik boş "0" haritasını açıyor.
