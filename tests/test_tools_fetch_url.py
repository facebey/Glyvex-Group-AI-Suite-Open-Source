"""
test_tools_fetch_url.py — Seguridad de fetch_url (ítem D1: SSRF por redirección).

Un sitio público puede devolver 302 hacia una dirección interna
(127.0.0.1, RFC1918, link-local) y esquivar el check de la URL original.
Cada `Location` debe validarse contra _host_is_private; los redirects se
siguen a mano (follow_redirects=False) con un tope de MAX_REDIRECTS saltos.

Sin red ni servidores reales: httpx.AsyncClient se sustituye por un cliente
con MockTransport que responde según el handler de cada test. Los hostnames
"públicos" usan el TLD reservado .example (nunca resuelve), así la suite es
determinística con o sin internet: _host_is_private los trata como públicos
(sin resolución) y las requests "salen" solo hacia el mock.
"""

from __future__ import annotations

import httpx
import pytest

import tools


def _html(text: str = "Contenido de prueba") -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": "text/html; charset=utf-8"},
        html=f"<html><body><p>{text}</p></body></html>",
    )


def _redirect(location: str, status: int = 302) -> httpx.Response:
    return httpx.Response(status, headers={"location": location})


def _install_mock(monkeypatch, handler, seen: list[str]) -> None:
    """
    Sustituye httpx.AsyncClient (referenciado en runtime por tools.fetch_url)
    por una fábrica que inyecta un MockTransport. `seen` registra cada URL
    que el handler recibe, para afirmar que una dirección interna JAMÁS fue
    consultada.
    """
    real_client = httpx.AsyncClient

    def factory(**kwargs):
        kwargs.pop("follow_redirects", None)

        def mock_handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return handler(request)

        return real_client(transport=httpx.MockTransport(mock_handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


async def test_fetch_url_host_privado_directo_sigue_bloqueado(monkeypatch):
    """Regresión del comportamiento previo: la URL original privada se rechaza."""
    seen: list[str] = []
    _install_mock(monkeypatch, lambda request: _html(), seen)

    result = await tools.fetch_url("http://127.0.0.1:7981/api/secret")

    assert result["ok"] is False
    assert "red interna" in result["content"]
    assert seen == []  # ni siquiera se lanzó la request


async def test_fetch_url_redirect_hacia_loopback_bloqueado(monkeypatch):
    """El caso D1: sitio público que 302 hacia 127.0.0.1."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "pagina.example":
            return _redirect("http://127.0.0.1:7981/api/secret")
        return _html()

    _install_mock(monkeypatch, handler, seen)

    result = await tools.fetch_url("https://pagina.example/articulo")

    assert result["ok"] is False
    assert "red interna" in result["content"]
    # La dirección interna nunca fue consultada:
    assert all("127.0.0.1" not in u for u in seen)
    assert len(seen) == 1


async def test_fetch_url_redirect_relativo_mismo_host_se_sigue(monkeypatch):
    """Los redirects legítimos (relativos, mismo host) siguen funcionando."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/articulo":
            return _redirect("/articulo/final")
        return _html("Página final")

    _install_mock(monkeypatch, handler, seen)

    result = await tools.fetch_url("https://pagina.example/articulo")

    assert result["ok"] is True
    assert len(seen) == 2
    assert all("pagina.example" in u for u in seen)
    assert seen[1].endswith("/articulo/final")


async def test_fetch_url_redirect_otro_host_publico_se_sigue(monkeypatch):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "pagina.example":
            return _redirect("https://otra.example/final")
        return _html("Otro host")

    _install_mock(monkeypatch, handler, seen)

    result = await tools.fetch_url("https://pagina.example/articulo")

    assert result["ok"] is True
    assert seen[1] == "https://otra.example/final"


async def test_fetch_url_redirecciones_excesivas_se_abortan(monkeypatch):
    """Cadena de redirects > MAX_REDIRECTS: se abandona, sin loop infinito."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        next_n = int(request.url.path.rsplit("/", 1)[-1]) + 1
        return _redirect(f"/r/{next_n}")

    _install_mock(monkeypatch, handler, seen)

    result = await tools.fetch_url("https://pagina.example/r/0")

    assert result["ok"] is False
    assert "redirecciones" in result["content"].lower()
    assert len(seen) == 1 + tools.MAX_REDIRECTS


async def test_fetch_url_redirect_a_esquema_no_http_bloqueado(monkeypatch):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return _redirect("file:///etc/passwd")

    _install_mock(monkeypatch, handler, seen)

    result = await tools.fetch_url("https://pagina.example/articulo")

    assert result["ok"] is False
    assert "no es una URL http(s) válida" in result["content"]
    assert len(seen) == 1


async def test_fetch_url_redirect_privado_permitido_con_optin(monkeypatch):
    """
    allow_private_hosts=True (opt-in del usuario) también aplica a las
    redirecciones: el check se salta igual que en la URL original.
    """
    original_settings = tools._settings
    fake_settings = {**original_settings(), "allow_private_hosts": True}
    monkeypatch.setattr(tools, "_settings", lambda: fake_settings)

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "pagina.example":
            return _redirect("http://127.0.0.1:7981/api/secret")
        return _html("Dato interno")

    _install_mock(monkeypatch, handler, seen)

    result = await tools.fetch_url("https://pagina.example/articulo")

    assert result["ok"] is True
    assert seen[-1] == "http://127.0.0.1:7981/api/secret"
