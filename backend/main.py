"""
main.py — App principal de Glyvex-AI-Suite.
"""

from __future__ import annotations

import logging
import os
import platform
import socket
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.gzip import GZipMiddleware

from config import config
from paths import BUNDLE_DIR, DATA_DIR, ENV_NAME, FROZEN
from database import db_all_attachment_ids, init_db
import benchmark
import chat
import agent_bridge
import launcher
import llm_metrics
import logsetup
import metrics
import metrics_export
import models
import runtime_api
import session_tokens
import themes
import attachments
import piper_runtime_api
import kokoro_runtime_api
import stt
import stt_runtime_api
import tts
import asyncio
import sys
import warnings

if sys.platform == "win32":
    # Subprocess en Windows necesita el event loop proactor. La clase está
    # deprecada desde 3.12 (y se elimina en 3.15, donde el selector ya
    # soporta subprocess): se silencia el warning y se salta si ya no existe.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        except AttributeError:
            pass

# En dev: frontend/dist del repo. En el bundle: el dist empaquetado
# (glyvex.spec, datas → _internal/frontend/dist).
FRONTEND_DIST = BUNDLE_DIR / "frontend" / "dist"

APP_VERSION = "0.11.0-alpha3"
APP_NAME = "Glyvex-AI-Suite"
APP_START_MONOTONIC = time.monotonic()

logger = logging.getLogger("glyvex.app")


# Campos cuyo valor no se escribe en el log. Se compara por nombre de campo
# (exacto o terminado en _<nombre>), no por substring: "image_tokens_estimate"
# contiene "token" y no es un secreto.
_SECRET_FIELDS = ("api_key", "token", "password")


def _is_secret_field(key: object) -> bool:
    name = str(key).lower()
    return any(name == f or name.endswith("_" + f) for f in _SECRET_FIELDS)


def _redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: "<redactado>" if _is_secret_field(k) and v else _redact_secrets(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_secrets(v) for v in value]
    return value


@asynccontextmanager
async def lifespan(app: FastAPI):
    logs_dir = logsetup.setup()
    await config.load()
    # Costo de sesión (MON-2): el total del chat sobrevive reinicios.
    await session_tokens.session_tokens.load()
    await init_db()
    # Exportador a InfluxDB antes que los pollers: ellos le encolan ventanas.
    await metrics_export.exporter.start()
    # Poller de métricas en segundo plano: guarda histórico en
    # <DATA_DIR>/metrics.db aunque el Monitor no esté abierto
    # (ver metrics.MetricsManager.start).
    await metrics.manager.start()
    # Métricas de llama-server (/metrics): después de metrics.manager.start()
    # porque escriben en el mismo store (<DATA_DIR>/metrics.db).
    await llm_metrics.manager.start()
    logger.info(
        "arranque: version=%s entorno=%s puerto=%s data=%s logs=%s",
        APP_VERSION,
        ENV_NAME,
        os.environ.get("GLYVEX_PORT", "7981"),
        DATA_DIR,
        logs_dir,
    )

    # Limpieza de adjuntos huérfanos: imágenes en disco que ya no referencia
    # ninguna conversación. Se acumulan sobre todo por borradores que nunca
    # se enviaron, que no quedan registrados en ningún lado.
    try:
        keep_ids = await db_all_attachment_ids()
        # I/O de disco síncrono fuera del event loop (D7).
        removed = await asyncio.to_thread(attachments.purge_orphans, keep_ids)
        if removed:
            logger.info("%d adjunto(s) huérfano(s) eliminados", removed)
    except Exception as exc:  # noqa: BLE001 — nunca debe impedir arrancar
        logger.warning("no se pudo limpiar adjuntos huérfanos: %s", exc)

    yield
    logger.info("shutdown: guardando config y cerrando procesos")
    await config.save()
    await llm_metrics.manager.stop()
    await metrics.manager.stop()
    # Último: los managers encolan sus ventanas pendientes al detenerse.
    await metrics_export.exporter.stop()
    await launcher.manager.shutdown()
    # AG-1: ultimo paso — retirar de la config de OpenCode los bloques que esta
    # instancia publico (los servers que gestione ya no van a seguir sirviendo).
    await agent_bridge.cleanup_all()
    stt.unload_model()


app = FastAPI(title="Glyvex-AI-Suite", version=APP_VERSION, lifespan=lifespan)

def _cors_origins() -> list[str]:
    """
    Orígenes permitidos, desde GLYVEX_CORS_ORIGINS (lista separada por comas).

    Cada instancia sirve su frontend en un puerto distinto, así que la lista no
    puede ser fija. El default mantiene el valor histórico para que una
    instalación que no setea la variable siga funcionando igual que antes.

    "*" se acepta sin aviso (E7): la app no usa cookies ni autenticación y el
    middleware va con allow_credentials=False, así que la combinación queda
    explícitamente válida y el único control es la lista de orígenes.
    """
    raw = os.environ.get("GLYVEX_CORS_ORIGINS", "").strip()
    if not raw:
        return ["http://localhost:5173"]
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    return origins or ["http://localhost:5173"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    # E7 (SEC-1): sin credenciales. Ningún endpoint usa cookies ni auth y el
    # frontend nunca manda credentials: 'include'; allow_credentials=True solo
    # sumaba superficie (y invalidaba el "*" con un warning en el log).
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Comprime JS/CSS/JSON del bundle en el cable (~70% de ahorro en el chunk de
# ~1.3 MB). SSE y WebSocket quedan fuera: exclude_content_types incluye
# text/event-stream por defecto y la middleware no toca conexiones no-HTTP.
app.add_middleware(GZipMiddleware, minimum_size=1024)

api_router = APIRouter(prefix="/api")


class HealthResponse(BaseModel):
    status: str
    version: str


class ConfigUpdate(BaseModel):
    model_config = {"extra": "allow"}


@api_router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version=APP_VERSION)


@api_router.get("/config")
async def get_config() -> dict[str, Any]:
    return config.data


@api_router.post("/config")
async def update_config(patch: ConfigUpdate) -> dict[str, Any]:
    data = patch.model_dump(exclude_unset=True)
    config.update(data)
    await config.save()
    # AG-1: si el toggle se apaga, las claves publicadas en la config de OpenCode
    # se retiran de inmediato: no conviene dejar bloques que ya no administra
    # ningun bridge activo.
    bridge_patch = data.get("agent_bridge")
    if isinstance(bridge_patch, dict) and bridge_patch.get("enabled") is False:
        await agent_bridge.cleanup_all()
    logger.info("config actualizada: %s", _redact_secrets(data))
    return config.data


@api_router.post("/config/reset")
async def reset_config() -> dict[str, Any]:
    data = await config.reset()
    logger.info("config reseteada a defaults: %s", sorted(data.keys()))
    return data


@api_router.get("/config/pricing-defaults")
async def get_pricing_defaults() -> dict[str, Any]:
    """Precios de nube por defecto (defaults de config.py): el botón
    "Restaurar valores por defecto" de Config los trae de acá para que la
    fuente de verdad sea una sola."""
    return config.defaults()["cloud_pricing"]


@api_router.get("/info")
async def get_info() -> dict[str, Any]:
    active_models = [
        p.model_name for p in launcher.manager.status_all() if p.state == "running"
    ]
    return {
        "app_name": APP_NAME,
        "version": APP_VERSION,
        "python_version": platform.python_version(),
        "active_models": active_models,
        "uptime_s": round(time.monotonic() - APP_START_MONOTONIC, 1),
    }


@api_router.get("/state")
async def get_state() -> dict[str, Any]:
    active_processes = [
        p.model_dump() for p in launcher.manager.status_all() if p.state != "stopped"
    ]
    gpu, gpu_error = metrics.manager.collector.collect_gpu()
    return {
        "active_processes": active_processes,
        "gpu": [g.model_dump() for g in gpu] if gpu else None,
        "gpu_error": gpu_error,
    }


api_router.include_router(models.router, prefix="/models", tags=["models"])
api_router.include_router(launcher.router, prefix="/launcher", tags=["launcher"])
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(benchmark.router, prefix="/benchmark", tags=["benchmark"])
api_router.include_router(metrics.router, prefix="/metrics", tags=["metrics"])
api_router.include_router(metrics_export.router, prefix="/metrics", tags=["metrics-export"])
api_router.include_router(session_tokens.router, prefix="/metrics", tags=["session-tokens"])
api_router.include_router(llm_metrics.router, prefix="/llm-metrics", tags=["llm-metrics"])
api_router.include_router(stt.router, prefix="/stt", tags=["stt"])
api_router.include_router(stt_runtime_api.router, prefix="/stt/runtime", tags=["stt-runtime"])
api_router.include_router(tts.router, prefix="/tts", tags=["tts"])
api_router.include_router(piper_runtime_api.router, prefix="/tts/piper", tags=["tts-piper"])
api_router.include_router(kokoro_runtime_api.router, prefix="/tts/kokoro", tags=["tts-kokoro"])
api_router.include_router(runtime_api.router, prefix="/runtime", tags=["runtime"])
api_router.include_router(agent_bridge.router, prefix="/agent-bridge", tags=["agent-bridge"])
api_router.include_router(themes.router, prefix="/themes", tags=["themes"])

app.include_router(api_router)

# Caché del empaquetado: la webview carga la SPA desde el origen del backend
# (http://127.0.0.1:<puerto>, puerto fijo por diseño — PERS-1). WebView2
# persiste su caché de disco entre arranques POR ORIGEN y, sin cabeceras
# explícitas, aplica frescura heurística: tras un update in-app el siguiente
# arranque sirve el index.html y los chunks de la versión anterior, y la UI
# nueva no aparece hasta recargar a mano (F5). Estas cabeceras cierran esa
# puerta: index.html y los archivos sin hash de public/ se revalidan siempre;
# los assets de Vite (/assets) llevan hash de contenido en el nombre y son
# inmutables por construcción.
_CACHE_IMMUTABLE = "public, max-age=31536000, immutable"
_CACHE_REVALIDATE = "no-cache"

# public/ trae archivos sin hash (fuentes, licencias). El media type se fija
# acá porque el SO no siempre tiene registrado el MIME de .woff2.
_STATIC_MEDIA_TYPES = {
    ".woff2": "font/woff2",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".txt": "text/plain",
}


class ImmutableStaticFiles(StaticFiles):
    """Todo lo que vive en /assets lleva hash de contenido de Vite en el
    nombre: si el nombre cambió, el contenido cambió; si no, es seguro
    servirlo de caché por un año sin preguntar."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code in (200, 206, 304):
            response.headers["Cache-Control"] = _CACHE_IMMUTABLE
        return response


def mount_frontend(app: FastAPI, dist: Path) -> None:
    """Serve la SPA construida. Recibe el dir de dist para poder probarse
    con un dist de juguete.

    T5.2: las rutas de la SPA viven en react-router (/launcher, /monitor,
    ...). El fallback sirve index.html para CUALQUIER ruta GET que no sea
    API ni un archivo real; sin esto, refrescar el empaquetado (F5 o el
    "Actualizar" del menú de contexto de WebView2) en un módulo distinto
    del Chat caía en 404 {"detail":"Not Found"}. La API se registra antes
    y gana el matching; /assets pasa por StaticFiles (sanitiza el path).
    """
    if (dist / "assets").is_dir():
        app.mount("/assets", ImmutableStaticFiles(directory=str(dist / "assets")), name="assets")

    @app.get("/gx-version.json", include_in_schema=False)
    async def gx_version():
        # Sello de la construcción actualmente en disco (lo escribe el build;
        # ver frontend/vite.config.js): el frontend que corre en la webview
        # compara esta versión con la suya. Si difieren, la webview está
        # mostrando un bundle cacheado de una instalación anterior y fuerza
        # un reload antes de seguir con la UI vieja.
        stamp = dist / "gx-version.json"
        if not stamp.is_file():
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return FileResponse(
            stamp, media_type="application/json", headers={"Cache-Control": "no-store"}
        )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        # /api/* que no matche ningún route sigue siendo 404 JSON (la
        # fallback de SPA no tapa a la API; el frontend espera JSON).
        if full_path == "api" or full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        # Archivos reales en la raíz de dist (public/: fuentes, licencias):
        # hoy caían en el fallback de index.html y el navegador las descartaba
        # por tipo. Se sirven; no llevan hash en el nombre, así que revalidan.
        if full_path:
            candidate = (dist / full_path).resolve()
            try:
                candidate.relative_to(dist.resolve())
            except ValueError:
                pass  # fuera de dist (path traversal): se trata como ruta SPA
            else:
                if candidate.is_file():
                    media = _STATIC_MEDIA_TYPES.get(candidate.suffix.lower())
                    return FileResponse(
                        candidate,
                        media_type=media,
                        headers={"Cache-Control": _CACHE_REVALIDATE},
                    )
        # index.html: apunta a chunks con hash, así que debe revalidarse en
        # cada carga; si la webview reusa un index viejo, la UI del update
        # no aparece hasta F5 (causa raíz del staleness post-update).
        return FileResponse(
            dist / "index.html", headers={"Cache-Control": _CACHE_REVALIDATE}
        )


if FRONTEND_DIST.exists():
    mount_frontend(app, FRONTEND_DIST)


def _bind_with_fallback(host: str, preferred: int) -> tuple[socket, int]:
    """
    Bind de `preferred` con escalera de vecinos. Devuelve (socket ya bound,
    puerto final). Con `preferred == 0` se pide uno libre al SO directamente.

    El orden importa para la persistencia de la UI (PERS-1): el primer
    candidato es siempre el que pidió el orquestador (7981), de modo que el
    origen de la webview sea el mismo en cada arranque; los vecinos solo
    entran cuando algo ocupa el preferido.
    """
    candidates = [preferred] if preferred else [0]
    candidates += [p for p in range(preferred + 1, preferred + 10)]
    last_err: OSError | None = None
    for candidate in candidates:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, candidate))
            return s, s.getsockname()[1]
        except OSError as err:
            last_err = err
            s.close()
    raise last_err  # type: ignore[misc]


if __name__ == '__main__':
    # El import redirige stdout/stderr al log cuando el bundle corre sin
    # consola (no hace nada en dev). Debe ir antes de uvicorn.run, que
    # escribe en stdout.
    import glyvex_console_hook  # noqa: F401
    import uvicorn
    # 127.0.0.1 por defecto (app local, sin autenticación); GLYVEX_HOST y
    # GLYVEX_PORT lo sobreescriben (mismo contrato que start.cmd/start.ps1).
    host = os.environ.get("GLYVEX_HOST", "127.0.0.1")
    port = int(os.environ.get("GLYVEX_PORT", "7981"))
    # Pre-bind con fallback: la webview vive en http://127.0.0.1:<puerto> y
    # localStorage es POR ORIGEN, así que el puerto debe ser estable entre
    # arranques (PERS-1). Se intenta el puerto pedido, luego el vecindario
    # (+1…+9: segunda instancia de la app, dev corriendo a la vez) y solo si
    # todo falla el SO asigna uno libre. El socket va pre-boundeado a
    # uvicorn: el puerto reportado es el realmente en uso (sin ventana entre
    # bind y re-bind que otro proceso podría ocupar).
    sock, port = _bind_with_fallback(host, port)
    # Marker para el orquestador: línea limpia con el puerto final. El
    # console hook (frozen sin consola) redirige stdout al log a menos que
    # GLYVEX_STDOUT_PIPE=1 (Tauri), en cuyo caso Rust lo lee por pipe.
    print(f"#PORT_ASSIGNED:{port}#", flush=True)
    # GLYVEX_NO_BROWSER lo exporta el launcher de Tauri: la ventana la da la
    # shell y no hay que abrir el navegador.
    _no_browser = os.environ.get("GLYVEX_NO_BROWSER", "").strip().lower() in ("1", "true", "yes")
    if FROZEN and not _no_browser:
        # El atajo del instalador apunta a este exe: cuando la API responde,
        # abrir la SPA en el navegador predeterminado (hasta que exista la
        # ventana nativa de Tauri).
        import threading
        import urllib.request
        import webbrowser

        def _open_when_ready() -> None:
            base = f"http://{os.environ.get('GLYVEX_HOST', '127.0.0.1')}:{port}"
            for _ in range(90):
                try:
                    with urllib.request.urlopen(base + "/api/health", timeout=2) as r:
                        if r.status == 200:
                            webbrowser.open(base)
                            return
                except Exception:
                    pass
                time.sleep(1)

        threading.Thread(target=_open_when_ready, daemon=True).start()
    # uvicorn hereda el socket ya bound (el puerto reportado arriba es el
    # definitivo). `sockets` va en Server.run(), no en Config (uvicorn >= 0.10).
    uvicorn.Server(uvicorn.Config(app, loop='asyncio')).run(sockets=[sock])
