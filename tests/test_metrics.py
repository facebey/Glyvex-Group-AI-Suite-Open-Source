"""Tests de metrics.py — estructura del snapshot, degradación sin GPU, buffer circular."""

import pytest

import metrics as metrics_module


async def test_snapshot_structure(client):
    res = await client.get("/api/metrics/snapshot")
    assert res.status_code == 200
    data = res.json()
    assert "gpu" in data
    assert "cpu" in data
    assert "ram" in data
    assert "processes" in data
    assert isinstance(data["processes"], list)


async def test_no_nvidia_graceful(client, monkeypatch):
    # Simulamos "pynvml no disponible" sin importar si la máquina que corre
    # los tests tiene GPU NVIDIA de verdad o no.
    monkeypatch.setattr(metrics_module, "PYNVML_AVAILABLE", False)

    res = await client.get("/api/metrics/snapshot")
    assert res.status_code == 200
    data = res.json()
    assert data["gpu"] is None
    assert data["gpu_error"] is not None


async def test_cpu_percent_range(client):
    res = await client.get("/api/metrics/snapshot")
    data = res.json()
    if data["cpu"] is None:
        pytest.skip("psutil no disponible en este entorno")
    assert 0.0 <= data["cpu"]["percent_total"] <= 100.0


async def test_ram_coherent(client):
    res = await client.get("/api/metrics/snapshot")
    data = res.json()
    if data["ram"] is None:
        pytest.skip("psutil no disponible en este entorno")
    assert data["ram"]["used_gb"] <= data["ram"]["total_gb"]


async def test_history_accumulates(client):
    for _ in range(3):
        res = await client.get("/api/metrics/snapshot")
        assert res.status_code == 200

    history = (await client.get("/api/metrics/history")).json()
    assert len(history) >= 3


# --------------------------------------------------------------------------
# MetricsService: series, agregación y histórico persistente
# --------------------------------------------------------------------------

import asyncio  # noqa: E402
import time  # noqa: E402




def _snapshot_with(gpu_temp=None, cpu_total=None, cpu_temp=None):
    gpu = None
    if gpu_temp is not None:
        gpu = [metrics_module.GpuMetrics(
            index=0, name="GPU", vram_used_mb=1000, vram_total_mb=24000, vram_free_mb=23000,
            vram_percent=4.2, gpu_utilization=50, temperature_c=gpu_temp, power_draw_w=None,
        )]
    cpu = metrics_module.CpuMetrics(percent_total=cpu_total or 0.0, temperature_c=cpu_temp)
    return metrics_module.MetricsSnapshot(timestamp="t", gpu=gpu, cpu=cpu, ram=None)


def test_flatten_omite_sensores_nulos():
    values, units = metrics_module.flatten_snapshot(_snapshot_with(gpu_temp=70, cpu_total=12.5))
    assert values["gpu.0.temp_c"] == 70.0
    assert units["gpu.0.temp_c"] == "°C"
    assert values["cpu.total_pct"] == 12.5
    # Sin dato no hay serie: un hueco en el gráfico, no un cero falso.
    assert "gpu.0.power_w" not in values
    assert "cpu.temp_c" not in values
    assert not any(k.startswith("ram.") for k in values)


def test_aggregator_ventanas_alineadas():
    agg = metrics_module.WindowAggregator(step_s=5)
    base = 1_780_000_000
    for i, temp in enumerate([60, 90, 61, 62, 63]):
        agg.add(base + i, {"gpu.0.temp_c": float(temp)}, {"gpu.0.temp_c": "°C"})
    agg.add(base + 5, {"gpu.0.temp_c": 70.0}, {})

    assert agg.pop_closed(base + 4) == []            # ventana todavía abierta
    closed = agg.pop_closed(base + 5)
    assert len(closed) == 1
    w = closed[0]
    assert (w.ts, w.min, w.max) == (base, 60.0, 90.0)
    assert w.avg == (60 + 90 + 61 + 62 + 63) / 5

    forced = agg.pop_closed(base + 5, force=True)    # al apagar se guarda lo pendiente
    assert [x.avg for x in forced] == [70.0]


async def test_query_sin_datos(client):
    res = await client.get("/api/metrics/query", params={"key": "gpu.0.temp_c"})
    assert res.status_code == 200
    data = res.json()
    assert data["enabled"] is True
    assert data["series"] == {"gpu.0.temp_c": []}


async def test_persistencia_y_query(client):
    store = metrics_module.manager.store
    agg = metrics_module.WindowAggregator()
    now = int(time.time()) // 5 * 5 - 60
    for i in range(30):
        snap = _snapshot_with(gpu_temp=65 + (i % 3), cpu_total=20.0)
        values, units = metrics_module.flatten_snapshot(snap)
        agg.add(now + i, values, units)
    store.write_raw(agg.pop_closed(now + 30), units=agg.units)

    series = (await client.get("/api/metrics/series")).json()
    keys = {s["key"] for s in series["series"]}
    assert {"gpu.0.temp_c", "cpu.total_pct"} <= keys

    res = await client.get(
        "/api/metrics/query",
        params=[("key", "gpu.0.temp_c"), ("key", "cpu.total_pct"),
                ("start", now), ("end", now + 30)],
    )
    data = res.json()
    assert data["tier"] == "raw"
    temps = data["series"]["gpu.0.temp_c"]
    assert len(temps) == 6
    assert all(65.0 <= p["min"] <= p["avg"] <= p["max"] <= 67.0 for p in temps)
    assert all(p["avg"] == 20.0 for p in data["series"]["cpu.total_pct"])


async def test_servicio_de_fondo_guarda_sin_clientes(tmp_path, monkeypatch):
    """El poller corre sin WebSocket conectado y al detenerse vuelca lo pendiente."""
    manager = metrics_module.MetricsManager()
    monkeypatch.setattr(metrics_module, "STREAM_INTERVAL_S", 0.05)
    monkeypatch.setattr(
        manager, "record_snapshot", lambda: _snapshot_with(gpu_temp=72, cpu_total=30.0)
    )

    await manager.start(tmp_path / "bg.db")
    assert manager.store is not None
    await asyncio.sleep(0.3)
    assert not manager._clients
    await manager.stop()

    store = metrics_module.MetricsStore(tmp_path / "bg.db")
    store.open()
    now = int(time.time())
    res = store.query(["gpu.0.temp_c"], now - 60, now + 60, now=now)
    assert res["series"]["gpu.0.temp_c"], "el servicio no guardó muestras"
    assert res["series"]["gpu.0.temp_c"][0]["avg"] == 72.0
    store.close()


async def test_historial_deshabilitado(tmp_path):
    import config as config_module

    config_module.config.set("monitor.history_enabled", False)
    manager = metrics_module.MetricsManager()
    await manager.start(tmp_path / "off.db")
    assert manager.store is None
    assert manager._broadcast_task is None
    assert not (tmp_path / "off.db").exists()
