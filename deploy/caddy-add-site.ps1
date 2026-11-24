# Adds the transformice.canarslan.net.tr static site to an existing Caddy setup (with automatic HTTPS)
# and turns IIS off so it can never take port 80 away from Caddy after a reboot.
# Existing Caddyfile blocks are not touched. Run in an elevated PowerShell:
#   powershell -ExecutionPolicy Bypass -File caddy-add-site.ps1
$ErrorActionPreference = 'Stop'

$Caddy     = 'C:\ATI\araclar\caddy.exe'
$Caddyfile = 'C:\ATI\ayarlar\Caddyfile'
$HostName  = 'transformice.canarslan.net.tr'
$SiteDir   = 'C:\inetpub\transformice.canarslan'
$PageUrl   = 'https://raw.githubusercontent.com/g2w4ypjjrp-lang/Transformice/local-build-setup/web/transformice.canarslan/index.html'
$Begin     = "# >>> $HostName (Transformice landing page; deploy/caddy-add-site.ps1)"
$End       = "# <<< $HostName"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
$utf8 = New-Object Text.UTF8Encoding($false)

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script in PowerShell opened with "Run as administrator".'
}
foreach ($p in $Caddy, $Caddyfile) { if (-not (Test-Path $p)) { throw "Not found: $p" } }

Step '1/5 Updating the site files'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
New-Item -ItemType Directory -Force -Path $SiteDir | Out-Null
Invoke-WebRequest -Uri $PageUrl -OutFile "$SiteDir\index.html" -UseBasicParsing
Remove-Item "$SiteDir\web.config" -ErrorAction SilentlyContinue   # IIS-only file, not needed with Caddy

Step '2/5 Backing up the Caddyfile'
$backup = "$Caddyfile.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
Copy-Item $Caddyfile $backup
Write-Host "    backup: $backup"

Step '3/5 Adding the site block'
$text = [IO.File]::ReadAllText($Caddyfile)
$rootPath = $SiteDir -replace '\\', '/'
$block = @"
$Begin
$HostName {
	encode zstd gzip
	root * $rootPath
	file_server {
		hide web.config
	}
	header {
		X-Content-Type-Options nosniff
		X-Frame-Options DENY
		Referrer-Policy strict-origin-when-cross-origin
		-Server
	}
}
$End
"@
$pattern = '(?s)' + [regex]::Escape("# >>> $HostName") + '.*?' + [regex]::Escape($End)
if ($text -match $pattern) {
    $text = [regex]::Replace($text, $pattern, $block.TrimEnd())
    Write-Host '    existing block replaced'
} else {
    $text = $text.TrimEnd() + "`n`n" + $block.TrimEnd() + "`n"
    Write-Host '    block appended'
}
$text = $text -replace "`r`n", "`n"
[IO.File]::WriteAllText($Caddyfile, $text, $utf8)

Step '4/5 Validating and reloading Caddy (no downtime for the other sites)'
# Caddy writes normal log lines to stderr; Windows PowerShell 5.1 would treat them as errors under 'Stop'.
$ErrorActionPreference = 'Continue'
& $Caddy validate --config $Caddyfile --adapter caddyfile *> "$env:TEMP\caddy-validate.log"
if ($LASTEXITCODE -ne 0) {
    Copy-Item $backup $Caddyfile -Force
    Get-Content "$env:TEMP\caddy-validate.log" -Tail 15
    throw 'The new Caddyfile is not valid. The original file was restored; nothing changed.'
}
& $Caddy reload --config $Caddyfile --adapter caddyfile *> "$env:TEMP\caddy-reload.log"
if ($LASTEXITCODE -ne 0) {
    Copy-Item $backup $Caddyfile -Force
    Get-Content "$env:TEMP\caddy-reload.log" -Tail 15
    throw 'Caddy could not reload the configuration. The original file was restored; the running config is unchanged.'
}
$ErrorActionPreference = 'Stop'
Write-Host '    Caddy reloaded'

Step '5/5 Turning IIS off (it must not take port 80 from Caddy after a reboot)'
$appcmd = "$env:windir\System32\inetsrv\appcmd.exe"
if (Test-Path $appcmd) {
    $ErrorActionPreference = 'Continue'
    & $appcmd delete site /site.name:'transformice.canarslan' *> $null
    $ErrorActionPreference = 'Stop'
}
$w3 = Get-Service W3SVC -ErrorAction SilentlyContinue
if ($w3) {
    Stop-Service W3SVC -Force -ErrorAction SilentlyContinue
    Set-Service W3SVC -StartupType Disabled
    Write-Host '    IIS (W3SVC) stopped and disabled'
}
Get-NetFirewallRule -DisplayName 'Transformice website HTTP' -ErrorAction SilentlyContinue | Remove-NetFirewallRule

Write-Host "`nChecking https://$HostName/ (the certificate can take up to a minute on first use)..." -ForegroundColor Cyan
$ok = $false
for ($i = 0; $i -lt 12; $i++) {
    Start-Sleep -Seconds 5
    try {
        $r = Invoke-WebRequest -Uri "https://$HostName/" -UseBasicParsing -TimeoutSec 15
        if ($r.StatusCode -eq 200 -and $r.Content -match 'Canarslan') { $ok = $true; break }
    } catch { }
}
if ($ok) {
    Write-Host "`nDONE. https://$HostName/ is live." -ForegroundColor Green
} else {
    Write-Warning "The site did not answer over HTTPS yet. If DNS was changed recently, wait a few minutes and open https://$HostName/ in a browser. Caddy log: C:\ATI\loglar"
}
Write-Host "Backup of the previous Caddyfile: $backup"
