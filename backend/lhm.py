"""
lhm.py — Telemetría de GPU AMD vía sidecar LibreHardwareMonitor (MON-6).

El Monitor lee GPUs por NVML, que solo existe para NVIDIA. En máquinas AMD no
hay equivalente en pynvml, así que LibreHardwareMonitor (LHM) se bundlea en el
instalador y se arranca como sidecar local: sus sensores se leen por el
servidor web interno (GET http://127.0.0.1:8185/data.json).

Decisiones clave (de la auditoría del código de LHM 0.9.6 antes de bundlear):
- LHM NO acepta argumentos CLI: su configuración vive en
  `LibreHardwareMonitor.exe.config` (MainForm.cs lo carga vía
  Path.ChangeExtension(ExecutablePath, ".config")) y los booleans se persisten
  como "true"/"false" exactos (PersistentSettings.GetValue compara
  str == "true"). Por eso se escribe el .config antes de cada spawn propio:
  LHM solo lo reescribe en un cierre gracioso y nosotros lo terminate'amos.
- Puerto dedicado 8185 (el default de LHM es 8085) para no chocar con un LHM
  del usuario. Si YA hay un data.json sirviendo en 8185 (instancia huérfana de
  una corrida anterior tras el kill duro de Tauri), se adopta sin spawnar.
- El zip pinned se bundlea vía `datas` del spec PyInstaller (destino
  assets/vendor, mismo patrón que backend/prompts) y se extrae a
  DATA_DIR/tools/... sin tocar la red, verificado con sha256.
- data.json no serializa el NOMBRE de los sensores, pero Sensor.Identifier =
  Hardware.Identifier + "/" + tipo.minúscula + "/" + índice (Sensor.cs:82) y
  el HardwareId de una GPU AMD es ("gpu-amd", adapterIndex) (AmdGpu.cs:69).
  El parser mapea por ese sufijo (tipo, índice) contra el layout verificado de
  AmdGpu.cs 0.9.6:
    temperature/0 = GPU Core      load/0 = core %       load/1 = memory %
    clock/0 = core MHz            clock/2 = memory MHz  control/0 = fan %
    data/0..2 = VRAM used/free/total (GB)
    power/3 = GPU Package (fallback power/0 = GPU Core)
- Licencia: MPL-2.0 (no MIT). Se bundlear sin modificar es compatible con
  distribución — ver THIRD-PARTY-NOTICES.txt.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import shutil
import subprocess
import time
import urllib.request
import zipfile
from pathlib import Path

from paths import BUNDLE_DIR, DATA_DIR

logger = logging.getLogger("glyvex.lhm")

LHM_VERSION = "0.9.6"
LHM_EXE = "LibreHardwareMonitor.exe"
LHM_PORT = 8185  # dedicado: 8085 es el default de LHM y puede estar ocupado
LHM_ZIP_RELPATH = Path("assets") / "vendor" / f"LibreHardwareMonitor-{LHM_VERSION}.zip"
LHM_ZIP_SHA256 = "086d9f1b5a99e643edc2cfaaac16051685b551e4c5ac0b32a57c58c0e529c001"

# Override para desarrollo/tests: carpeta con LibreHardwareMonitor.exe ya lista.
ENV_LHM_DIR = "GLYVEX_LHM_DIR"

_TOOL_SUBPATH = Path("tools") / "librehardwaremonitor"
_VERSION_MARKER = ".glyvex-version"

# Espera de health tras el spawn: LHM tarda unos segundos en inicializar la
# GPU vía AGS. Son polls a localhost, no a la red.
_HEALTH_POLL_ATTEMPTS = 25
_HEALTH_POLL_INTERVAL_S = 0.4
# Tras un spawn fallido, no reintentar en cada snapshot (1 Hz): la causa suele
# ser permanente (sin .NET, puerto ocupado) y reintentar cuesta procesos.
_FAILURE_COOLDOWN_S = 300.0
_HTTP_TIMEOUT_S = 3.0

# Layout fijo verificado contra LibreHardwareMonitorLib/Hardware/Gpu/AmdGpu.cs
# de la versión pinned (los índices de Sensor son estables dentro de 0.9.6).
_GPU_HARDWARE_PREFIX = "gpu-amd"
# Tipos de sensor con mapeo a GpuMetrics; cualquier otro tipo de LHM (Level,
# Throughput, Voltage, ...) se descarta al poblar el índice.
_KNOWN_SENSOR_KINDS = frozenset({"temperature", "load", "clock", "control", "power", "data"})


def _tool_dir() -> Path:
    # DATA_DIR se lee al momento de llamar (no se congela en un módulo
    # constante): conftest redirige lhm.DATA_DIR a tmp y eso debe tener efecto.
    return DATA_DIR / _TOOL_SUBPATH


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _locate_exe(base: Path) -> Path | None:
    """Carpeta que contiene el exe (algunos layouts del zip lo anidan en una
    subcarpeta; devolvemos el directorio real del exe en ese caso)."""
    if (base / LHM_EXE).is_file():
        return base
    for candidate in base.rglob(LHM_EXE):
        return candidate.parent
    return None


def _bundled_zip_path() -> Path:
    return BUNDLE_DIR / LHM_ZIP_RELPATH


def resource_available() -> bool:
    """Chequeo barato (solo stat) de si existe un recurso LHM usable. Lo usa
    metrics.py para decidir si vale la pena crear el sidecar: sin recurso, el
    error de GPU degradada debe quedar como estaba."""
    env_dir = os.environ.get(ENV_LHM_DIR)
    if env_dir and (Path(env_dir) / LHM_EXE).is_file():
        return True
    tool_dir = _tool_dir()
    if (_tool_dir() / _VERSION_MARKER).is_file() and _locate_exe(tool_dir) is not None:
        return True
    return _bundled_zip_path().is_file()


def resolve_lhm_dir() -> tuple[Path | None, str | None]:
    """Devuelve (directorio_del_exe, None) o (None, error). Extrae del bundle
    si hace falta; nunca toca la red."""
    env_dir = os.environ.get(ENV_LHM_DIR)
    if env_dir:
        found = _locate_exe(Path(env_dir))
        if found is not None:
            return found, None
        return None, f"GLYVEX_LHM_DIR apunta a una carpeta sin {LHM_EXE}"

    tool_dir = _tool_dir()
    marker = tool_dir / _VERSION_MARKER
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == LHM_VERSION:
        found = _locate_exe(tool_dir)
        if found is not None:
            return found, None

    zip_path = _bundled_zip_path()
    if not zip_path.is_file():
        return None, "LibreHardwareMonitor no está disponible (sin recurso empaquetado)"

    digest = _sha256_file(zip_path)
    if digest != LHM_ZIP_SHA256:
        return None, "El paquete de LibreHardwareMonitor no coincide con la firma esperada"

    try:
        # Marker ausente o de versión vieja: se re-extrae limpio.
        shutil.rmtree(tool_dir, ignore_errors=True)
        tool_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tool_dir)
        found = _locate_exe(tool_dir)
        if found is None:
            return None, f"El paquete de LibreHardwareMonitor no contiene {LHM_EXE}"
        marker.write_text(LHM_VERSION, encoding="utf-8")
        logger.info("LibreHardwareMonitor %s extraído en %s", LHM_VERSION, tool_dir)
        return found, None
    except OSError as exc:
        return None, f"No se pudo extraer LibreHardwareMonitor: {exc}"


# Claves UserOption exactas leídas en LibreHardwareMonitor.Windows.Forms/UI/
# MainForm.cs (auditadas en master 2026-10). Con gpu-menu encendido y el
# resto apagado: menos carga y no se necesitan privilegios de administrador
# (los sensores CPU/mainboard vía Ring0 sí los requerirían; la GPU AMD vía
# AGS no).
_APP_SETTINGS: list[tuple[str, str]] = [
    ("runWebServerMenuItem", "true"),
    ("listenerPort", str(LHM_PORT)),
    ("authenticationEnabled", "false"),
    ("startMinMenuItem", "true"),
    ("minTrayMenuItem", "true"),
    ("gpuMenuItem", "true"),
    ("cpuMenuItem", "false"),
    ("mainboardMenuItem", "false"),
    ("ramMenuItem", "false"),
    ("hddMenuItem", "false"),
    ("nicMenuItem", "false"),
    ("psuMenuItem", "false"),
    ("powerMonitorMenuItem", "false"),
    ("fanControllerMenuItem", "false"),
]


def write_sidecar_config(tool_dir: Path) -> None:
    lines = ['<?xml version="1.0" encoding="utf-8"?>', "<configuration>", "  <appSettings>"]
    lines += [f'    <add key="{key}" value="{value}" />' for key, value in _APP_SETTINGS]
    lines += ["  </appSettings>", "</configuration>", ""]
    (tool_dir / f"{LHM_EXE}.config").write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------
# Parser de data.json
# --------------------------------------------------------------------------


def _id_parts(identifier) -> list[str]:
    return [part for part in str(identifier).split("/") if part]


def _finite(value) -> float | None:
    # data.json permite literales con nombre ("NaN") vía
    # JsonNumberHandling.AllowNamedFloatingPointLiterals (HttpServer.cs).
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def parse_gpu_data_json(payload: dict) -> list[dict]:
    """data.json -> lista de dicts con los campos de GpuMetrics para cada GPU
    AMD. Se construyen los GpuMetrics en metrics.py para no crear un import
    circular; este módulo no importa metrics.

    Los GPUs no-AMD (NVIDIA vía NVML, Intel) se ignoran por diseño: la vía
    NVIDIA ya existe y MON-6 cubre el hueco AMD.
    """
    hardwares: list[dict] = []

    def walk(node, ctx):
        if not isinstance(node, dict):
            return
        hw_id = node.get("HardwareId")
        if hw_id is not None:
            parts = _id_parts(hw_id)
            if parts and parts[0] == _GPU_HARDWARE_PREFIX:
                adapter = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else len(hardwares)
                ctx = {
                    "index": adapter,
                    "name": str(node.get("Text") or f"GPU AMD #{adapter}"),
                    "values": {},
                }
                hardwares.append(ctx)
            else:
                ctx = None  # otro hardware (mainboard, NVIDIA, ...): fuera de este parser
        sensor_id = node.get("SensorId")
        if ctx is not None and sensor_id is not None:
            parts = _id_parts(sensor_id)
            # El sufijo del identifier es "/<tipo>/<indice>" (Sensor.cs:82).
            if len(parts) >= 2 and parts[-2] in _KNOWN_SENSOR_KINDS and parts[-1].isdigit():
                value = _finite(node.get("RawValue"))
                if value is not None:
                    ctx["values"].setdefault((parts[-2], int(parts[-1])), []).append(value)
        for child in node.get("Children") or []:
            walk(child, ctx)

    walk(payload, None)

    gpus: list[dict] = []
    for hw in hardwares:
        values = hw["values"]
        if not values:
            continue  # hardware AMD sin ningún sensor utilizable: solo ruido

        def get(kind: str, index: int):
            # AGS y el fallback D3D pueden reportar el mismo (tipo, índice);
            # max/min mantiene la tripleta used/total/free auto-consistente.
            candidates = values.get((kind, index))
            return max(candidates) if candidates else None

        power = get("power", 3)  # GPU Package ≈ board power
        if power is None:
            power = get("power", 0)  # drivers sin package: usar GPU Core
        temperature = get("temperature", 0)
        core_load = get("load", 0)
        memory_load = get("load", 1)
        clock_core = get("clock", 0)
        clock_memory = get("clock", 2)
        fan_pct = get("control", 0)
        used_gb = get("data", 0)
        free_gb = get("data", 1)
        total_gb = get("data", 2)

        # Las unidades crudas de LHM (verificada contra la tabla Prometheus de
        # HttpServer.cs): Clock=MHz, Data=GB, Load/Control=%, Power=W,
        # Temperature=°C.
        vram_total_mb = round(total_gb * 1024, 1) if total_gb is not None else 0.0
        vram_used_mb = round(used_gb * 1024, 1) if used_gb is not None else 0.0
        if free_gb is not None:
            vram_free_mb = round(free_gb * 1024, 1)
        else:
            vram_free_mb = max(round(vram_total_mb - vram_used_mb, 1), 0.0)
        vram_percent = round(vram_used_mb / vram_total_mb * 100, 1) if vram_total_mb else 0.0

        gpus.append({
            "index": hw["index"],
            "name": hw["name"],
            "vram_used_mb": vram_used_mb,
            "vram_total_mb": vram_total_mb,
            "vram_free_mb": vram_free_mb,
            "vram_percent": vram_percent,
            "gpu_utilization": int(round(core_load)) if core_load is not None else None,
            "memory_utilization": int(round(memory_load)) if memory_load is not None else None,
            "temperature_c": int(round(temperature)) if temperature is not None else None,
            "power_draw_w": round(power, 1) if power is not None else None,
            "power_limit_w": None,  # LHM no expone el límite de potencia
            "clock_graphics_mhz": int(round(clock_core)) if clock_core is not None else None,
            "clock_memory_mhz": int(round(clock_memory)) if clock_memory is not None else None,
            "fan_speed_pct": int(round(fan_pct)) if fan_pct is not None else None,
        })

    gpus.sort(key=lambda g: (g["index"], g["name"]))
    return gpus


# --------------------------------------------------------------------------
# Ciclo de vida del sidecar
# --------------------------------------------------------------------------


class LhmSidecar:
    """Gestiona el proceso LHM propio (spawn + terminate) o adopta uno que ya
    está sirviendo en 8185 sin matarlo (puede ser del usuario o una huérfana
    de una corrida anterior)."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._dir: Path | None = None
        self._cooldown_until = 0.0
        self._last_error: str | None = None

    # -- IO hooks (los tests los reemplazan en la instancia) --------------

    def _probe(self, timeout_s: float = _HTTP_TIMEOUT_S) -> dict | None:
        url = f"http://127.0.0.1:{LHM_PORT}/data.json"
        try:
            request = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                if response.status != 200:
                    return None
                return json.loads(response.read().decode("utf-8"))
        except Exception:
            return None

    def _spawn(self) -> subprocess.Popen:
        # cwd=dir del exe: LHM carga su .config junto al exe y escribe su
        # log/csv ahí.
        return subprocess.Popen([str(self._dir / LHM_EXE)], cwd=str(self._dir))

    # -- ciclo de vida -----------------------------------------------------

    def _fail(self, error: str) -> tuple[dict | None, str]:
        self._last_error = error
        self._cooldown_until = time.monotonic() + _FAILURE_COOLDOWN_S
        return None, error

    def _ensure_data(self) -> tuple[dict | None, str | None]:
        data = self._probe()
        if data is not None:
            return data, None
        if self._proc is not None and self._proc.poll() is None:
            # Proceso nuestro vivo pero sin responder: no spawnar un segundo
            # (evita duplicados); el próximo snapshot reintenta el probe.
            return None, self._last_error or "El sidecar de LibreHardwareMonitor no responde"
        if time.monotonic() < self._cooldown_until:
            return None, self._last_error or "Reintentando LibreHardwareMonitor en unos minutos"

        resolved, error = resolve_lhm_dir()
        if resolved is None:
            return self._fail(error)
        self._dir = resolved
        try:
            write_sidecar_config(self._dir)
            self._proc = self._spawn()
        except OSError as exc:
            return self._fail(f"LibreHardwareMonitor no se pudo iniciar: {exc}")

        for _ in range(_HEALTH_POLL_ATTEMPTS):
            time.sleep(_HEALTH_POLL_INTERVAL_S)
            if self._proc.poll() is not None:
                break
            data = self._probe()
            if data is not None:
                self._last_error = None
                return data, None

        self._terminate_child()
        return self._fail(f"LibreHardwareMonitor no respondió en el puerto {LHM_PORT}")

    def collect(self) -> tuple[list[dict], str | None]:
        data, error = self._ensure_data()
        if error is not None:
            return [], error
        gpus = parse_gpu_data_json(data)
        if not gpus:
            return [], "Sin GPUs AMD reportadas por LibreHardwareMonitor"
        return gpus, None

    def _terminate_child(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None or proc.poll() is not None:
            return
        try:
            # terminate() (CloseMainWindow en Windows) y solo kill si no
            # cooperó: un kill duro igual no reescribe el .config, así que
            # nuestro preset queda intacto para el próximo arranque.
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def shutdown(self) -> None:
        self._terminate_child()
