"""Tests de metrics.py — estructura del snapshot, degradación sin GPU, buffer circular."""

import asyncio
import json

import pytest

import metrics as metrics_module
from config import Config


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


# --------------------------------------------------------------------------
# Límite de potencia (TDP): GET/POST /api/metrics/gpu/power-limit
# --------------------------------------------------------------------------


class _FakePynvml:
    """Sustituye pynvml sin tocar una GPU real: una sola GPU con power limit
    soportado (240–350 W, por defecto 350 W) y un interruptor para hacer fallar
    el set (permisos, no soportado, etc.)."""

    def __init__(self, fail_set: str | None = None, fail_fan_set: str | None = None,
                 ignore_fan_set: bool = False, fan_speed: int = 42):
        self.fail_set = fail_set
        self.fail_fan_set = fail_fan_set
        self.ignore_fan_set = ignore_fan_set
        self.fan_speed = fan_speed
        self.last_set = None
        self.last_fan_set = None
        self.shutdowns = 0

    def nvmlShutdown(self):
        self.shutdowns += 1

    def nvmlDeviceGetCount(self):
        return 1

    def nvmlDeviceGetHandleByIndex(self, index):
        return f"handle-{index}"

    def nvmlDeviceGetName(self, handle):
        return b"NVIDIA GeForce RTX 3090"

    def nvmlDeviceGetPowerManagementLimit(self, handle):
        return 350_000  # mW

    def nvmlDeviceGetPowerManagementLimitConstraints(self, handle):
        return (240_000, 350_000)  # (min, max) mW

    def nvmlDeviceGetPowerManagementDefaultLimit(self, handle):
        return 350_000  # mW

    def nvmlDeviceSetPowerManagementLimit(self, handle, limit_mw):
        if self.fail_set:
            raise Exception(self.fail_set)
        self.last_set = limit_mw

    def nvmlDeviceGetFanSpeed(self, handle):
        return self.fan_speed

    def nvmlDeviceSetFanSpeed_v2(self, handle, fan, percent):
        if self.fail_fan_set:
            raise Exception(self.fail_fan_set)
        self.last_fan_set = percent
        # Con `ignore_fan_set` simula el driver que "acepta" la escritura y la
        # ignora en silencio (GeForce con VBIOS de consumo): el read-back
        # del collector debe detectar que el valor real no cambió.
        if not self.ignore_fan_set:
            self.fan_speed = percent


def _mock_nvml(monkeypatch, fake: _FakePynvml) -> None:
    monkeypatch.setattr(metrics_module, "PYNVML_AVAILABLE", True)
    monkeypatch.setattr(metrics_module, "pynvml", fake)
    monkeypatch.setattr(metrics_module.manager.collector, "_nvml_ready", True)


class _FakeNvApi:
    """Sustituye el adapter NvAPI Direct sin cargar nvapi64.dll: `available`
    decide si el collector toma la vía Windows o cae a NVML. `control` simula
    los coolers como [(ControlMode, Level)]: `fan_control` lo lee y las
    escrituras exitosas lo modifican, así las pruebas de MON-3 pueden verificar
    qué se capturó como baseline y qué se restauró."""

    def __init__(self, available: bool = False,
                 set_result: tuple[bool, str | None] = (False, "nvapi_no_disponible"),
                 control: list[tuple[int, int]] | None = None):
        self.available = available
        self.set_result = set_result
        self.control = [tuple(c) for c in (control if control is not None else [(0, 30)])]
        self.calls = []
        self.auto_calls: list[int] = []
        self.restored: list[tuple[int, list[tuple[int, int]]]] = []

    def set_manual(self, index, percent, tolerance=2, readback_s=1.0):
        self.calls.append((index, percent, tolerance, readback_s))
        if self.set_result[0]:
            self.control = [(1, percent)]
        return self.set_result

    def fan_control(self, index):
        return list(self.control)

    def set_auto(self, index):
        self.auto_calls.append(index)
        self.control = [(0, self.control[0][1])]
        return True, None

    def restore(self, index, items, tolerance=2):
        self.restored.append((index, [tuple(i) for i in items]))
        self.control = [tuple(i) for i in items]
        return True, None


def _mock_nvapi(monkeypatch, fake: _FakeNvApi) -> None:
    monkeypatch.setattr(metrics_module.manager.collector, "_nvapi_fan", fake)


async def test_power_limit_get(client, monkeypatch):
    _mock_nvml(monkeypatch, _FakePynvml())
    res = await client.get("/api/metrics/gpu/power-limit")
    assert res.status_code == 200
    data = res.json()
    assert data["available"] is True
    assert isinstance(data["privileged"], bool)
    assert data["error"] is None
    gpu = data["gpus"][0]
    assert gpu["index"] == 0
    assert gpu["name"] == "NVIDIA GeForce RTX 3090"
    assert gpu["supported"] is True
    assert gpu["current_w"] == 350.0
    assert gpu["min_w"] == 240.0
    assert gpu["max_w"] == 350.0
    assert gpu["default_w"] == 350.0


async def test_power_limit_get_privilege_hint(client, monkeypatch):
    # La clave depende del SO ("admin" en Windows, "root" en POSIX); el
    # frontend la traduce a su texto i18n.
    _mock_nvml(monkeypatch, _FakePynvml())
    monkeypatch.setattr(metrics_module.platform, "system", lambda: "Windows")
    res = await client.get("/api/metrics/gpu/power-limit")
    assert res.json()["privilege_hint"] == "admin"

    monkeypatch.setattr(metrics_module.platform, "system", lambda: "Linux")
    res = await client.get("/api/metrics/gpu/power-limit")
    data = res.json()
    assert data["privilege_hint"] == "root"
    assert data["privileged"] is False


async def test_power_limit_set(client, monkeypatch):
    fake = _FakePynvml()
    _mock_nvml(monkeypatch, fake)
    res = await client.post("/api/metrics/gpu/power-limit", json={"index": 0, "watts": 250.0})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["error"] is None
    assert fake.last_set == 250_000  # mW


async def test_power_limit_set_default(client, monkeypatch):
    fake = _FakePynvml()
    _mock_nvml(monkeypatch, fake)
    res = await client.post("/api/metrics/gpu/power-limit", json={"index": 0, "default": True})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert fake.last_set == 350_000  # vuelve al límite de fábrica


async def test_power_limit_set_sin_watts(client, monkeypatch):
    fake = _FakePynvml()
    _mock_nvml(monkeypatch, fake)
    res = await client.post("/api/metrics/gpu/power-limit", json={"index": 0})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is False
    assert data["error"]
    assert fake.last_set is None  # no tocó NVML


async def test_power_limit_set_error(client, monkeypatch):
    fake = _FakePynvml(fail_set="NVML_ERROR_NO_PERMISSION")
    _mock_nvml(monkeypatch, fake)
    res = await client.post("/api/metrics/gpu/power-limit", json={"index": 0, "watts": 250.0})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is False
    assert "NO_PERMISSION" in data["error"]


async def test_power_limit_sin_nvidia(client, monkeypatch):
    monkeypatch.setattr(metrics_module, "PYNVML_AVAILABLE", False)
    res = await client.get("/api/metrics/gpu/power-limit")
    assert res.status_code == 200
    data = res.json()
    assert data["available"] is False
    assert data["gpus"] is None
    assert data["error"] is not None


# --------------------------------------------------------------------------
# Ventilador: fan_speed_pct en el snapshot, POST /api/metrics/gpu/fan
# --------------------------------------------------------------------------


async def test_fan_speed_en_snapshot(client, monkeypatch):
    _mock_nvml(monkeypatch, _FakePynvml(fan_speed=55))
    res = await client.get("/api/metrics/gpu")
    assert res.status_code == 200
    data = res.json()
    assert data["gpu"][0]["fan_speed_pct"] == 55


async def test_fan_set_ok(client, monkeypatch):
    fake = _FakePynvml()
    _mock_nvml(monkeypatch, fake)
    _mock_nvapi(monkeypatch, _FakeNvApi())
    res = await client.post("/api/metrics/gpu/fan", json={"index": 0, "percent": 70})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["error"] is None
    assert data["percent"] == 70
    assert data["privilege_hint"] in ("admin", "root")
    assert fake.last_fan_set == 70
    assert fake.fan_speed == 70  # el read-back confirma el cambio real


async def test_fan_set_silent_noop(client, monkeypatch):
    fake = _FakePynvml(ignore_fan_set=True)
    _mock_nvml(monkeypatch, fake)
    _mock_nvapi(monkeypatch, _FakeNvApi())
    res = await client.post("/api/metrics/gpu/fan", json={"index": 0, "percent": 70})
    data = res.json()
    assert data["ok"] is False
    assert data["error"] == "fan_no_change"
    assert fake.fan_speed == 42  # el valor real no cambió


async def test_fan_set_error(client, monkeypatch):
    fake = _FakePynvml(fail_fan_set="NVML_ERROR_NOT_SUPPORTED")
    _mock_nvml(monkeypatch, fake)
    _mock_nvapi(monkeypatch, _FakeNvApi())
    res = await client.post("/api/metrics/gpu/fan", json={"index": 0, "percent": 70})
    data = res.json()
    assert data["ok"] is False
    # El backend normaliza el mensaje crudo de NVML a una clave estable.
    assert data["error"] == "fan_not_supported"
    assert fake.last_fan_set is None


async def test_fan_set_no_permission(client, monkeypatch):
    fake = _FakePynvml(fail_fan_set="NVML_ERROR_NO_PERMISSION")
    _mock_nvml(monkeypatch, fake)
    _mock_nvapi(monkeypatch, _FakeNvApi())
    res = await client.post("/api/metrics/gpu/fan", json={"index": 0, "percent": 70})
    data = res.json()
    assert data["ok"] is False
    assert data["error"] == "fan_no_privilege"
    assert data["privilege_hint"] in ("admin", "root")


async def test_fan_set_percent_fuera_de_rango(client, monkeypatch):
    fake = _FakePynvml()
    _mock_nvml(monkeypatch, fake)
    _mock_nvapi(monkeypatch, _FakeNvApi())
    res = await client.post("/api/metrics/gpu/fan", json={"index": 0, "percent": 101})
    assert res.status_code == 422
    assert fake.last_fan_set is None


# --------------------------------------------------------------------------
# Ventilador vía NvAPI Direct (Windows): el adapter reemplaza a NVML, que en
# GeForce rechaza la escritura con NOT_SUPPORTED.
# --------------------------------------------------------------------------


async def test_fan_set_windows_nvapi_ok(client, monkeypatch):
    fake = _FakePynvml()
    _mock_nvml(monkeypatch, fake)
    nv = _FakeNvApi(available=True, set_result=(True, None))
    _mock_nvapi(monkeypatch, nv)
    res = await client.post("/api/metrics/gpu/fan", json={"index": 1, "percent": 65})
    data = res.json()
    assert data["ok"] is True
    assert data["error"] is None
    assert nv.calls == [(1, 65, 2, 1.0)]
    assert fake.last_fan_set is None  # la vía Windows no toca NVML


async def test_fan_set_windows_nvapi_sin_admin(client, monkeypatch):
    _mock_nvml(monkeypatch, _FakePynvml())
    nv = _FakeNvApi(available=True, set_result=(False, "fan_no_privilege"))
    _mock_nvapi(monkeypatch, nv)
    res = await client.post("/api/metrics/gpu/fan", json={"index": 0, "percent": 50})
    data = res.json()
    assert data["ok"] is False
    assert data["error"] == "fan_no_privilege"
    # El hint permite que el frontend pida admin/root sin conocer el SO.
    assert data["privilege_hint"] in ("admin", "root")


async def test_fan_set_windows_nvapi_no_cambio(client, monkeypatch):
    _mock_nvml(monkeypatch, _FakePynvml())
    nv = _FakeNvApi(available=True, set_result=(False, "fan_no_change"))
    _mock_nvapi(monkeypatch, nv)
    res = await client.post("/api/metrics/gpu/fan", json={"index": 0, "percent": 50})
    data = res.json()
    assert data["ok"] is False
    assert data["error"] == "fan_no_change"


async def test_fan_set_windows_nvapi_no_soportado(client, monkeypatch):
    fake = _FakePynvml()
    _mock_nvml(monkeypatch, fake)
    nv = _FakeNvApi(available=True, set_result=(False, "fan_not_supported"))
    _mock_nvapi(monkeypatch, nv)
    res = await client.post("/api/metrics/gpu/fan", json={"index": 0, "percent": 50})
    data = res.json()
    assert data["ok"] is False
    assert data["error"] == "fan_not_supported"
    assert fake.last_fan_set is None  # sin fallback a NVML: también fallaría


# --------------------------------------------------------------------------
# MON-3: fan automático, baseline+restore y curvas de temperatura. La vía
# NvAPI es la única con baseline/restore (NVML no tiene forma documentada de
# volver a "auto", verificado contra pynvml).
# --------------------------------------------------------------------------


def _isolated_config(monkeypatch, tmp_path) -> Config:
    """Punta el config del módulo metrics a un tmp: las curvas se persisten
    en config.json sin tocar el data/ real."""
    cfg = Config(tmp_path / "config.json")
    monkeypatch.setattr(metrics_module, "config", cfg)
    return cfg


def _reset_fan_state():
    """El collector del manager es singleton: limpiar su estado de fan entre
    tests para que no arrastren baselines o targets de la prueba anterior."""
    collector = metrics_module.manager.collector
    collector._fan_baselines.clear()
    collector._fan_curve_state.clear()


def _fake_gpu_snap(temp_c: int | None, index: int = 0):
    return metrics_module.MetricsSnapshot(
        timestamp="2026-10-16T12:00:00",
        gpu=[metrics_module.GpuMetrics(
            index=index, name="Fake GPU", vram_used_mb=1, vram_total_mb=2,
            vram_free_mb=1, vram_percent=50, temperature_c=temp_c,
            fan_speed_pct=30,
        )],
    )


async def test_fan_auto_nvapi_ok(client, monkeypatch):
    _mock_nvml(monkeypatch, _FakePynvml())
    nv = _FakeNvApi(available=True, set_result=(True, None))
    _mock_nvapi(monkeypatch, nv)
    _reset_fan_state()
    res = await client.post("/api/metrics/gpu/fan/auto", json={"index": 0})
    data = res.json()
    assert data["ok"] is True and data["error"] is None
    assert nv.auto_calls == [0]


async def test_fan_auto_sin_nvapi(client, monkeypatch):
    _mock_nvml(monkeypatch, _FakePynvml())
    _mock_nvapi(monkeypatch, _FakeNvApi(available=False))
    _reset_fan_state()
    res = await client.post("/api/metrics/gpu/fan/auto", json={"index": 0})
    data = res.json()
    assert data["ok"] is False
    assert data["error"] == "fan_auto_unsupported"


async def test_fan_baseline_se_captura_y_restore_devuelve(client, monkeypatch):
    _mock_nvml(monkeypatch, _FakePynvml())
    nv = _FakeNvApi(available=True, set_result=(True, None), control=[(0, 30)])
    _mock_nvapi(monkeypatch, nv)
    _reset_fan_state()

    # Sin baseline capturado: restore responde vacío y no escribe nada.
    res = await client.post("/api/metrics/gpu/fan/restore-all")
    assert res.json() == {"ok": True, "restored": {}}
    assert nv.calls == [] and nv.restored == []

    # Fijar el fan captura lo que había antes de la escritura (auto, 30).
    res = await client.post("/api/metrics/gpu/fan", json={"index": 0, "percent": 80})
    assert res.json()["ok"] is True
    assert nv.control == [(1, 80)]

    # Restore: reescribe exactamente lo capturado y lo consume.
    res = await client.post("/api/metrics/gpu/fan/restore-all")
    assert res.json()["restored"]["0"]["ok"] is True
    assert nv.restored == [(0, [(0, 30)])]

    # Idempotente: una segunda llamada ya no tiene nada que restaurar.
    res = await client.post("/api/metrics/gpu/fan/restore-all")
    assert res.json() == {"ok": True, "restored": {}}


async def test_fan_auto_explicit_descarta_el_baseline(client, monkeypatch):
    _mock_nvml(monkeypatch, _FakePynvml())
    nv = _FakeNvApi(available=True, set_result=(True, None), control=[(0, 30)])
    _mock_nvapi(monkeypatch, nv)
    _reset_fan_state()
    await client.post("/api/metrics/gpu/fan", json={"index": 0, "percent": 80})
    await client.post("/api/metrics/gpu/fan/auto", json={"index": 0})
    # El usuario pidió automático explícitamente: eso es lo que debe quedar
    # al cerrar, no el estado que había antes de tocarlo.
    res = await client.post("/api/metrics/gpu/fan/restore-all")
    assert res.json() == {"ok": True, "restored": {}}


async def test_fan_curve_put_get_y_persistencia(client, monkeypatch, tmp_path):
    _mock_nvapi(monkeypatch, _FakeNvApi(available=True, set_result=(True, None)))
    cfg = _isolated_config(monkeypatch, tmp_path)
    _reset_fan_state()

    res = await client.put("/api/metrics/gpu/fan-curve", json={
        "index": 0, "enabled": True,
        "points": [{"temp_c": 45, "pct": 30}, {"temp_c": 70, "pct": 80}],
    })
    data = res.json()
    assert data["ok"] is True

    res = await client.get("/api/metrics/gpu/fan-curve", params={"index": 0})
    data = res.json()
    assert data["enabled"] is True
    assert data["points"] == [{"temp_c": 45, "pct": 30}, {"temp_c": 70, "pct": 80}]
    assert data["auto_available"] is True

    # Queda en config.json en disco: el cierre de la app suele ser un kill
    # duro y el save del lifespan no llegaría a correr.
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved["monitor"]["fan_curves"]["0"]["enabled"] is True

    # El PUT con curvas activas prende el motor del manager.
    assert metrics_module.manager._fan_curves_active is True

    # Cleanup: este test levantó el broadcast loop solo por la curva. Se
    # cancela aquí para que el loop del test cierre sin tareas colgando.
    metrics_module.manager._fan_curves_active = False
    task = metrics_module.manager._broadcast_task
    if task is not None and not task.done():
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    metrics_module.manager._broadcast_task = None


async def test_fan_curve_invalidas(client, monkeypatch, tmp_path):
    _mock_nvapi(monkeypatch, _FakeNvApi(available=True, set_result=(True, None)))
    _isolated_config(monkeypatch, tmp_path)
    _reset_fan_state()

    res = await client.put("/api/metrics/gpu/fan-curve", json={
        "index": 0, "enabled": True,
        "points": [{"temp_c": 70, "pct": 80}, {"temp_c": 45, "pct": 30}],
    })
    assert res.json()["error"] == "fan_curve_points_order"

    res = await client.put("/api/metrics/gpu/fan-curve", json={
        "index": 0, "enabled": True, "points": [],
    })
    assert res.json()["error"] == "fan_curve_points_count"

    # Pydantic corta valores fuera de rango antes que la lógica de curva.
    res = await client.put("/api/metrics/gpu/fan-curve", json={
        "index": 0, "enabled": True, "points": [{"temp_c": 45, "pct": 130}],
    })
    assert res.status_code == 422


async def test_fan_curve_sin_nvapi_no_se_habilita(client, monkeypatch, tmp_path):
    # Sin NvAPI no hay garantía de devolver el fan a auto al cerrar: la
    # curva queda bloqueada en el endpoint, no solo oculta en la UI.
    _mock_nvapi(monkeypatch, _FakeNvApi(available=False))
    cfg = _isolated_config(monkeypatch, tmp_path)
    _reset_fan_state()
    res = await client.put("/api/metrics/gpu/fan-curve", json={
        "index": 0, "enabled": True,
        "points": [{"temp_c": 45, "pct": 30}],
    })
    data = res.json()
    assert data["ok"] is False
    assert data["error"] == "fan_auto_unsupported"
    res = await client.get("/api/metrics/gpu/fan-curve", params={"index": 0})
    assert res.json()["auto_available"] is False
    assert cfg.get("monitor.fan_curves", {}) == {}


def test_apply_fan_curves_interpola_y_respeta_delta(monkeypatch, tmp_path):
    cfg = _isolated_config(monkeypatch, tmp_path)
    cfg.set("monitor.fan_curves", {"0": {"enabled": True, "points": [
        {"temp_c": 45, "pct": 30}, {"temp_c": 70, "pct": 80}]}})
    _reset_fan_state()
    collector = metrics_module.manager.collector
    nv = _FakeNvApi(available=True, set_result=(True, None), control=[(0, 30)])
    monkeypatch.setattr(collector, "_nvapi_fan", nv)

    # Bajo el primer punto: extremo 30%, y captura el baseline antes de escribir.
    res = collector.apply_fan_curves(_fake_gpu_snap(40))
    assert res["applied"] == [0]
    assert nv.calls[-1] == (0, 30, 2, 0.0)  # readback_s=0: sin sleep en el ciclo
    assert collector._fan_baselines[0] == [(0, 30)]

    # Target casi igual al aplicado (30 vs 33): no reescribe (anti-oscilación).
    res = collector.apply_fan_curves(_fake_gpu_snap(41))
    assert res["applied"] == [] and len(nv.calls) == 1

    # 58°C → interpolado 56%: delta >= 5, aplica.
    res = collector.apply_fan_curves(_fake_gpu_snap(58))
    assert res["applied"] == [0]
    assert nv.calls[-1][1] == 56

    # Sin curva habilitada para la GPU: no toca nada.
    cfg.set("monitor.fan_curves", {})
    res = collector.apply_fan_curves(_fake_gpu_snap(90))
    assert res["applied"] == [] and len(nv.calls) == 2

    # GPU sin temperatura leída: ciclo saltado (no se puede decidir).
    cfg.set("monitor.fan_curves", {"0": {"enabled": True, "points": [
        {"temp_c": 45, "pct": 30}]}})
    res = collector.apply_fan_curves(_fake_gpu_snap(None))
    assert res["applied"] == [] and len(nv.calls) == 2


def test_apply_fan_curves_autodisable_tras_fallos(monkeypatch, tmp_path):
    cfg = _isolated_config(monkeypatch, tmp_path)
    cfg.set("monitor.fan_curves", {"0": {"enabled": True, "points": [
        {"temp_c": 45, "pct": 30}]}})
    _reset_fan_state()
    collector = metrics_module.manager.collector
    # Escritura siempre rechazada (p. ej. app sin admin): reintenta cada
    # ciclo y se auto-deshabilita tras el máximo, pidiendo persistir la config.
    nv = _FakeNvApi(available=True, set_result=(False, "fan_no_privilege"))
    monkeypatch.setattr(collector, "_nvapi_fan", nv)

    for _ in range(3):
        res = collector.apply_fan_curves(_fake_gpu_snap(60))
    assert res["disabled"] == [0]
    assert cfg.get("monitor.fan_curves")["0"]["enabled"] is False
    assert len(nv.calls) == 3

    # Deshabilitada: ya no intenta escribir.
    res = collector.apply_fan_curves(_fake_gpu_snap(60))
    assert res == {"applied": [], "disabled": []} and len(nv.calls) == 3


# --------------------------------------------------------------------------
# D6: NVML se cierra explícitamente (lifespan → manager.stop), no por __del__
# --------------------------------------------------------------------------


async def test_manager_stop_cierra_nvml(tmp_path, monkeypatch):
    fake = _FakePynvml()
    manager = metrics_module.MetricsManager()
    manager.collector._nvml_ready = True
    monkeypatch.setattr(metrics_module, "pynvml", fake)

    await manager.start(tmp_path / "d6.db")
    await manager.stop()

    assert fake.shutdowns == 1
    assert manager.collector._nvml_ready is False

    # Idempotente: un segundo stop no vuelve a llamar a nvmlShutdown.
    await manager.stop()
    assert fake.shutdowns == 1
