"""Tests del serve estático empaquetado: cabeceras de caché y sello de build.

Causa raíz del "F5 tras un update in-app": sin instrucciones del backend, la
caché de WebView2 reusa lo servido en el origen fijo (PERS-1), y al arranque
siguiente la app carga el index.html y los chunks de la versión anterior.
mount_frontend ahora envía: /assets immutable (llevar hash de build en el
nombre), index.html y archivos de public/ sin hash con revalidación, y
/gx-version.json (no-store) como sello que la app compara al arrancar para
autocargarse si está con un bundle viejo.
"""

import httpx
import pytest
from fastapi import FastAPI

import main as main_module


@pytest.fixture
def dist(tmp_path):
    """Un dist de juguete con la forma exacta que produce Vite."""
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "fonts").mkdir()
    (d / "index.html").write_text("<!doctype html><title>INDEX</title>", encoding="utf-8")
    (d / "assets" / "index-Du8mS_7q.js").write_text("console.log('x')", encoding="utf-8")
    (d / "fonts" / "glyvex-sans-wght.woff2").write_bytes(b"\0\1\2woff2")
    (d / "gx-version.json").write_text('{"version":"0.0.0-test"}', encoding="utf-8")
    (tmp_path / "secret.txt").write_text("afuera de dist", encoding="utf-8")
    return d


@pytest.fixture
async def dist_client(dist):
    app = FastAPI()
    main_module.mount_frontend(app, dist)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as ac:
        yield ac


async def test_assets_inmutables_por_hash_de_build(dist_client):
    # El nombre con hash de contenido es la validación: si cambió el
    # contenido cambió la URL, así que un año de caché es seguro.
    r = await dist_client.get("/assets/index-Du8mS_7q.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == main_module._CACHE_IMMUTABLE


async def test_index_revalida_en_cada_carga(dist_client):
    # Cualquier ruta SPA cae en index.html: sin revalidación WebView2 aplica
    # frescura heurística y tras un update sirve el index de la versión
    # anterior — el F5 manual del report post-update 0.8.1→0.9.0.
    r = await dist_client.get("/monitor")
    assert r.status_code == 200
    assert "INDEX" in r.text
    assert r.headers["cache-control"] == main_module._CACHE_REVALIDATE


async def test_cache_buster_por_version_sirve_index(dist_client):
    # PUB-13: el bootstrap redirige a /?glyvex=<version> (clave de caché
    # distinta por versión en WebView2). La query no altera la ruta: sigue
    # cayendo en index.html con revalidación, también en /provision.
    r = await dist_client.get("/?glyvex=0.10.0-beta")
    assert r.status_code == 200
    assert "INDEX" in r.text
    assert r.headers["cache-control"] == main_module._CACHE_REVALIDATE
    r2 = await dist_client.get("/provision?glyvex=0.10.0-beta")
    assert r2.status_code == 200
    assert "INDEX" in r2.text


async def test_archivos_de_public_se_sirven_con_revalidacion(dist_client):
    # Antes de este fix /fonts/* caía en el fallback de index.html y el
    # navegador descartaba la fuente por tipo. No tienen hash en el nombre,
    # así que se revalidan en vez de cacheares un año.
    r = await dist_client.get("/fonts/glyvex-sans-wght.woff2")
    assert r.status_code == 200
    assert r.content == b"\0\1\2woff2"
    assert r.headers["content-type"].startswith("font/woff2")
    assert r.headers["cache-control"] == main_module._CACHE_REVALIDATE


async def test_gx_version_es_sello_sin_caché(dist_client):
    # no-store: el guard de obsolesencia necesita SIEMPRE el valor del dist
    # en disco, nunca lo que la webview tenga cacheado.
    r = await dist_client.get("/gx-version.json")
    assert r.status_code == 200
    assert r.json() == {"version": "0.0.0-test"}
    assert r.headers["cache-control"] == "no-store"


async def test_gx_version_404_sin_dist_sellado(tmp_path):
    # Un dist viejo (pre-sello) no rompe el guard: 404 JSON y el frontend
    # deja el guard dormido.
    (tmp_path / "index.html").write_text("<title>INDEX</title>", encoding="utf-8")
    app = FastAPI()
    main_module.mount_frontend(app, tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as ac:
        r = await ac.get("/gx-version.json")
        assert r.status_code == 404
        assert r.json() == {"detail": "Not Found"}


async def test_api_no_tapada_por_la_fallback_spa(dist_client):
    r = await dist_client.get("/api/no-existe")
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}


async def test_path_traversal_no_saca_archivos_de_fuera_de_dist(dist_client):
    # ../secret.txt vive en tmp_path, afuera de dist: no debe filtrarse,
    # se trata como ruta SPA (index.html con revalidación).
    r = await dist_client.get("/fonts/%2e%2e/secret.txt")
    assert "afuera de dist" not in r.text
    assert r.status_code == 200
    assert r.headers["cache-control"] == main_module._CACHE_REVALIDATE
