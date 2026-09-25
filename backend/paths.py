"""
paths.py — Fuente única de las rutas del proyecto.

Antes cada módulo recalculaba su propio `BASE_DIR = Path(__file__).parent.parent`
y colgaba sus archivos de `BASE_DIR / "data"`. Eso hacía imposible correr dos
instancias del proyecto en la misma máquina sin que compartieran config, base
de datos, adjuntos, logs, benchmarks y métricas.

Ahora todo sale de acá:

- BASE_DIR: raíz del repo (el directorio que contiene backend/ y frontend/).
  No se mueve nunca: es dónde está instalado el código.
- DATA_DIR: dónde vive el estado de ESTA instancia. Se puede mover con la
  variable de entorno GLYVEX_DATA_DIR; si no está, es BASE_DIR/"data" (el
  comportamiento histórico, así que una instalación existente no cambia nada).

GLYVEX_DATA_DIR admite ruta relativa (se resuelve contra BASE_DIR, p. ej.
"./data-testing") o absoluta ("/var/lib/glyvex/data"). Se resuelve una sola vez
al importar el módulo: las variables de entorno se fijan antes de arrancar
uvicorn (ver environments/*.env y los scripts start.*), así que no cambian
durante la vida del proceso.

Dentro del bundle PyInstaller (Tauri sidecar, ver glyvex.spec): `sys.frozen`
existe. El modo es onedir con layout de PyInstaller 6: el exe vive en la raíz
y los recursos empaquetados (frontend/dist, data/templates) en `_internal/`
(= `sys._MEIPASS` = BUNDLE_DIR). BASE_DIR pasa a ser el directorio del
ejecutable y DATA_DIR por defecto a %LOCALAPPDATA%\Glyvex-AI-Suite\data,
porque la carpeta de instalación puede ser de solo lectura (Program Files)
y el estado es por usuario.

Nota para los tests: conftest.py aísla el estado redirigiendo los nombres a
nivel de módulo de cada consumidor (p. ej. `launcher_module.DATA_DIR = tmp_path`),
no este archivo. Por eso cada módulo sigue definiendo su propia constante
module-level derivada de DATA_DIR en vez de llamar a paths.DATA_DIR adentro de
las funciones: si lo hicieran, el redireccionamiento de los tests dejaría de
tener efecto EN SILENCIO y la suite escribiría sobre el data/ real.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# PyInstaller: dentro del bundle `sys.frozen` existe. El modo es onedir:
# exe en la raíz del bundle; PyInstaller 6 guarda recursos en `_internal/`
# (sys._MEIPASS), con fallback BUNDLE_DIR = dir del exe.
FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    BASE_DIR = Path(sys.executable).resolve().parent
    # Raíz de los recursos que viajan dentro del bundle (frontend/dist,
    # data/templates). En dev es lo mismo que BASE_DIR.
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", str(BASE_DIR)))
else:
    # Raíz del repo: paths.py vive en backend/, así que subimos un nivel.
    BASE_DIR = Path(__file__).resolve().parent.parent
    BUNDLE_DIR = BASE_DIR


def _frozen_default_data_dir() -> Path:
    """Estado por usuario fuera de la carpeta de instalación (solo lectura)."""
    local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
    if local_appdata:
        return Path(local_appdata) / "Glyvex-AI-Suite" / "data"
    return BASE_DIR / "data"


def _resolve_data_dir() -> Path:
    """DATA_DIR según GLYVEX_DATA_DIR; default: BASE_DIR/"data" (bundle: LOCALAPPDATA)."""
    raw = os.environ.get("GLYVEX_DATA_DIR", "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            # Relativa al repo, no al cwd: uvicorn se lanza desde backend/ (ver
            # start.sh), así que resolver contra el cwd daría backend/data-testing.
            candidate = BASE_DIR / candidate
        return candidate.resolve()
    if FROZEN:
        return _frozen_default_data_dir()
    return BASE_DIR / "data"


DATA_DIR = _resolve_data_dir()

# Nombre del entorno activo. Solo informativo (lo loguea main.py al arrancar y
# lo expone /api/health), para poder distinguir de un vistazo si la ventana que
# estás mirando es la de producción o la de testing.
ENV_NAME = os.environ.get("GLYVEX_ENV", "production").strip() or "production"

__all__ = ["BASE_DIR", "BUNDLE_DIR", "DATA_DIR", "ENV_NAME", "FROZEN"]
