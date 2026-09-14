"""
tools.py — Tools de búsqueda y navegación web para el chat (Bloque 2 / M3).

Expone dos funciones al modelo:

- web_search(query, max_results) — busca en internet a través del proveedor
  configurado en `tools.search_provider`.
- fetch_url(url) — trae una página y extrae el texto legible con trafilatura
  (o readability-lxml como alternativa).

PROVEEDORES DE BÚSQUEDA

La búsqueda no está atada a ningún servicio. Hay cuatro backends y el default
no necesita infraestructura, porque la app se empaqueta con Tauri y todo lo
que requiera un daemon aparte (Docker) dejaría de funcionar dentro del
bundle:

  ddgs     (default) DuckDuckGo vía el paquete `ddgs`. Cero configuración,
                     cero servidor, cero API key: entra en el bundle como
                     una dependencia Python más. A cambio scrapea, así que
                     con uso intensivo puede recibir límites de tasa.
  searxng            Instancia propia de SearXNG. Sin límites de tasa y sin
                     que las búsquedas salgan con tu identidad, pero hay que
                     levantarla (ver docs/busqueda-web/).
  brave              Brave Search API. Necesita key.
  tavily             Tavily. Necesita key. Pensada para agentes: devuelve
                     snippets más largos que un buscador tradicional.

No hay fallback automático entre proveedores: si elegiste SearXNG por
privacidad, caer en silencio a DuckDuckGo sería exactamente lo contrario de
lo que pediste. Cuando el proveedor configurado falla, el error dice qué
pasó y cómo cambiarlo.

Las API keys se leen primero de las variables de entorno BRAVE_API_KEY y
TAVILY_API_KEY, y recién después de config.json — así no hace falta dejar un
secreto escrito en un archivo que se sincroniza o se comparte.

SEGURIDAD

`fetch_url` recibe la URL del modelo, no del usuario, y un resultado de
búsqueda puede intentar inyectarle una dirección interna. Por eso las
direcciones privadas (loopback, RFC1918, link-local) se bloquean salvo que
se habiliten explícitamente en `tools.allow_private_hosts`.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

from config import config

# ---------------------------------------------------------------------------
# Esquemas que se le mandan al modelo
# ---------------------------------------------------------------------------

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Busca en internet y devuelve título, URL y resumen de los "
                "resultados. Usala cuando necesites información actual, "
                "posterior a tu fecha de corte, o datos que no conocés con "
                "certeza. Después podés leer una página concreta con fetch_url."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Términos de búsqueda, concisos y específicos.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Cantidad de resultados a devolver (1-10).",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": (
                "Trae una página web y devuelve su texto principal, sin menús "
                "ni publicidad. Usala para leer en detalle un resultado de "
                "web_search cuando el resumen no alcance."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL completa, con http:// o https://.",
                    },
                },
                "required": ["url"],
            },
        },
    },
]

TOOL_NAMES = {spec["function"]["name"] for spec in TOOL_SPECS}

PROVIDER_LABELS = {
    "ddgs": "DuckDuckGo",
    "searxng": "SearXNG",
    "brave": "Brave Search",
    "tavily": "Tavily",
}

DEFAULT_PROVIDER = "ddgs"


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------


def _settings() -> dict[str, Any]:
    provider = str(config.get("tools.search_provider", DEFAULT_PROVIDER)).lower()
    if provider not in PROVIDER_LABELS:
        provider = DEFAULT_PROVIDER
    return {
        "provider": provider,
        "searxng_url": str(config.get("tools.searxng_url", "http://127.0.0.1:8888")),
        "max_results": int(config.get("tools.max_results", 5)),
        "fetch_max_chars": int(config.get("tools.fetch_max_chars", 8000)),
        "timeout_s": float(config.get("tools.timeout_s", 20.0)),
        "allow_private_hosts": bool(config.get("tools.allow_private_hosts", False)),
        "user_agent": str(config.get("tools.user_agent", "Glyvex-AI-Suite/0.1")),
        "region": str(config.get("tools.region", "wt-wt")),
        # Entorno primero: un secreto no debería quedar escrito en config.json.
        "brave_api_key": os.environ.get("BRAVE_API_KEY")
        or str(config.get("tools.brave_api_key", "")),
        "tavily_api_key": os.environ.get("TAVILY_API_KEY")
        or str(config.get("tools.tavily_api_key", "")),
    }


class ProviderError(Exception):
    """El proveedor no pudo responder. El mensaje va al modelo tal cual."""


# ---------------------------------------------------------------------------
# Proveedores — todos devuelven [{title, url, snippet}]
# ---------------------------------------------------------------------------


def _ddgs_sync(query: str, limit: int, region: str, timeout: float) -> list[dict[str, str]]:
    """
    `ddgs` es sync, así que esto corre en un thread.

    El paquete se llamaba `duckduckgo_search` y se renombró a `ddgs`; se
    prueban los dos para no romperle a quien tenga el viejo instalado.
    """
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # type: ignore[no-redef]
        except ImportError:
            raise ProviderError(
                "El paquete `ddgs` no está instalado. Instalalo con "
                "`pip install ddgs` o elegí otro proveedor en "
                "tools.search_provider."
            ) from None

    try:
        with DDGS(timeout=int(timeout)) as client:
            raw = list(client.text(query, region=region, max_results=limit))
    except TypeError:
        # Las firmas cambiaron entre versiones del paquete; el mínimo común
        # denominador es text(query, max_results).
        try:
            with DDGS() as client:
                raw = list(client.text(query, max_results=limit))
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(
                f"DuckDuckGo no respondió ({type(exc).__name__})."
            ) from exc
    except Exception as exc:  # noqa: BLE001 — límite de tasa, captcha, HTML cambiado
        raise ProviderError(
            f"DuckDuckGo no respondió ({type(exc).__name__}). Puede ser un "
            "límite de tasa temporal: esperá un momento o configurá otro "
            "proveedor en tools.search_provider."
        ) from exc

    results: list[dict[str, str]] = []
    for item in raw[:limit]:
        results.append(
            {
                "title": (item.get("title") or "").strip(),
                "url": (item.get("href") or item.get("url") or "").strip(),
                "snippet": " ".join(
                    (item.get("body") or item.get("description") or "").split()
                ),
            }
        )
    return results


async def _search_ddgs(query: str, limit: int, settings: dict[str, Any]) -> list[dict[str, str]]:
    return await asyncio.to_thread(
        _ddgs_sync, query, limit, settings["region"], settings["timeout_s"]
    )


async def _search_searxng(
    query: str, limit: int, settings: dict[str, Any]
) -> list[dict[str, str]]:
    base = settings["searxng_url"].rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=settings["timeout_s"]) as client:
            res = await client.get(
                f"{base}/search",
                params={"q": query, "format": "json", "safesearch": "0", "language": "auto"},
                headers={"User-Agent": settings["user_agent"]},
            )
    except httpx.ConnectError as exc:
        raise ProviderError(
            f"No hay una instancia de SearXNG escuchando en {base}. Levantala "
            "(ver docs/busqueda-web/) o cambiá tools.search_provider a `ddgs`, que "
            "no necesita servidor."
        ) from exc
    except (httpx.TimeoutException, httpx.HTTPError) as exc:
        raise ProviderError(f"SearXNG no respondió: {exc}") from exc

    if res.status_code == 403:
        raise ProviderError(
            "SearXNG devolvió 403 para el formato JSON. Agregá `json` a "
            "`search.formats` en settings.yml y reiniciá el contenedor: por "
            "defecto solo sirve HTML."
        )
    if res.status_code >= 400:
        raise ProviderError(f"SearXNG devolvió {res.status_code}.")

    try:
        payload = res.json()
    except ValueError as exc:
        raise ProviderError(
            "SearXNG devolvió HTML en vez de JSON. Falta habilitar `json` en "
            "`search.formats` de settings.yml."
        ) from exc

    return [
        {
            "title": (item.get("title") or "").strip(),
            "url": (item.get("url") or "").strip(),
            "snippet": " ".join((item.get("content") or "").split()),
        }
        for item in (payload.get("results") or [])[:limit]
    ]


async def _search_brave(query: str, limit: int, settings: dict[str, Any]) -> list[dict[str, str]]:
    key = settings["brave_api_key"]
    if not key:
        raise ProviderError(
            "Falta la API key de Brave Search. Ponela en la variable de "
            "entorno BRAVE_API_KEY o en tools.brave_api_key."
        )

    try:
        async with httpx.AsyncClient(timeout=settings["timeout_s"]) as client:
            res = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": limit},
                headers={"Accept": "application/json", "X-Subscription-Token": key},
            )
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as exc:
        raise ProviderError(f"Brave Search no respondió: {exc}") from exc

    if res.status_code in (401, 403):
        raise ProviderError("Brave Search rechazó la API key.")
    if res.status_code == 429:
        raise ProviderError("Brave Search devolvió 429: se agotó la cuota del plan.")
    if res.status_code >= 400:
        raise ProviderError(f"Brave Search devolvió {res.status_code}.")

    try:
        payload = res.json()
    except ValueError as exc:
        raise ProviderError("Brave Search devolvió una respuesta ilegible.") from exc

    return [
        {
            "title": (item.get("title") or "").strip(),
            "url": (item.get("url") or "").strip(),
            "snippet": " ".join((item.get("description") or "").split()),
        }
        for item in ((payload.get("web") or {}).get("results") or [])[:limit]
    ]


async def _search_tavily(query: str, limit: int, settings: dict[str, Any]) -> list[dict[str, str]]:
    key = settings["tavily_api_key"]
    if not key:
        raise ProviderError(
            "Falta la API key de Tavily. Ponela en la variable de entorno "
            "TAVILY_API_KEY o en tools.tavily_api_key."
        )

    try:
        async with httpx.AsyncClient(timeout=settings["timeout_s"]) as client:
            res = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": key,
                    "query": query,
                    "max_results": limit,
                    "search_depth": "basic",
                },
            )
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as exc:
        raise ProviderError(f"Tavily no respondió: {exc}") from exc

    if res.status_code in (401, 403):
        raise ProviderError("Tavily rechazó la API key.")
    if res.status_code == 429:
        raise ProviderError("Tavily devolvió 429: se agotó la cuota del plan.")
    if res.status_code >= 400:
        raise ProviderError(f"Tavily devolvió {res.status_code}.")

    try:
        payload = res.json()
    except ValueError as exc:
        raise ProviderError("Tavily devolvió una respuesta ilegible.") from exc

    return [
        {
            "title": (item.get("title") or "").strip(),
            "url": (item.get("url") or "").strip(),
            "snippet": " ".join((item.get("content") or "").split()),
        }
        for item in (payload.get("results") or [])[:limit]
    ]


PROVIDERS = {
    "ddgs": _search_ddgs,
    "searxng": _search_searxng,
    "brave": _search_brave,
    "tavily": _search_tavily,
}


# ---------------------------------------------------------------------------
# web_search
# ---------------------------------------------------------------------------


async def web_search(query: str, max_results: int | None = None) -> dict[str, Any]:
    settings = _settings()
    query = (query or "").strip()
    if not query:
        return _failure("web_search", "La búsqueda llegó vacía.")

    limit = max(1, min(10, int(max_results or settings["max_results"])))
    provider = settings["provider"]
    label = PROVIDER_LABELS[provider]

    try:
        results = await PROVIDERS[provider](query, limit, settings)
    except ProviderError as exc:
        return _failure("web_search", str(exc))
    except Exception as exc:  # noqa: BLE001 — un proveedor roto no corta el chat
        return _failure("web_search", f"{label} falló: {type(exc).__name__}: {exc}")

    results = [r for r in results if r["url"]]

    if not results:
        return {
            "ok": True,
            "name": "web_search",
            "content": f"La búsqueda «{query}» no devolvió resultados.",
            "summary": f"Sin resultados para «{query}»",
            "sources": [],
            "provider": provider,
        }

    lines: list[str] = [f"Resultados para «{query}» ({label}):", ""]
    sources: list[dict[str, str]] = []
    for index, item in enumerate(results, start=1):
        title = item["title"] or "(sin título)"
        lines.append(f"{index}. {title}\n   {item['url']}\n   {item['snippet']}")
        sources.append({"title": title, "url": item["url"]})

    return {
        "ok": True,
        "name": "web_search",
        "content": "\n".join(lines),
        "summary": f"{len(results)} resultado(s) para «{query}»",
        "sources": sources,
        "provider": provider,
    }


# ---------------------------------------------------------------------------
# fetch_url
# ---------------------------------------------------------------------------


def _host_is_private(hostname: str) -> bool:
    """
    True si el hostname resuelve a una dirección no ruteable en internet.

    Se resuelve el DNS acá y no se confía en el literal: `evil.example.com`
    puede apuntar a 127.0.0.1 igual que `localhost`.
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        # Si no resuelve, la request va a fallar sola; no la bloqueamos acá.
        return False

    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            return True
    return False


def _extract_readable(html: str, url: str) -> tuple[str, str]:
    """
    Devuelve (texto, motor). Se prueba trafilatura, después readability-lxml,
    y si no hay ninguna instalada se cae a un strip de etiquetas.
    """
    try:
        import trafilatura

        text = trafilatura.extract(
            html, url=url, include_comments=False, include_tables=True
        )
        if text and text.strip():
            return text.strip(), "trafilatura"
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 — trafilatura rompe con HTML muy roto
        pass

    try:
        from readability import Document

        summary_html = Document(html).summary()
        text = _strip_tags(summary_html)
        if text.strip():
            return text.strip(), "readability"
    except ImportError:
        pass
    except Exception:  # noqa: BLE001
        pass

    return _strip_tags(html).strip(), "fallback"


def _strip_tags(html: str) -> str:
    """Último recurso: sacar script/style y etiquetas, colapsar espacios."""
    import re

    cleaned = re.sub(
        r"<(script|style|nav|footer|header)[^>]*>.*?</\1>",
        " ",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = (
        cleaned.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    lines = [" ".join(line.split()) for line in cleaned.splitlines()]
    return "\n".join(line for line in lines if line)


async def fetch_url(url: str) -> dict[str, Any]:
    settings = _settings()
    url = (url or "").strip()

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return _failure("fetch_url", f"`{url}` no es una URL http(s) válida.")

    if not settings["allow_private_hosts"]:
        if await asyncio.to_thread(_host_is_private, parsed.hostname):
            return _failure(
                "fetch_url",
                f"`{parsed.hostname}` apunta a una dirección de red interna y "
                "el acceso a direcciones privadas está deshabilitado.",
            )

    try:
        async with httpx.AsyncClient(
            timeout=settings["timeout_s"],
            follow_redirects=True,
            headers={
                "User-Agent": settings["user_agent"],
                "Accept": "text/html,application/xhtml+xml",
            },
        ) as client:
            res = await client.get(url)
    except httpx.ConnectError:
        return _failure("fetch_url", f"No se pudo conectar a {parsed.hostname}.")
    except (httpx.TimeoutException, httpx.HTTPError) as exc:
        return _failure("fetch_url", f"La página no respondió: {exc}")

    if res.status_code >= 400:
        return _failure("fetch_url", f"{url} devolvió {res.status_code}.")

    content_type = res.headers.get("content-type", "")
    if "html" not in content_type and "text" not in content_type:
        return _failure(
            "fetch_url",
            f"{url} devolvió contenido de tipo {content_type or 'desconocido'}, "
            "que no se puede leer como texto.",
        )

    text, engine = await asyncio.to_thread(_extract_readable, res.text, url)

    if not text:
        return _failure(
            "fetch_url",
            f"No se pudo extraer texto legible de {url}. Puede ser una página "
            "que se arma con JavaScript.",
        )

    limit = settings["fetch_max_chars"]
    truncated = len(text) > limit
    if truncated:
        text = text[:limit]

    header = f"Contenido de {url}"
    if truncated:
        header += f" (truncado a {limit:,} caracteres)".replace(",", ".")

    return {
        "ok": True,
        "name": "fetch_url",
        "content": f"{header}:\n\n{text}",
        "summary": f"{parsed.hostname} · {len(text):,} caracteres".replace(",", "."),
        "sources": [{"title": parsed.hostname or url, "url": url}],
        "engine": engine,
    }


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def _failure(name: str, message: str) -> dict[str, Any]:
    """
    Un error de tool no es un error del stream: se le devuelve al modelo como
    resultado para que decida si reintenta, prueba otra cosa o se lo dice al
    usuario.
    """
    return {
        "ok": False,
        "name": name,
        "content": f"Error al ejecutar {name}: {message}",
        "summary": message,
        "sources": [],
    }


async def execute_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    await config.load()

    if name == "web_search":
        return await web_search(
            query=str(arguments.get("query") or ""),
            max_results=arguments.get("max_results"),
        )
    if name == "fetch_url":
        return await fetch_url(url=str(arguments.get("url") or ""))

    return _failure(name, f"La tool `{name}` no existe.")


async def check_search_provider() -> dict[str, Any]:
    """
    Diagnóstico para la UI: ¿el proveedor configurado puede buscar ahora?

    Se hace una búsqueda real y descartable en vez de un ping: es la única
    forma de detectar el 403 de SearXNG por JSON deshabilitado, una API key
    vencida o un límite de tasa de DuckDuckGo antes de que el usuario active
    el toggle y se coma el error a mitad de una respuesta.
    """
    settings = _settings()
    provider = settings["provider"]
    label = PROVIDER_LABELS[provider]

    detail: dict[str, Any] = {"provider": provider, "provider_label": label}
    if provider == "searxng":
        detail["url"] = settings["searxng_url"].rstrip("/")

    try:
        results = await PROVIDERS[provider]("glyvex", 1, settings)
    except ProviderError as exc:
        return {**detail, "ready": False, "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {**detail, "ready": False, "reason": f"{type(exc).__name__}: {exc}"}

    if not results:
        return {
            **detail,
            "ready": False,
            "reason": f"{label} respondió pero no devolvió resultados.",
        }

    return {**detail, "ready": True, "reason": None}


def provider_catalog() -> list[dict[str, Any]]:
    """Proveedores disponibles y qué necesita cada uno, para la pantalla de configuración."""
    return [
        {
            "id": "ddgs",
            "label": "DuckDuckGo",
            "requires": None,
            "hint": "Sin configuración. Puede recibir límites de tasa con uso intensivo.",
        },
        {
            "id": "searxng",
            "label": "SearXNG",
            "requires": "instancia propia",
            "hint": "Sin límites de tasa y sin exponer tu identidad. Ver docs/busqueda-web/.",
        },
        {
            "id": "brave",
            "label": "Brave Search",
            "requires": "API key",
            "hint": "Variable de entorno BRAVE_API_KEY o tools.brave_api_key.",
        },
        {
            "id": "tavily",
            "label": "Tavily",
            "requires": "API key",
            "hint": "Variable de entorno TAVILY_API_KEY o tools.tavily_api_key.",
        },
    ]


__all__ = [
    "TOOL_SPECS",
    "TOOL_NAMES",
    "PROVIDER_LABELS",
    "execute_tool",
    "check_search_provider",
    "provider_catalog",
]
