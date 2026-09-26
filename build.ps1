# build.ps1 — Sidecar backend para el empaquetado Tauri (T-1, Fase 1).
# Pipeline: npm run build → PyInstaller (backend/glyvex.spec) → smoke test del exe
# → (opcional) instalador MSI con WiX 3.14 (distribution/wix/glyvex.wxs).
# Uso: .\build.ps1 [-SkipFrontend] [-SkipSmoke] [-SkipSidecar] [-MakeInstaller] [-Version 0.6.0-beta1]
# -SkipSidecar: no correr PyInstaller; usar el bundle ya existente en
# backend\dist\glyvex-backend.

param(
    [switch]$SkipFrontend,
    [switch]$SkipSmoke,
    [switch]$SkipSidecar,
    [switch]$MakeInstaller,
    [string]$Version = "0.6.0-beta1"
)

$ErrorActionPreference = "Stop"
$repo = $PSScriptRoot

$py = "D:\Proyectos\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

Write-Host "==> [1/3] Build frontend" -ForegroundColor Cyan
if ($SkipFrontend) {
    Write-Host "    omitido (-SkipFrontend)"
} else {
    Push-Location (Join-Path $repo "frontend")
    try { npm run build } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw "npm run build falló (exit $LASTEXITCODE)" }
}

if ($SkipSidecar) {
    Write-Host "==> [2/3] Sidecar: omitido (-SkipSidecar, usar bundle existente)" -ForegroundColor Cyan
} else {
    Write-Host "==> [2/3] PyInstaller (onedir)" -ForegroundColor Cyan
    # Los paths relativos del spec se resuelven desde backend/: se corre ahí.
    Push-Location (Join-Path $repo "backend")
    try { & $py -m PyInstaller glyvex.spec --noconfirm } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller falló (exit $LASTEXITCODE)" }
}

$bundle = Join-Path $repo "backend\dist\glyvex-backend"
$exe = Join-Path $bundle "glyvex-backend.exe"
if (-not (Test-Path $exe)) { throw "exe no generado: $exe" }
# babel\locale-data (28 MB, 1000+ .dat por locale; entra via courlan/trafilatura
# solo para formatear fechas): la app trabaja en en/es, el resto se recorta
# post-build. babel sigue importable: Locale cae a fallback si falta un .dat.
$ld = Join-Path $bundle "_internal\babel\locale-data"
if (Test-Path $ld) {
    $keep = @("en.dat","en_US.dat","en_001.dat","es.dat","es_MX.dat","es_419.dat","es_ES.dat")
    $antes = (Get-ChildItem $ld -File | Measure-Object Length -Sum).Sum / 1MB
    Get-ChildItem $ld -File | Where-Object { $keep -notcontains $_.Name } | Remove-Item -Force
    $despues = (Get-ChildItem $ld -File | Measure-Object Length -Sum).Sum / 1MB
    Write-Host "    locale-data babel: $([math]::Round($antes,1)) MB -> $([math]::Round($despues,1)) MB"
}
$mb = [math]::Round((Get-ChildItem $bundle -Recurse -File | Measure-Object Length -Sum).Sum / 1MB)
Write-Host "    bundle: $mb MB ($exe)"

if ($SkipSmoke) {
    Write-Host "==> [3/3] Smoke test: omitido (-SkipSmoke)" -ForegroundColor Cyan
} else {

Write-Host "==> [3/3] Smoke test del exe" -ForegroundColor Cyan
if (Get-NetTCPConnection -LocalPort 7981 -State Listen -ErrorAction SilentlyContinue) {
    throw "puerto 7981 ocupado: detener la instancia en marcha antes de build"
}

# GLYVEX_NO_BROWSER=1: el bundle sin consola abre el navegador cuando la API
# responde; en el pipeline de build eso no se quiere.
$env:GLYVEX_NO_BROWSER = "1"
$p = Start-Process -FilePath $exe -WindowStyle Hidden -PassThru
$ok = $false
$t0 = Get-Date
try {
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        if ($p.HasExited) { break }
        try {
            $r = Invoke-RestMethod "http://127.0.0.1:7981/api/health" -TimeoutSec 2
            if ($r.status -eq "ok") { $ok = $true; break }
        } catch {}
    }
    if ($ok) {
        $spa = (Invoke-WebRequest "http://127.0.0.1:7981/" -TimeoutSec 5).Content
        if ($spa.Length -lt 100) { throw "SPA no servida ($($spa.Length) bytes)" }
        Write-Host "    health OK en $([math]::Round(((Get-Date) - $t0).TotalSeconds, 1))s, SPA $($spa.Length) bytes"
    }
}
finally {
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    Remove-Item Env:GLYVEX_NO_BROWSER -ErrorAction SilentlyContinue
}
    if (-not $ok) { throw "smoke test falló: /api/health no respondió en 60s" }
}

if ($MakeInstaller) {
    Write-Host "==> [4/4] Instalador MSI (WiX 3.14: heat + candle + light)" -ForegroundColor Cyan
    # WiX portable (sin admin): wix314-binaries.zip de wixtoolset/wix3 en wix3/.
    $wix = "D:\Proyectos\glyvex-ai-suite\wix3"
    if (-not (Test-Path (Join-Path $wix "candle.exe"))) { throw "WiX no encontrado en $wix (descargar wix314-binaries.zip de github.com/wixtoolset/wix3)" }
    $wixdir = Join-Path $repo "distribution\wix"
    New-Item -ItemType Directory -Force -Path (Join-Path $repo "distribution\dist") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $wixdir "obj") | Out-Null
    # La version MSI debe tener 4 partes numericas: 0.6.0-beta1 -> 0.6.0.0.
    $pv = $Version -replace '[-+].*$', ''
    while (($pv -split '\.').Count -lt 4) { $pv += ".0" }
    # Re-cosecha el bundle en cada build (files.wxi no se versiona).
    $relBundle = "backend\dist\glyvex-backend"
    $bundleDir = Join-Path $repo $relBundle
    # -dr APPDIR: heat SIEMPRE anida el dir fuente como hijo del -dr, asi el
    # bundle queda en %LOCALAPPDATA%\Glyvex-AI-Suite\glyvex-backend\ (sin un
    # "app" intermedio; el atajo apunta a [APPDIR]glyvex-backend\glyvex-backend.exe)
    & (Join-Path $wix "heat.exe") dir $bundleDir -var "var.SourceDir=$relBundle" -dr APPDIR -cg AppFiles -gg -nologo -out (Join-Path $wixdir "files.wxi")
    if ($LASTEXITCODE -ne 0) { throw "heat falló (exit $LASTEXITCODE)" }
    # heat 3.14 escribe $(var.SourceDir=<rel>) inline; candle no resuelve ese
    # formato. Reemplazarlo por la ruta absoluta del bundle.
    $fwi = Join-Path $wixdir "files.wxi"
    $xml = [System.IO.File]::ReadAllText($fwi)
    $xml = $xml.Replace('$(var.SourceDir=' + $relBundle + ')', $bundleDir)
    # heat anida el dir fuente bajo APPDIR con un Id generado (dir<hash>).
    # Renombrarlo a APPBUNDLE (estable) para que glyvex.wxs lo use en los atajos.
    if ($xml -match '<DirectoryRef Id="APPDIR">\s*<Directory Id="(dir[0-9A-Fa-f]+)"') {
        $xml = $xml.Replace('Id="' + $Matches[1] + '"', 'Id="APPBUNDLE"')
        $xml = $xml.Replace('Directory="' + $Matches[1] + '"', 'Directory="APPBUNDLE"')
    }
    [System.IO.File]::WriteAllText($fwi, $xml)
    # Banner del wizard (WixUI_Banner) e icono (Icon SourceFile) de glyvex.wxs
    # via $(var.*): ambos se resuelven contra el cwd de candle, no el .wxs,
    # asi build.ps1 los pasa con ruta absoluta (por eso la -d va aca, no a light).
    $banner = Join-Path $repo "assets\wixui-banner.bmp"
    if (-not (Test-Path $banner)) { throw "Banner del wizard no encontrado: $banner" }
    $ico = Join-Path $repo "assets\glyvex.ico"
    if (-not (Test-Path $ico)) { throw "Icono no encontrado: $ico" }
    # Licencia (Apache 2.0): LICENSE de la raiz del repo, usado en dos frentes:
    # (a) -dWixLicense (candle): el File Id="License" lo instala en [APPDIR]LICENSE;
    # (b) -dWixUILicenseRtf (LIGHT, SIN prefijo wix.): el wixlib de WixUI fija el
    #     control LicenseText con !(WixUILicenseRtf=<path>) = leer un archivo RTF y
    #     embeber su contenido. Por eso generamos un RTF desde LICENSE y lo pasamos
    #     a light. OJO: -dwix.WixUILicenseRtf (con prefijo) se ignora, wix.* es un
    #     namespace interno de WiX. Y debe ser un RTF, no el LICENSE de texto plano.
    $lic = Join-Path $repo "LICENSE"
    if (-not (Test-Path $lic)) { throw "LICENSE no encontrado: $lic" }
    # RTF del wizard generado desde LICENSE (una sola fuente de verdad). Estilo
    # Tahoma fs20 como el default de WixUI. Escapar \ { } y unir lineas con \par.
    $licRtf = Join-Path $wixdir "license.rtf"
    $licLines = [System.IO.File]::ReadAllLines($lic)
    $rtfSb = [System.Text.StringBuilder]::new()
    [void]$rtfSb.Append('{\rtf1\ansi\ansicpg1252\deff0{\fonttbl{\f0\fswiss\fprq2\fcharset0 Tahoma;}}\f0\fs20 ')
    for ($i = 0; $i -lt $licLines.Count; $i++) {
        $ln = $licLines[$i].Replace('\','\\').Replace('{','\{').Replace('}','\}')
        if ($i -lt $licLines.Count - 1) { [void]$rtfSb.Append($ln).Append('\par ') } else { [void]$rtfSb.Append($ln) }
    }
    [void]$rtfSb.Append('}')
    [System.IO.File]::WriteAllText($licRtf, $rtfSb.ToString())
    # files.wxi (heat) va como fuente separada, no via <?include ?>.
    & (Join-Path $wix "candle.exe") "-dProductVersion=$pv" "-dWixBanner=$banner" "-dWixIcon=$ico" "-dWixLicense=$lic" (Join-Path $wixdir "glyvex.wxs") (Join-Path $wixdir "files.wxi") -nologo
    if ($LASTEXITCODE -ne 0) { throw "candle falló (exit $LASTEXITCODE)" }
    # Con varias fuentes, candle escribe los .wixobj en el cwd (raiz del repo).
    # -ext WixUIExtension (nombre solo): WixUIExtension.dll (junto a light.exe en
    # wix3/) trae embebidos wixui.wixlib + wixstd.wixlib, que el zip portable no
    # trae sueltos en sdk\. Con -ext:<ruta> falla: PowerShell 7 suelta el guion.
    $msi = Join-Path $repo "distribution\dist\Glyvex-AI-Suite-Setup-$Version.msi"
    # El banner ya fue resuelto por candle ($(var.WixBanner)); light solo
    # enlaza los .wixobj.
    # -dWixUILicenseRtf va a LIGHT (aqui se enlaza el wixlib y se resuelve el
    # !(WixUILicenseRtf=...) leyendo el RTF generado arriba).
    & (Join-Path $wix "light.exe") (Join-Path $repo "glyvex.wixobj") (Join-Path $repo "files.wixobj") "-dWixUILicenseRtf=$licRtf" -ext WixUIExtension -cultures:en-us -nologo -out $msi
    # Exit 204 (LGHT0204) = solo errores de validacion ICE: ICE38/ICE18 (keypath HKCU
    # vs archivo, propio de installs perUser) e ICE91 (dirs per-user). Esperados: el
    # MSI se escribe igual y msiexec lo instala sin problemas (verificado en beta).
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 204) { throw "light falló (exit $LASTEXITCODE)" }
    if (-not (Test-Path $msi)) { throw "MSI no generado: $msi" }
    $msimb = [math]::Round((Get-Item $msi).Length / 1MB, 1)
    Write-Host "    MSI: $msimb MB ($msi)"
}

Write-Host "==> Build listo: $exe" -ForegroundColor Green
