"""
T5.2: el deep-link /provision (redirección de Tauri con --provision) debe
servir el index.html de la SPA — react-router resuelve la ruta en cliente.

Extensión (fix/exe-refresh-spa-fallback): CUALQUIER ruta GET que no sea API
ni archivo estático debe servir index.html. El empaquetado anterior
(StaticFiles en /) devolvía 404 {"detail":"Not Found"} al refrescar
(/launcher, /monitor, ...) desde el "Actualizar" del menú de contexto de
WebView2; solo / (Chat) resolvía.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import main as main_module

pytestmark = pytest.mark.skipif(
    not (Path(main_module.FRONTEND_DIST) / "index.html").exists(),
    reason="frontend/dist no construido",
)

SPA_ROUTES = ("/", "/provision", "/launcher", "/benchmark", "/monitor", "/reports", "/config")


@pytest.mark.parametrize("path", SPA_ROUTES)
async def test_spa_routes_serve_index(client, path):
    res = await client.get(path)
    assert res.status_code == 200, path
    assert "text/html" in res.headers.get("content-type", "")
    assert 'id="root"' in res.text


async def test_static_assets_sigue_servido(client):
    assets = sorted((main_module.FRONTEND_DIST / "assets").glob("*"))
    archivo = next((a for a in assets if a.is_file()), None)
    if archivo is None:
        pytest.skip("dist/assets vacío")
    res = await client.get(f"/assets/{archivo.name}")
    assert res.status_code == 200, archivo.name
    assert 'id="root"' not in res.text


async def test_spa_fallback_no_cubre_api(client):
    # Un path /api inexistente sigue siendo 404 (la fallback de SPA no
    # tapa a la API).
    res = await client.get("/api/no-existe")
    assert res.status_code == 404
