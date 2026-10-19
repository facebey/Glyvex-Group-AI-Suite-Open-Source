"""
themes.py — Temas externos: carga desde archivos (TH-2).

Un tema es una carpeta en <DATA_DIR>/themes/<nombre>/ con al menos un
archivo .css (las texturas van en la misma carpeta y se refieren con
url() relativo desde el CSS). Las carpetas se detectan en cada request
de listado: sin reinstalar, sin tocar código, sin reiniciar.

Contrato del CSS (mismo token set que los temas internos de index.css):
variables --color-glyvex-* (por defecto sobre :root) y, opcionalmente,
los hooks de terminación (.gx-app, .gx-shell, ...). El frontend inyecta
un <link> al CSS solo mientras su tema está activo, así un tema externo
jamás interfiere con los demás.
"""

from __future__ import annotations

import mimetypes
import re
from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from paths import DATA_DIR

# Nombre de carpeta = nombre del tema: empieza con alfanumérico, luego
# alfanuméricos, espacios, guiones y puntos (máx. 64). Es el mismo
# contrato que valida el frontend (lib/theme.js) y, sobre todo, acota
# qué puede aparecer en la URL del endpoint de archivos.
THEME_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-.]{0,63}$")

THEMES_DIR = DATA_DIR / "themes"

DEFAULT_SWATCH = "#64748b"

# El swatch es el primer color reconocible que el tema define para la
# app: el acento (la identidad del tema) y, si no, el fondo.
_SWATCH_PATTERNS = (
    re.compile(r"--color-glyvex-accent\s*:\s*([^;]+);"),
    re.compile(r"--color-glyvex-bg\s*:\s*([^;]+);"),
)


def _looks_like_color(value: str) -> bool:
    v = value.lower().strip()
    return (
        v.startswith("#")
        or v.startswith("rgb")
        or v.startswith("hsl")
        or v.startswith("hwb")
        or v.startswith("lab")
        or v.startswith("lch")
        or v.startswith("oklab")
        or v.startswith("oklch")
        or v in ("transparent", "inherit", "currentcolor")
    )


def swatch_from_css(css: str) -> str:
    """Color de muestra para el selector: acento, luego fondo, luego gris
    neutro. Solo acepta valores que se parecen a colores CSS (un
    `var(...)` o un valor roto no ensucia el selector)."""
    for pattern in _SWATCH_PATTERNS:
        m = pattern.search(css)
        if not m:
            continue
        value = m.group(1).strip().strip("'\"")
        if _looks_like_color(value):
            return value
    return DEFAULT_SWATCH


def list_themes() -> list[dict[str, Any]]:
    """Todas las carpetas de <DATA_DIR>/themes con ≥1 archivo .css.

    `files` viene en orden alfabético = orden en que el frontend los
    carga; `mtime` (máximo de todos los archivos de la carpeta) sirve de
    cache-buster en el <link>.
    """
    if not THEMES_DIR.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for folder in sorted(THEMES_DIR.iterdir(), key=lambda p: p.name.lower()):
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        try:
            css_files = sorted(
                (
                    p
                    for p in folder.iterdir()
                    if p.is_file() and p.suffix.lower() == ".css"
                ),
                key=lambda p: p.name.lower(),
            )
            if not css_files:
                continue
            try:
                head = css_files[0].read_text(encoding="utf-8", errors="replace")
                swatch = swatch_from_css(head[:65536])
            except OSError:
                swatch = DEFAULT_SWATCH
            mtimes = [p.stat().st_mtime for p in folder.rglob("*") if p.is_file()]
            mtimes.append(folder.stat().st_mtime)
            found.append(
                {
                    "name": folder.name,
                    "files": [p.name for p in css_files],
                    "swatch": swatch,
                    "mtime": int(max(mtimes)),
                }
            )
        except OSError:
            # La carpeta desapareció a mitad de escaneo: se ignora.
            continue
    return found


# --------------------------------------------------------------------------
# Router: /api/themes (se registra en main.py con prefix "/themes")
# --------------------------------------------------------------------------

router = APIRouter()


@router.get("")
async def get_themes() -> dict[str, Any]:
    """Listado de temas externos. Sin caché a propósito: es un escaneo
    local barato y el usuario muta las carpetas con la app corriendo."""
    return {"themes": list_themes()}


@router.get("/{name}/{file_path:path}")
async def get_theme_file(name: str, file_path: str) -> Any:
    """Sirve el CSS y las texturas de un tema. Las url() relativas del CSS
    resuelven contra esta URL, así las texturas pueden vivir en
    subcarpetas de la carpeta del tema.

    no-cache: contenido del usuario que muta en caliente; el <link> ya
    fuerza la recarga con ?v=<mtime>."""
    if not THEME_NAME_RE.match(name):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    theme_dir = (THEMES_DIR / name).resolve()
    try:
        theme_dir.relative_to(THEMES_DIR.resolve())
    except ValueError:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    if not theme_dir.is_dir():
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    target = (theme_dir / file_path).resolve()
    try:
        target.relative_to(theme_dir)
    except ValueError:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    if not target.is_file():
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    if target.suffix.lower() == ".css":
        media = "text/css; charset=utf-8"
    else:
        media = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(
        target, media_type=media, headers={"Cache-Control": "no-cache"}
    )
