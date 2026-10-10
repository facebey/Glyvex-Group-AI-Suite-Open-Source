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
import launcher
import llm_metrics
import logsetup
import metrics
import metrics_export
import models
import runtime_api
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

APP_VERSION = "0.7.7-beta"
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
    stt.unload_model()


app = FastAPI(title="Glyvex-AI-Suite", version=APP_VERSION, lifespan=lifespan)

def _cors_origins() -> list[str]:
    """
    Orígenes permitidos, desde GLYVEX_CORS_ORIGINS (lista separada por comas).

    Cada instancia sirve su frontend en un puerto distinto, así que la lista no
    puede ser fija. El default mantiene el valor histórico para que una
    instalación que no setea la variable siga funcionando igual que antes.

    "*" se acepta pero se avisa: con allow_credentials=True los navegadores
    rechazan esa combinación, así que es casi siempre un error de
    configuración y conviene que quede en el log en vez de fallar en silencio
    desde el browser.
    """
    raw = os.environ.get("GLYVEX_CORS_ORIGINS", "").strip()
    if not raw:
        return ["http://localhost:5173"]
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    if "*" in origins:
        logger.warning(
            "GLYVEX_CORS_ORIGINS='*' junto con allow_credentials=True: los "
            "navegadores rechazan esa combinación. Listá los orígenes."
        )
    return origins or ["http://localhost:5173"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
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
    logger.info("config actualizada: %s", _redact_secrets(data))
    return config.data


@api_router.post("/config/reset")
async def reset_config() -> dict[str, Any]:
    data = await config.reset()
    logger.info("config reseteada a defaults: %s", sorted(data.keys()))
    return data


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
api_router.include_router(llm_metrics.router, prefix="/llm-metrics", tags=["llm-metrics"])
api_router.include_router(stt.router, prefix="/stt", tags=["stt"])
api_router.include_router(stt_runtime_api.router, prefix="/stt/runtime", tags=["stt-runtime"])
api_router.include_router(tts.router, prefix="/tts", tags=["tts"])
api_router.include_router(piper_runtime_api.router, prefix="/tts/piper", tags=["tts-piper"])
api_router.include_router(kokoro_runtime_api.router, prefix="/tts/kokoro", tags=["tts-kokoro"])
api_router.include_router(runtime_api.router, prefix="/runtime", tags=["runtime"])

app.include_router(api_router)

if FRONTEND_DIST.exists():
    # T5.2: las rutas de la SPA viven en react-router (/launcher, /monitor,
    # ...). El fallback sirve index.html para CUALQUIER ruta GET que no sea
    # API ni un archivo real; sin esto, refrescar el empaquetado (F5 o el
    # "Actualizar" del menú de contexto de WebView2) en un módulo distinto
    # del Chat caía en 404 {"detail":"Not Found"}. La API se registra antes
    # y gana el matching; /assets pasa por StaticFiles (sanitiza el path).
    if (FRONTEND_DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        # /api/* que no matche ningún route sigue siendo 404 JSON (la
        # fallback de SPA no tapa a la API; el frontend espera JSON).
        if full_path == "api" or full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return FileResponse(FRONTEND_DIST / "index.html")


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
