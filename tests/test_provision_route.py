"""
T5.2: el deep-link /provision (redirección de Tauri con --provision) debe
servir el index.html de la SPA — react-router resuelve la ruta en cliente.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import main as main_module

pytestmark = pytest.mark.skipif(
    not (Path(main_module.FRONTEND_DIST) / "index.html").exists(),
    reason="frontend/dist no construido",
)


async def test_provision_serves_spa_index(client):
    res = await client.get("/provision")
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")
    assert 'id="root"' in res.text


async def test_provision_route_no_cubre_api(client):
    # Un path /api inexistente sigue siendo 404 (la fallback de SPA no
    # tapa a la API).
    res = await client.get("/api/no-existe")
    assert res.status_code == 404
