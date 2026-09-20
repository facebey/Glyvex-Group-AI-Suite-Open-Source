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

Nota para los tests: conftest.py aísla el estado redirigiendo los nombres a
nivel de módulo de cada consumidor (p. ej. `launcher_module.DATA_DIR = tmp_path`),
no este archivo. Por eso cada módulo sigue definiendo su propia constante
module-level derivada de DATA_DIR en vez de llamar a paths.DATA_DIR adentro de
las funciones: si lo hicieran, el redireccionamiento de los tests dejaría de
tener efecto EN SILENCIO y la suite escribiría sobre el data/ real.
"""

from __future__ import annotations

import os
from pathlib import Path

# Raíz del repo: paths.py vive en backend/, así que subimos un nivel.
BASE_DIR = Path(__file__).resolve().parent.parent


def _resolve_data_dir() -> Path:
    """DATA_DIR según GLYVEX_DATA_DIR, con BASE_DIR/"data" como default."""
    raw = os.environ.get("GLYVEX_DATA_DIR", "").strip()
    if not raw:
        return BASE_DIR / "data"
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        # Relativa al repo, no al cwd: uvicorn se lanza desde backend/ (ver
        # start.sh), así que resolver contra el cwd daría backend/data-testing.
        candidate = BASE_DIR / candidate
    return candidate.resolve()


DATA_DIR = _resolve_data_dir()

# Nombre del entorno activo. Solo informativo (lo loguea main.py al arrancar y
# lo expone /api/health), para poder distinguir de un vistazo si la ventana que
# estás mirando es la de producción o la de testing.
ENV_NAME = os.environ.get("GLYVEX_ENV", "production").strip() or "production"

__all__ = ["BASE_DIR", "DATA_DIR", "ENV_NAME"]
