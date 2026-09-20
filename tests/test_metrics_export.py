"""Tests de metrics_export.py y de la visibilidad de métricas (display)."""

import re
from types import SimpleNamespace

import config as config_module
import llm_metrics as llm
import launcher as launcher_module
import metrics as metrics_module
import metrics_export as mx
from metrics_store import SampleWindow
from mock_llm_server import INFLUX_STATE, reset_influx


def _influx(base_url, **over):
    cfg = {
        "enabled": True, "version": "v2", "url": base_url, "token": "tok-123",
        "org": "casa", "bucket": "glyvex", "database": "glyvex",
        "username": "", "password": "", "interval_s": 10, "measurement_prefix": "glyvex",
    }
    cfg.update(over)
    config_module.config.set("exports.influx", cfg)
    return cfg


# -- line protocol ----------------------------------------------------------------


def test_line_protocol_escapa_y_omite_tags_vacios():
    windows = [SampleWindow("llm.tg_tps", 1789525000, 37.25, 36.0, 38.5)]
    lines = mx.windows_to_lines(
        windows, measurement="glyvex_llm", units={"llm.tg_tps": "t/s"},
        tags={"process_id": "abc", "model": "Qwen 3.6, 27B=Q4", "vacio": ""},
    )
    assert lines == [
        r"glyvex_llm,series=llm.tg_tps,process_id=abc,model=Qwen\ 3.6\,\ 27B\=Q4,unit=t/s "
        "avg=37.25,min=36.0,max=38.5 1789525000"
    ]


def test_line_protocol_sin_unidad():
    lines = mx.windows_to_lines([SampleWindow("llm.requests_processing", 10, 1, 1, 1)],
                                measurement="glyvex_llm", units={"llm.requests_processing": ""})
    assert lines == ["glyvex_llm,series=llm.requests_processing avg=1.0,min=1.0,max=1.0 10"]


def test_build_write_request_v2_y_v1(monkeypatch):
    monkeypatch.delenv(mx.INFLUX_TOKEN_ENV, raising=False)
    v2 = mx.build_write_request({"version": "v2", "url": "http://h:8086/", "org": "o", "bucket": "b", "token": "t"})
    assert v2["url"] == "http://h:8086/api/v2/write"
    assert v2["params"] == {"org": "o", "bucket": "b", "precision": "s"}
    assert v2["headers"]["Authorization"] == "Token t"

    v1 = mx.build_write_request({"version": "v1", "url": "http://h:8428", "database": "d", "username": "u", "password": "p"})
    assert v1["url"] == "http://h:8428/write" and v1["params"]["db"] == "d" and v1["auth"] == ("u", "p")

    monkeypatch.setenv(mx.INFLUX_TOKEN_ENV, "desde-env")
    assert mx.build_write_request({"version": "v2", "url": "http://h", "bucket": "b", "token": "t"})[
        "headers"]["Authorization"] == "Token desde-env"


def test_build_write_request_errores():
    for bad in ({"url": "h:8086", "bucket": "b"}, {"url": "http://h", "bucket": ""},
                {"version": "v1", "url": "http://h", "database": ""}):
        try:
            mx.build_write_request(bad)
        except ValueError:
            continue
        raise AssertionError(f"debería fallar: {bad}")


# -- exportador -------------------------------------------------------------------


def test_enqueue_apagado_no_acumula():
    config_module.config.set("exports.influx.enabled", False)
    exp = mx.InfluxExporter()
    assert exp.enqueue([SampleWindow("cpu.total_pct", 1, 1, 1, 1)], scope="hw", units={}) == 0
    assert exp.queued == 0


def test_cola_llena_descarta_lo_mas_viejo(monkeypatch, caplog, mock_llama_server):
    _influx(mock_llama_server)
    monkeypatch.setattr(mx, "INFLUX_MAX_QUEUE", 5)
    exp = mx.InfluxExporter()
    with caplog.at_level("WARNING", logger="glyvex.metrics_export"):
        for ts in range(8):
            exp.enqueue([SampleWindow("cpu.total_pct", ts, ts, ts, ts)], scope="hw", units={})
    assert exp.queued == 5
    assert exp._queue[0].endswith(" 3")          # quedaron los 5 más nuevos (3..7)
    assert len([r for r in caplog.records if "cola de InfluxDB llena" in r.getMessage()]) == 1


async def test_envio_v2_contra_mock(mock_llama_server):
    reset_influx()
    _influx(mock_llama_server)
    exp = mx.InfluxExporter()
    exp.enqueue([SampleWindow("gpu.0.temp_c", 1789525000, 65.0, 64.0, 68.0)], scope="hw", units={"gpu.0.temp_c": "°C"})
    exp.enqueue([SampleWindow("llm.tg_tps", 1789525000, 37.2, 36.0, 38.0)], scope="llm",
                units=llm.PERSISTED_UNITS, process_id="p1", model_name="Qwen3.6-27B")

    assert await exp.flush_once() is True
    assert exp.queued == 0 and exp.sent_lines == 2
    write = INFLUX_STATE["writes"][0]
    assert write["path"] == "/api/v2/write"
    assert write["params"] == {"org": "casa", "bucket": "glyvex", "precision": "s"}
    assert write["authorization"] == "Token tok-123"
    assert write["lines"][0] == "glyvex_hw,series=gpu.0.temp_c,unit=°C avg=65.0,min=64.0,max=68.0 1789525000"
    assert write["lines"][1].startswith("glyvex_llm,series=llm.tg_tps,process_id=p1,model=Qwen3.6-27B,unit=t/s ")


async def test_caida_conserva_la_cola_y_se_recupera(mock_llama_server, caplog):
    reset_influx()
    _influx(mock_llama_server)
    exp = mx.InfluxExporter()
    exp.enqueue([SampleWindow("cpu.total_pct", 100, 1, 1, 1)], scope="hw", units={})

    INFLUX_STATE["status"] = 500
    with caplog.at_level("INFO", logger="glyvex.metrics_export"):
        assert await exp.flush_once() is False
        assert await exp.flush_once() is False
        assert exp.queued == 1                    # no se pierde nada
        assert exp._failures == 2 and exp._next_attempt > 0

        INFLUX_STATE["status"] = 204
        assert await exp.flush_once() is True
    assert exp.queued == 0 and exp._failures == 0
    mensajes = [r.getMessage() for r in caplog.records]
    assert len([m for m in mensajes if "no se pudo escribir en InfluxDB" in m]) == 1   # un aviso por racha
    assert any("vuelve a aceptar" in m for m in mensajes)


async def test_envio_v1(mock_llama_server):
    reset_influx()
    _influx(mock_llama_server, version="v1", database="metricas")
    exp = mx.InfluxExporter()
    exp.enqueue([SampleWindow("ram.pct", 5, 50, 49, 51)], scope="hw", units={"ram.pct": "%"})
    assert await exp.flush_once() is True
    assert INFLUX_STATE["writes"][0]["path"] == "/write"
    assert INFLUX_STATE["writes"][0]["params"] == {"db": "metricas", "precision": "s"}


async def test_probar_conexion(client, mock_llama_server):
    reset_influx()
    res = await client.post("/api/metrics/export/influx/test", json={
        "version": "v2", "url": mock_llama_server, "bucket": "glyvex", "org": "casa", "token": "x"})
    data = res.json()
    assert data["ok"] is True and data["status_code"] == 204
    assert INFLUX_STATE["writes"][0]["lines"][0].startswith("glyvex_test,source=glyvex ok=1i ")

    INFLUX_STATE["status"] = 401
    data = (await client.post("/api/metrics/export/influx/test", json={
        "version": "v2", "url": mock_llama_server, "bucket": "glyvex"})).json()
    assert data["ok"] is False and data["status_code"] == 401

    data = (await client.post("/api/metrics/export/influx/test", json={"url": "sin-esquema"})).json()
    assert data["ok"] is False and "http://" in data["detail"]


# -- Prometheus -------------------------------------------------------------------


def _hw_snapshot():
    return metrics_module.MetricsSnapshot(
        timestamp="t",
        gpu=[metrics_module.GpuMetrics(
            index=0, name='RTX "3090"', vram_used_mb=23756, vram_total_mb=24576, vram_free_mb=820,
            vram_percent=96.6, gpu_utilization=2, temperature_c=38, power_draw_w=141.8, power_limit_w=390,
        )],
        cpu=metrics_module.CpuMetrics(percent_total=12.5, temperature_c=None),
        ram=None,
    )


def test_prometheus_texto():
    snap = llm.build_snapshot(
        launcher_module.ProcessInfo(process_id="p1", model_id="m", model_name="Qwen3.6-27B",
                                    backend="llama_server", state="running", host="127.0.0.1",
                                    port=8080, launch_config={"n_ctx": 131072}),
        {"tokens_predicted_total": 954, "prompt_tokens_cached_total": 900, "prompt_tokens_total": 100},
        {**llm.EMPTY_RATES, "tg_tps": 37.2}, None,
        {"ctx_used": 986, "n_ctx": 131072, "slots_total": 1, "slots_busy": 0},
    )
    text = mx.prometheus_text(_hw_snapshot(), [snap])

    assert "# TYPE glyvex_gpu_temperature_celsius gauge" in text
    assert 'glyvex_gpu_temperature_celsius{gpu="0",name="RTX \\"3090\\""} 38.0' in text
    expected_bytes = float(23756 * 1024 * 1024)
    assert re.search(rf'^glyvex_gpu_memory_used_bytes{{.*}} {re.escape(repr(expected_bytes))}$', text, re.M)
    assert 'glyvex_gpu_utilization_ratio{gpu="0",name="RTX \\"3090\\""} 0.02' in text
    assert "glyvex_cpu_temperature_celsius" not in text          # None: no se inventa
    assert 'glyvex_llm_context_tokens{process_id="p1",model="Qwen3.6-27B"} 986.0' in text
    assert 'glyvex_llm_generation_tokens_per_second{process_id="p1",model="Qwen3.6-27B"} 37.2' in text
    assert 'glyvex_llm_prompt_cache_hit_ratio{process_id="p1",model="Qwen3.6-27B"} 0.9' in text
    assert "# TYPE glyvex_llm_tokens_predicted_total counter" in text
    # Cada familia declara HELP y TYPE una sola vez.
    assert text.count("# TYPE glyvex_gpu_temperature_celsius ") == 1


async def test_prometheus_endpoint_apagado_y_token(client, monkeypatch):
    monkeypatch.setattr(metrics_module.manager.collector, "snapshot", lambda: _hw_snapshot())

    assert (await client.get("/api/metrics/prometheus")).status_code == 404

    config_module.config.set("exports.prometheus_enabled", True)
    res = await client.get("/api/metrics/prometheus")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/plain")
    assert "glyvex_up 1" in res.text and "glyvex_gpu_temperature_celsius" in res.text

    config_module.config.set("exports.prometheus_token", "secreto")
    assert (await client.get("/api/metrics/prometheus")).status_code == 401
    assert (await client.get("/api/metrics/prometheus",
                             headers={"Authorization": "Bearer otro"})).status_code == 401
    assert (await client.get("/api/metrics/prometheus",
                             headers={"Authorization": "Bearer secreto"})).status_code == 200


# -- visibilidad ------------------------------------------------------------------


def test_procesos_ocultos_no_se_recorren(monkeypatch):
    collector = metrics_module.MetricsCollector()
    llamadas = []
    monkeypatch.setattr(collector, "collect_processes", lambda: llamadas.append(1) or [])

    collector.snapshot()
    assert llamadas == [1]

    config_module.config.set("display.hidden", ["monitor.processes"])
    snap = collector.snapshot()
    assert llamadas == [1]                          # no se volvió a llamar
    assert snap.processes == []


async def test_historico_apagado_pero_influx_activo(tmp_path, monkeypatch, mock_llama_server):
    """Sin histórico, el poller corre igual para armar las ventanas que se exportan."""
    config_module.config.set("monitor.history_enabled", False)
    _influx(mock_llama_server)
    manager = metrics_module.MetricsManager()
    await manager.start(tmp_path / "no.db")
    try:
        assert manager.store is None
        assert manager._running is True
        assert not (tmp_path / "no.db").exists()
    finally:
        await manager.stop()
