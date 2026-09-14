"""
main.py — App principal de Glyvex-AI-Suite.
"""

from __future__ import annotations

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
from database import init_db
import benchmark
import chat
import launcher
import metrics
import models
import stt
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"

APP_VERSION = "0.1.0"
APP_NAME = "Glyvex-AI-Suite"
APP_START_MONOTONIC = time.monotonic()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await config.load()
    await init_db()
    yield
    await config.save()
    await launcher.manager.shutdown()
    stt.unload_model()


app = FastAPI(title="Glyvex-AI-Suite", version=APP_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
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
    config.update(patch.model_dump(exclude_unset=True))
    await config.save()
    return config.data


@api_router.post("/config/reset")
async def reset_config() -> dict[str, Any]:
    return await config.reset()


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
api_router.include_router(stt.router, prefix="/stt", tags=["stt"])

app.include_router(api_router)

if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=7860, loop='asyncio')
