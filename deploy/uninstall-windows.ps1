# Removes everything the Transformice installers added on Windows:
# Caddy site block (other Caddy sites untouched), game server task, files, secrets, game data,
# firewall rules and IIS. MongoDB is stopped and disabled; Java/MongoDB/Git stay installed
# (commands to remove them are printed at the end).
# Run in an elevated PowerShell:
#   powershell -ExecutionPolicy Bypass -File uninstall-windows.ps1
$ErrorActionPreference = 'Stop'

$App       = 'transformice.canarslan'
$AppDir    = "C:\$App"
$SiteDir   = "C:\inetpub\$App"
$SecretDir = "$env:ProgramData\$App"
$Caddy     = 'C:\ATI\araclar\caddy.exe'
$Caddyfile = 'C:\ATI\ayarlar\Caddyfile'
$HostName  = 'transformice.canarslan.net.tr'

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Run-Quiet([string]$exe, [string[]]$argList, [string]$log) {
    # Start-Process keeps stderr output from aborting the script on Windows PowerShell 5.1.
    $p = Start-Process -FilePath $exe -ArgumentList $argList -NoNewWindow -Wait -PassThru -RedirectStandardOutput "$log.out" -RedirectStandardError $log
    return $p.ExitCode
}

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script in PowerShell opened with "Run as administrator".'
}

Step '1/7 Removing the website from Caddy (other sites keep running)'
if ((Test-Path $Caddy) -and (Test-Path $Caddyfile)) {
    $text = [IO.File]::ReadAllText($Caddyfile)
    $pattern = '(?s)\n*' + [regex]::Escape("# >>> $HostName") + '.*?' + [regex]::Escape("# <<< $HostName") + '[^\n]*\n?'
    if ($text -match $pattern) {
        $backup = "$Caddyfile.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
        Copy-Item $Caddyfile $backup
        $new = ([regex]::Replace($text, $pattern, "`n")).TrimEnd() + "`n"
        [IO.File]::WriteAllText($Caddyfile, $new, (New-Object Text.UTF8Encoding($false)))
        $cfgArgs = @('--config', "`"$Caddyfile`"", '--adapter', 'caddyfile')
        if ((Run-Quiet $Caddy (@('validate') + $cfgArgs) "$env:TEMP\caddy-validate.log") -ne 0 -or
            (Run-Quiet $Caddy (@('reload') + $cfgArgs) "$env:TEMP\caddy-reload.log") -ne 0) {
            Copy-Item $backup $Caddyfile -Force
            Get-Content "$env:TEMP\caddy-validate.log", "$env:TEMP\caddy-reload.log" -Tail 10 -ErrorAction SilentlyContinue
            throw 'Caddy did not accept the change; the Caddyfile was restored. Nothing else was removed.'
        }
        Write-Host "    site block removed, Caddy reloaded (backup: $backup)"
    } else {
        Write-Host '    no transformice block in the Caddyfile'
    }
} else {
    Write-Host '    Caddy not found, skipped'
}

Step '2/7 Stopping and removing the game server task'
$task = Get-ScheduledTask -TaskName $App -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask -TaskName $App -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $App -Confirm:$false
    Write-Host '    scheduled task removed'
}
Get-CimInstance Win32_Process -Filter "Name='java.exe'" |
    Where-Object { $_.CommandLine -like "*$AppDir\Transformice.jar*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host "    stopped server process $($_.ProcessId)" }

Step '3/7 Deleting the game data from MongoDB'
$envFile = "$SecretDir\server.env"
$mongosh = (Get-Command mongosh -ErrorAction SilentlyContinue).Source
if (-not $mongosh) { $mongosh = Get-ChildItem "$env:LOCALAPPDATA\Programs\mongosh", 'C:\Program Files\mongosh' -Recurse -Filter 'mongosh.exe' -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName }
if ((Test-Path $envFile) -and $mongosh -and (Get-Service MongoDB -ErrorAction SilentlyContinue)) {
    $cfg = @{}
    Get-Content $envFile | Where-Object { $_ -match '^[A-Z_]+=' } | ForEach-Object { $k, $v = $_ -split '=', 2; $cfg[$k] = $v }
    Start-Service MongoDB -ErrorAction SilentlyContinue
    $uri = "mongodb://$($cfg.TFM_DB_USERNAME):$($cfg.TFM_DB_PASSWORD)@127.0.0.1:27017/$($cfg.TFM_DB_NAME)"
    $code = Run-Quiet $mongosh @('--quiet', "`"$uri`"", '--eval', '"db.getCollectionNames().forEach(function(c){ db.getCollection(c).drop(); })"') "$env:TEMP\tfm-mongo-drop.log"
    if ($code -eq 0) { Write-Host '    game collections deleted' } else { Write-Warning 'Could not delete the game collections (see %TEMP%\tfm-mongo-drop.log). Continuing.' }
} else {
    Write-Host '    nothing to delete'
}
$mongoSvc = Get-Service MongoDB -ErrorAction SilentlyContinue
if ($mongoSvc) {
    Stop-Service MongoDB -Force -ErrorAction SilentlyContinue
    Set-Service MongoDB -StartupType Disabled
    Write-Host '    MongoDB service stopped and disabled'
}

Step '4/7 Deleting files and secrets'
foreach ($d in $AppDir, $SiteDir, $SecretDir, "$env:USERPROFILE\.m2\repository\com\github\NoFileFound", "$env:TEMP\PSFramework-build") {
    if (Test-Path $d) { Remove-Item -Recurse -Force $d; Write-Host "    deleted $d" }
}
Remove-Item "$env:TEMP\tfm-*.ps1", "$env:TEMP\tfm-mongo-drop.log*" -ErrorAction SilentlyContinue

Step '5/7 Removing firewall rules (game ports 11801-14801 closed)'
Get-NetFirewallRule -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName -like 'Transformice TCP *' -or $_.DisplayName -eq 'Transformice website HTTP' } |
    ForEach-Object { Remove-NetFirewallRule -Name $_.Name; Write-Host "    removed rule: $($_.DisplayName)" }

Step '6/7 Removing IIS (it was enabled only for this site)'
$appcmd = "$env:windir\System32\inetsrv\appcmd.exe"
if (Test-Path $appcmd) {
    Run-Quiet $appcmd @('delete', 'site', "/site.name:$App") "$env:TEMP\tfm-appcmd.log" | Out-Null
}
if ((Get-WindowsOptionalFeature -Online -FeatureName IIS-WebServerRole).State -eq 'Enabled') {
    Disable-WindowsOptionalFeature -Online -FeatureName IIS-WebServerRole -NoRestart | Out-Null
    Write-Host '    IIS disabled (fully removed after the next restart)'
}

Step '7/7 Check'
$left = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in 11801, 12801, 13801, 14801 }
if ($left) { Write-Warning "Something is still listening on a game port: $($left.LocalPort -join ', ')" } else { Write-Host '    game ports are closed' }

Write-Host "`nDONE. Transformice server and website removed." -ForegroundColor Green
Write-Host 'Optional - to also uninstall the tools that were installed for it:'
Write-Host '    winget uninstall --id MongoDB.Server -e'
Write-Host '    winget uninstall --id MongoDB.Shell -e'
Write-Host '    winget uninstall --id EclipseAdoptium.Temurin.25.JDK -e'
