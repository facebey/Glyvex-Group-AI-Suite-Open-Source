# build.ps1 — Sidecar backend para el empaquetado Tauri (T-1, Fase 1).
# Pipeline: npm run build → PyInstaller (backend/glyvex.spec) → smoke test del exe.
# El instalador se genera aparte con `tauri build` (NSIS).
# Uso: .\build.ps1 [-SkipFrontend] [-SkipSmoke] [-SkipSidecar]
# -SkipSidecar: no correr PyInstaller; usar el bundle ya existente en
# backend\dist\glyvex-backend.

param(
    [switch]$SkipFrontend,
    [switch]$SkipSmoke,
    [switch]$SkipSidecar
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
# Junction src-tauri\resources\glyvex-backend -> backend\dist\glyvex-backend:
# tauri-bundler instala cada ".." fuera de src-tauri como "_up_", asi el
# resource debe vivir DENTRO de src-tauri para que caiga aplanado en $INSTDIR
# (resource_dir()/glyvex-backend/, que es lo que resuelve el shell Rust).
$junction = Join-Path $repo "src-tauri\resources\glyvex-backend"
if (Test-Path $junction) {
    $j = Get-Item $junction
    if ($j.LinkType -ne 'Junction' -or $j.Target -ne @($bundle)) {
        Remove-Item $junction -Force
        New-Item -ItemType Junction -Path $junction -Target $bundle | Out-Null
    }
} else {
    New-Item -ItemType Directory -Force -Path (Join-Path $repo "src-tauri\resources") | Out-Null
    New-Item -ItemType Junction -Path $junction -Target $bundle | Out-Null
}
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

Write-Host "==> Build listo: $exe" -ForegroundColor Green
