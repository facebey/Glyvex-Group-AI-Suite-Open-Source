"""
kokoro_runtime_api.py — API del motor TTS neural Kokoro (TTS-1).

GET    /api/tts/kokoro/status              — estado del motor + modelo + voces.
GET    /api/tts/kokoro/voices              — catálogo de voces (bin completo).
POST   /api/tts/kokoro/model/download      — descarga modelo+bin con SSE.
DELETE /api/tts/kokoro/model               — borra modelo+bin.

A diferencia de Piper (un par de archivos por voz), Kokoro descarga UN
modelo ONNX + un bin con las 54 voces, así que la descarga y la borrado
son de modelo, no por voz. La lógica real vive en kokoro_runtime.py.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

import kokoro_runtime

logger = logging.getLogger("glyvex.kokoro_runtime_api")

router = APIRouter()


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@router.get("/status")
async def status() -> dict:
    return kokoro_runtime.kokoro_runtime_status()


@router.get("/voices")
async def voices() -> dict:
    return {"voices": kokoro_runtime.kokoro_voices_catalog()}


@router.post("/model/download")
async def model_download() -> StreamingResponse:
    state = kokoro_runtime.kokoro_model_status()["state"]
    if state == "downloading":
        raise HTTPException(
            status_code=409, detail="Ya hay una descarga del modelo Kokoro en curso"
        )

    queue: asyncio.Queue[dict] = asyncio.Queue()

    def on_progress(pct: float, detail: str) -> None:
        queue.put_nowait({"type": "progress", "pct": round(pct, 1), "detail": detail})

    async def runner() -> None:
        try:
            result = await kokoro_runtime.download_kokoro_model(on_progress)
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


@router.delete("/model")
async def model_delete() -> dict:
    kokoro_runtime.delete_kokoro_model()
    logger.info("modelo Kokoro eliminado")
    return {"deleted": "kokoro-v1.0.onnx", "status": kokoro_runtime.kokoro_model_status()}
