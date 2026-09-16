# start.ps1 — Build del frontend + arranque del backend (modo producción/todo-en-uno).
# Uso:
#   .\start.ps1          # build del frontend (si hace falta) + uvicorn en :7981
#   .\start.ps1 -Dev     # informa cómo levantar el modo desarrollo
param (
    [switch]$Dev
)

$ErrorActionPreference = "Stop"
chcp 65001 > $null

$RootDir = $PSScriptRoot
$BackendDir = Join-Path $RootDir "backend"
$FrontendDir = Join-Path $RootDir "frontend"
$DistDir = Join-Path $FrontendDir "dist"

$Port = if ($env:GLYVEX_PORT) { $env:GLYVEX_PORT } else { "7981" }
$BindHost = if ($env:GLYVEX_HOST) { $env:GLYVEX_HOST } else { "127.0.0.1" }

if ($Dev) {
    Write-Host "Modo desarrollo: corre estos dos comandos en terminales separadas:"
    Write-Host "  1) cd backend  ; uvicorn main:app --reload --port 7981"
    Write-Host "  2) cd frontend ; npm run dev   # sirve en :5173 con proxy a /api"
    exit 0
}

Write-Host "== Glyvex-AI-Suite: build + arranque =="

if (-not (Test-Path $DistDir)) {
    Write-Host "-- No existe frontend/dist, generando build de producción --"
    Set-Location $FrontendDir
    npm install
    npm run build
} else {
    Write-Host "-- frontend/dist ya existe, se omite build (borra el directorio para forzar rebuild) --"
}

Write-Host "-- Arrancando backend en ${BindHost}:${Port} --"
Set-Location $BackendDir
uvicorn main:app --host $BindHost --port $Port
