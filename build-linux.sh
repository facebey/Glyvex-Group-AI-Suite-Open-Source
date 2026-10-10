#!/usr/bin/env bash
# build-linux.sh — Sidecar backend PyInstaller para Linux x64 (RT-14, fase L1).
#
# Pipeline (espejo de build.ps1, sin el junction de Tauri — eso es L2):
#   [1/3] npm run build (frontend/dist)
#   [2/3] PyInstaller onedir con backend/glyvex.spec -> backend/dist/glyvex-backend/
#         + recorte de babel/locale-data (misma lista que build.ps1)
#   [3/3] Smoke test: arranca el binario, espera /api/health + SPA, lo mata.
#
# Uso (desde la raíz del repo, con el venv del proyecto activo o PY=/ruta/python):
#   ./build-linux.sh                # build completo + smoke test
#   ./build-linux.sh --skip-frontend
#   ./build-linux.sh --skip-smoke
#
# PyInstaller NO cross-compila: este script corre en el mismo Linux donde va a
# ejecutarse el binario (Ubuntu x64). El venv necesita los requirements del
# backend + pyinstaller (si falta, el script lo instala solo).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
FRONTEND_DIR="${ROOT_DIR}/frontend"
DIST_DIR="${FRONTEND_DIR}/dist"

SKIP_FRONTEND=0
SKIP_SMOKE=0
for arg in "$@"; do
  case "$arg" in
    --skip-frontend) SKIP_FRONTEND=1 ;;
    --skip-smoke)    SKIP_SMOKE=1 ;;
    *) echo "Error: opción desconocida '$arg' (usá --skip-frontend / --skip-smoke)" >&2; exit 2 ;;
  esac
done

PY="${PY:-}"
if [[ -z "${PY}" ]]; then
  for cand in "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/venv/bin/python" "$(command -v python3 || true)"; do
    if [[ -n "${cand}" && -x "${cand}" ]]; then PY="${cand}"; break; fi
  done
fi
[[ -n "${PY}" && -x "${PY}" ]] || { echo "Error: no hay python interpretable (usá PY=/ruta/python)" >&2; exit 1; }
echo "== Python: ${PY} ($("${PY}" --version 2>&1)) =="

if ! "${PY}" -c "import PyInstaller" >/dev/null 2>&1; then
  echo "-- PyInstaller no está en el venv, instalando --"
  "${PY}" -m pip install --quiet pyinstaller
fi

echo "== [1/3] Build frontend =="
if [[ "${SKIP_FRONTEND}" -eq 1 ]]; then
  echo "    omitido (--skip-frontend)"
else
  (cd "${FRONTEND_DIR}" && npm run build)
fi
[[ -f "${DIST_DIR}/index.html" ]] || { echo "Error: frontend/dist/index.html no existe (corré sin --skip-frontend)" >&2; exit 1; }

echo "== [2/3] PyInstaller (onedir) =="
# Los paths relativos del spec se resuelven desde backend/: se corre ahí.
(cd "${BACKEND_DIR}" && "${PY}" -m PyInstaller glyvex.spec --noconfirm)

BUNDLE="${BACKEND_DIR}/dist/glyvex-backend"
BIN="${BUNDLE}/glyvex-backend"
[[ -x "${BIN}" ]] || { echo "Error: binario no generado: ${BIN}" >&2; exit 1; }

# babel/locale-data (28 MB, 1000+ .dat por locale; entra vía courlan/trafilatura
# solo para formatear fechas): la app trabaja en en/es, el resto se recorta
# post-build. babel sigue importable: Locale cae a fallback si falta un .dat.
LD="${BUNDLE}/_internal/babel/locale-data"
if [[ -d "${LD}" ]]; then
  for f in "${LD}"/*.dat; do
    case "$(basename "$f")" in
      en.dat|en_US.dat|en_001.dat|es.dat|es_MX.dat|es_419.dat|es_ES.dat) : ;;
      *) rm -f "$f" ;;
    esac
  done
fi
MB=$(du -sm "${BUNDLE}" | cut -f1)
echo "    bundle: ${MB} MB (${BIN})"

if [[ "${SKIP_SMOKE}" -eq 1 ]]; then
  echo "== [3/3] Smoke test: omitido (--skip-smoke)"
else
  echo "== [3/3] Smoke test del binario =="
  # Sin curl/ss: el smoke corre en Python (urllib), único requerido por el
  # build. GLYVEX_DATA_DIR temporal: no toca el estado real del usuario y da
  # la ruta exacta del log del sidecar (el binario es console=False). El
  # puerto final sale del marker #PORT_ASSIGNED:<puerto># del log: el app
  # pre-bindea 7981 con fallback +1..+9, así que no se asume.
  SMOKE_DATA="$(mktemp -d)"
  GLYVEX_NO_BROWSER=1 GLYVEX_DATA_DIR="${SMOKE_DATA}" "${BIN}" &
  PID=$!
  trap 'kill "${PID}" 2>/dev/null || true; rm -rf "${SMOKE_DATA}"' EXIT
  OK=1
  "${PY}" - "${SMOKE_DATA}" <<'PYEOF' || OK=0
import os
import re
import sys
import time
import urllib.request

data_dir = sys.argv[1]
log_path = os.path.join(data_dir, "logs", "sidecar-console.log")


def fail(msg):
    print(msg, file=sys.stderr)
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            tail = fh.read().splitlines()[-25:]
        if tail:
            print("---- tail del log del sidecar ----", file=sys.stderr)
            print("\n".join(tail), file=sys.stderr)
    raise SystemExit(1)


port = None
t0 = time.time()
while time.time() - t0 < 60:
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            found = re.findall(r"#PORT_ASSIGNED:(\d+)#", fh.read())
        if found:
            port = int(found[-1])
            break
    time.sleep(0.5)
if port is None:
    fail("sin #PORT_ASSIGNED en el log tras 60s (el binario no arrancó)")
print(f"    puerto: {port}")

t1 = time.time()
while time.time() - t1 < 60:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
            body = r.read().decode("utf-8", "replace")
        if '"status":"ok"' in body:
            print(f"    health OK en {time.time() - t1:.1f}s")
            break
    except Exception:
        pass
    time.sleep(1)
else:
    fail("/api/health no respondió en 60s")

try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
        spa = r.read()
except Exception as exc:
    fail(f"SPA no servida: {exc}")
if len(spa) < 100:
    fail(f"SPA sospechosamente chica ({len(spa)} bytes)")
print(f"    SPA {len(spa)} bytes")
PYEOF
  kill "${PID}" 2>/dev/null || true
  wait "${PID}" 2>/dev/null || true
  trap - EXIT
  rm -rf "${SMOKE_DATA}"
  [[ "${OK}" -eq 1 ]] || { echo "Error: smoke test falló (detalle en stderr arriba)" >&2; exit 1; }
  echo "    smoke test OK"
fi

echo "== Build listo: ${BIN} =="
