"""Tests de session_tokens.py — acumulador de tokens del chat por sesión (MON-2)."""

import json

import session_tokens as session_tokens_module


# --------------------------------------------------------------------------
# Clase SessionTokens (unidades, sin app)
# --------------------------------------------------------------------------


async def test_record_sums_and_persists(tmp_path):
    st = session_tokens_module.SessionTokens(path=tmp_path / "session_tokens.json")
    await st.record_turn(prompt_tokens=100, completion_tokens=50, cached_tokens=40)
    await st.record_turn(prompt_tokens=200, completion_tokens=25, cached_tokens=10)

    snap = st.snapshot()
    assert snap["prompt_tokens"] == 300
    assert snap["completion_tokens"] == 75
    assert snap["cached_tokens"] == 50
    assert snap["turns"] == 2
    assert snap["started_at"] is not None
    assert snap["updated_at"] is not None

    on_disk = json.loads((tmp_path / "session_tokens.json").read_text(encoding="utf-8"))
    assert on_disk["prompt_tokens"] == 300
    assert on_disk["turns"] == 2


async def test_load_restores_previous_state(tmp_path):
    path = tmp_path / "session_tokens.json"
    st = session_tokens_module.SessionTokens(path=path)
    await st.record_turn(prompt_tokens=10, completion_tokens=5, cached_tokens=2)

    # Simula un reinicio de la app: nueva instancia, mismo archivo.
    st2 = session_tokens_module.SessionTokens(path=path)
    await st2.load()
    snap = st2.snapshot()
    assert snap["prompt_tokens"] == 10
    assert snap["cached_tokens"] == 2
    assert snap["turns"] == 1
    assert snap["started_at"] == st.snapshot()["started_at"]


async def test_load_tolerates_missing_or_corrupt_file(tmp_path):
    path = tmp_path / "session_tokens.json"
    st = session_tokens_module.SessionTokens(path=path)

    await st.load()  # sin archivo
    assert st.snapshot()["turns"] == 0

    path.write_text("{no es json", encoding="utf-8")
    await st.load()  # corrupto
    assert st.snapshot()["turns"] == 0

    path.write_text("[1, 2, 3]", encoding="utf-8")
    await st.load()  # JSON que no es un objeto
    assert st.snapshot()["turns"] == 0


async def test_reset_zeros_and_persists(tmp_path):
    st = session_tokens_module.SessionTokens(path=tmp_path / "session_tokens.json")
    await st.record_turn(prompt_tokens=100, completion_tokens=50)
    snap = await st.reset()
    assert snap["prompt_tokens"] == 0
    assert snap["turns"] == 0
    assert snap["started_at"] is None
    assert snap["updated_at"] is None

    st2 = session_tokens_module.SessionTokens(path=st.path)
    await st2.load()
    assert st2.snapshot()["turns"] == 0


# --------------------------------------------------------------------------
# API (contra la app, singleton aislado por _isolated_state)
# --------------------------------------------------------------------------


async def test_api_session_tokens_endpoint(client):
    res = await client.get("/api/metrics/session-tokens")
    assert res.status_code == 200
    data = res.json()
    assert data["tokens"]["turns"] == 0
    assert data["tokens"]["prompt_tokens"] == 0
    # Los precios de referencia viajan en la misma respuesta: el Monitor
    # no necesita pedir /api/config aparte.
    assert data["pricing"]["currency"] == "USD"
    assert data["pricing"]["default_provider_id"] in [p["id"] for p in data["pricing"]["providers"]]
    assert len(data["pricing"]["providers"]) >= 2


async def test_api_session_tokens_reflects_recorded_turn(client):
    await session_tokens_module.session_tokens.record_turn(
        prompt_tokens=123, completion_tokens=45, cached_tokens=9
    )
    data = (await client.get("/api/metrics/session-tokens")).json()
    assert data["tokens"]["prompt_tokens"] == 123
    assert data["tokens"]["completion_tokens"] == 45
    assert data["tokens"]["cached_tokens"] == 9
    assert data["tokens"]["turns"] == 1


async def test_api_reset(client):
    await session_tokens_module.session_tokens.record_turn(prompt_tokens=10, completion_tokens=1)
    res = await client.post("/api/metrics/session-tokens/reset")
    assert res.status_code == 200
    assert res.json()["turns"] == 0

    data = (await client.get("/api/metrics/session-tokens")).json()
    assert data["tokens"]["turns"] == 0
    assert data["tokens"]["prompt_tokens"] == 0


async def test_api_pricing_defaults(client):
    res = await client.get("/api/config/pricing-defaults")
    assert res.status_code == 200
    data = res.json()
    assert data["default_provider_id"] in [p["id"] for p in data["providers"]]
    for provider in data["providers"]:
        assert provider["input_per_mtok"] >= 0
        assert provider["output_per_mtok"] >= 0
