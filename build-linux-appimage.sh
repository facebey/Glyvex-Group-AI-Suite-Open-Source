#!/usr/bin/env bash
# build-linux-appimage.sh — AppImage de Tauri para Linux x64 (RT-14 L2).
# Corre en el server Linux (Ubuntu 22.04+), desde la raiz del repo.
#
# Pipeline:
#   [1/4] build-linux.sh        (frontend dist + sidecar PyInstaller + smoke)
#   [2/4] bundle sidecar -> src-tauri/resources/glyvex-backend/ (copia real)
#   [3/4] tauri build --ci --bundles appimage (firmado para el updater)
#   [4/4] verificar .AppImage + .AppImage.sig (opcion: liveness con xvfb)
#
# Firmado: REQUIERE TAURI_SIGNING_PRIVATE_KEY (env, contenido de la key o
# ruta a un archivo .key — la misma key que usa el build Windows; el public
# key embebido en tauri.conf.json debe coincidir o el updater rechaza).
# Sin ella tauri build falla con createUpdaterArtifacts=true.
#
# Uso:
#   ./build-linux-appimage.sh [--smoke] [--skip-frontend]
#
# El artifact final queda en:
#   src-tauri/target/release/bundle/appimage/<nombre>_<version>_<arch>.AppImage
#   ... .AppImage.sig
# Copiar ambos a Windows (src-tauri\target\release\bundle\appimage\) y correr
# build-latest-json.ps1 -LinuxBundle <dir> para que latest.json incluya
# linux-x86_64. Subir AppImage + .sig + latest.json como assets de la release.

set -euo pipefail
cd "$(dirname "$0")"

VERSION="$(grep -oP '"version"\s*:\s*"\K[^"]+' src-tauri/tauri.conf.json | head -n1)"
SMOKE=0
SKIP_FRONTEND=0
for arg in "$@"; do
  case "$arg" in
    --smoke) SMOKE=1 ;;
    --skip-frontend) SKIP_FRONTEND=1 ;;
  esac
done

info() { echo "==> $*" >&2; }
die()  { echo "ERROR: $*" >&2; exit 1; }

info "Glyvex AI Suite — AppImage build  version=$VERSION"

# --- [1/4] frontend + sidecar (build-linux.sh) -------------------------------
if [ "$SKIP_FRONTEND" -eq 1 ]; then
  info "[1/4] sidecar (build-linux.sh --skip-frontend)"
  ./build-linux.sh --skip-frontend
else
  info "[1/4] frontend + sidecar (build-linux.sh)"
  ./build-linux.sh
fi

# --- [2/4] copiar bundle del sidecar a resources -----------------------------
# En Windows el build.ps1 deja un junction; en Linux tauri necesita una copia
# real (src-tauri/resources/glyvex-backend/ esta en .gitignore).
info "[2/4] copiar sidecar a src-tauri/resources/glyvex-backend/"
rm -rf src-tauri/resources/glyvex-backend
mkdir -p src-tauri/resources
cp -a backend/dist/glyvex-backend src-tauri/resources/
test -f src-tauri/resources/glyvex-backend/glyvex-backend \
  || die "binario del sidecar no encontrado en src-tauri/resources/glyvex-backend/"

# --- [3/4] tauri build firmado ------------------------------------------------
info "[3/4] tauri build --bundles appimage"
[ -n "${TAURI_SIGNING_PRIVATE_KEY:-}" ] \
  || die "TAURI_SIGNING_PRIVATE_KEY no definida (env con el contenido de la key o ruta al archivo .key)"

if ! pkg-config --exists webkit2gtk-4.1 2>/dev/null; then
  die "webkit2gtk-4.1 no instalado. En el server: sudo apt install libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev libfuse2 libssl-dev pkg-config"
fi

# beforeBuildCommand=null: el frontend ya lo construyo build-linux.sh.
TAURI_BIN=./frontend/node_modules/.bin/tauri
test -x "$TAURI_BIN" || die "tauri CLI no encontrado en frontend/node_modules/.bin/tauri (correr npm install en frontend/)"

# AppImageTool (modo --appimage-extract-and-run, forzado por tauri-bundler)
# extrae a $TMPDIR. Un run anterior crashado puede dejar /tmp/appimage_extracted_*
# con permisos de solo lectura (permisos del payload squashfs): el plugin
# linuxdeploy no puede extraer su AppImage, sale con exit 127 y tauri solo
# muestra el generico "failed to run linuxdeploy" (con -ci el stderr se traga).
# TMPDIR privado para el build + limpieza best-effort de restos en /tmp.
rm -rf /tmp/appimage_extracted_* 2>/dev/null || true
export TMPDIR="$HOME/.tmp-appimage"
mkdir -p "$TMPDIR"

"$TAURI_BIN" build --ci --bundles appimage --config '{"build":{"beforeBuildCommand":null}}'

# --- [4/4] verificacion de artifacts ------------------------------------------
info "[4/4] verificar artifacts"
OUT_DIR=src-tauri/target/release/bundle/appimage
IMG="$(find "$OUT_DIR" -maxdepth 1 -name "*${VERSION}*.AppImage" -type f 2>/dev/null | head -n1 || true)"
[ -n "$IMG" ] || die "AppImage no generado en $OUT_DIR"
SIG="${IMG}.sig"
[ -f "$SIG" ] || die "firma no encontrada: $SIG (el updater la necesita)"

info "AppImage: $IMG ($(du -h "$IMG" | cut -f1))"
info "Firma:    $SIG"

# --- liveness opcional ----------------------------------------------------------
if [ "$SMOKE" -eq 1 ]; then
  if ! command -v xvfb-run >/dev/null 2>&1; then
    info "SKIP smoke: xvfb-run no instalado (sudo apt install xvfb); el AppImage se verifico solo como artifact."
  else
    info "Smoke: arrancar AppImage bajo Xvfb (--appimage-extract-and-run) y verificar liveness"
    rm -rf /tmp/glyvex-appimage-smoke && mkdir -p /tmp/glyvex-appimage-smoke
    GLYVEX_NO_BROWSER=1 xvfb-run -a "$IMG" --appimage-extract-and-run >/tmp/glyvex-appimage-smoke/out.log 2>&1 &
    SMOKE_PID=$!
    sleep 20
    if kill -0 "$SMOKE_PID" 2>/dev/null; then
      info "Smoke OK: proceso vivo tras 20s"
    else
      wait "$SMOKE_PID" || true
      die "Smoke FAIL: el AppImage murio al arrancar (log en /tmp/glyvex-appimage-smoke/out.log)"
    fi
    kill "$SMOKE_PID" 2>/dev/null || true
    wait "$SMOKE_PID" 2>/dev/null || true
  fi
fi

info "AppImage listo. Siguiente: copiar $IMG y $SIG a Windows y correr"
info "  .\build-latest-json.ps1 -LinuxBundle <dir>   (genera latest.json con linux-x86_64)"
