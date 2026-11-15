"""Tests de CHAT-REASON-CAP — presupuesto de razonamiento por request y
capacidades honestas de reasoning_effort (U1).

El budget del popover de la lámpara se reenvía ahora como
reasoning_budget_tokens (llama.cpp tools/server/server-common.cpp acepta ese
nombre y thinking_budget_tokens como alias); reasoning_effort es una variable
del chat_template, así que la capacidad la decide el template, no el server.
"""

import chat as chat_module
from helpers import drain_sse
from mock_llm_server import COMPLETION_REQUESTS, reset_completion_requests, set_props


async def _complete(client, mock_llama_server, **extra) -> dict:
    payload = {
        "endpoint": mock_llama_server,
        "messages": [{"role": "user", "content": "hola"}],
        "model": "test-model",
        **extra,
    }
    async with client.stream("POST", "/api/chat/completions", json=payload) as res:
        assert res.status_code == 200
        await drain_sse(res)
    return COMPLETION_REQUESTS[-1]


async def test_budget_viaja_con_thinking_activo(client, mock_llama_server):
    """Lo que la UI muestra como esfuerzo es lo que el server recibe."""
    reset_completion_requests()
    body = await _complete(
        client, mock_llama_server, thinking_enabled=True, budget_tokens=4096
    )
    assert body["reasoning_budget_tokens"] == 4096


async def test_budget_no_viaja_sin_thinking(client, mock_llama_server):
    """Sin thinking no hay nada que presupuestar: la clave no aparece."""
    reset_completion_requests()
    body = await _complete(client, mock_llama_server, budget_tokens=4096)
    assert "reasoning_budget_tokens" not in body


async def test_budget_sin_limite_viaja_explicito(client, mock_llama_server):
    """El -1 de la UI ('∞') se manda explícito: anula el budget del launch
    (--reasoning-budget), que sin esto ganaría por defecto en el server."""
    reset_completion_requests()
    body = await _complete(
        client, mock_llama_server, thinking_enabled=True, budget_tokens=-1
    )
    assert body["reasoning_budget_tokens"] == -1


async def test_effort_y_budget_conviven(client, mock_llama_server):
    """Un endpoint que entiende ambos (QwQ-style) los recibe juntos; el
    effort sigue enviándose solo cuando no es 'none'."""
    reset_completion_requests()
    body = await _complete(
        client,
        mock_llama_server,
        thinking_enabled=True,
        budget_tokens=8192,
        reasoning_effort="high",
    )
    assert body["reasoning_budget_tokens"] == 8192
    assert body["reasoning_effort"] == "high"

    reset_completion_requests()
    body = await _complete(
        client,
        mock_llama_server,
        thinking_enabled=True,
        budget_tokens=8192,
        reasoning_effort="none",
    )
    assert "reasoning_effort" not in body
    assert body["reasoning_budget_tokens"] == 8192


# ---------------------------------------------------------------------------
# Capacidad de reasoning_effort: la decide el chat_template
# ---------------------------------------------------------------------------


async def test_caps_respetan_chat_template_caps(client, mock_llama_server):
    """Con caps presentes (builds actuales) manda lo que reporta el server,
    true y false por igual: llama.cpp lo calcula analizando el template
    (common/jinja/caps.cpp), así que un false es una afirmación real."""
    chat_module._caps_cache.clear()
    set_props({"n_ctx": 8192, "chat_template_caps": {"supports_reasoning_effort": True}})
    try:
        res = await client.get("/api/chat/capabilities", params={"endpoint": mock_llama_server})
        assert res.json()["reasoning_effort"] is True
    finally:
        set_props({"n_ctx": 8192, "chat_template_caps": {"supports_reasoning_effort": False}})
        chat_module._caps_cache.clear()
        try:
            res = await client.get("/api/chat/capabilities", params={"endpoint": mock_llama_server})
            assert res.json()["reasoning_effort"] is False
        finally:
            set_props(None)
            chat_module._caps_cache.clear()


async def test_caps_sin_caps_usan_el_chat_template(client, mock_llama_server):
    """Builds viejas sin chat_template_caps: se busca la variable en el
    template, igual que con tools. Un template estilo Qwen3 la lee; uno
    estilo Gemma 4 no."""
    qwen_like = (
        "{% if reasoning_effort %}ESFUERZO: {{ reasoning_effort }}{% endif %}"
        "{{ message['content'] }}"
    )
    gemma_like = (
        "{% if enable_thinking %}<|channel>thought{% endif %}"
        "{{ message['content'] }}"
    )

    chat_module._caps_cache.clear()
    set_props({"n_ctx": 8192, "chat_template": qwen_like})
    try:
        res = await client.get("/api/chat/capabilities", params={"endpoint": mock_llama_server})
        assert res.json()["reasoning_effort"] is True
    finally:
        set_props(None)
        chat_module._caps_cache.clear()

    set_props({"n_ctx": 8192, "chat_template": gemma_like})
    try:
        res = await client.get("/api/chat/capabilities", params={"endpoint": mock_llama_server})
        assert res.json()["reasoning_effort"] is False
    finally:
        set_props(None)
        chat_module._caps_cache.clear()
