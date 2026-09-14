"""Tests de benchmark.py — sets reales, runner asyncio.Task, keywords, historial, cancelación, persistencia en SQLite."""

import json
from pathlib import Path

import benchmark as benchmark_module
from helpers import wait_until

BACKEND_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "backend" / "prompts"


async def _run_and_wait_completed(client, sets, timeout=5.0, **extra_config):
    res = await client.post(
        "/api/benchmark/run",
        json={"endpoint": "http://127.0.0.1:18080", "model_name": "test-model", "sets": sets, **extra_config},
    )
    assert res.status_code == 200
    run = res.json()
    run_id = run["run_id"]

    async def is_done():
        current = (await client.get(f"/api/benchmark/run/{run_id}")).json()
        return current["status"] in ("completed", "error", "cancelled")

    finished = await wait_until(is_done, timeout=timeout)
    assert finished, "el benchmark no terminó dentro del timeout"

    final = (await client.get(f"/api/benchmark/run/{run_id}")).json()
    return final


# --------------------------------------------------------------------------
# Contenido real de backend/prompts/ (sin mock, sin red)
# --------------------------------------------------------------------------


async def test_sets_loaded(client):
    res = await client.get("/api/benchmark/sets")
    assert res.status_code == 200
    data = res.json()
    assert len(data) >= 6
    ids = {s["id"] for s in data}
    assert {"coder", "math", "science", "logic", "spanish", "infra_network"} <= ids


async def test_coder_set_has_10_prompts(client):
    res = await client.get("/api/benchmark/sets/coder")
    assert res.status_code == 200
    data = res.json()
    assert len(data["prompts"]) == 10


async def test_infra_set_has_8_prompts(client):
    res = await client.get("/api/benchmark/sets/infra_network")
    assert res.status_code == 200
    data = res.json()
    assert len(data["prompts"]) == 8


def test_all_prompt_jsons_valid():
    json_files = sorted(BACKEND_PROMPTS_DIR.glob("*.json"))
    assert len(json_files) >= 6

    for path in json_files:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "id" in data
        assert "name" in data
        assert isinstance(data["prompts"], list)
        assert len(data["prompts"]) > 0
        for prompt in data["prompts"]:
            assert "id" in prompt
            assert "title" in prompt
            assert "prompt" in prompt
            assert "expected_keywords" in prompt


# --------------------------------------------------------------------------
# Runner real contra mock_llama_server, con un set efímero e in-memory
# --------------------------------------------------------------------------


async def test_run_returns_run_id(client, mock_llama_server, custom_prompt_set):
    res = await client.post(
        "/api/benchmark/run",
        json={"endpoint": mock_llama_server, "model_name": "test-model", "sets": [custom_prompt_set]},
    )
    assert res.status_code == 200
    data = res.json()
    assert "run_id" in data and data["run_id"]
    assert data["status"] == "running"


async def test_run_saves_to_disk(client, mock_llama_server, custom_prompt_set):
    final = await _run_and_wait_completed(client, [custom_prompt_set], endpoint=mock_llama_server)
    assert final["status"] == "completed"

    json_path = benchmark_module.RUNS_DIR / f"{final['run_id']}.json"
    assert json_path.exists()

    on_disk = json.loads(json_path.read_text(encoding="utf-8"))
    assert on_disk["run_id"] == final["run_id"]


async def test_metrics_captured(client, mock_llama_server, custom_prompt_set):
    final = await _run_and_wait_completed(client, [custom_prompt_set], endpoint=mock_llama_server)
    result = final["results"][0]
    assert result["metrics"] is not None
    assert "tps" in result["metrics"]
    assert "ttft_ms" in result["metrics"]
    assert "tokens_generated" in result["metrics"]
    assert result["metrics"]["tokens_generated"] == 2  # "Hola" + " mundo"


async def test_keyword_check_found(client, mock_llama_server, custom_prompt_set):
    # custom_prompt_set pide expected_keywords=["Hola", "cache"] contra una
    # respuesta mockeada literal "Hola mundo".
    final = await _run_and_wait_completed(client, [custom_prompt_set], endpoint=mock_llama_server)
    result = final["results"][0]
    assert "Hola" in result["keywords_found"]


async def test_keyword_check_missing(client, mock_llama_server, custom_prompt_set):
    final = await _run_and_wait_completed(client, [custom_prompt_set], endpoint=mock_llama_server)
    result = final["results"][0]
    assert "cache" in result["keywords_missing"]


async def test_history_shows_run(client, mock_llama_server, custom_prompt_set):
    final = await _run_and_wait_completed(client, [custom_prompt_set], endpoint=mock_llama_server)

    history = (await client.get("/api/benchmark/history")).json()
    assert any(entry["run_id"] == final["run_id"] for entry in history)


async def test_cancel_run(client, mock_llama_server, custom_prompt_set):
    # repetitions alto para tener margen de sobra entre el POST /run y el
    # DELETE — cada repetición contra el mock tarda ~20ms, así que 30
    # repeticiones dan un buen colchón para cancelar a mitad de camino.
    res = await client.post(
        "/api/benchmark/run",
        json={
            "endpoint": mock_llama_server,
            "model_name": "test-model",
            "sets": [custom_prompt_set],
            "repetitions": 30,
        },
    )
    run_id = res.json()["run_id"]

    del_res = await client.delete(f"/api/benchmark/run/{run_id}")
    assert del_res.status_code == 200
    assert del_res.json().get("cancelled") is True

    async def is_cancelled():
        current = (await client.get(f"/api/benchmark/run/{run_id}")).json()
        return current["status"] == "cancelled"

    assert await wait_until(is_cancelled, timeout=5.0)


# --------------------------------------------------------------------------
# Persistencia en SQLite (módulo M7) — la DB de estos tests es in-memory,
# aislada por el autouse _isolated_state de conftest.py.
# --------------------------------------------------------------------------


async def test_run_saved_to_db(client, mock_llama_server, custom_prompt_set):
    """Un run completado queda registrado en la DB."""
    from database import BenchmarkRunRow, get_session

    final = await _run_and_wait_completed(client, [custom_prompt_set], endpoint=mock_llama_server)
    assert final["status"] == "completed"

    async with get_session() as session:
        row = await session.get(BenchmarkRunRow, final["run_id"])
        assert row is not None
        assert row.status == "completed"


async def test_history_reads_from_db(client, mock_llama_server, custom_prompt_set):
    """GET /history lee desde la DB (no solo desde JSON)."""
    final = await _run_and_wait_completed(client, [custom_prompt_set], endpoint=mock_llama_server)

    history = (await client.get("/api/benchmark/history")).json()
    assert any(entry["run_id"] == final["run_id"] for entry in history)


async def test_delete_run_removes_from_db(client, mock_llama_server, custom_prompt_set):
    """DELETE /run/{id} elimina el run de la DB además del JSON."""
    from database import BenchmarkRunRow, get_session

    final = await _run_and_wait_completed(client, [custom_prompt_set], endpoint=mock_llama_server)
    run_id = final["run_id"]

    await client.delete(f"/api/benchmark/run/{run_id}")

    async with get_session() as session:
        row = await session.get(BenchmarkRunRow, run_id)
        assert row is None


async def test_judge_endpoint(client, mock_llama_server, custom_prompt_set):
    """POST /run/{id}/judge evalúa los resultados y actualiza score_judge en DB."""
    final = await _run_and_wait_completed(client, [custom_prompt_set], endpoint=mock_llama_server)
    run_id = final["run_id"]

    # Usamos el mismo mock como judge: devuelve texto libre ("Hola mundo"),
    # así que el parseo del JSON del judge falla — el endpoint tiene que
    # manejarlo sin romper y completar el stream igual.
    res = await client.post(
        f"/api/benchmark/run/{run_id}/judge",
        json={"endpoint": mock_llama_server, "model": "test-model", "api_key": ""},
    )
    assert res.status_code == 200
    content = res.text
    assert "complete" in content or "evaluated" in content


async def test_migration_imports_existing_json(tmp_path):
    """Los JSONs existentes en data/benchmarks/ se importan a la DB al arrancar."""
    import database as db_module
    from database import BenchmarkRunRow, get_session, init_db

    runs_dir = tmp_path / "benchmarks"
    runs_dir.mkdir(exist_ok=True)
    test_run = {
        "run_id": "test-migration-123",
        "started_at": "2026-09-01T10:00:00+00:00",
        "finished_at": "2026-09-01T10:05:00+00:00",
        "status": "completed",
        "config": {"endpoint": "http://localhost:8080", "model_name": "test", "sets": ["coder"]},
        "results": [],
        "summary": {
            "total_prompts": 0, "completed": 0, "errors": 0,
            "avg_tps": 0, "avg_ttft_ms": 0, "avg_tokens": 0,
            "total_duration_s": 0, "keyword_hit_rate": 0,
        },
    }
    (runs_dir / "test-migration-123.json").write_text(
        json.dumps(test_run), encoding="utf-8"
    )

    original_runs_dir = db_module.RUNS_DIR
    db_module.RUNS_DIR = runs_dir
    try:
        await init_db()
        async with get_session() as session:
            row = await session.get(BenchmarkRunRow, "test-migration-123")
            assert row is not None
            assert row.status == "completed"
    finally:
        db_module.RUNS_DIR = original_runs_dir
