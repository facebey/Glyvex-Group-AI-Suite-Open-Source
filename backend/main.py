"""
main.py — App principal de Glyvex-AI-Suite.
"""

from __future__ import annotations

import logging
import os
import platform
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import config
from paths import BASE_DIR, DATA_DIR, ENV_NAME
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
import stt
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

FRONTEND_DIST = BASE_DIR / "frontend" / "dist"

APP_VERSION = "0.5.0"
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
        removed = attachments.purge_orphans(await db_all_attachment_ids())
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
api_router.include_router(runtime_api.router, prefix="/runtime", tags=["runtime"])

app.include_router(api_router)

if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")


if __name__ == '__main__':
    import uvicorn
    # 127.0.0.1 por defecto (app local, sin autenticación); GLYVEX_HOST y
    # GLYVEX_PORT lo sobreescriben (mismo contrato que start.cmd/start.ps1).
    port = int(os.environ.get("GLYVEX_PORT", "7981"))
    uvicorn.run(app, host=os.environ.get("GLYVEX_HOST", "127.0.0.1"), port=port, loop='asyncio')
