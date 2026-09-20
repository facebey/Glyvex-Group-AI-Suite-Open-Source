"""
mock_llm_server.py — servidor HTTP mock que simula llama-server/OpenAI para tests.

No es uno de los archivos explícitamente listados en el prompt, pero hace
falta para implementar la fixture `mock_llama_server` de conftest.py sin
depender de un llama-server real. Usa Starlette (ya viene con FastAPI, no es
una dependencia nueva) + uvicorn (ya está en requirements.txt).

Expone:
  GET  /health                → {"status": "ok"}
  GET  /v1/models             → {"data": [{"id": "test-model", ...}]}
  GET  /slots                 → un slot con n_prompt_tokens creciendo (prompt + generados)
  GET  /metrics               → formato Prometheus de llama-server; cada lectura
                                avanza los contadores (50 tokens en 1 s de decode).
  POST /v1/chat/completions   → SSE con dos tokens ("Hola" + " mundo") y [DONE].
                                Con "__REASONING_TEST__" en el mensaje: razonamiento
                                en reasoning_content + usage y timings al final.
"""

from __future__ import annotations

import asyncio

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route


async def _health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def _v1_models(request: Request) -> JSONResponse:
    return JSONResponse({"data": [{"id": "test-model", "object": "model"}]})


# Estado del /metrics simulado. Los tests lo reinician con reset_metrics().
_METRICS_STATE = {"scrapes": 0, "slots": 0}


def reset_metrics() -> None:
    _METRICS_STATE["scrapes"] = 0
    _METRICS_STATE["slots"] = 0


async def _metrics(request: Request) -> PlainTextResponse:
    n = _METRICS_STATE["scrapes"]
    _METRICS_STATE["scrapes"] += 1
    # Nombres del build actual: n_tokens_max (antes n_past_max),
    # prompt_tokens_cached_total y spec_decode_*. El caché usa notación
    # científica a propósito, como lo imprime llama-server.
    body = f"""# HELP llamacpp:prompt_tokens_total Number of prompt tokens processed, excluding cached tokens
# TYPE llamacpp:prompt_tokens_total counter
llamacpp:prompt_tokens_total {100 * n}
# TYPE llamacpp:prompt_tokens_cached_total counter
llamacpp:prompt_tokens_cached_total {900.0 * n:e}
# TYPE llamacpp:prompt_seconds_total counter
llamacpp:prompt_seconds_total {0.25 * n}
# TYPE llamacpp:tokens_predicted_total counter
llamacpp:tokens_predicted_total {50 * n}
# TYPE llamacpp:tokens_predicted_seconds_total counter
llamacpp:tokens_predicted_seconds_total {1.0 * n}
# TYPE llamacpp:n_tokens_max counter
llamacpp:n_tokens_max {1200 + 10 * n}
# TYPE llamacpp:spec_decode_num_draft_tokens_total counter
llamacpp:spec_decode_num_draft_tokens_total {40 * n}
# TYPE llamacpp:spec_decode_num_accepted_tokens_total counter
llamacpp:spec_decode_num_accepted_tokens_total {30 * n}
# TYPE llamacpp:requests_processing gauge
llamacpp:requests_processing 1
# TYPE llamacpp:requests_deferred gauge
llamacpp:requests_deferred 2
"""
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4")


async def _slots(request: Request) -> JSONResponse:
    # Mismo formato que el build 2026-09: n_prompt_tokens = prompt + generados.
    # Contador propio: /metrics y /slots se leen en paralelo, compartir el
    # contador haría el valor dependiente de cuál responde primero.
    n = _METRICS_STATE["slots"]
    _METRICS_STATE["slots"] += 1
    return JSONResponse([{
        "id": 0, "n_ctx": 32768, "is_processing": True,
        "n_prompt_tokens": 4000 + 50 * n, "n_prompt_tokens_processed": 4000,
        "next_token": [{"has_next_token": True, "n_decoded": 50 * n}],
    }])


# Receptor de escrituras de InfluxDB (v1 /write y v2 /api/v2/write). Los tests
# leen lo recibido y cambian "status" para simular un destino caído.
INFLUX_STATE: dict = {"status": 204, "writes": []}


def reset_influx() -> None:
    INFLUX_STATE["status"] = 204
    INFLUX_STATE["writes"] = []


async def _influx_write(request: Request) -> Response:
    body = (await request.body()).decode("utf-8")
    INFLUX_STATE["writes"].append({
        "path": request.url.path,
        "params": dict(request.query_params),
        "authorization": request.headers.get("authorization"),
        "lines": body.splitlines(),
    })
    status = INFLUX_STATE["status"]
    return Response(status_code=status, content=b"" if status < 300 else b'{"message":"simulated failure"}')


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

        if "__REASONING_TEST__" in last_user_content:
            # Variante estilo llama-server con --reasoning-format por defecto:
            # razonamiento en reasoning_content, respuesta en content y
            # usage + timings en el último chunk (choices vacío).
            await asyncio.sleep(0.05)
            for piece in ("pensando", " un", " poco"):
                yield 'data: {"choices": [{"delta": {"reasoning_content": "%s"}}]}\n\n' % piece
                await asyncio.sleep(0.02)
            yield 'data: {"choices": [{"delta": {"content": "listo"}}]}\n\n'
            yield 'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\n'
            yield (
                'data: {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 4}, '
                '"timings": {"prompt_n": 12, "prompt_ms": 30.0, "cache_n": 0, '
                '"predicted_n": 4, "predicted_ms": 80.0}}\n\n'
            )
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
            Route("/metrics", _metrics),
            Route("/slots", _slots),
            Route("/api/v2/write", _influx_write, methods=["POST"]),
            Route("/write", _influx_write, methods=["POST"]),
            Route("/v1/chat/completions", _chat_completions, methods=["POST"]),
        ]
    )
