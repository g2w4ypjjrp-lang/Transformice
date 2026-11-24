# Transformice private server installer for Windows (Windows 11 / Windows Server 2022+).
# Run in an elevated PowerShell:
#   powershell -ExecutionPolicy Bypass -File install-windows.ps1
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
# Some VDS networks have broken IPv6; make Java (Gradle, Maven downloads) use IPv4.
$env:JAVA_TOOL_OPTIONS = '-Djava.net.preferIPv4Stack=true'

$App        = 'transformice.canarslan'
$AppDir     = "C:\$App"
$SrcDir     = "$AppDir\src"
$SecretDir  = "$env:ProgramData\$App"
$RepoUrl    = 'https://github.com/g2w4ypjjrp-lang/Transformice.git'
$Branch     = 'local-build-setup'
$GamePorts  = 11801, 12801, 13801, 14801
$DbName     = 'tfmdb69'
$DbUser     = 'tfm'

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Refresh-Path { $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User') }
function New-Secret([int]$bytes) {
    $b = New-Object byte[] $bytes
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    -join ($b | ForEach-Object { '{0:x2}' -f $_ })
}
function Winget-Install($id) {
    Write-Host "    winget: $id"
    winget install --id $id -e --silent --accept-package-agreements --accept-source-agreements --disable-interactivity | Out-Null
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne -1978335189) { throw "winget could not install $id (code $LASTEXITCODE)" }  # -1978335189 = already installed
}
function Invoke-Native([scriptblock]$cmd, [string]$what) {
    & $cmd
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" }
}

function Use-LocalGradle([string]$projectDir) {
    # Downloads the Gradle distribution with PowerShell (with retries) and points the wrapper at the local file,
    # so the build does not depend on Java's own HTTPS download (which can time out on some VDS networks).
    $props = Join-Path $projectDir 'gradle\wrapper\gradle-wrapper.properties'
    $text = Get-Content $props -Raw
    if ($text -notmatch '(?m)^distributionUrl=(.+)$') { throw "distributionUrl not found in $props" }
    $url = $Matches[1].Trim().Replace('\:', ':')
    if ($url -like 'file:*') { return }
    $cache = "$AppDir\cache"
    New-Item -ItemType Directory -Force -Path $cache | Out-Null
    $zip = Join-Path $cache ([IO.Path]::GetFileName($url))
    if (-not (Test-Path $zip)) {
        for ($i = 1; $i -le 4; $i++) {
            try {
                Write-Host "    downloading $url (attempt $i)"
                Invoke-WebRequest -Uri $url -OutFile "$zip.part" -UseBasicParsing -TimeoutSec 600
                Move-Item "$zip.part" $zip -Force
                break
            } catch {
                if ($i -eq 4) { throw "Could not download $url : $_" }
                Start-Sleep -Seconds (5 * $i)
            }
        }
    }
    $local = 'file:///' + ($zip -replace '\\', '/')
    $text = $text -replace '(?m)^distributionUrl=.+$', ("distributionUrl=" + $local.Replace(':', '\:'))
    Set-Content -Path $props -Value $text -Encoding ASCII
}

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script in PowerShell opened with "Run as administrator".'
}
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw 'winget was not found. Install "App Installer" from the Microsoft Store, then run this script again.'
}

Step '1/8 Installing Git, Java 25, MongoDB and mongosh'
Winget-Install 'Git.Git'
try { Winget-Install 'EclipseAdoptium.Temurin.25.JDK' } catch { Write-Warning $_; Winget-Install 'Microsoft.OpenJDK.25' }
Winget-Install 'MongoDB.Server'
Winget-Install 'MongoDB.Shell'
Refresh-Path

$jdk = Get-ChildItem 'C:\Program Files\Eclipse Adoptium', 'C:\Program Files\Microsoft' -Directory -Filter 'jdk-25*' -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
if (-not $jdk) { throw 'Java 25 was not found under C:\Program Files.' }
$env:JAVA_HOME = $jdk.FullName
$Java = Join-Path $jdk.FullName 'bin\java.exe'
$Git = (Get-Command git -ErrorAction SilentlyContinue).Source
if (-not $Git) { $Git = 'C:\Program Files\Git\cmd\git.exe' }
$Mongosh = (Get-Command mongosh -ErrorAction SilentlyContinue).Source
if (-not $Mongosh) {
    $Mongosh = Get-ChildItem "$env:LOCALAPPDATA\Programs\mongosh", 'C:\Program Files\mongosh' -Recurse -Filter 'mongosh.exe' -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $Mongosh) { throw 'mongosh was not found after installation.' }
Write-Host "    Java: $Java"

Step '2/8 Generating secrets (stored only in ProgramData, Administrators/SYSTEM only)'
New-Item -ItemType Directory -Force -Path $SecretDir | Out-Null
$envFile = "$SecretDir\server.env"
$firstInstall = -not (Test-Path $envFile)
if ($firstInstall) {
    $dbPass  = New-Secret 24
    $admPass = New-Secret 16
    @(
        'TFM_DB_HOST=127.0.0.1'
        'TFM_DB_PORT=27017'
        "TFM_DB_NAME=$DbName"
        "TFM_DB_USERNAME=$DbUser"
        "TFM_DB_PASSWORD=$dbPass"
        'TFM_ADMIN_USERNAME=admin'
        "TFM_ADMIN_PASSWORD=$admPass"
    ) | Set-Content -Path $envFile -Encoding ASCII
}
# Lock down the secrets folder: Administrators + SYSTEM full, LOCAL SERVICE (the server account) read-only.
icacls $SecretDir /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' '*S-1-5-19:(OI)(CI)R' | Out-Null
$dbPass = ((Get-Content $envFile | Where-Object { $_ -like 'TFM_DB_PASSWORD=*' }) -split '=', 2)[1]

Step '3/8 Configuring MongoDB (localhost only, authentication on)'
$mongoSvc = Get-Service -Name MongoDB -ErrorAction SilentlyContinue
if (-not $mongoSvc) { throw 'The MongoDB Windows service was not found after installation.' }
$mongoCfg = Get-ChildItem 'C:\Program Files\MongoDB\Server' -Recurse -Filter 'mongod.cfg' | Select-Object -First 1 -ExpandProperty FullName
$cfgText = Get-Content $mongoCfg -Raw
if ($cfgText -notmatch 'bindIp:\s*127\.0\.0\.1') { Write-Warning "MongoDB bindIp is not 127.0.0.1 in $mongoCfg - check it so the database is not reachable from the internet." }
if ($cfgText -notmatch 'authorization:\s*enabled') {
    Start-Service MongoDB
    Start-Sleep -Seconds 3
    # Create the application user while auth is still off (fresh install only).
    $js = "const d = db.getSiblingDB('$DbName'); if (!d.getUser('$DbUser')) { d.createUser({user: '$DbUser', pwd: '$dbPass', roles: [{role: 'readWrite', db: '$DbName'}]}); print('user created'); } else { d.updateUser('$DbUser', {pwd: '$dbPass'}); print('user updated'); }"
    Invoke-Native { & $Mongosh --quiet 'mongodb://127.0.0.1:27017' --eval $js } 'Creating the MongoDB user'
    Add-Content -Path $mongoCfg -Value "`r`nsecurity:`r`n  authorization: enabled`r`n"
    Restart-Service MongoDB
    Start-Sleep -Seconds 3
}
Set-Service MongoDB -StartupType Automatic

Step '4/8 Downloading the server source'
New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
if (Test-Path "$SrcDir\.git") {
    Invoke-Native { & $Git -C $SrcDir fetch -q origin $Branch } 'git fetch'
    Invoke-Native { & $Git -C $SrcDir checkout -q $Branch } 'git checkout'
    Invoke-Native { & $Git -C $SrcDir reset -q --hard "origin/$Branch" } 'git reset'
} else {
    Invoke-Native { & $Git clone -q -b $Branch $RepoUrl $SrcDir } 'git clone'
}

Step '5/8 Building the PSFramework dependency from source'
$psDir = Join-Path $env:TEMP 'PSFramework-build'
if (Test-Path $psDir) { Remove-Item -Recurse -Force $psDir }
Invoke-Native { & $Git clone -q --depth 1 --branch 1.0.2 https://github.com/NoFileFound/PSFramework $psDir } 'Cloning PSFramework'
$gradleFile = Join-Path $psDir 'build.gradle'
$g = Get-Content $gradleFile -Raw
$g = $g.Replace('JavaLanguageVersion.of(22)', 'JavaLanguageVersion.of(25)')
$g = $g.Replace("id 'java-library'", "id 'java-library'`n    id 'maven-publish'")
$g += "`npublishing { publications { maven(MavenPublication) { groupId = 'com.github.NoFileFound'; artifactId = 'PSFramework'; version = '1.0.2'; from components.java } } }`n"
[IO.File]::WriteAllText($gradleFile, $g, (New-Object Text.UTF8Encoding($false)))  # no BOM: Gradle rejects it
Use-LocalGradle $psDir
Push-Location $psDir
try { Invoke-Native { & .\gradlew.bat -q --no-daemon publishToMavenLocal } 'Building PSFramework' } finally { Pop-Location }

Step '6/8 Building the game server'
Use-LocalGradle $SrcDir
Push-Location $SrcDir
try { Invoke-Native { & .\gradlew.bat -q --no-daemon shadowJar } 'Building the server' } finally { Pop-Location }
$jar = Get-ChildItem "$SrcDir\build\libs" -Filter 'Transformice-*.jar' | Select-Object -First 1
Copy-Item $jar.FullName "$AppDir\Transformice.jar" -Force
if (Test-Path "$AppDir\config") { Remove-Item -Recurse -Force "$AppDir\config" }
Copy-Item "$SrcDir\config" "$AppDir\config" -Recurse
New-Item -ItemType Directory -Force -Path "$AppDir\logs" | Out-Null

# Launcher used by the scheduled task: loads secrets into the process environment and starts Java.
# A .cmd file is used so Java's stderr warnings are simply logged instead of stopping PowerShell.
@(
    '@echo off'
    "for /f `"usebackq tokens=1,* delims==`" %%a in (`"$envFile`") do set `"%%a=%%b`""
    "cd /d `"$AppDir`""
    "`"$Java`" --enable-native-access=ALL-UNNAMED -Xms256m -Xmx1g -jar `"$AppDir\Transformice.jar`" < nul >> `"$AppDir\logs\console.log`" 2>&1"
) | Set-Content -Path "$AppDir\start.cmd" -Encoding ASCII

# The server runs as LOCAL SERVICE: read access to the app, write access only to logs.
icacls $AppDir /grant '*S-1-5-19:(OI)(CI)RX' | Out-Null
icacls "$AppDir\logs" /grant '*S-1-5-19:(OI)(CI)M' | Out-Null

Step '7/8 Registering the server to start automatically'
$taskName = $App
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
$action    = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"$AppDir\start.cmd`""
$trigger   = New-ScheduledTaskTrigger -AtStartup
$principalTask = New-ScheduledTaskPrincipal -UserId 'NT AUTHORITY\LOCALSERVICE' -LogonType ServiceAccount
$settings  = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principalTask -Settings $settings | Out-Null
Start-ScheduledTask -TaskName $taskName

Step '8/8 Opening the game ports in Windows Firewall (MongoDB stays closed)'
foreach ($p in $GamePorts) {
    $rule = "Transformice TCP $p"
    if (-not (Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName $rule -Direction Inbound -Protocol TCP -LocalPort $p -Action Allow | Out-Null
    }
}

Write-Host "`nWaiting for the server to start..." -ForegroundColor Cyan
$started = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    if (Test-Path "$AppDir\logs\console.log") {
        if (Select-String -Path "$AppDir\logs\console.log" -Pattern 'The server is started' -Quiet) { $started = $true; break }
    }
}
if ($started) {
    Write-Host "`nDONE. The server is running on ports $($GamePorts -join ', ')." -ForegroundColor Green
} else {
    Write-Warning "The server did not report 'started' within 60 seconds. Last log lines:"
    if (Test-Path "$AppDir\logs\console.log") { Get-Content "$AppDir\logs\console.log" -Tail 25 }
}
Write-Host "Log file : $AppDir\logs\console.log"
Write-Host "Secrets  : $SecretDir (administrators only)"
