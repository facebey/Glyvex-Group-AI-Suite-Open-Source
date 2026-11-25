"""Tests de chat_presets.py — registro built-in y resolución por modelo (U1)."""

from chat_presets import BUILTIN_PRESETS, detect_family, resolve_builtin_presets, validate_params
from helpers import drain_sse
from mock_llm_server import COMPLETION_REQUESTS, reset_completion_requests

# Valores exactos de las cards HF verificadas 2026-11-14 (Engram #1526), con la
# pareja de penalidades completa donde la card las declara (ver PRESET-CARD: el
# A/B de código midió que presence sin repeat viaja mezclado con el default 1,1).
# Si alguien los toca, este test explica contra qué card estaban fijados.
_EXPECTED_PARAMS = {
    "qwen3-coder": {
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 20,
        "repeat_penalty": 1.05,
    },
    "qwen3.8-thinking": {
        "temperature": 1.0,
        "top_p": 0.95,
        "top_k": 20,
        "min_p": 0.0,
        "repeat_penalty": 1.0,
    },
    "qwen3.8-instruct": {
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 20,
        "presence_penalty": 1.5,
        "repeat_penalty": 1.0,
    },
    "gemma4": {
        "temperature": 1.0,
        "top_p": 0.95,
        "top_k": 64,
    },
}


def test_registry_fiel_a_las_cards():
    assert {p["id"]: p["params"] for p in BUILTIN_PRESETS} == _EXPECTED_PARAMS


def test_penalidades_de_las_cards_de_qwen38_vienen_completas():
    """Un preset de Qwen3.8 no puede declarar solo una penalidad.

    Los params de un preset se mergean sobre los DEFAULT_PARAMS del Chat
    (Chat.jsx), que traen repeat_penalty 1.1. Sin el repeat explícito el
    qwen3.8-instruct enviaba presence 1.5 + repeat 1.1, una combinación que no
    está en ninguna card: en el A/B de código medido 2026-11-22 dio 2/12
    correctos contra 38/60 de la banda presence <= 0.3 (Fisher p = 0.0007), y
    8/12 con su pareja 1.5/1.0.
    """
    for preset_id in ("qwen3.8-thinking", "qwen3.8-instruct"):
        params = next(p["params"] for p in BUILTIN_PRESETS if p["id"] == preset_id)
        assert params["repeat_penalty"] == 1.0, preset_id


async def test_preset_qwen38_instruct_envia_la_pareja_al_endpoint(client, mock_llama_server):
    """Aplicar el preset (merge sobre los defaults del Chat) no debe degradar la pareja."""
    reset_completion_requests()
    merged = dict(
        temperature=0.7, top_p=0.9, top_k=40, min_p=0.0, repeat_penalty=1.1,
        presence_penalty=0.0,
    )
    merged.update(next(p["params"] for p in BUILTIN_PRESETS if p["id"] == "qwen3.8-instruct"))
    payload = {
        "endpoint": mock_llama_server,
        "messages": [{"role": "user", "content": "hola"}],
        "model": "Qwen3.8-27B-it-UD-Q4_K_M",
        **merged,
    }
    async with client.stream("POST", "/api/chat/completions", json=payload) as res:
        assert res.status_code == 200
        await drain_sse(res)
    sent = COMPLETION_REQUESTS[-1]
    assert sent["presence_penalty"] == 1.5 and sent["repeat_penalty"] == 1.0
    assert sent["top_p"] == 0.8 and sent["top_k"] == 20


def test_resuelve_gemma4_por_nombre_de_endpoint():
    for name in (
        "Gemma4-26B-A4B-it-Q4_K_XL",
        "gemma-4-26b-it",
        "Gemma 4 31B-it",
        "gemma-4-31B-it-QAT-Q4_0",
    ):
        presets = resolve_builtin_presets(name)
        assert [p["id"] for p in presets] == ["gemma4"]
        # Familia de un solo preset: recomendado siempre, cualquier mode.
        assert presets[0]["recommended"] is True


def test_resuelve_qwen3_coder_sin_colisionar_con_qwen38():
    presets = resolve_builtin_presets("Qwen3-Coder-30B-A3B-Instruct-Q4_K_M")
    assert [p["id"] for p in presets] == ["qwen3-coder"]
    assert presets[0]["recommended"] is True


def test_qwen38_devuelve_ambas_variantes_y_el_mode_decide():
    name = "Qwen3.8-27B-it-UD-Q4_K_XL"
    ids = {p["id"] for p in resolve_builtin_presets(name)}
    assert ids == {"qwen3.8-thinking", "qwen3.8-instruct"}

    by_mode = resolve_builtin_presets(name, mode="thinking")
    rec = {p["id"]: p["recommended"] for p in by_mode}
    assert rec == {"qwen3.8-thinking": True, "qwen3.8-instruct": False}

    by_mode = resolve_builtin_presets(name, mode="instruct")
    rec = {p["id"]: p["recommended"] for p in by_mode}
    assert rec == {"qwen3.8-thinking": False, "qwen3.8-instruct": True}

    # Sin mode no se adivina: ninguna variante recomendada.
    plain = resolve_builtin_presets(name)
    assert all(p["recommended"] is False for p in plain)


def test_no_inventa_presets_para_modelos_ajenos():
    # "Qwen3-8B" es Qwen3 antiguo: el punto literal de qwen3.8 lo excluye.
    for name in (
        "Qwen3-8B",
        "Qwen3-32B",
        "gemma-3-12b-it",
        "meggemma-4b-it",  # 'gemma4' dentro de 'meggemma' no cuenta
        "llama-3.1-8b-it",
        "",
        None,
    ):
        assert resolve_builtin_presets(name) == []


async def test_presence_penalty_viaja_al_endpoint(client, mock_llama_server):
    reset_completion_requests()
    payload = {
        "endpoint": mock_llama_server,
        "messages": [{"role": "user", "content": "hola"}],
        "model": "test-model",
        "presence_penalty": 1.5,
    }
    async with client.stream("POST", "/api/chat/completions", json=payload) as res:
        assert res.status_code == 200
        await drain_sse(res)
    assert COMPLETION_REQUESTS[-1]["presence_penalty"] == 1.5


async def test_presence_penalty_no_viaja_si_esta_desactivado(client, mock_llama_server):
    reset_completion_requests()
    payload = {
        "endpoint": mock_llama_server,
        "messages": [{"role": "user", "content": "hola"}],
        "model": "test-model",
    }
    async with client.stream("POST", "/api/chat/completions", json=payload) as res:
        assert res.status_code == 200
        await drain_sse(res)
    assert "presence_penalty" not in COMPLETION_REQUESTS[-1]


# --------------------------------------------------------------------------
# U3/U4: API de presets de usuario y merge con los built-in
# --------------------------------------------------------------------------


def test_detect_family_y_validacion_de_params():
    assert detect_family("Gemma4-26B-A4B-it-Q4_K_XL") == "gemma4"
    assert detect_family("Qwen3.8-27B-it") == "qwen3.8"
    assert detect_family("Qwen3-8B") is None

    clean, errors = validate_params({"temperature": 0.9, "top_k": 30})
    assert clean == {"temperature": 0.9, "top_k": 30} and errors == []

    # Uno por cada tipo de rechazo: fuera de rango, no-entero, desconocido.
    clean, errors = validate_params({"temperature": 3.0, "top_k": 1.5, "otro": 2})
    assert clean == {} and len(errors) == 3

    _, errors = validate_params({})
    assert errors


async def test_crud_de_presets_de_usuario(client):
    res = await client.post(
        "/api/chat/presets",
        json={"name": "mi-preset", "family": "gemma4", "params": {"temperature": 0.9, "top_k": 30}},
    )
    assert res.status_code == 200
    saved = res.json()
    assert saved["builtin"] is False and saved["params"] == {"temperature": 0.9, "top_k": 30}

    # Upsert: mismo nombre actualiza en vez de duplicar.
    res = await client.post(
        "/api/chat/presets",
        json={"name": "mi-preset", "params": {"temperature": 0.2}},
    )
    assert res.status_code == 200

    res = await client.get("/api/chat/presets")
    listed = res.json()
    assert [p["name"] for p in listed] == ["mi-preset"]
    assert listed[0]["params"] == {"temperature": 0.2}
    assert listed[0]["family"] is None  # el upsert reemplaza la fila completa

    res = await client.delete("/api/chat/presets/mi-preset")
    assert res.status_code == 200
    res = await client.get("/api/chat/presets")
    assert res.json() == []
    res = await client.delete("/api/chat/presets/mi-preset")
    assert res.status_code == 404


async def test_post_rechaza_colision_con_builtin_y_params_invalidos(client):
    res = await client.post("/api/chat/presets", json={"name": "gemma4", "params": {"temperature": 0.5}})
    assert res.status_code == 409

    res = await client.post("/api/chat/presets", json={"name": "x", "params": {"temperature": 3.0}})
    assert res.status_code == 422

    res = await client.post("/api/chat/presets", json={"name": "x", "params": {"top_k": 3.5}})
    assert res.status_code == 422

    res = await client.post("/api/chat/presets", json={"name": "x", "family": "noexiste", "params": {"temperature": 0.5}})
    assert res.status_code == 422


async def test_get_con_model_mergea_builtin_y_presets_de_usuario(client):
    await client.post(
        "/api/chat/presets",
        json={"name": "mi-gemma", "family": "gemma4", "params": {"temperature": 0.9}},
    )
    await client.post(
        "/api/chat/presets",
        json={"name": "por-nombre", "model_name": "llama-3.1-8b-it", "params": {"temperature": 0.2}},
    )

    res = await client.get("/api/chat/presets", params={"model": "Gemma4-26B-A4B-it"})
    entries = {p["id"]: p for p in res.json()}
    assert set(entries) == {"gemma4", "mi-gemma"}
    assert entries["gemma4"]["builtin"] is True and entries["gemma4"]["recommended"] is True
    assert entries["mi-gemma"]["builtin"] is False

    # Coincidencia por nombre exacto (case-insensitive) sin familia conocida.
    res = await client.get("/api/chat/presets", params={"model": "LLaMA-3.1-8B-IT"})
    assert [p["id"] for p in res.json()] == ["por-nombre"]

    # Modelo sin familia ni presets: nada que ofrecer.
    res = await client.get("/api/chat/presets", params={"model": "test-model"})
    assert res.json() == []
