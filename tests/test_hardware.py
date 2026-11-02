"""
test_hardware.py — A2 (M6): sondas de hardware_detection.py.

Todo mockeado (convención del repo): psutil/pynvml fake, registro de video
controllers fake, sidecar LHM fake, /proc/cpuinfo fake en tmp_path, env
PROCESSOR_IDENTIFIER fake. Ningún test depende del hardware real de la máquina.
El cache TTL del módulo se limpia en el fixture autouse: la foto cacheada es
estado global y sobreviviría entre tests.
"""

from __future__ import annotations

import json
from collections import namedtuple

import pytest

import hardware_detection as hw


@pytest.fixture(autouse=True)
def _clean_hw_state():
    # La foto cacheada (_cache) y el sidecar LHM perezoso son estado de
    # módulo: sin esto, un test heredaria la foto del anterior.
    hw._cache = None
    hw._lhm_sidecar = None
    yield
    hw._cache = None
    hw._lhm_sidecar = None


# --------------------------------------------------------------------------
# Fakes de mundo real
# --------------------------------------------------------------------------


class _FakePsutil:
    """psutil de mentira con números fijos: los tests no dependen de la CPU
    ni de la RAM de la máquina que los corre."""

    def __init__(self, physical=8, logical=16, ram_total=32 * 1024 ** 3,
                 ram_available=24 * 1024 ** 3, swap=8 * 1024 ** 3, fail=False):
        self._values = (physical, logical)
        self.ram_total = ram_total
        self.ram_available = ram_available
        self.swap = swap
        self.fail = fail

    def cpu_count(self, logical=True):
        if self.fail:
            raise RuntimeError("cpu_count")
        return self._values[1] if logical else self._values[0]

    def cpu_freq(self):
        if self.fail:
            raise RuntimeError("cpu_freq")

        class _F:
            max = 4200.0
            current = 3100.0
            min = 800.0

        return _F()

    def virtual_memory(self):
        if self.fail:
            raise RuntimeError("virtual_memory")

        class _VM:
            total = self.ram_total
            available = self.ram_available

        return _VM()

    def swap_memory(self):
        class _SW:
            total = self.swap

        return _SW()


_MemInfo = namedtuple("_MemInfo", "total used free")


class _FakePynvml:
    """pynvml de mentira: N GPUs configurables, con nombres y VRAM fijos."""

    def __init__(self, gpus=None, fail_init=False):
        self.gpus = gpus if gpus is not None else [
            ("NVIDIA GeForce RTX 4090", 24 * 1024 ** 3, 2 * 1024 ** 3, (12, 0)),
        ]
        self.fail_init = fail_init
        self.inits = 0

    def nvmlInit(self):
        self.inits += 1
        if self.fail_init:
            raise RuntimeError("nvmlInit")

    def nvmlDeviceGetCount(self):
        return len(self.gpus)

    def nvmlDeviceGetHandleByIndex(self, index):
        return f"handle-{index}"

    def nvmlDeviceGetName(self, handle):
        return self.gpus[int(handle.split("-")[-1])][0].encode("utf-8")

    def nvmlDeviceGetMemoryInfo(self, handle):
        _, total, used, _ = self.gpus[int(handle.split("-")[-1])]
        return _MemInfo(total=total, used=used, free=total - used)

    def nvmlDeviceGetCudaComputeCapability(self, handle):
        return self.gpus[int(handle.split("-")[-1])][3]


class _FakeLhmModule:
    """Reemplaza el módulo lhm importado por hardware_detection: decide si el
    recurso existe y qué reporta el sidecar, sin tocar el LHM real."""

    def __init__(self, entries=None, available=True, boom=False):
        self.available = available
        self.entries = entries if entries is not None else [
            {"index": 0, "name": "AMD Radeon RX 7600", "vram_total_mb": 8192.0,
             "vram_used_mb": 1024.0},
        ]
        self.boom = boom
        self.sidecar_calls = 0

    def resource_available(self):
        return self.available

    def LhmSidecar(self):
        outer = self

        class _Sidecar:
            def collect(self_inner):
                outer.sidecar_calls += 1
                if outer.boom:
                    raise RuntimeError("sidecar caído")
                return list(outer.entries), None

        return _Sidecar()


def _force_windows(monkeypatch):
    monkeypatch.setattr(hw.platform, "system", lambda: "Windows")


def _force_linux(monkeypatch):
    monkeypatch.setattr(hw.platform, "system", lambda: "Linux")


def _mock_probes(monkeypatch, *, cpu=None, ram=None, gpus=None, gpu_reason=None):
    """Fija las sondas que un test no quiere ejercitar, dejando viva la que
    interesa. Devuelve un snapshot base predecible para los tests de composición."""
    monkeypatch.setattr(hw, "_probe_cpu", lambda: (cpu, None if cpu else "cpu"))
    monkeypatch.setattr(hw, "_probe_ram", lambda: (ram, None if ram else "ram"))
    monkeypatch.setattr(hw, "_probe_gpus", lambda: (gpus or [], gpu_reason))


# --------------------------------------------------------------------------
# CPU
# --------------------------------------------------------------------------


def test_cpu_vendor_from_identifier():
    # El env PROCESSOR_IDENTIFIER termina con el feature string del CPU.
    assert hw._cpu_vendor_from_identifier(
        "Intel64 Family 6 Model 151 Stepping 2 GenuineIntel") == "intel"
    assert hw._cpu_vendor_from_identifier(
        "AMD64 Family 23 Model 113 Stepping 2 AuthenticAMD") == "amd"
    assert hw._cpu_vendor_from_identifier("ARM64 Family 1 Model 2") is None
    assert hw._cpu_vendor_from_identifier("") is None


def test_cpu_windows_vendor_via_env(monkeypatch):
    # Vía primaria en Windows: el env, SIN subprocess (la trampa AMD64/ISA).
    _force_windows(monkeypatch)
    monkeypatch.setattr(hw, "psutil", _FakePsutil(physical=6, logical=12))
    monkeypatch.setenv("PROCESSOR_IDENTIFIER", "Intel64 Family 6 GenuineIntel")
    info, degraded = hw._probe_cpu()
    assert degraded is None
    assert info.vendor == "intel"
    assert info.source == "env"
    assert info.physical_cores == 6
    assert info.logical_cores == 12
    # psutil.cpu_freq supplye la frecuencia cuando el env no la trae.
    assert info.max_mhz == 4200.0


def test_cpu_windows_authenticamd(monkeypatch):
    _force_windows(monkeypatch)
    monkeypatch.setattr(hw, "psutil", _FakePsutil())
    monkeypatch.setenv("PROCESSOR_IDENTIFIER", "AMD64 Family 23 AuthenticAMD")
    info, degraded = hw._probe_cpu()
    assert degraded is None
    assert info.vendor == "amd"
    assert info.source == "env"


def test_cpu_windows_falls_back_to_cim(monkeypatch):
    # Sin el env (o con un valor irreconocible): CIM vía PowerShell.
    _force_windows(monkeypatch)
    monkeypatch.setattr(hw, "psutil", _FakePsutil())
    monkeypatch.delenv("PROCESSOR_IDENTIFIER", raising=False)

    class _Proc:
        returncode = 0
        stdout = json.dumps({
            "Name": "AMD Ryzen 7 5800X 8-Core",
            "Vendor": "AuthenticAMD",
            "MaxClockSpeed": 4200,
        })

    monkeypatch.setattr(hw.subprocess, "run", lambda *a, **k: _Proc())
    info, degraded = hw._probe_cpu()
    assert degraded is None
    assert info.vendor == "amd"
    assert info.model == "AMD Ryzen 7 5800X 8-Core"
    assert info.max_mhz == 4200.0
    assert info.source == "cim"


def test_cpu_cim_failure_marks_degraded(monkeypatch):
    _force_windows(monkeypatch)
    monkeypatch.setattr(hw, "psutil", _FakePsutil())
    monkeypatch.delenv("PROCESSOR_IDENTIFIER", raising=False)

    def _boom(*a, **k):
        raise OSError("powershell no está")

    monkeypatch.setattr(hw.subprocess, "run", _boom)
    info, degraded = hw._probe_cpu()
    assert info is None
    assert degraded == "cpu.cim"


def test_cpu_linux_via_proc_cpuinfo(tmp_path, monkeypatch):
    _force_linux(monkeypatch)
    monkeypatch.setattr(hw, "psutil", _FakePsutil(physical=4, logical=8))
    fake = tmp_path / "cpuinfo"
    fake.write_text(
        "vendor_id\t: AuthenticAMD\n"
        "model name\t: AMD Ryzen 5 3600 6-Core Processor\n"
        "cpu MHz\t\t: 3500.000\n",
        encoding="utf-8",
    )
    info, degraded = hw._probe_cpu_linux(str(fake))
    assert degraded is None
    assert info.vendor == "amd"
    assert info.model == "AMD Ryzen 5 3600 6-Core Processor"
    assert info.source == "proc"


def test_cpu_linux_unknown_vendor(tmp_path):
    fake = tmp_path / "cpuinfo"
    fake.write_text("vendor_id\t: SomeOtherVendor\nmodel name\t: Weird CPU\n",
                    encoding="utf-8")
    info, degraded = hw._probe_cpu_linux(str(fake))
    assert info is None
    assert degraded == "cpu.vendor"


def test_cpu_cpuinfo_missing_file(tmp_path):
    info, degraded = hw._probe_cpu_linux(str(tmp_path / "nope"))
    assert info is None
    assert degraded == "cpu.proc"


def test_cpu_physical_never_exceeds_logical(monkeypatch):
    # Algunas VMs reportan physical > logical (o None); el snapshot nunca
    # debe exponer physical > logical (n_threads se deriva de esto).
    _force_windows(monkeypatch)
    monkeypatch.setattr(hw, "psutil", _FakePsutil(physical=10, logical=4))
    monkeypatch.setenv("PROCESSOR_IDENTIFIER", "Intel64 GenuineIntel")
    info, _ = hw._probe_cpu()
    assert info.physical_cores == 4


# --------------------------------------------------------------------------
# GPU
# --------------------------------------------------------------------------


def test_gpu_vendor_from_name():
    assert hw._gpu_vendor_from_name("NVIDIA GeForce RTX 3060") == "nvidia"
    assert hw._gpu_vendor_from_name("NVIDIA RTX 4090") == "nvidia"
    assert hw._gpu_vendor_from_name("AMD Radeon RX 7600") == "amd"
    assert hw._gpu_vendor_from_name("Intel(R) UHD Graphics 630") == "intel"
    assert hw._gpu_vendor_from_name("Intel Arc A750") == "intel"
    assert hw._gpu_vendor_from_name("Microsoft Basic Render Driver") == "other"


def test_gpus_nvml_primary(monkeypatch):
    fake = _FakePynvml()
    monkeypatch.setattr(hw, "PYNVML_AVAILABLE", True)
    monkeypatch.setattr(hw, "pynvml", fake)
    gpus, reason = hw._probe_gpus_nvml()
    assert reason is None
    assert len(gpus) == 1
    g = gpus[0]
    assert g.vendor == "nvidia"
    assert g.name == "NVIDIA GeForce RTX 4090"
    assert g.vram_total_mb == pytest.approx(24576.0)
    assert g.vram_used_mb == pytest.approx(2048.0)
    assert g.compute_capability == "12.0"


def test_gpus_nvml_no_devices(monkeypatch):
    fake = _FakePynvml(gpus=[])
    monkeypatch.setattr(hw, "PYNVML_AVAILABLE", True)
    monkeypatch.setattr(hw, "pynvml", fake)
    gpus, reason = hw._probe_gpus_nvml()
    assert gpus == []
    assert reason == "gpu.nvml"


def test_gpus_nvml_unavailable_marks_reason(monkeypatch):
    monkeypatch.setattr(hw, "PYNVML_AVAILABLE", False)
    gpus, reason = hw._probe_gpus_nvml()
    assert gpus == []
    assert reason == "gpu.nvml"


def test_gpus_amd_via_lhm_after_registry_gate(monkeypatch):
    # Sin NVML y con una Radeon en el registro: el sidecar LHM provee la VRAM.
    monkeypatch.setattr(hw, "PYNVML_AVAILABLE", False)
    monkeypatch.setattr(hw, "_video_controller_names",
                       lambda: ["AMD Radeon RX 7600"])
    fake_lhm = _FakeLhmModule()
    monkeypatch.setattr(hw, "lhm", fake_lhm)
    gpus, reason = hw._probe_gpus()
    assert reason is None
    assert len(gpus) == 1
    assert gpus[0].vendor == "amd"
    assert gpus[0].name == "AMD Radeon RX 7600"
    assert gpus[0].vram_total_mb == pytest.approx(8192.0)
    assert fake_lhm.sidecar_calls == 1


def test_gpus_lhm_never_spawned_without_amd(monkeypatch):
    # Gate duro: sin AMD en el registro, el sidecar NO se toca (no spawnear
    # LHM en equipos Intel es parte del diseño A2).
    monkeypatch.setattr(hw, "PYNVML_AVAILABLE", False)
    monkeypatch.setattr(hw, "_video_controller_names",
                       lambda: ["Intel(R) UHD Graphics 630"])

    class _NoTouchLhm:
        def resource_available(self):
            raise AssertionError("LHM no debe consultarse sin GPU AMD")

        def LhmSidecar(self):
            raise AssertionError("LHM no debe spawnearse sin GPU AMD")

    monkeypatch.setattr(hw, "lhm", _NoTouchLhm())
    gpus, reason = hw._probe_gpus()
    assert len(gpus) == 1
    assert gpus[0].vendor == "intel"
    # Intel WDDM sin NVML: sin proveedor local fiable de VRAM (limitación
    # documentada) — se lista SIN vram y se marca el motivo.
    assert gpus[0].vram_total_mb is None
    assert reason == "gpu.vram.unknown"


def test_gpus_amd_gate_without_lhm_resource_lists_by_name(monkeypatch):
    # Gate AMD pero el paquete LHM no está instalado: se lista la GPU por
    # nombre SIN VRAM y queda el motivo de degradación.
    monkeypatch.setattr(hw, "PYNVML_AVAILABLE", False)
    monkeypatch.setattr(hw, "_video_controller_names",
                       lambda: ["AMD Radeon RX 7600"])
    monkeypatch.setattr(hw, "lhm", _FakeLhmModule(available=False))
    gpus, reason = hw._probe_gpus()
    assert len(gpus) == 1
    assert gpus[0].vendor == "amd"
    assert gpus[0].vram_total_mb is None
    assert reason == "gpu.lhm.unavailable"


def test_gpus_amd_sidecar_crash_degrades(monkeypatch):
    monkeypatch.setattr(hw, "PYNVML_AVAILABLE", False)
    monkeypatch.setattr(hw, "_video_controller_names",
                       lambda: ["AMD Radeon RX 7600"])
    monkeypatch.setattr(hw, "lhm", _FakeLhmModule(boom=True))
    gpus, reason = hw._probe_gpus()
    # El sidecar lanzó: fallback por nombre + motivo (nunca excepcion al caller).
    assert len(gpus) == 1
    assert gpus[0].vram_total_mb is None
    assert reason == "gpu.lhm"


def test_gpus_lhm_zero_vram_sensor_becomes_none(monkeypatch):
    # LHM reporta vram_total_mb=0.0 cuando no expone el sensor: None deja
    # claro que no hay dato (el motor de recomendaciones ignora GPUs sin VRAM).
    fake_entry = {"index": 0, "name": "AMD Radeon RX 6600",
                  "vram_total_mb": 0.0, "vram_used_mb": 0.0}
    monkeypatch.setattr(hw, "PYNVML_AVAILABLE", False)
    monkeypatch.setattr(hw, "_video_controller_names",
                       lambda: ["AMD Radeon RX 6600"])
    monkeypatch.setattr(hw, "lhm", _FakeLhmModule(entries=[fake_entry]))
    gpus, reason = hw._probe_gpus()
    # La GPU se lista (existe) pero SIN VRAM: best_gpu la ignora y el motor
    # de recomendaciones emite no_vram_source.
    assert len(gpus) == 1
    assert gpus[0].vram_total_mb is None
    assert reason is None


def test_gpus_nvml_wins_over_registry(monkeypatch):
    # Con NVML vivo el registro y LHM no se consultan (orden de la cascada).
    monkeypatch.setattr(hw, "PYNVML_AVAILABLE", True)
    monkeypatch.setattr(hw, "pynvml", _FakePynvml())
    monkeypatch.setattr(
        hw, "_video_controller_names",
        lambda: (_ for _ in ()).throw(AssertionError("no debe consultarse")))

    class _NoTouchLhm:
        def resource_available(self):
            raise AssertionError("no debe consultarse")

        def LhmSidecar(self):
            raise AssertionError("no debe consultarse")

    monkeypatch.setattr(hw, "lhm", _NoTouchLhm())
    gpus, reason = hw._probe_gpus()
    assert reason is None
    assert gpus[0].vendor == "nvidia"


def test_gpu_family_cascade():
    nvidia = hw.GpuInfo(index=0, name="RTX", vendor="nvidia", vram_total_mb=1.0)
    amd = hw.GpuInfo(index=0, name="RX", vendor="amd", vram_total_mb=1.0)
    intel = hw.GpuInfo(index=0, name="UHD", vendor="intel")
    other = hw.GpuInfo(index=0, name="Basic", vendor="other")
    assert hw._gpu_family([nvidia, amd, intel]) == "nvidia"
    assert hw._gpu_family([amd, intel]) == "amd"
    assert hw._gpu_family([intel]) == "intel"
    assert hw._gpu_family([other]) == "cpu"
    assert hw._gpu_family([]) == "cpu"


# --------------------------------------------------------------------------
# RAM / disco
# --------------------------------------------------------------------------


def test_ram_probe_values(monkeypatch):
    monkeypatch.setattr(hw, "psutil", _FakePsutil(
        ram_total=32 * 1024 ** 3, ram_available=24 * 1024 ** 3))
    ram, reason = hw._probe_ram()
    assert reason is None
    assert ram.total_mb == pytest.approx(32768.0)
    assert ram.available_mb == pytest.approx(24576.0)
    assert ram.swap_total_mb == pytest.approx(8192.0)


def test_ram_probe_failure_degrades(monkeypatch):
    monkeypatch.setattr(hw, "psutil", _FakePsutil(fail=True))
    ram, reason = hw._probe_ram()
    assert ram is None
    assert reason == "ram"


def test_disks_cover_configured_model_dirs(tmp_path):
    # Los GGUF viven en model_dirs: ahí se mide el espacio libre. Dos dirs en
    # el mismo drive (el tmp_path del test) se deduplican por anchor: un
    #ModelDir por drive, reportando el primer path configurado.
    from config import config
    d1 = tmp_path / "models"
    d1.mkdir()
    d2 = tmp_path / "models2"
    d2.mkdir()
    config._data["model_dirs"] = [str(d1), str(d2)]
    disks, reason = hw._probe_disks()
    assert reason is None
    assert len(disks) == 1
    assert disks[0].path == str(d1)
    assert disks[0].free_mb is not None and disks[0].free_mb > 0
    assert disks[0].total_mb is not None


def test_disks_dedupe_same_drive(tmp_path):
    from config import config
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    config._data["model_dirs"] = [str(a), str(b), str(a)]
    disks, _ = hw._probe_disks()
    assert len(disks) == 1


def test_disks_missing_dir_uses_drive_anchor(tmp_path):
    # Un model_dir borrado no invalida la foto: se sondea su drive.
    from config import config
    ghost = tmp_path / "borrado"
    config._data["model_dirs"] = [str(ghost)]
    disks, reason = hw._probe_disks()
    assert reason is None
    assert len(disks) == 1
    assert disks[0].path == str(ghost)
    assert disks[0].free_mb is not None


# --------------------------------------------------------------------------
# Composición + caché TTL
# --------------------------------------------------------------------------


def _snapshot_cpu():
    return hw.CpuInfo(vendor="amd", model="Ryzen", logical_cores=12,
                      physical_cores=6, max_mhz=4200.0, source="env")


def _snapshot_ram():
    return hw.RamInfo(total_mb=32768.0, available_mb=24576.0, swap_total_mb=None)


def _snapshot_gpu():
    return [hw.GpuInfo(index=0, name="AMD Radeon RX 7600", vendor="amd",
                       vram_total_mb=8192.0, vram_used_mb=1024.0)]


def test_detect_hardware_composes_full_snapshot(monkeypatch):
    _mock_probes(monkeypatch, cpu=_snapshot_cpu(), ram=_snapshot_ram(),
                 gpus=_snapshot_gpu())
    snap = hw.detect_hardware()
    assert snap.cpu.vendor == "amd"
    assert snap.ram.available_mb == pytest.approx(24576.0)
    assert snap.gpu_family == "amd"
    assert snap.degraded == []
    assert snap.detected_at  # timestamp UTC ISO presente
    assert snap.disks  # sin model_dirs configurados => sondea DATA_DIR (tmp)


def test_detect_hardware_records_degraded_reasons(monkeypatch):
    # Sondas que fallan: campos None + motivos listados, respuesta válida.
    _mock_probes(monkeypatch, cpu=None, ram=None, gpus=[], gpu_reason="gpu.none")
    snap = hw.detect_hardware()
    assert snap.cpu is None
    assert snap.ram is None
    assert snap.gpus == []
    assert snap.gpu_family == "cpu"
    assert "cpu" in snap.degraded
    assert "ram" in snap.degraded
    assert "gpu.none" in snap.degraded


def test_detect_hardware_caches_within_ttl(monkeypatch):
    calls = {"n": 0}

    def counting_gpus():
        calls["n"] += 1
        return _snapshot_gpu(), None

    _mock_probes(monkeypatch, cpu=_snapshot_cpu(), ram=_snapshot_ram())
    monkeypatch.setattr(hw, "_probe_gpus", counting_gpus)
    first = hw.detect_hardware()
    second = hw.detect_hardware()
    assert calls["n"] == 1  # segunda llamada sale del cache
    assert second is first
    third = hw.detect_hardware(force=True)
    assert calls["n"] == 2  # ?refresh=1 fuerza una nueva tanda de sondas
    assert third is not first


def test_detect_hardware_ttl_expiry(monkeypatch):
    _mock_probes(monkeypatch, cpu=_snapshot_cpu(), ram=_snapshot_ram(),
                 gpus=_snapshot_gpu())
    hw.detect_hardware()
    # Envejar artificialmente el cache más allá del TTL.
    stamp, snap = hw._cache
    hw._cache = (stamp - hw.SNAPSHOT_TTL_S - 1, snap)
    calls = {"n": 0}

    def counting():
        calls["n"] += 1
        return _snapshot_gpu(), None

    monkeypatch.setattr(hw, "_probe_gpus", counting)
    hw.detect_hardware()
    assert calls["n"] == 1
