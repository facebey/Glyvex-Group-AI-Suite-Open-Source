"""Tests de B3: alerta de "fuga creciente" (backend/leak_detect.py).

Parte pura: el detector con series sintéticas. Parte de integración: el
endpoint /api/metrics/leak-alert sobre un historial sintético inyectado en
el ring del manager. Sin GPU ni mundo real.
"""

from datetime import datetime, timedelta, timezone

import leak_detect
import metrics as metrics_module

UTC = timezone.utc
BASE = datetime(2026, 10, 31, 12, 0, 0, tzinfo=UTC)

CFG = dict(leak_detect.DEFAULTS)


def ramp(n=270, step_mb=1.2, start_mb=1000.0, util=None, base_ts=0.0):
    """Serie en trepa lineal: 1 sample/s, +step_mb por intervalo."""
    return [(base_ts + i, start_mb + i * step_mb, util) for i in range(n)]


def flat(n=270, mb=1000.0, util=None, base_ts=0.0):
    return [(base_ts + i, mb, util) for i in range(n)]


# ---------------------------------------------------------------------------
# Detector puro
# ---------------------------------------------------------------------------


def test_flat_series_no_alert():
    assert leak_detect.evaluate_series(flat(), CFG) is None


def test_sustained_climb_alerts():
    # Sin datos de utilización (util=None): avg_util=0 → no suprime.
    verdict = leak_detect.evaluate_series(ramp(), CFG)
    assert verdict is not None
    assert verdict["slope_mb_per_min"] == 72.0  # 1.2 MB/s mediano
    assert verdict["growth_mb"] >= CFG["growth_min_mb"]
    assert verdict["monotonic_ratio"] == 1.0


def test_model_load_step_not_flagged():
    # Carga de modelo: un salto enorme dentro de la ventana y después estable.
    # El crecimiento total es grande, pero la trepa NO es sostenida: la
    # pendiente mediana es ~0.
    pts = [(i, 2000.0 if i < 100 else 5000.0, None) for i in range(270)]
    assert leak_detect.evaluate_series(pts, CFG) is None


def test_declining_no_alert():
    pts = [(i, 5000.0 - i * 10.0, None) for i in range(270)]
    assert leak_detect.evaluate_series(pts, CFG) is None


def test_zigzag_no_alert():
    # Subibajada de contexto (slots que se asignan y liberan): no es fuga.
    pts = [(i, 2000.0 + (20.0 if i % 2 == 0 else -20.0), None) for i in range(270)]
    assert leak_detect.evaluate_series(pts, CFG) is None


def test_small_growth_no_alert():
    assert leak_detect.evaluate_series(ramp(step_mb=0.1), CFG) is None


def test_busy_gpu_suppressed():
    # Con la GPU generando, la trepa es esperada (KV/activaciones): no alerta.
    assert leak_detect.evaluate_series(ramp(util=60), CFG) is None


def test_short_span_no_alert():
    # Historial más corto que la ventana: no se opina (evita falsos positivos
    # recién arrancado el poller).
    assert leak_detect.evaluate_series(ramp(n=120), CFG) is None


def test_big_gap_not_counted():
    # Dos bloques separados por un hueco de 100 s (poller detenido): el salto
    # entre bloques no cuenta como intervalo y la pendiente mediana queda en 0.
    pts = flat(50, mb=1000.0, base_ts=150.0) + flat(100, mb=4000.0, base_ts=300.0)
    verdict = leak_detect.evaluate_series(pts, CFG)
    assert verdict is None


def test_window_is_the_tail_of_history():
    # Datos viejos (fuera de la ventana) no contaminan: la trepa real está
    # en los últimos minutos y eso es lo que se evalúa.
    pts = flat(100, mb=1000.0) + ramp(200, step_mb=1.5, start_mb=1000.0, base_ts=100.0)
    verdict = leak_detect.evaluate_series(pts, CFG)
    assert verdict is not None
    assert verdict["n_points"] <= CFG["window_min"] * 60 + 1


# ---------------------------------------------------------------------------
# evaluate_history sobre snapshots
# ---------------------------------------------------------------------------


def _snapshot(seconds_offset, vram_used_mb, util=None, index=42, total_mb=8192.0, name="Test GPU"):
    gpu = metrics_module.GpuMetrics(
        index=index,
        name=name,
        vram_used_mb=vram_used_mb,
        vram_total_mb=total_mb,
        vram_free_mb=max(0.0, total_mb - vram_used_mb),
        vram_percent=round(vram_used_mb / total_mb * 100, 1),
        gpu_utilization=util,
    )
    ts = (BASE + timedelta(seconds=seconds_offset)).isoformat()
    return metrics_module.MetricsSnapshot(timestamp=ts, gpu=[gpu])


def _history(climb_mb_per_s, seconds=270, **gpu_kwargs):
    return [
        _snapshot(i, 1000.0 + i * climb_mb_per_s, **gpu_kwargs)
        for i in range(seconds)
    ]


def test_history_with_climb_alerts():
    # GPU index 42: los tests de endpoint comparten el manager real de la app
    # y un índice propio evita cruzarse con GPUs reales de la máquina.
    alerts = leak_detect.evaluate_history(_history(1.2), CFG)
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["gpu_index"] == 42
    assert alert["gpu_name"] == "Test GPU"
    assert alert["slope_mb_per_min"] == 72.0
    assert alert["vram_total_mb"] == 8192.0


def test_history_flat_and_empty_no_alerts():
    assert leak_detect.evaluate_history(_history(0.0), CFG) == []
    assert leak_detect.evaluate_history([], CFG) == []


def test_history_multi_gpu_sorted_by_slope():
    # La "GPU lenta" crece a 1.1 MB/s: supera los umbrales pero con menor
    # pendiente, y la alerta peor queda primera.
    history = []
    for i in range(270):
        history.append(_snapshot(i, 1000.0 + i * 1.2, index=0, name="GPU rápida"))
        history.append(_snapshot(i, 1000.0 + i * 1.1, index=1, name="GPU lenta"))
    alerts = leak_detect.evaluate_history(history, CFG)
    assert [a["gpu_index"] for a in alerts] == [0, 1]
    assert alerts[0]["slope_mb_per_min"] > alerts[1]["slope_mb_per_min"]


def test_disabled_via_config(monkeypatch):
    def fake_get(key, default=None):
        if key == "monitor.leak_alert":
            return {"enabled": False}
        return default

    monkeypatch.setattr(leak_detect.config, "get", fake_get)
    cfg = leak_detect.load_config()
    assert cfg["enabled"] is False
    assert leak_detect.evaluate_history(_history(1.2)) == []


# ---------------------------------------------------------------------------
# Integración: GET /api/metrics/leak-alert
# ---------------------------------------------------------------------------


async def test_leak_alert_endpoint_flags_growing_vram(client):
    saved = list(metrics_module.manager.history)
    try:
        metrics_module.manager.history.clear()
        for snap in _history(1.2):
            metrics_module.manager.history.append(snap)
        res = await client.get("/api/metrics/leak-alert")
        assert res.status_code == 200
        alerts = res.json()["alerts"]
        assert len(alerts) == 1
        assert alerts[0]["gpu_index"] == 42
        assert alerts[0]["slope_mb_per_min"] > 0
    finally:
        metrics_module.manager.history.clear()
        for snap in saved:
            metrics_module.manager.history.append(snap)


async def test_leak_alert_endpoint_quiet_when_flat(client):
    saved = list(metrics_module.manager.history)
    try:
        metrics_module.manager.history.clear()
        for snap in _history(0.0):
            metrics_module.manager.history.append(snap)
        res = await client.get("/api/metrics/leak-alert")
        assert res.status_code == 200
        assert res.json()["alerts"] == []
    finally:
        metrics_module.manager.history.clear()
        for snap in saved:
            metrics_module.manager.history.append(snap)
