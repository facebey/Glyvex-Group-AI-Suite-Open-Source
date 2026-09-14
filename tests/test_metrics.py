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
