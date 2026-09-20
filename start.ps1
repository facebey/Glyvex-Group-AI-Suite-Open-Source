# start.ps1 — Build del frontend + arranque del backend (modo producción/todo-en-uno).
# Uso:
#   .\start.ps1                          # instancia de producción
#   .\start.ps1 -Env testing             # instancia de testing (otros puertos y data)
#   .\start.ps1 -Dev                     # informa cómo levantar el modo desarrollo
#   .\start.ps1 -Env testing -Dev        # idem, con los puertos de testing
#
# El entorno se define en environments\<nombre>.env: ese archivo es la ÚNICA
# fuente de puertos y rutas de la instancia (lo lee también vite.config.js).
param (
    [string]$Env = "production",
    [switch]$Dev
)

$ErrorActionPreference = "Stop"
chcp 65001 > $null

$RootDir = $PSScriptRoot
$BackendDir = Join-Path $RootDir "backend"
$FrontendDir = Join-Path $RootDir "frontend"
$DistDir = Join-Path $FrontendDir "dist"
$EnvironmentsDir = Join-Path $RootDir "environments"

$EnvFile = Join-Path $EnvironmentsDir "$Env.env"
if (-not (Test-Path $EnvFile)) {
    Write-Host "Error: no existe $EnvFile"
    Write-Host "Entornos disponibles:"
    Get-ChildItem -Path $EnvironmentsDir -Filter "*.env" -ErrorAction SilentlyContinue |
        ForEach-Object { Write-Host ("  " + $_.BaseName) }
    exit 1
}

# Carga del .env: KEY=value, ignorando vacías y comentarios. Se parsea a mano
# (no Invoke-Expression) porque estos archivos son datos, no script.
# El Split con límite 2 parte en el PRIMER "=" nada más, así un valor que
# contenga "=" no se trunca.
foreach ($line in Get-Content -LiteralPath $EnvFile) {
    $trimmed = $line.Trim()
    if ([string]::IsNullOrWhiteSpace($trimmed)) { continue }
    if ($trimmed.StartsWith("#")) { continue }
    if ($trimmed -notmatch "=") { continue }
    $parts = $trimmed.Split("=", 2)
    $key = $parts[0].Trim()
    $value = $parts[1].Trim()
    if ([string]::IsNullOrWhiteSpace($key)) { continue }
    Set-Item -Path ("Env:" + $key) -Value $value
}

$Port = if ($env:GLYVEX_PORT) { $env:GLYVEX_PORT } else { "7981" }
$BindHost = if ($env:GLYVEX_HOST) { $env:GLYVEX_HOST } else { "127.0.0.1" }
$DevPort = if ($env:GLYVEX_DEV_PORT) { $env:GLYVEX_DEV_PORT } else { "5173" }
$DataDir = if ($env:GLYVEX_DATA_DIR) { $env:GLYVEX_DATA_DIR } else { "./data" }

if ($Dev) {
    # Comando y explicacion en lineas separadas: si fueran una sola linea con
    # un "# comentario" al final, copiarla a CMD (que NO trata # como
    # comentario) le pasaria el texto como argumentos a vite.
    Write-Host "Modo desarrollo (entorno: $Env): corre estos dos comandos en terminales separadas:"
    Write-Host ""
    Write-Host "  1) Backend (queda escuchando en ${BindHost}:${Port}):"
    Write-Host "       cd backend"
    Write-Host "       `$env:GLYVEX_DATA_DIR = '$DataDir'"
    Write-Host "       uvicorn main:app --reload --host $BindHost --port $Port"
    Write-Host ""
    Write-Host "  2) Frontend (abris http://localhost:${DevPort}):"
    Write-Host "       cd frontend"
    if ($Env -eq "production") {
        Write-Host "       npm run dev"
    } else {
        Write-Host "       npm run dev -- --mode $Env"
    }
    Write-Host ""
    exit 0
}

Write-Host "== Glyvex-AI-Suite: build + arranque (entorno: $Env) =="

if (-not (Test-Path $DistDir)) {
    Write-Host "-- No existe frontend/dist, generando build de producción --"
    Set-Location $FrontendDir
    npm install
    npm run build
} else {
    Write-Host "-- frontend/dist ya existe, se omite build (borra el directorio para forzar rebuild) --"
}

Write-Host "-- Datos en $DataDir --"
Write-Host "-- Arrancando backend en ${BindHost}:${Port} --"
Set-Location $BackendDir
uvicorn main:app --host $BindHost --port $Port
