"""
test_api_integration.py — flujos de punta a punta cruzando varios módulos.

Para el lanzamiento de modelos usamos backend="lm_studio" apuntado a
mock_llama_server: según el diseño de M2, lm_studio no dispara un subprocess
real, solo hace un health check contra host/puerto — así probamos el flujo
completo (config -> scan -> inventario -> launch -> running -> chat -> stop)
sin depender de un binario de llama-server real en la máquina que corre los
tests.
"""

import json

import benchmark as benchmark_module
from helpers import drain_sse, scan_and_wait, wait_until


async def test_full_flow(client, sample_model_dir, mock_llama_server):
    # 1. Config con el directorio de modelos de prueba.
    res = await client.post("/api/config", json={"model_dirs": [str(sample_model_dir)]})
    assert res.status_code == 200

    # 2. Scan -> esperar el evento "complete".
    events = await scan_and_wait(client)
    assert any('"complete"' in e for e in events)

    # 3. Al menos un modelo en el inventario.
    models_list = (await client.get("/api/models")).json()
    assert len(models_list) >= 1
    model_id = models_list[0]["id"]

    # 4. Lanzar el primer modelo.
    launch_res = await client.post(
        "/api/launcher/launch",
        json={"model_id": model_id, "backend": "lm_studio", "host": "127.0.0.1", "port": 18080},
    )
    assert launch_res.status_code == 200
    process_id = launch_res.json()["process_id"]

    # 5. Esperar status=running (polling con timeout).
    async def is_running():
        status = (await client.get(f"/api/launcher/status/{process_id}")).json()
        return status["state"] == "running"

    assert await wait_until(is_running, timeout=5.0)

    # 6. Chat -> recibir tokens.
    async with client.stream(
        "POST",
        "/api/chat/completions",
        json={
            "endpoint": mock_llama_server,
            "messages": [{"role": "user", "content": "hola"}],
            "model": "test-model",
        },
    ) as res:
        raw = await drain_sse(res)

    parsed = [
        json.loads(line[len("data:"):].strip())
        for line in raw
        if line.startswith("data:") and line.strip() != "data: [DONE]"
    ]
    token_events = [e for e in parsed if e.get("type") == "token"]
    assert token_events
    assert "".join(e["content"] for e in token_events) == "Hola mundo"

    # 7. Detener el proceso.
    stop_res = await client.post(f"/api/launcher/stop/{process_id}")
    assert stop_res.status_code == 200

    # 8. Confirmar estado "stopped".
    final_status = (await client.get(f"/api/launcher/status/{process_id}")).json()
    assert final_status["state"] == "stopped"


async def test_benchmark_end_to_end(client, mock_llama_server):
    # Usamos el set real "logic", truncado a 2 prompts en memoria para que el
    # run sea rápido y determinístico. Se restaura al final para no afectar
    # otros tests que dependen del set completo (test_sets_loaded, etc.).
    await benchmark_module.prompt_sets.ensure_loaded()
    logic_set = benchmark_module.prompt_sets.get("logic")
    assert logic_set is not None
    original_prompts = logic_set.prompts

    logic_set.prompts = original_prompts[:2]
    try:
        res = await client.post(
            "/api/benchmark/run",
            json={"endpoint": mock_llama_server, "model_name": "test-model", "sets": ["logic"]},
        )
        assert res.status_code == 200
        run_id = res.json()["run_id"]

        async def is_done():
            current = (await client.get(f"/api/benchmark/run/{run_id}")).json()
            return current["status"] in ("completed", "error", "cancelled")

        assert await wait_until(is_done, timeout=5.0)

        final = (await client.get(f"/api/benchmark/history/{run_id}")).json()
        assert final["status"] == "completed"
        assert len(final["results"]) == 2
        for result in final["results"]:
            assert result["metrics"] is not None
            assert result["metrics"]["tokens_generated"] > 0
    finally:
        logic_set.prompts = original_prompts
