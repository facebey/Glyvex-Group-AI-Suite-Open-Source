"""Tests de lhm.py — parser de data.json y ciclo de vida del sidecar (MON-6).

Todo simulado: fixtures JSON con la forma real de data.json (HttpServer.cs),
procs fake, sin red ni GPU (convención de la suite).
"""

import hashlib
import zipfile

import pytest

import lhm as lhm_module


# ---------------------------------------------------------------------------
# Fixture builder: data.json con la estructura verificada de HttpServer.cs
# ---------------------------------------------------------------------------


def _leaf(sensor_id: str, kind: str, index: int, raw):
    return {
        "id": 900,
        "Text": "GPU Core",
        "Min": "",
        "Value": "valor",
        "Max": "",
        "SensorId": f"{sensor_id}/{kind}/{index}",
        "Type": kind.capitalize(),
        "RawMin": 0.0,
        "RawValue": raw,
        "RawMax": 0.0,
    }


def _group(text: str, leaves: list):
    return {"id": 500, "Text": text, "Min": "", "Value": "", "Max": "", "Children": leaves}


def _gpu_hw(hw_id: str, name: str) -> dict:
    """GPU AMD con el layout completo verificado en AmdGpu.cs 0.9.6."""
    core = [
        _leaf(hw_id, "temperature", 0, 52.0),
        _leaf(hw_id, "temperature", 1, 60.0),      # GPU Memory temp: se ignora
        _leaf(hw_id, "load", 0, 37.0),
        _leaf(hw_id, "clock", 0, 2500.0),
        _leaf(hw_id, "clock", 1, 1100.0),          # GPU SoC clock: se ignora
        _leaf(hw_id, "control", 0, 45.0),
        _leaf(hw_id, "power", 0, 71.5),
        _leaf(hw_id, "power", 3, 98.7),            # package: tiene prioridad
        _leaf(hw_id, "throughput", 0, 1234.0),     # tipo fuera del mapping
    ]
    memory = [
        _leaf(hw_id, "load", 1, 12.0),
        _leaf(hw_id, "clock", 2, 2000.0),
        _leaf(hw_id, "data", 0, 3.2),              # GB used
        _leaf(hw_id, "data", 1, 4.1),              # GB free
        _leaf(hw_id, "data", 2, 8.0),              # GB total
    ]
    return {
        "id": 400,
        "Text": name,
        "HardwareId": hw_id,
        "Min": "", "Value": "", "Max": "",
        "ImageURL": "images_icon/gpu.png",
        "Children": [_group("GPU Core", core), _group("GPU Memory", memory)],
    }


def _data_json(hardware_children: list) -> dict:
    return {
        "id": 1,
        "Version": "0.9.6",
        "Text": "Sensor",
        "Min": "Min", "Value": "Value", "Max": "Max",
        "ImageURL": "",
        "Children": [{
            "id": 2, "Text": "equipo", "Min": "", "Value": "", "Max": "",
            "ImageURL": "images_icon/computer.png",
            "Children": hardware_children,
        }],
    }


def test_parse_amd_gpu_full_mapping():
    payload = _data_json([_gpu_hw("/gpu-amd/0", "AMD Radeon RX 7600")])
    gpus = lhm_module.parse_gpu_data_json(payload)

    assert len(gpus) == 1
    gpu = gpus[0]
    assert gpu["index"] == 0
    assert gpu["name"] == "AMD Radeon RX 7600"
    assert gpu["temperature_c"] == 52
    assert gpu["gpu_utilization"] == 37
    assert gpu["memory_utilization"] == 12
    assert gpu["clock_graphics_mhz"] == 2500
    assert gpu["clock_memory_mhz"] == 2000
    assert gpu["fan_speed_pct"] == 45
    assert gpu["power_draw_w"] == 98.7  # package (power/3) sobre core (power/0)
    assert gpu["power_limit_w"] is None
    # GB -> MB con las conversiones del Monitor
    assert gpu["vram_used_mb"] == pytest.approx(3276.8)
    assert gpu["vram_free_mb"] == pytest.approx(4198.4)
    assert gpu["vram_total_mb"] == pytest.approx(8192.0)
    assert gpu["vram_percent"] == pytest.approx(40.0)


def test_parse_ignores_non_amd_and_unknown_hardware():
    mainboard = {
        "id": 401, "Text": "Motherboard", "HardwareId": "/mainboard/0",
        "Min": "", "Value": "", "Max": "",
        "Children": [_group("CPU", [_leaf("/mainboard/0", "temperature", 0, 44.0)])],
    }
    payload = _data_json([mainboard])
    assert lhm_module.parse_gpu_data_json(payload) == []


def test_parse_skips_nan_and_falls_back_to_power_core():
    hw = _gpu_hw("/gpu-amd/0", "AMD Radeon RX 7600")
    # Romper power/3 con "NaN" (permitido por AllowNamedFloatingPointLiterals)
    # y los datos de VRAM: power debe caer a power/0 y el VRAM a 0/None.
    for group in hw["Children"]:
        for leaf in group["Children"]:
            if leaf["SensorId"].endswith("/power/3") or "/data/" in leaf["SensorId"]:
                leaf["RawValue"] = "NaN"
    payload = _data_json([hw])
    gpu = lhm_module.parse_gpu_data_json(payload)[0]
    assert gpu["power_draw_w"] == pytest.approx(71.5)
    assert gpu["vram_used_mb"] == 0.0
    assert gpu["vram_total_mb"] == 0.0
    assert gpu["vram_percent"] == 0.0
    assert gpu["temperature_c"] == 52  # el resto de sensores sigue vivo


def test_parse_partial_sensors_yield_none_fields():
    hw = {
        "id": 400, "Text": "AMD Radeon RX 6600", "HardwareId": "/gpu-amd/0",
        "Min": "", "Value": "", "Max": "",
        "Children": [_group("GPU Core", [_leaf("/gpu-amd/0", "load", 0, 15.4)])],
    }
    gpu = lhm_module.parse_gpu_data_json(_data_json([hw]))[0]
    assert gpu["gpu_utilization"] == 15  # round, como hace el camino NVML
    assert gpu["temperature_c"] is None
    assert gpu["fan_speed_pct"] is None
    assert gpu["power_draw_w"] is None
    assert gpu["vram_used_mb"] == 0.0
    assert gpu["vram_free_mb"] == 0.0


def test_parse_multiple_adapters_sorted_by_index():
    hw1 = _gpu_hw("/gpu-amd/1", "AMD Radeon RX 6600")
    hw0 = _gpu_hw("/gpu-amd/0", "AMD Radeon RX 7600")
    payload = _data_json([hw1, hw0])
    gpus = lhm_module.parse_gpu_data_json(payload)
    assert [g["index"] for g in gpus] == [0, 1]
    assert gpus[0]["name"] == "AMD Radeon RX 7600"


def test_parse_drops_amd_hardware_without_any_usable_sensor():
    hw = {
        "id": 400, "Text": "AMD Radeon Graphics", "HardwareId": "/gpu-amd/0",
        "Min": "", "Value": "", "Max": "",
        "Children": [_group("GPU Core", [_leaf("/gpu-amd/0", "throughput", 0, 10.0)])],
    }
    assert lhm_module.parse_gpu_data_json(_data_json([hw])) == []


def test_parse_empty_payload():
    assert lhm_module.parse_gpu_data_json(_data_json([])) == []


# ---------------------------------------------------------------------------
# Resolución del recurso (extracción del bundle, sha256, marker de versión)
# ---------------------------------------------------------------------------


def _isolate(tmp_path, monkeypatch):
    # Mismo patrón que conftest: redirigir las constantes module-level del
    # consumidor, no paths.py.
    monkeypatch.setattr(lhm_module, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(lhm_module, "BUNDLE_DIR", tmp_path / "bundle")
    monkeypatch.delenv(lhm_module.ENV_LHM_DIR, raising=False)


def _make_fake_zip(bundle_dir):
    zip_path = bundle_dir / lhm_module.LHM_ZIP_RELPATH
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(lhm_module.LHM_EXE, "MZ fake exe")
        zf.writestr("LibreHardwareMonitorLib.dll", "dll")
    return zip_path


def _pin_zip_hash(monkeypatch, zip_path):
    monkeypatch.setattr(
        lhm_module, "LHM_ZIP_SHA256", hashlib.sha256(zip_path.read_bytes()).hexdigest()
    )


def test_resolve_extracts_bundled_zip(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _pin_zip_hash(monkeypatch, _make_fake_zip(tmp_path / "bundle"))

    resolved, error = lhm_module.resolve_lhm_dir()
    assert error is None
    tool_dir = tmp_path / "data" / "tools" / "librehardwaremonitor"
    assert resolved == tool_dir
    assert (tool_dir / lhm_module.LHM_EXE).is_file()
    assert (tool_dir / lhm_module._VERSION_MARKER).read_text(encoding="utf-8") == lhm_module.LHM_VERSION
    assert lhm_module.resource_available() is True


def test_resolve_reuses_existing_extraction(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _pin_zip_hash(monkeypatch, _make_fake_zip(tmp_path / "bundle"))
    resolved, _ = lhm_module.resolve_lhm_dir()
    sentinel = resolved / "sentinel.txt"
    sentinel.write_text("x", encoding="utf-8")

    resolved2, error2 = lhm_module.resolve_lhm_dir()
    # Si re-extrajera, el marker y el rmtree previos borrarían el sentinel.
    assert resolved2 == resolved and sentinel.is_file()


def test_resolve_rejects_tampered_zip(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    zip_path = _make_fake_zip(tmp_path / "bundle")
    _pin_zip_hash(monkeypatch, zip_path)
    zip_path.write_bytes(b"tampered")  # la firma fija ya no coincide

    resolved, error = lhm_module.resolve_lhm_dir()
    assert resolved is None and "firma" in error
    assert not (tmp_path / "data" / "tools" / "librehardwaremonitor" / lhm_module.LHM_EXE).exists()


def test_resolve_reextracts_on_stale_version_marker(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _pin_zip_hash(monkeypatch, _make_fake_zip(tmp_path / "bundle"))
    resolved, _ = lhm_module.resolve_lhm_dir()
    (resolved / lhm_module._VERSION_MARKER).write_text("0.0.1", encoding="utf-8")
    (resolved / lhm_module.LHM_EXE).unlink()  # extracción de versión vieja

    resolved2, error2 = lhm_module.resolve_lhm_dir()
    assert error2 is None
    assert resolved2 / lhm_module.LHM_EXE
    assert resolved2 == tmp_path / "data" / "tools" / "librehardwaremonitor"
    marker = resolved2 / lhm_module._VERSION_MARKER
    assert marker.read_text(encoding="utf-8") == lhm_module.LHM_VERSION


def test_resolve_nested_zip_layout(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    zip_path = tmp_path / "bundle" / lhm_module.LHM_ZIP_RELPATH
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(f"LibreHardwareMonitor/{lhm_module.LHM_EXE}", "MZ")
    _pin_zip_hash(monkeypatch, zip_path)

    resolved, error = lhm_module.resolve_lhm_dir()
    assert error is None
    # El exe anidado: resolvemos el directorio REAL del exe, no la raíz.
    assert resolved == (
        tmp_path / "data" / "tools" / "librehardwaremonitor" / "LibreHardwareMonitor"
    )


def test_resolve_env_override_skips_extraction(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    lhm_dir = tmp_path / "lhm-dev"
    lhm_dir.mkdir()
    (lhm_dir / lhm_module.LHM_EXE).write_text("MZ", encoding="utf-8")
    monkeypatch.setenv(lhm_module.ENV_LHM_DIR, str(lhm_dir))

    resolved, error = lhm_module.resolve_lhm_dir()
    assert error is None and resolved == lhm_dir
    assert not (tmp_path / "data" / "tools").exists()  # jamás extrajo nada


def test_resolve_env_override_bad_dir(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv(lhm_module.ENV_LHM_DIR, str(tmp_path / "no-existe"))
    resolved, error = lhm_module.resolve_lhm_dir()
    assert resolved is None and "GLYVEX_LHM_DIR" in error


def test_resolve_no_resource_available(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert lhm_module.resource_available() is False
    resolved, error = lhm_module.resolve_lhm_dir()
    assert resolved is None and "empaquetado" in error


def test_sidecar_config_xml(tmp_path):
    lhm_module.write_sidecar_config(tmp_path)
    text = (tmp_path / f"{lhm_module.LHM_EXE}.config").read_text(encoding="utf-8")
    # Booleans como "true"/"false" exactos: PersistentSettings.GetValue compara
    # str == "true". Y puerto dedicado, sin auth, minimized.
    assert '<add key="runWebServerMenuItem" value="true" />' in text
    assert f'<add key="listenerPort" value="{lhm_module.LHM_PORT}" />' in text
    assert '<add key="authenticationEnabled" value="false" />' in text
    assert '<add key="startMinMenuItem" value="true" />' in text
    assert '<add key="gpuMenuItem" value="true" />' in text
    assert '<add key="cpuMenuItem" value="false" />' in text
    assert '<add key="mainboardMenuItem" value="false" />' in text


# ---------------------------------------------------------------------------
# Ciclo de vida del sidecar (procs y HTTP fake; nunca procesos reales)
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(self):
        self.alive = True
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.terminated = True
        self.alive = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


@pytest.fixture
def sidecar(tmp_path, monkeypatch):
    """Sidecar con recurso garantizado por env override y polls de health sin
    sleep real."""
    _isolate(tmp_path, monkeypatch)
    lhm_dir = tmp_path / "lhm"
    lhm_dir.mkdir()
    (lhm_dir / lhm_module.LHM_EXE).write_text("MZ", encoding="utf-8")
    monkeypatch.setenv(lhm_module.ENV_LHM_DIR, str(lhm_dir))
    monkeypatch.setattr(lhm_module, "_HEALTH_POLL_INTERVAL_S", 0.0)
    return lhm_module.LhmSidecar(), lhm_dir


def _instrument(sidecar, payloads):
    """Reemplaza _probe/_spawn en la instancia: payloads = secuencia de
    data.json (None = sin respuesta). Devuelve contadores y los procs creados."""
    counters = {"spawn": 0, "probe": 0}
    sequence = list(payloads)
    spawned = []

    def fake_probe():
        counters["probe"] += 1
        return sequence.pop(0) if sequence else None

    def fake_spawn():
        counters["spawn"] += 1
        proc = _FakeProc()
        spawned.append(proc)
        return proc

    sidecar._probe = fake_probe
    sidecar._spawn = fake_spawn
    return counters, spawned


def test_collect_adopts_existing_server_without_spawn(sidecar):
    sidecar_obj, _ = sidecar
    payload = _data_json([_gpu_hw("/gpu-amd/0", "AMD Radeon RX 7600")])
    counters, spawned = _instrument(sidecar_obj, [payload])

    gpus, error = sidecar_obj.collect()
    assert error is None
    assert gpus[0]["name"] == "AMD Radeon RX 7600"
    # Ya había un data.json sirviendo: nada de spawnear un segundo LHM.
    assert counters["spawn"] == 0 and spawned == []
    sidecar_obj.shutdown()
    assert sidecar_obj._proc is None  # lo adoptado no se termina


def test_spawn_writes_config_then_health_poll(sidecar):
    sidecar_obj, lhm_dir = sidecar
    payload = _data_json([_gpu_hw("/gpu-amd/0", "AMD Radeon RX 7600")])
    counters, spawned = _instrument(sidecar_obj, [None, payload])

    gpus, error = sidecar_obj.collect()
    assert error is None and len(gpus) == 1
    assert counters["spawn"] == 1
    # El .config se escribió ANTES del spawn (LHM lo lee al arrancar).
    config = (lhm_dir / f"{lhm_module.LHM_EXE}.config").read_text(encoding="utf-8")
    assert f'<add key="listenerPort" value="{lhm_module.LHM_PORT}" />' in config


def test_spawn_failure_terminates_child_and_sets_cooldown(sidecar):
    sidecar_obj, _ = sidecar
    counters, spawned = _instrument(sidecar_obj, [None])  # nunca responde

    gpus, error = sidecar_obj.collect()
    assert gpus == [] and error is not None
    assert spawned[0].terminated  # no se deja un proceso muerto-colgando
    assert sidecar_obj._proc is None

    gpus2, error2 = sidecar_obj.collect()
    assert gpus2 == [] and error2 is not None
    assert counters["spawn"] == 1  # cooldown: no re-spawnea en cada snapshot


def test_alive_sidecar_without_response_does_not_respawn(sidecar):
    sidecar_obj, _ = sidecar
    payload = _data_json([_gpu_hw("/gpu-amd/0", "AMD Radeon RX 7600")])
    counters, _ = _instrument(sidecar_obj, [None, payload, None])

    gpus, error = sidecar_obj.collect()  # spawn + health OK
    assert error is None

    gpus2, error2 = sidecar_obj.collect()  # probe falla pero el proc está vivo
    assert gpus2 == [] and error2 is not None
    assert counters["spawn"] == 1  # no spawna un duplicado


def test_collect_without_amd_hardware_reports_error(sidecar):
    sidecar_obj, _ = sidecar
    _instrument(sidecar_obj, [_data_json([])])
    gpus, error = sidecar_obj.collect()
    assert gpus == [] and "AMD" in error


def test_shutdown_terminates_own_child(sidecar):
    sidecar_obj, _ = sidecar
    payload = _data_json([_gpu_hw("/gpu-amd/0", "AMD Radeon RX 7600")])
    _, spawned = _instrument(sidecar_obj, [None, payload])
    assert sidecar_obj.collect()[1] is None

    sidecar_obj.shutdown()
    assert spawned[0].terminated is True
    assert sidecar_obj._proc is None
