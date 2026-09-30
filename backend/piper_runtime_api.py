"""
piper_runtime_api.py — API del motor TTS neural Piper (TTS-1).

GET    /api/tts/piper/status              — estado del motor + voces.
GET    /api/tts/piper/voices              — catálogo de voces + estado.
POST   /api/tts/piper/voices/{name}/download — descarga con progreso por SSE.
DELETE /api/tts/piper/voices/{name}        — borra una voz descargada.

La lógica real vive en piper_runtime.py (mismo patrón que stt_runtime_api.py).
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

import piper_runtime

logger = logging.getLogger("glyvex.piper_runtime_api")

router = APIRouter()


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@router.get("/status")
async def status() -> dict:
    return piper_runtime.piper_runtime_status()


@router.get("/voices")
async def voices() -> dict:
    return {"voices": piper_runtime.piper_voices_status()}


@router.post("/voices/{name}/download")
async def voice_download(name: str) -> StreamingResponse:
    try:
        piper_runtime.piper_voice_status(name)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Voz Piper desconocida: {name}")
    if name in piper_runtime._downloading_voices:
        raise HTTPException(
            status_code=409, detail=f"Ya hay una descarga de la voz Piper {name} en curso"
        )

    queue: asyncio.Queue[dict] = asyncio.Queue()

    def on_progress(pct: float, detail: str) -> None:
        queue.put_nowait({"type": "progress", "pct": round(pct, 1), "detail": detail})

    async def runner() -> None:
        try:
            result = await piper_runtime.download_piper_voice(name, on_progress)
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


@router.delete("/voices/{name}")
async def voice_delete(name: str) -> dict:
    try:
        piper_runtime.delete_piper_voice(name)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Voz Piper desconocida: {name}")
    logger.info("voz Piper %s eliminada", name)
    return {"deleted": name, "status": piper_runtime.piper_voice_status(name)}
