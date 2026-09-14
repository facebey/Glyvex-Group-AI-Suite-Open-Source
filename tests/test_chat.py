"""Tests de chat.py — proxy de streaming, separación de <think>, métricas, endpoints."""

import json

from helpers import drain_sse


def _parse_events(raw_lines: list[str]) -> list[dict]:
    """Convierte líneas 'data: {...}' en dicts, ignorando el '[DONE]' final."""
    events = []
    for line in raw_lines:
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            continue
        events.append(json.loads(payload))
    return events


async def test_stream_tokens(client, mock_llama_server):
    payload = {
        "endpoint": mock_llama_server,
        "messages": [{"role": "user", "content": "hola"}],
        "model": "test-model",
    }
    async with client.stream("POST", "/api/chat/completions", json=payload) as res:
        assert res.status_code == 200
        raw = await drain_sse(res)

    events = _parse_events(raw)
    token_events = [e for e in events if e["type"] == "token"]
    assert token_events  # al menos un token
    full_text = "".join(e["content"] for e in token_events)
    assert full_text == "Hola mundo"


async def test_metrics_event_present(client, mock_llama_server):
    payload = {
        "endpoint": mock_llama_server,
        "messages": [{"role": "user", "content": "hola"}],
        "model": "test-model",
    }
    async with client.stream("POST", "/api/chat/completions", json=payload) as res:
        raw = await drain_sse(res)

    events = _parse_events(raw)
    metrics_events = [e for e in events if e["type"] == "metrics"]
    assert metrics_events
    assert "tps" in metrics_events[0]
    assert "ttft_ms" in metrics_events[0]


async def test_done_event_present(client, mock_llama_server):
    payload = {
        "endpoint": mock_llama_server,
        "messages": [{"role": "user", "content": "hola"}],
        "model": "test-model",
    }
    async with client.stream("POST", "/api/chat/completions", json=payload) as res:
        raw = await drain_sse(res)

    events = _parse_events(raw)
    done_events = [e for e in events if e["type"] == "done"]
    assert len(done_events) == 1
    assert done_events[0]["finish_reason"] == "stop"
    assert done_events[0]["total_tokens"] == 2  # "Hola" + " mundo"
    assert raw[-1] == "data: [DONE]"


async def test_preserve_thinking_split(client, mock_llama_server):
    payload = {
        "endpoint": mock_llama_server,
        "messages": [{"role": "user", "content": "__THINKING_TEST__ hola"}],
        "model": "test-model",
        "preserve_thinking": True,
    }
    async with client.stream("POST", "/api/chat/completions", json=payload) as res:
        raw = await drain_sse(res)

    events = _parse_events(raw)
    thinking_text = "".join(e["content"] for e in events if e["type"] == "thinking_token")
    token_text = "".join(e["content"] for e in events if e["type"] == "token")

    assert thinking_text == "razonando paso a paso"
    assert token_text == "respuesta final"


async def test_no_think_tag_in_response(client, mock_llama_server):
    payload = {
        "endpoint": mock_llama_server,
        "messages": [{"role": "user", "content": "__THINKING_TEST__ hola"}],
        "model": "test-model",
        "preserve_thinking": True,
    }
    async with client.stream("POST", "/api/chat/completions", json=payload) as res:
        raw = await drain_sse(res)

    events = _parse_events(raw)
    for e in events:
        if e["type"] == "token":
            assert "<think>" not in e["content"]
            assert "</think>" not in e["content"]


async def test_abort_stops_stream(client, mock_llama_server):
    # Equivalente backend de "AbortController": abrimos el stream, leemos un
    # solo chunk y cerramos la conexión antes del [DONE]. El objetivo es que
    # el servidor no cuelgue ni deje el generador colgado — lo verificamos
    # haciendo una segunda request normal inmediatamente después.
    payload = {
        "endpoint": mock_llama_server,
        "messages": [{"role": "user", "content": "hola"}],
        "model": "test-model",
    }
    async with client.stream("POST", "/api/chat/completions", json=payload) as res:
        async for _ in res.aiter_lines():
            break  # cortamos apenas llega la primera línea

    # Si el generador anterior hubiera quedado colgado o el server roto, esta
    # segunda call fallaría o se colgaría.
    async with client.stream("POST", "/api/chat/completions", json=payload) as res2:
        raw = await drain_sse(res2)
    assert any('"type": "done"' in line or '"type":"done"' in line for line in raw)


async def test_endpoints_list(client):
    res = await client.get("/api/chat/endpoints")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert "url" in data[0] and "status" in data[0]
