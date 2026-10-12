# build-latest-json.ps1 — Genera latest.json para el auto-update de Tauri (T6.1, RT-14 L2).
# Corre DESPUES de `tauri build` firmado (clave en $env:TAURI_SIGNING_PRIVATE_KEY).
# Busca el instalador NSIS x64 + su .sig en src-tauri\target\release\bundle\nsis\
# y OPCIONALMENTE el AppImage Linux + su .sig (bundle\appimage, generado en el
# server con build-linux-appimage.sh y copiado aca) y escribe latest.json
# (un asset que hay que subir a la GitHub Release).
#
# Endpoint prod (tauri.conf.json):
#   https://github.com/facebey/Glyvex-Group-AI-Suite-Open-Source/releases/latest/download/latest.json
# "latest" resuelve a la release marcada "Latest", asi latest.json debe subirse
# como asset en la release actual (mismo nombre de archivo en todas las releases
# para que las versiones viejas siempre apunten a la ultima).
#
# Formato: STATIC con bloque "platforms" (claves OS-ARCH). Un solo archivo sirve
# a Windows Y Linux: el updater toma la entrada de su target
# (windows-x86_64 / linux-x86_64). Tauri valida TODO el archivo, asi cada
# entrada presente debe ser completa (url + signature). Si no hay AppImage
# firmado, la release es solo-Windows (las installs Linux no ven update hasta
# que una release incluya el AppImage).
#
# Subir (manual, hasta que exista CI en T7.4):
#   gh release upload <tag> <path>\latest.json --clobber
#
# Uso:
#   .\build-latest-json.ps1 [-Notes "texto de la release"] [-Out <dir>]
#     [-LinuxBundle <dir con *.AppImage + *.AppImage.sig>]

param(
    [string]$Notes = "",
    [string]$Out = "",
    [string]$LinuxBundle = ""
)

$ErrorActionPreference = "Stop"
$repo = $PSScriptRoot

# Version: fuente tauri.conf.json (es la que usa tauri-bundler en el nombre del exe).
$conf = Get-Content (Join-Path $repo "src-tauri\tauri.conf.json") -Raw | ConvertFrom-Json
$version = $conf.version
if (-not $version) { throw "version no leida de tauri.conf.json" }
$tag = "v$version"

# Instalador NSIS x64 + firma. El .sig es la firma del contenido del .exe.
$nsis = Join-Path $repo "src-tauri\target\release\bundle\nsis"
$exe = Get-ChildItem -Path $nsis -Filter "*${version}_x64-setup.exe" -File -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $exe) { throw "instalador no encontrado en $nsis (correr `tauri build` primero)" }
$sig = Get-ChildItem -Path $nsis -Filter "*${version}_x64-setup.exe.sig" -File -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $sig) { throw ".sig no encontrado para $($exe.Name) (build sin firma?)" }
if ($sig.Name -ne ($exe.Name + ".sig")) { throw "el .sig no corresponde al .exe" }

# signature = contenido LITERAL del .sig (el updater lo verifica contra la pubkey),
# no la URL.
$signature = (Get-Content $sig.FullName -Raw).Trim()

# URL de descarga GitHub: GitHub renombra los espacios del asset a dots al
# subirlos ("Glyvex AI Suite_..." -> "Glyvex.AI.Suite_..."), asi la URL debe
# usar el nombre con dots, no %20 (un %20 da 404 y rompe el auto-update).
function New-GitHubUrl([string]$assetName) {
    $encName = [uri]::EscapeDataString(($assetName -replace ' ', '.'))
    "https://github.com/facebey/Glyvex-Group-AI-Suite-Open-Source/releases/download/$tag/$encName"
}
$winUrl = New-GitHubUrl $exe.Name

# Linux (opcional): el AppImage + su .sig salen del server (build-linux-appimage.sh)
# y se copian a src-tauri\target\release\bundle\appimage\ (o se pasa -LinuxBundle).
# Sin .sig no hay updater funcional para Linux: se omite la entrada y se avisa.
$linuxDir = if ($LinuxBundle) { $LinuxBundle } else { Join-Path $repo "src-tauri\target\release\bundle\appimage" }
$appimage = $null
$linUrl = $null
$linSig = $null
if (Test-Path $linuxDir) {
    $appimage = Get-ChildItem -Path $linuxDir -Filter "*${version}*.AppImage" -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($appimage) {
        $appsig = Join-Path $linuxDir ($appimage.Name + ".sig")
        if (-not (Test-Path $appsig)) { throw ".sig no encontrado para $($appimage.Name): sin firma no hay updater Linux" }
        $linSig = (Get-Content $appsig -Raw).Trim()
        $linUrl = New-GitHubUrl $appimage.Name
    }
}

# Versiones de plugins incluidas en esta build (se leen de Cargo.lock). El
# updater las usa para validar compatibilidad incremental del update.
$lock = Get-Content (Join-Path $repo "src-tauri\Cargo.lock") -Raw
function Get-LockVersion([string]$pkg) {
    $re = [regex]"name = ""$pkg""\r?\n\s*version = ""([^""]+)"""
    $m = $re.Match($lock)
    if ($m.Success) { $m.Groups[1].Value } else { $null }
}
$plugins = @{}
foreach ($p in @("tauri-plugin-updater", "tauri-plugin-process")) {
    $v = Get-LockVersion $p
    if ($v) { $plugins[$p] = $v }
}

if (-not $Notes) { $Notes = "Actualizacion a $version" }

# Formato STATIC: version/notes/pub_date globales + platforms[OS-ARCH].
# El updater valida TODO el archivo: cada plataforma presente debe ser completa.
$platforms = [ordered]@{
    "windows-x86_64" = [ordered]@{ url = $winUrl; signature = $signature }
}
if ($appimage) { $platforms["linux-x86_64"] = [ordered]@{ url = $linUrl; signature = $linSig } }

$latest = [ordered]@{
    version   = $version
    notes     = $Notes
    pub_date  = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    platforms = $platforms
    plugins   = $plugins
}

$json = $latest | ConvertTo-Json -Depth 5
$outDir = if ($Out) { $Out } else { $nsis }
$outFile = Join-Path $outDir "latest.json"
# UTF-8 sin BOM (regla del repo).
[System.IO.File]::WriteAllText($outFile, $json, [System.Text.UTF8Encoding]::new($false))

Write-Host "==> latest.json listo: $outFile" -ForegroundColor Green
Write-Host "    version=$version  tag=$tag"
Write-Host "    windows-x86_64: $winUrl"
Write-Host "    windows sig=$($signature.Substring(0, [math]::Min(48, $signature.Length)))..."
if ($appimage) {
    Write-Host "    linux-x86_64:   $linUrl"
    Write-Host "    linux sig=  $($linSig.Substring(0, [math]::Min(48, $linSig.Length)))..."
} else {
    Write-Host "    linux-x86_64:   OMITIDO (sin AppImage+sig en $linuxDir; usar -LinuxBundle <dir>)" -ForegroundColor Yellow
}
Write-Host "    plugins=$($plugins.Keys -join ', ')"
Write-Host ""
Write-Host "Subir el asset:  gh release upload $tag `"$outFile`" --clobber" -ForegroundColor Yellow
