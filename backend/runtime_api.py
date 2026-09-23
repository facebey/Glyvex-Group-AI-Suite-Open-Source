"""
runtime_api.py — API del runtime gestionado (RT-3).

GET  /api/runtime/status   — estado del runtime + GPU detectada y su familia
                              (RT-10). La UI la usa para ofrecer el download
                              (base + aceleración por familia) y mostrar la
                              estimación de tamaño (RT-4).
POST /api/runtime/download — descarga con progreso por SSE (mismo patrón que
                             chat/benchmark). La lógica real vive en runtime.py;
                             acá solo se puentea on_progress al stream.
POST /api/runtime/reset    — borra el runtime gestionado (reinstalar =
                             reset + download).
"""

from __future__ import annotations

import asyncio
import json
import logging
import platform

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

import runtime

logger = logging.getLogger("glyvex.runtime_api")

router = APIRouter()


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@router.get("/status")
async def status() -> dict:
    return {
        **runtime.runtime_status(),
        "gpu": runtime.detect_gpu(),
        "gpu_family": runtime.detect_gpu_family(),
        "platform": platform.system(),
    }


@router.post("/download")
async def download() -> StreamingResponse:
    if platform.system() != "Windows":
        raise HTTPException(
            status_code=400, detail="El runtime gestionado es Windows-only en v1"
        )
    if runtime.runtime_status()["state"] == "downloading":
        raise HTTPException(status_code=409, detail="Ya hay una descarga de runtime en curso")
    if not runtime.can_download():
        raise HTTPException(
            status_code=400, detail="No hay fuente de runtime base con sha256 verificable"
        )

    # on_progress se invoca desde download_runtime (mismo event loop), así que
    # put_nowait es seguro; el stream termina en el evento "done"/"error".
    queue: asyncio.Queue[dict] = asyncio.Queue()

    def on_progress(pct: float, detail: str) -> None:
        queue.put_nowait({"type": "progress", "pct": round(pct, 1), "detail": detail})

    async def runner() -> None:
        try:
            result = await runtime.download_runtime(on_progress)
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
    was_reset = runtime.reset_runtime()
    logger.info("runtime reseteado: %s", was_reset)
    return {"reset": was_reset, "status": runtime.runtime_status()}
