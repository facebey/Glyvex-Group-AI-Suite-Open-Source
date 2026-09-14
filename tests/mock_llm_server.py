"""
mock_llm_server.py — servidor HTTP mock que simula llama-server/OpenAI para tests.

No es uno de los archivos explícitamente listados en el prompt, pero hace
falta para implementar la fixture `mock_llama_server` de conftest.py sin
depender de un llama-server real. Usa Starlette (ya viene con FastAPI, no es
una dependencia nueva) + uvicorn (ya está en requirements.txt).

Expone:
  GET  /health                → {"status": "ok"}
  GET  /v1/models             → {"data": [{"id": "test-model", ...}]}
  POST /v1/chat/completions   → SSE con dos tokens ("Hola" + " mundo") y [DONE]
"""

from __future__ import annotations

import asyncio

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route


async def _health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def _v1_models(request: Request) -> JSONResponse:
    return JSONResponse({"data": [{"id": "test-model", "object": "model"}]})


async def _chat_completions(request: Request) -> StreamingResponse:
    body = await request.json()
    messages = body.get("messages", [])
    last_user_content = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user_content = m.get("content", "")
            break

    async def event_stream():
        if "__THINKING_TEST__" in last_user_content:
            # Variante con bloque <think> embebido, para test_preserve_thinking_split
            # y test_no_think_tag_in_response.
            yield (
                'data: {"choices": [{"delta": '
                '{"content": "<think>razonando paso a paso</think>respuesta final"}}]}\n\n'
            )
            await asyncio.sleep(0.01)
            yield 'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\n'
            yield "data: [DONE]\n\n"
            return

        yield 'data: {"choices": [{"delta": {"content": "Hola"}}]}\n\n'
        await asyncio.sleep(0.01)
        yield 'data: {"choices": [{"delta": {"content": " mundo"}}]}\n\n'
        await asyncio.sleep(0.01)
        yield 'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\n'
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def build_mock_app() -> Starlette:
    return Starlette(
        routes=[
            Route("/health", _health),
            Route("/v1/models", _v1_models),
            Route("/v1/chat/completions", _chat_completions, methods=["POST"]),
        ]
    )
