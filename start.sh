#!/usr/bin/env bash
# start.sh — Build del frontend + arranque del backend (modo producción/todo-en-uno).
#
# Uso:
#   ./start.sh          # build del frontend (si hace falta) + uvicorn en :7981
#   ./start.sh --dev     # informa cómo levantar el modo desarrollo (2 procesos)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
FRONTEND_DIR="${ROOT_DIR}/frontend"
DIST_DIR="${FRONTEND_DIR}/dist"
PORT="${GLYVEX_PORT:-7981}"
HOST="${GLYVEX_HOST:-127.0.0.1}"

if [[ "${1:-}" == "--dev" ]]; then
  echo "Modo desarrollo: corré estos dos comandos en terminales separadas:"
  echo "  1) cd backend  && uvicorn main:app --reload --port 7981"
  echo "  2) cd frontend && npm run dev   # sirve en :5173 con proxy a /api"
  exit 0
fi

echo "== Glyvex-AI-Suite: build + arranque =="

if [[ ! -d "${DIST_DIR}" ]]; then
  echo "-- No existe frontend/dist, generando build de producción --"
  (cd "${FRONTEND_DIR}" && npm install && npm run build)
else
  echo "-- frontend/dist ya existe, se omite build (borrá el directorio para forzar rebuild) --"
fi

echo "-- Arrancando backend en ${HOST}:${PORT} --"
cd "${BACKEND_DIR}"
exec uvicorn main:app --host "${HOST}" --port "${PORT}"
