"""Tests de llm_metrics.py — parseo Prometheus, tasas, snapshot, poller y API."""

import asyncio
import json
import time
from pathlib import Path

import launcher as launcher_module
import llm_metrics as llm
import metrics as metrics_module
from mock_llm_server import reset_metrics

SAMPLE = """# HELP llamacpp:prompt_tokens_total Number of prompt tokens processed.
# TYPE llamacpp:prompt_tokens_total counter
llamacpp:prompt_tokens_total 1500
llamacpp:tokens_predicted_total 820
llamacpp:tokens_predicted_seconds_total 9.5
llamacpp:requests_processing 1
llamacpp:requests_deferred 0
llamacpp:n_past_max 4096
llamacpp:kv_cache_usage_ratio NaN
llamacpp:n_busy_slots_per_decode{model="a"} 1
llamacpp:n_busy_slots_per_decode{model="b"} 2
garbage line without value
other_metric 7 1712345678000
"""


FIXTURES = Path(__file__).parent / "fixtures"


def _fixture_json(name):
    return json.loads((FIXTURES / name).read_text())


def _process(port=18080, state="running", backend="llama_server", **cfg) -> launcher_module.ProcessInfo:
    return launcher_module.ProcessInfo(
        process_id="proc-test",
        model_id="m",
        model_name="Qwen3-Test",
        backend=backend,
        state=state,
        pid=123,
        host="0.0.0.0",
        port=port,
        launch_config={"n_ctx": 32768, **cfg},
    )


# -- parseo ---------------------------------------------------------------------


def test_parse_prometheus():
    values = llm.parse_prometheus(SAMPLE)
    assert values["prompt_tokens_total"] == 1500
    assert values["tokens_predicted_seconds_total"] == 9.5
    assert values["n_past_max"] == 4096
    assert "kv_cache_usage_ratio" not in values        # NaN se ignora
    assert values["n_busy_slots_per_decode"] == 3      # etiquetas distintas se suman
    assert values["other_metric"] == 7                 # timestamp opcional
    assert "garbage" not in values


# -- tasas ----------------------------------------------------------------------


def test_rates_primer_scrape_sin_tasa():
    tracker = llm.RateTracker()
    rates = tracker.update("p", {"tokens_predicted_total": 100, "tokens_predicted_seconds_total": 2}, 1000.0)
    assert rates == llm.EMPTY_RATES
    assert all(v is None for v in rates.values())


def test_rates_generando_y_ocioso():
    tracker = llm.RateTracker()
    base = {"tokens_predicted_total": 100, "tokens_predicted_seconds_total": 2.0,
            "prompt_tokens_total": 50, "prompt_seconds_total": 0.1}
    tracker.update("p", base, 1000.0)

    gen = {"tokens_predicted_total": 240, "tokens_predicted_seconds_total": 3.0,
           "prompt_tokens_total": 450, "prompt_seconds_total": 0.3}
    rates = tracker.update("p", gen, 1002.0)
    assert rates["tg_tps"] == 140.0          # 140 tokens en 1 s de decode
    assert rates["pp_tps"] == 2000.0         # 400 tokens en 0,2 s
    assert rates["throughput_tps"] == 70.0   # 140 tokens en 2 s de reloj

    idle = tracker.update("p", gen, 1003.0)
    assert idle["tg_tps"] is None            # sin generación no hay velocidad
    assert idle["throughput_tps"] == 0.0     # pero sí throughput cero


def test_rates_reinicio_con_mismo_id():
    tracker = llm.RateTracker()
    tracker.update("p", {"tokens_predicted_total": 5000, "tokens_predicted_seconds_total": 50}, 1000.0)
    after_restart = tracker.update("p", {"tokens_predicted_total": 30, "tokens_predicted_seconds_total": 0.3}, 1001.0)
    assert after_restart == llm.EMPTY_RATES
    # La siguiente lectura ya calcula contra el valor post-reinicio.
    ok = tracker.update("p", {"tokens_predicted_total": 130, "tokens_predicted_seconds_total": 1.3}, 1002.0)
    assert ok["tg_tps"] == 100.0


# -- snapshot -------------------------------------------------------------------


def test_snapshot_sin_kv_cache_usa_pico():
    values = llm.parse_prometheus(SAMPLE)
    snap = llm.build_snapshot(_process(), values, {**llm.EMPTY_RATES, "tg_tps": 80.0, "throughput_tps": 40.0}, None)
    assert snap.scrape_ok
    assert snap.ctx_total == 32768
    assert snap.ctx_used is None and snap.ctx_usage_ratio is None
    assert snap.ctx_peak == 4096
    assert snap.requests_processing == 1

    flat = llm.flatten_llm_snapshot(snap)
    assert flat["llm.tg_tps"] == 80.0
    assert "llm.ctx_pct" not in flat         # null no genera muestra


def test_snapshot_con_kv_cache():
    values = {"kv_cache_tokens": 8192, "tokens_predicted_total": 1}
    snap = llm.build_snapshot(_process(), values, dict(llm.EMPTY_RATES), None)
    assert snap.ctx_used == 8192
    assert snap.ctx_usage_ratio == 0.25
    assert llm.flatten_llm_snapshot(snap)["llm.ctx_pct"] == 25.0


def test_salida_real_de_llama_server_2026_09():
    """Salida de /metrics de un llama-server real (build 2026-09)."""
    text = (Path(__file__).parent / "fixtures" / "llama_server_metrics_2026-09.txt").read_text()
    values = llm.parse_prometheus(text)
    assert values["prompt_tokens_cached_total"] == 1_723_230   # notación científica
    assert "n_past_max" not in values

    snap = llm.build_snapshot(_process(), values, dict(llm.EMPTY_RATES), None)
    assert snap.ctx_peak == 50569                 # antes quedaba vacío
    assert snap.ctx_used is None                  # este build no expone el uso actual
    assert snap.cache_hit_pct_total == 94.9       # 1.72M / (1.72M + 92k)
    assert snap.spec_accept_pct_total is None     # 0 drafts: sin MTP, no 0 %
    assert snap.requests_processing == 0


def test_rates_cache_y_especulativa():
    tracker = llm.RateTracker()
    base = {"tokens_predicted_total": 0, "prompt_tokens_total": 1000, "prompt_tokens_cached_total": 0,
            "spec_decode_num_draft_tokens_total": 0, "spec_decode_num_accepted_tokens_total": 0}
    tracker.update("p", base, 1000.0)
    after = {**base, "prompt_tokens_total": 1200, "prompt_tokens_cached_total": 1800,
             "spec_decode_num_draft_tokens_total": 64, "spec_decode_num_accepted_tokens_total": 48}
    rates = tracker.update("p", after, 1005.0)
    assert rates["cache_hit_pct"] == 90.0         # 1800 / (1800 + 200)
    assert rates["spec_accept_pct"] == 75.0

    idle = tracker.update("p", after, 1010.0)
    assert idle["cache_hit_pct"] is None          # sin prompts en el intervalo
    assert idle["spec_accept_pct"] is None


def test_tope_global_de_procesos(monkeypatch):
    """buffers/_models/RateTracker._prev están acotados a MAX_TRACKED_PROCESSES."""
    monkeypatch.setattr(llm, "MAX_TRACKED_PROCESSES", 3)
    manager = llm.LlmMetricsManager()

    def snap(pid):
        return llm.LlmMetricsSnapshot(
            timestamp="t", process_id=pid, model_name=f"m-{pid}",
            state="running", scrape_ok=True,
        )

    for pid in ("a", "b", "c"):
        manager.record(snap(pid))
    assert set(manager.buffers) == {"a", "b", "c"}

    manager.record(snap("d"))
    assert set(manager.buffers) == {"b", "c", "d"}
    assert set(manager._models) == {"b", "c", "d"}

    tracker = llm.RateTracker()
    for pid in ("a", "b", "c", "d"):
        tracker.update(pid, {}, 1.0)
    assert set(tracker._prev) == {"b", "c", "d"}


async def test_intervalo_adaptativo_y_despertar(monkeypatch):
    """En fondo espera 5 s; al conectarse un cliente se despierta enseguida."""
    manager = llm.LlmMetricsManager()
    polls = []

    async def fake_poll():
        polls.append(time.monotonic())
        return []

    monkeypatch.setattr(manager, "poll_once", fake_poll)
    monkeypatch.setattr(llm, "BACKGROUND_INTERVAL_S", 5.0)
    monkeypatch.setattr(llm, "POLL_INTERVAL_S", 0.05)

    await manager.start()
    await asyncio.sleep(0.3)
    assert len(polls) == 1                        # en fondo: una lectura y a esperar

    class FakeWS:
        async def send_text(self, _):
            pass

    ws = FakeWS()
    manager.add_client("p", ws)
    await asyncio.sleep(0.3)
    assert len(polls) >= 4                        # despertó y pasó a 1 s (acá 0,05 s)

    manager.remove_client("p", ws)
    await manager.stop()


# -- /slots (build 2026-09) -----------------------------------------------------


def test_slots_generando_es_prompt_mas_generados():
    a = llm.parse_slots(_fixture_json("llama_server_slots_generating_a_2026-09.json"))
    b = llm.parse_slots(_fixture_json("llama_server_slots_generating_b_2026-09.json"))
    assert a == {"ctx_used": 220, "n_ctx": 131072, "slots_total": 1, "slots_busy": 1}
    assert b["ctx_used"] == 334
    # La evidencia del formato: crece a la par de n_decoded (152 -> 266), con
    # el prompt (n_prompt_tokens_processed = 68) fijo.
    assert b["ctx_used"] - a["ctx_used"] == 266 - 152


def test_slots_ocioso_conserva_la_secuencia_en_cache():
    idle = llm.parse_slots(_fixture_json("llama_server_slots_idle_2026-09.json"))
    assert idle == {"ctx_used": 68514, "n_ctx": 131072, "slots_total": 1, "slots_busy": 0}


def test_slots_formato_inesperado():
    assert llm.parse_slots({"error": "x"}) is None
    assert llm.parse_slots([]) is None
    assert llm.parse_slots([{"id": 0}]) is None                    # sin n_prompt_tokens
    assert llm.parse_slots([{"n_prompt_tokens": True}]) is None     # bool no es int
    two = llm.parse_slots([
        {"n_ctx": 16384, "n_prompt_tokens": 1000, "is_processing": True},
        {"n_ctx": 16384, "n_prompt_tokens": 500, "is_processing": False},
    ])
    assert two == {"ctx_used": 1500, "n_ctx": 16384, "slots_total": 2, "slots_busy": 1}


def test_snapshot_real_metrics_mas_slots():
    values = llm.parse_prometheus((FIXTURES / "llama_server_metrics_2026-09.txt").read_text())
    slots = llm.parse_slots(_fixture_json("llama_server_slots_idle_2026-09.json"))
    snap = llm.build_snapshot(_process(n_ctx=131072), values, dict(llm.EMPTY_RATES), None, slots)
    assert snap.ctx_used == 68514
    assert snap.ctx_source == "slots"
    assert snap.ctx_total == 131072
    assert snap.ctx_usage_ratio == 0.5227                  # 68514 / 131072
    assert snap.ctx_peak == 50569                          # el pico sigue disponible
    assert abs(llm.flatten_llm_snapshot(snap)["llm.ctx_pct"] - 52.27) < 1e-9


def test_ratio_de_contexto_no_supera_100():
    slots = {"ctx_used": 40000, "n_ctx": 131072, "slots_total": 1, "slots_busy": 0}
    snap = llm.build_snapshot(_process(n_ctx=32768), {}, dict(llm.EMPTY_RATES), None, slots)
    assert snap.ctx_usage_ratio == 1.0


def test_kv_cache_tiene_prioridad_sobre_slots():
    slots = {"ctx_used": 9999, "n_ctx": 32768, "slots_total": 1, "slots_busy": 0}
    snap = llm.build_snapshot(_process(), {"kv_cache_tokens": 8192}, dict(llm.EMPTY_RATES), None, slots)
    assert snap.ctx_used == 8192 and snap.ctx_source == "kv_cache"


def test_scrape_host_y_errores():
    assert llm.scrape_host("0.0.0.0") == "127.0.0.1"
    assert llm.scrape_host("192.168.1.10") == "192.168.1.10"
    assert "--metrics" in llm.describe_scrape_error(501, None)


# -- poller contra el mock ------------------------------------------------------


async def test_poll_once_contra_mock(mock_llama_server):
    reset_metrics()
    launcher_module.manager._info["proc-test"] = _process()

    first = await llm.manager.poll_once()
    second = await llm.manager.poll_once()

    assert len(first) == 1 and first[0].scrape_ok
    assert first[0].tg_tps is None               # primer scrape: sin tasa
    snap = second[0]
    assert snap.tg_tps == 50.0                   # mock: +50 tokens por 1 s de decode
    assert snap.pp_tps == 400.0                  # +100 tokens por 0,25 s
    assert snap.throughput_tps is not None and snap.throughput_tps > 0
    assert snap.requests_deferred == 2
    assert snap.ctx_peak == 1210                 # n_tokens_max del build actual
    assert snap.cache_hit_pct == 90.0            # +900 cacheados por +100 procesados
    assert snap.spec_accept_pct == 75.0          # +30 aceptados de +40 propuestos
    assert snap.ctx_source == "slots"
    assert snap.ctx_used == 4050                 # mock /slots: 4000 + 50 generados
    assert snap.ctx_usage_ratio == round(4050 / 32768, 4)
    assert snap.slots_busy == 1
    assert len(llm.manager.buffers["proc-test"]) == 2


async def test_poll_ignora_otros_backends_y_estados(mock_llama_server):
    launcher_module.manager._info = {
        "a": _process(backend="ollama").model_copy(update={"process_id": "a"}),
        "b": _process(state="starting").model_copy(update={"process_id": "b"}),
        "c": _process(state="stopped").model_copy(update={"process_id": "c"}),
    }
    assert await llm.manager.poll_once() == []


async def test_scrape_error_se_informa():
    # Puerto sin nada escuchando.
    launcher_module.manager._info["proc-test"] = _process(port=1)
    snaps = await llm.manager.poll_once()
    assert snaps[0].scrape_ok is False
    assert snaps[0].scrape_error


async def test_persistencia_por_proceso_y_api(client, mock_llama_server):
    reset_metrics()
    launcher_module.manager._info["proc-test"] = _process()
    manager = llm.manager

    now = time.time() // 5 * 5
    for i in range(3):
        snaps = await manager.poll_once()
        for snap in snaps:
            agg = manager._aggregators.setdefault(snap.process_id, metrics_module.WindowAggregator())
            agg.add(now + i, llm.flatten_llm_snapshot(snap), llm.PERSISTED_UNITS)
    await manager._flush(now + 60, force=True)

    store = metrics_module.manager.store
    res = store.query(["llm.tg_tps"], int(now) - 5, int(now) + 10, now=int(now) + 10, process_id="proc-test")
    assert res["series"]["llm.tg_tps"][0]["avg"] == 50.0

    # Las series de hardware no se mezclan: sin process_id no aparece.
    hw = store.query(["llm.tg_tps"], int(now) - 5, int(now) + 10, now=int(now) + 10)
    assert hw["series"]["llm.tg_tps"] == []

    data = (await client.get("/api/llm-metrics/processes")).json()
    assert [p["process_id"] for p in data["live"]] == ["proc-test"]
    assert data["stored"][0]["process_id"] == "proc-test"
    assert data["stored"][0]["model_name"] == "Qwen3-Test"

    history = (await client.get("/api/llm-metrics/proc-test/history")).json()
    assert len(history) == 3

    via_hw_api = (await client.get(
        "/api/metrics/query",
        params={"key": "llm.tg_tps", "start": int(now) - 5, "end": int(now) + 10, "process_id": "proc-test"},
    )).json()
    assert via_hw_api["series"]["llm.tg_tps"][0]["avg"] == 50.0
