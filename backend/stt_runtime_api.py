"""
stt_runtime_api.py — API del runtime de STT (whisper.cpp), T2.2.

GET  /api/stt/runtime/status   — estado del runtime de STT + probe del binario
                                  cuando está "ready" (arranca de verdad).
POST /api/stt/runtime/download — descarga con progreso por SSE (mismo patrón
                                  que /api/runtime/download). La lógica real
                                  vive en stt_runtime.py.
POST /api/stt/runtime/reset    — borra el runtime de STT gestionado.
GET    /api/stt/runtime/models                 — catálogo + estado por modelo.
POST   /api/stt/runtime/models/{name}/download — descarga con progreso por SSE.
DELETE /api/stt/runtime/models/{name}          — borra un modelo descargado.
"""

from __future__ import annotations

import asyncio
import json
import logging
import platform

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

import stt_runtime

logger = logging.getLogger("glyvex.stt_runtime_api")

router = APIRouter()


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@router.get("/status")
async def status() -> dict:
    result = stt_runtime.stt_runtime_status()
    if result["state"] == "ready":
        result["probe_ok"] = await stt_runtime.probe_stt_binary(result["binary_path"])
    return result


@router.post("/download")
async def download() -> StreamingResponse:
    if platform.system() != "Windows":
        raise HTTPException(
            status_code=400, detail="El runtime de STT es Windows-only en v1"
        )
    if stt_runtime.stt_runtime_status()["state"] == "downloading":
        raise HTTPException(status_code=409, detail="Ya hay una descarga de runtime STT en curso")
    if not stt_runtime.can_download():
        raise HTTPException(
            status_code=400, detail="No hay fuente de runtime STT con sha256 verificable"
        )

    # on_progress se invoca desde download_stt_runtime (mismo event loop),
    # así que put_nowait es seguro; el stream termina en "done"/"error".
    queue: asyncio.Queue[dict] = asyncio.Queue()

    def on_progress(pct: float, detail: str) -> None:
        queue.put_nowait({"type": "progress", "pct": round(pct, 1), "detail": detail})

    async def runner() -> None:
        try:
            result = await stt_runtime.download_stt_runtime(on_progress)
            queue.put_nowait({"type": "done", "status": result})
        except Exception as exc:
            # El error se serializa al cliente por SSE; la meta queda con
            # state "error" y el próximo /status lo muestra.
            queue.put_nowait({"type": "error", "message": str(exc)})

    task = asyncio.create_task(runner())

    async def event_stream():
        while True:
            event = await queue.get()
            yield _sse(event)
            if event["type"] in ("done", "error"):
                break
        await task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/reset")
async def reset() -> dict:
    was_reset = stt_runtime.reset_stt_runtime()
    logger.info("runtime STT reseteado: %s", was_reset)
    return {"reset": was_reset, "status": stt_runtime.stt_runtime_status()}


@router.get("/models")
async def models() -> dict:
    return stt_runtime.stt_models_status()


@router.post("/models/{name}/download")
async def model_download(name: str) -> StreamingResponse:
    try:
        stt_runtime.stt_model_spec(name)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Modelo STT desconocido: {name}")
    if platform.system() != "Windows":
        raise HTTPException(
            status_code=400, detail="La descarga de modelos de STT es Windows-only en v1"
        )
    if name in stt_runtime._downloading_models:
        raise HTTPException(
            status_code=409, detail=f"Ya hay una descarga del modelo STT {name} en curso"
        )

    queue: asyncio.Queue[dict] = asyncio.Queue()

    def on_progress(pct: float, detail: str) -> None:
        queue.put_nowait({"type": "progress", "pct": round(pct, 1), "detail": detail})

    async def runner() -> None:
        try:
            result = await stt_runtime.download_stt_model(name, on_progress)
            queue.put_nowait({"type": "done", "status": result})
        except Exception as exc:
            queue.put_nowait({"type": "error", "message": str(exc)})

    task = asyncio.create_task(runner())

    async def event_stream():
        while True:
            event = await queue.get()
            yield _sse(event)
            if event["type"] in ("done", "error"):
                break
        await task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/models/{name}")
async def model_delete(name: str) -> dict:
    try:
        deleted = stt_runtime.delete_stt_model(name)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Modelo STT desconocido: {name}")
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    logger.info("modelo STT %s eliminado: %s", name, deleted)
    return {"deleted": deleted, "status": stt_runtime.stt_model_status(name)}
