#!/usr/bin/env bash
# start.sh — Build del frontend + arranque del backend (modo producción/todo-en-uno).
#
# Uso:
#   ./start.sh                      # instancia de producción
#   ./start.sh --env testing        # instancia de testing (otros puertos y data)
#   ./start.sh --dev                # informa cómo levantar el modo desarrollo
#   ./start.sh --env testing --dev  # idem, con los puertos de testing
#
# El entorno se define en environments/<nombre>.env: ese archivo es la ÚNICA
# fuente de puertos y rutas de la instancia (lo lee también vite.config.js).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
FRONTEND_DIR="${ROOT_DIR}/frontend"
DIST_DIR="${FRONTEND_DIR}/dist"

ENV_NAME="production"
DEV_MODE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)
      [[ $# -ge 2 ]] || { echo "Error: --env necesita un valor (production|testing)" >&2; exit 2; }
      ENV_NAME="$2"; shift 2 ;;
    --env=*)
      ENV_NAME="${1#*=}"; shift ;;
    --dev)
      DEV_MODE=1; shift ;;
    *)
      echo "Error: opción desconocida '$1' (usá --env <nombre> o --dev)" >&2; exit 2 ;;
  esac
done

ENV_FILE="${ROOT_DIR}/environments/${ENV_NAME}.env"
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Error: no existe ${ENV_FILE}" >&2
  echo "Entornos disponibles:" >&2
  ls -1 "${ROOT_DIR}/environments/"*.env 2>/dev/null | xargs -n1 basename 2>/dev/null | sed 's/\.env$/  /' >&2 || true
  exit 1
fi

# Carga del .env: KEY=value, ignorando vacías y comentarios. Se hace sin
# `source` a propósito — source ejecutaría el contenido del archivo como bash
# (backticks, $(...) y demás), y estos archivos son datos, no scripts.
# Se recortan los espacios alrededor del "=" y se acepta CRLF, por si el
# archivo se editó en Windows.
while IFS= read -r line || [[ -n "$line" ]]; do
  line="${line%$'\r'}"
  [[ -z "${line//[[:space:]]/}" ]] && continue
  [[ "${line#"${line%%[![:space:]]*}"}" == \#* ]] && continue
  [[ "$line" != *"="* ]] && continue
  key="${line%%=*}"
  value="${line#*=}"
  key="${key#"${key%%[![:space:]]*}"}"; key="${key%"${key##*[![:space:]]}"}"
  value="${value#"${value%%[![:space:]]*}"}"; value="${value%"${value##*[![:space:]]}"}"
  [[ -z "$key" ]] && continue
  export "${key}=${value}"
done < "${ENV_FILE}"

PORT="${GLYVEX_PORT:-7981}"
HOST="${GLYVEX_HOST:-127.0.0.1}"
DEV_PORT="${GLYVEX_DEV_PORT:-5173}"

if [[ "${DEV_MODE}" -eq 1 ]]; then
  # El comando y la explicación van en líneas separadas a propósito: si se
  # imprimieran juntos con un "# comentario" al final, alguien que copie la
  # línea entera en CMD (que NO trata # como comentario) le pasaría el texto
  # como argumentos a vite.
  echo "Modo desarrollo (entorno: ${ENV_NAME}): corré estos dos comandos en terminales separadas:"
  echo
  echo "  1) Backend (queda escuchando en ${HOST}:${PORT}):"
  echo "       cd backend"
  echo "       export GLYVEX_DATA_DIR='${GLYVEX_DATA_DIR:-./data}'"
  echo "       uvicorn main:app --reload --host ${HOST} --port ${PORT}"
  echo
  echo "  2) Frontend (abrís http://localhost:${DEV_PORT}):"
  echo "       cd frontend"
  if [[ "${ENV_NAME}" == "production" ]]; then
    echo "       npm run dev"
  else
    echo "       npm run dev -- --mode ${ENV_NAME}"
  fi
  echo
  exit 0
fi

echo "== Glyvex-AI-Suite: build + arranque (entorno: ${ENV_NAME}) =="

if [[ ! -d "${DIST_DIR}" ]]; then
  echo "-- No existe frontend/dist, generando build de producción --"
  (cd "${FRONTEND_DIR}" && npm install && npm run build)
else
  echo "-- frontend/dist ya existe, se omite build (borrá el directorio para forzar rebuild) --"
fi

echo "-- Datos en ${GLYVEX_DATA_DIR:-./data} --"
echo "-- Arrancando backend en ${HOST}:${PORT} --"
cd "${BACKEND_DIR}"
exec uvicorn main:app --host "${HOST}" --port "${PORT}"
