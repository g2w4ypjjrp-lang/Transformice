# Publishes the transformice.canarslan landing page on this Windows machine with IIS.
# Run in an elevated PowerShell:
#   powershell -ExecutionPolicy Bypass -File install-website.ps1
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$SiteName = 'transformice.canarslan'
$HostName = 'transformice.canarslan.net.tr'
$SiteDir  = "C:\inetpub\$SiteName"
$PageUrl  = 'https://raw.githubusercontent.com/g2w4ypjjrp-lang/Transformice/local-build-setup/web/transformice.canarslan/index.html'

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script in PowerShell opened with "Run as administrator".'
}

Step '1/4 Enabling IIS (static files only)'
$features = 'IIS-WebServerRole', 'IIS-WebServer', 'IIS-CommonHttpFeatures', 'IIS-StaticContent', 'IIS-DefaultDocument', 'IIS-HttpErrors', 'IIS-HttpCompressionStatic', 'IIS-RequestFiltering'
foreach ($f in $features) {
    $state = (Get-WindowsOptionalFeature -Online -FeatureName $f).State
    if ($state -ne 'Enabled') {
        Write-Host "    enabling $f"
        Enable-WindowsOptionalFeature -Online -FeatureName $f -All -NoRestart | Out-Null
    }
}
$appcmd = "$env:windir\System32\inetsrv\appcmd.exe"
if (-not (Test-Path $appcmd)) { throw 'IIS was not installed correctly (appcmd.exe missing). Restart Windows and run this script again.' }

Step '2/4 Downloading the site files'
New-Item -ItemType Directory -Force -Path $SiteDir | Out-Null
Invoke-WebRequest -Uri $PageUrl -OutFile "$SiteDir\index.html" -UseBasicParsing
# Basic security headers; directory listing stays off (IIS default).
@'
<?xml version="1.0" encoding="UTF-8"?>
<configuration>
  <system.webServer>
    <defaultDocument enabled="true">
      <files><clear /><add value="index.html" /></files>
    </defaultDocument>
    <httpProtocol>
      <customHeaders>
        <remove name="X-Powered-By" />
        <add name="X-Content-Type-Options" value="nosniff" />
        <add name="X-Frame-Options" value="DENY" />
        <add name="Referrer-Policy" value="strict-origin-when-cross-origin" />
        <add name="Content-Security-Policy" value="default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'" />
      </customHeaders>
    </httpProtocol>
  </system.webServer>
</configuration>
'@ | Set-Content -Path "$SiteDir\web.config" -Encoding UTF8

Step '3/4 Creating the IIS site'
# Stop the IIS sample site so this site also answers on the plain IP address.
$ErrorActionPreference = 'Continue'   # appcmd reports "not found" as an error; that is fine here
& $appcmd stop site /site.name:"Default Web Site" *> $null
& $appcmd list site /name:$SiteName *> $null
if ($LASTEXITCODE -eq 0) { & $appcmd delete site /site.name:$SiteName *> $null }
$ErrorActionPreference = 'Stop'
& $appcmd add site /name:$SiteName /physicalPath:$SiteDir /bindings:"http/*:80:$HostName,http/*:80:" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not create the IIS site.' }
& $appcmd start site /site.name:$SiteName | Out-Null

Step '4/4 Opening port 80 in Windows Firewall'
if (-not (Get-NetFirewallRule -DisplayName 'Transformice website HTTP' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName 'Transformice website HTTP' -Direction Inbound -Protocol TCP -LocalPort 80 -Action Allow | Out-Null
}

try {
    $r = Invoke-WebRequest -Uri 'http://127.0.0.1/' -UseBasicParsing -TimeoutSec 10
    if ($r.StatusCode -eq 200 -and $r.Content -match 'Canarslan') {
        Write-Host "`nDONE. The site is running: http://$HostName/  (also http://<this server's IP>/)" -ForegroundColor Green
    } else {
        Write-Warning "IIS answered with status $($r.StatusCode) but the page content was unexpected."
    }
} catch {
    Write-Warning "Local test request failed: $_"
}
Write-Host "Site folder: $SiteDir"
