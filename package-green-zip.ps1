# package-green-zip.ps1 — ZIP "green" (portable sin instalador) a partir del
# instalador NSIS YA construido (build.ps1 + tauri build). No reemplaza al NSIS
# ni al updater: el zip NO entra en latest.json; su update es MANUAL.
# Pipeline: silent-install del NSIS a un TEMP (/S /D=) -> validar estructura
# instalada -> staging sin uninstall.exe + README-PORTABLE.txt -> zip con
# carpeta de arriba -> SHA-256 -> desinstalar el TEMP y restaurar
# registro/Start Menu de la maquina que empaqueta.
# Uso:
#   .\package-green-zip.ps1 [-Installer <path del setup.exe>] [-Out <dir>]
# Salida por defecto: src-tauri\target\release\bundle\nsis\ (junto al instalador).

param(
    [string]$Installer = "",
    [string]$Out = ""
)

$ErrorActionPreference = "Stop"
$repo = $PSScriptRoot

# Version/productName/startMenuFolder: fuente tauri.conf.json (la misma que usa
# tauri-bundler para nombrar el instalador y build-latest-json.ps1 para la release).
$conf = Get-Content (Join-Path $repo "src-tauri\tauri.conf.json") -Raw | ConvertFrom-Json
$version = $conf.version
if (-not $version) { throw "version no leida de tauri.conf.json" }
$productName = $conf.productName
$startMenu = $conf.bundle.windows.nsis.startMenuFolder
if (-not $startMenu) { $startMenu = $productName }

# Instalador NSIS x64 de ESTA version (mismo selector que build-latest-json.ps1).
$nsisDir = Join-Path $repo "src-tauri\target\release\bundle\nsis"
$installerPath = if ($Installer) { $Installer } else {
    (Get-ChildItem -Path $nsisDir -Filter "*${version}_x64-setup.exe" -File -ErrorAction SilentlyContinue | Select-Object -First 1).FullName
}
if (-not $installerPath -or -not (Test-Path $installerPath)) {
    throw "instalador no encontrado (correr tauri build primero o pasar -Installer <path>)"
}

$readme = Join-Path $repo "docs\README-PORTABLE.txt"
if (-not (Test-Path $readme)) { throw "no existe ${readme}: el zip green lo incluye como guia de uso" }

# Work dir en TEMP y SIN espacios: /D= de NSIS es mas robusto con rutas simples.
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("glyvex-green-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
$tmpInstall = Join-Path $work "install"
$staging = Join-Path $work "staging"
$backupDir = Join-Path $work "backups"
New-Item -ItemType Directory -Force -Path $work, $staging, $backupDir | Out-Null

# El silent-install escribe en el registro (Uninstall\<productName>) y en el Start
# Menu: sobrescribe la entrada de la instalacion real y su uninstall la BORRA.
# Se respaldan ANTES y se restauran SIEMPRE (finally): empaquetar no debe romper
# el auto-update ni el menu Inicio de esta maquina.
function Get-AppUninstallKeys([string]$displayName) {
    Get-ChildItem "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall" -ErrorAction SilentlyContinue |
        Where-Object { (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).DisplayName -eq $displayName }
}
$regBackups = @()
$smDir = Join-Path $env:APPDATA ("Microsoft\Windows\Start Menu\Programs\" + $startMenu)
$smBackup = Join-Path $backupDir "startmenu"
$smHadBackup = $false

function Get-Sha256Lower([string]$path) {
    # Get-FileHash existe solo desde PowerShell 5: hash por .NET directo para
    # funcionar en WMF viejo (la maquina de release corre PowerShell 4).
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $fs = [System.IO.File]::OpenRead($path)
    try {
        ($sha.ComputeHash($fs) | ForEach-Object { $_.ToString("x2") }) -join ""
    } finally { $fs.Dispose() }
}

try {
    # 1) Backup de lo que el NSIS toca (registro Uninstall + Start Menu) ANTES
    #    de instalar la copia temporal. Backup nativo via provider de registro
    #    (reg.exe escribe directo en la consola y no es silenciable del todo).
    foreach ($k in Get-AppUninstallKeys $productName) {
        $vals = [ordered]@{}
        $ip = Get-ItemProperty -Path $k.PSPath
        foreach ($p in $ip.PSObject.Properties) {
            if ($p.Name -notmatch '^PS') { $vals[$p.Name] = $p.Value }
        }
        $regBackups += @{ Key = $k.PSChildName; Values = $vals }
        Write-Host "    backup registro: $($k.PSChildName) ($($vals.Count) valores)"
    }
    if (Test-Path $smDir) {
        Copy-Item $smDir $smBackup -Recurse
        $smHadBackup = $true
        Write-Host "    backup Start Menu: $smDir"
    }

    # 2) Silent-install al TEMP. /D va ULTIMO (regla NSIS). Layout validado por
    #    probe: <app>.exe + resources/glyvex-backend + _up_/ + uninstall.exe.
    $p = Start-Process -FilePath $installerPath -ArgumentList '/S', "/D=$tmpInstall" -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "silent-install fallo (exit $($p.ExitCode))" }

    # 3) Cordura: la estructura instalada debe estar completa antes de empaquetar.
    $mainExe = Get-ChildItem $tmpInstall -Filter "*.exe" -File |
        Where-Object { $_.Name -ne "uninstall.exe" } | Select-Object -First 1
    if (-not $mainExe) { throw "no hay exe principal en $tmpInstall" }
    if (-not (Test-Path (Join-Path $tmpInstall "resources\glyvex-backend\glyvex-backend.exe"))) {
        throw "falta resources/glyvex-backend en la instalacion temporal"
    }

    # 4) Staging: copia de lo instalado + README-PORTABLE. El uninstall.exe SE
    #    EXCLUYE: una copia green no esta registrada; ejecutar su uninstaller
    #    borraría la entrada de Installaciones de la instalacion real de la PC.
    $zipTop = Join-Path $staging $productName
    Copy-Item $tmpInstall $zipTop -Recurse
    Remove-Item (Join-Path $zipTop "uninstall.exe") -Force
    Copy-Item $readme (Join-Path $zipTop "README-PORTABLE.txt")

    # 5) ZIP con tar.exe (bsdtar, incluido en Windows 10+; mas rapido que
    #    Compress-Archive con cientos de MB). Lleva una carpeta de arriba con el
    #    mismo nombre que la carpeta de instalacion: se extrae tal cual.
    $outDir = if ($Out) { $Out } else { $nsisDir }
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    $zipPath = Join-Path $outDir ("{0}_{1}_windows-x64-green.zip" -f $productName, $version)
    Write-Host "==> Comprimiendo -> $zipPath" -ForegroundColor Cyan
    & tar.exe -a -c -f $zipPath -C $staging $productName
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $zipPath)) { throw "tar.exe fallo (exit $LASTEXITCODE)" }

    $sha = Get-Sha256Lower $zipPath
    $mb = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
    Write-Host "==> ZIP listo: $zipPath ($mb MB)" -ForegroundColor Green
    Write-Host "    SHA-256: $sha"
    Write-Host "    GitHub renombra los espacios del asset a dots al subirlo (gh CLI)."
    Write-Host "    Recordatorio: este zip NO entra en latest.json; su update es MANUAL."
}
finally {
    # 6) Desinstalar la copia temporal (borra su dir, su entrada de registro y su
    #    acceso directo) y restaurar los backups de la instalacion real.
    $un = Join-Path $tmpInstall "uninstall.exe"
    if (Test-Path $un) {
        Start-Process -FilePath $un -ArgumentList '/S' -Wait | Out-Null
    }
    if (Test-Path $tmpInstall) { Remove-Item $tmpInstall -Recurse -Force -ErrorAction SilentlyContinue }
    foreach ($b in $regBackups) {
        $keyPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\" + $b.Key
        if (-not (Test-Path $keyPath)) { New-Item -Path $keyPath -Force | Out-Null }
        foreach ($e in $b.Values.GetEnumerator()) {
            Set-ItemProperty -Path $keyPath -Name $e.Key -Value $e.Value
        }
        Write-Host "    registro restaurado: $($b.Key)"
    }
    if ($smHadBackup) {
        if (Test-Path $smDir) { Copy-Item (Join-Path $smBackup "*") $smDir -Recurse -Force }
        else { Copy-Item $smBackup $smDir -Recurse }
        Write-Host "    Start Menu restaurado"
    } elseif (Test-Path $smDir) {
        # Antes no habia Start Menu: lo que queda es acceso roto de la copia temporal.
        Remove-Item $smDir -Recurse -Force
    }
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
}
