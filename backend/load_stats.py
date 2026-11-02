"""
A2F-2: "cómo levantó el modelo" — parser tolerante del log de carga de
llama-server + modelo de datos para la tarjeta de carga del Launcher.

Dos familias de datos, deliberadamente separadas:
- DERIVADAS DEL LOG (estimado): capas offload GPU/CPU, tamaño del KV, buffers,
  tiempo de carga declarado por el server. Se parsean las líneas que imprime
  `llama-server` con `--verbosity 4` (la que usa el launcher). El formato del
  log CAMBIA entre builds históricas de llama.cpp:
    * builds viejas (≈b11003–b11145): prefijos `llm_load_*` / `llm_load_tensors`,
      p. ej. `llm_load_tensors: offloaded 23/35 layers to GPU`.
    * builds modernas (≈b11146+): prefijos `load:` / `llama_load:` / `ggml_*`,
      p. ej. `load: offloaded 40/63 layers to GPU device 0`.
  La era b11009 (validada contra logs reales del usuario) anuncia la build solo
  en `common_params_print_info: build 11009 (hash)` y declara el tamaño de
  pesos por dispositivo con `<DEVICE> model buffer size = ... MiB`.
  Por eso cada dato tiene varias familias de patrones y NINGUNA coincidencia es
  un error: los campos quedan en None y la tarjeta muestra lo que sí se pudo
  determinar (honestidad: lo que no se pudo leer del log no se inventa).
- MEDIDOS (real): deltas de RAM/VRAM tomados por el launcher antes del spawn y
  justo antes de promover el proceso a "running" (collector de metrics.py,
  NVML + psutil). El parser no los produce: los rellena el launcher.

La UI debe distinguir visualmente ambas familias (lo medido vs lo estimado).
"""

from __future__ import annotations

import re
from typing import Iterable

from pydantic import BaseModel

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Modelo expuesto al frontend (via ProcessInfo.load_stats)
# ---------------------------------------------------------------------------


class DeviceOffload(BaseModel):
    """Capas y pesos que el log declara cargados en un dispositivo concreto.
    `layers` puede faltar si el log solo declara pesos (buffers por device
    sin líneas de offload); entonces el dispositivo existe pero sus capas
    quedan None — no se inventan."""

    device_index: int | None = None
    device_name: str | None = None  # si el log lo nombra (CUDA/Metal)
    layers: int | None = None
    model_buffer_mib: float | None = None  # p. ej. "CUDA0 model buffer size"


class MeasuredGpu(BaseModel):
    """Delta REAL de VRAM de una GPU (NVML) entre el arranque y el 'ready'.
    float | None: los valores del collector vienen con un decimal (MiB reales
    redondeados) — con int Pydantic rechazaría p. ej. 1195.9."""

    index: int
    name: str | None = None
    used_before_mib: float | None = None
    used_after_mib: float | None = None
    delta_mib: float | None = None


class LoadStats(BaseModel):
    # --- estimado: derivado del log (con --verbosity 4) ---
    model_filename: str | None = None
    build: str | None = None  # "b11827" si el propio log del server lo dice
    total_layers: int | None = None
    gpu_layers: int | None = None
    cpu_layers: int | None = None  # total - gpu si el log no lo imprime explícito
    devices: list[DeviceOffload] = []
    kv_cache_mib: float | None = None
    kv_layers: int | None = None
    compute_mib: float | None = None
    cpu_buffer_mib: float | None = None
    # pesos que el log deja en RAM ("CPU_Mapped model buffer size"), no buffer
    # de cómputo: es otra cosa que cpu_buffer_mib y se muestra aparte
    cpu_model_buffer_mib: float | None = None
    log_load_seconds: float | None = None  # "model load time" declarado por el server

    # --- medido: deltas reales tomados por el launcher (relleno fuera) ---
    wall_load_seconds: float | None = None  # spawn -> /health 200 (medido)
    ram_before_mib: float | None = None
    ram_after_mib: float | None = None
    ram_delta_mib: float | None = None
    process_rss_mib: float | None = None
    gpus: list[MeasuredGpu] = []

    # códigos estables de degradación (p. ej. "unrecognized_format"); la UI los
    # traduce; nunca texto suelto: las notas no se traducen desde el backend.
    notes: list[str] = []


# ---------------------------------------------------------------------------
# Parseo tolerante por familias (ningún patrón es obligatorio)
# ---------------------------------------------------------------------------

# build anunciada por el propio server al arrancar: "version 1.0.0 (b11827)",
# "build: b11009 (...)", "load: build = 11349 (c00b1f51)" o, era b11009 real,
# "common_params_print_info: build 11009 (fb27a525d) with Clang ..."
_RE_BUILD = re.compile(
    r"\(\s*(b\d{4,6})\s*\)|\bbuild:\s*(b\d{4,6})|\bbuild\s*=\s*(\d{4,6})\b"
    r"|\bbuild\s+(b?\d{4,6})\s*\("
)

# nombre del archivo cargado (ambas eras usan "loading model 'path'")
_RE_MODEL_FILE = re.compile(r"loading model\s+\S*?['\"]?([^'\"]+?\.gguf)['\"]?", re.IGNORECASE)

# tiempo de carga declarado (solo builds modernas): "load: model load time = 15210.55 ms"
_RE_LOAD_TIME = re.compile(r"model load time\s*=\s*([\d.]+)\s*ms", re.IGNORECASE)

# total de capas: "load: number of layers = 63" (excluye "view layers") o
# "llama_model_loader: n_layer ... = 63" (familia antigua/archivos internos)
_RE_TOTAL_LAYERS = re.compile(
    r"number of (?!view\s)(?:repeating\s)?layers\s*=\s*(\d+)|\bn_layer\b[^=]*=\s*(\d+)",
    re.IGNORECASE,
)

# offload a GPU:
#   moderna:  "load: offloaded 40/63 layers to GPU device 0"
#   moderna multi: "load:  CUDA0[0]  2522 MiB, used  10003 MiB" -> no se usa para capas
#   antigua:  "llm_load_tensors: offloaded 23/35 layers to GPU"
#             "llm_load_tensors: offloading 20 repeating layers to GPU" (intermedio)
_RE_OFFLOAD_GPU_DEV = re.compile(
    r"offloaded\s+(\d+)/(\d+)\s+layers?\s+to\s+GPU(?:\s+device\s+(\d+))?",
    re.IGNORECASE,
)
_RE_OFFLOAD_GPU_BACKEND = re.compile(
    r"offloaded\s+(\d+)/(\d+)\s+layers?\s+to\s+(CUDA|Metal|Vulkan|SYCL|RPC)\b",
    re.IGNORECASE,
)
_RE_OFFLOAD_CPU = re.compile(
    r"offloaded\s+(\d+)(?:/\d+)?\s+layers?\s+to\s+CPU|moving\s+(\d+)\s+layers?\s+to\s+CPU",
    re.IGNORECASE,
)

# tamaño del KV:
#   moderna:  "(size: 240.00 MiB, n_layers: 25)"  (llama_kv / kv unified)
#   ambas:    "llm_load_kv: KV buffer size =  150.00 MiB" / "load: KV buffer size = ..."
#   unificada: "kv_unified: size = 900.00 MiB, n_ctx = 8192"
# El token "kv" es obligatorio: "CUDA0[0] buffer size" o "CPU buffer size" son
# buffers de pesos, no KV, y no deben contaminar.
_RE_KV_INLINE = re.compile(
    r"\(\s*size:\s*([\d.]+)\s*MiB,\s*n_layers:\s*(\d+)\s*\)", re.IGNORECASE
)
_RE_KV_SIZE = re.compile(
    r"kv(?:_unified)?\b[^=\n]*?\bsize\s*=\s*([\d.]+)\s*MiB", re.IGNORECASE
)

# buffers: "compute buffer size = 1410.55 MiB" (el total; "compute[dev0] buffer
# size" tiene corchetes y no matchea a propósito), "CPU buffer size = 290.11 MiB"
_RE_COMPUTE_MIB = re.compile(r"compute buffer size\s*=\s*([\d.]+)\s*MiB", re.IGNORECASE)
_RE_CPU_BUFFER_MIB = re.compile(r"CPU(?:_buffer)?\s+buffer size\s*=\s*([\d.]+)\s*MiB", re.IGNORECASE)

# pesos por dispositivo, el formato es idéntico en ambas eras:
#   "load_tensors:   CPU_Mapped model buffer size =   682.03 MiB"
#   "load_tensors:        CUDA0 model buffer size = 15718.48 MiB"
_RE_MODEL_BUFFER_MIB = re.compile(
    r"([A-Za-z][A-Za-z0-9_]*)\s+model buffer size\s*=\s*([\d.]+)\s*MiB",
    re.IGNORECASE,
)

# nombres de dispositivos GPU anunciados:
#   " Device 0: NVIDIA GeForce RTX 3060: ; compute = 8.6; ..." (CUDA)
#   "Found Metal device: Apple M1" / "ggml metal initialized with device name ..."
_RE_CUDA_DEVICE = re.compile(r"Device\s+(\d+):\s*([^:;\n]+?)\s*[:;\n]", re.IGNORECASE)
_RE_METAL_DEVICE = re.compile(
    r"(?:found metal device|metal initialized with device name)[:\s]+([^\n]+)",
    re.IGNORECASE,
)


def _to_float(text: str) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def process_rss_mib(pid: int | None) -> float | None:
    """RSS actual del proceso hijo (psutil). None si no hay psutil o el proceso
    ya no existe (p. ej. cayó justo antes de leerlo)."""
    if pid is None or psutil is None:
        return None
    try:
        return round(psutil.Process(pid).memory_info().rss / (1024**2), 1)
    except Exception:  # noqa: BLE001 — NoSuchProcess/AccessDenied y compañía
        return None


def _is_noise(line: str) -> bool:
    """Líneas de debug internas que repetían datos obsoletos en builds antiguas."""
    return "print_info" in line or "overridden" in line


def _model_buffer_target(token: str) -> tuple[str, int | None]:
    """A qué bucket pertenece un '<token> model buffer size': pesos en host
    ('cpu') o pesos de una GPU ('gpu', índice None si el log no lo numera)."""
    up = token.upper()
    if up.startswith(("CPU", "HOST")):
        return "cpu", None
    num = re.search(r"\d+$", up)
    return "gpu", int(num.group()) if num else None


def parse_load_logs(lines: Iterable[str]) -> LoadStats:
    """
    Extrae de las líneas de log (el ring buffer del launcher, ya filtrado por
    should_forward_log_line) lo que el server dice de su propia carga.

    Tolerante por diseño: cada patrón es opcional; si una build cambió el
    formato, ese campo queda None y se anota en `notes` (no es un error).
    """
    stats = LoadStats()
    saw_anything = False

    gpu_offload: dict[int | None, int] = {}  # device_index -> capas
    gpu_total_candidates: list[tuple[int, int]] = []
    cpu_layers_explicit: int | None = None
    kv_inline: tuple[float, int] | None = None
    kv_size_candidates: list[float] = []
    device_names: dict[int | None, str] = {}
    gpu_weights: dict[int | None, float] = {}  # pesos declarados por device
    cpu_weights_mib: float | None = None  # pesos declarados en host

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        m = _RE_BUILD.search(line)
        if m:
            raw = m.group(1) or m.group(2) or m.group(3) or m.group(4)
            # normaliza a "bNNNN": el log puede anunciar "11009" sin prefijo
            stats.build = raw if raw.startswith("b") else f"b{raw}"
            saw_anything = True
            continue

        m = _RE_MODEL_FILE.search(line)
        if m and not stats.model_filename:
            # solo el nombre del archivo, no la ruta completa
            stats.model_filename = m.group(1).replace("\\", "/").rsplit("/", 1)[-1]
            saw_anything = True

        m = _RE_LOAD_TIME.search(line)
        if m:
            val = _to_float(m.group(1))
            if val is not None:
                stats.log_load_seconds = val / 1000.0
                saw_anything = True

        m = _RE_TOTAL_LAYERS.search(line)
        if m and not _is_noise(line):
            val = m.group(1) or m.group(2)
            if stats.total_layers is None:
                stats.total_layers = int(val)
                saw_anything = True

        m = _RE_OFFLOAD_GPU_DEV.search(line)
        if m:
            gpu_n, total_n, dev = int(m.group(1)), int(m.group(2)), m.group(3)
            key = int(dev) if dev is not None else None
            gpu_offload[key] = max(gpu_offload.get(key, 0), gpu_n)
            gpu_total_candidates.append((gpu_n, total_n))
            saw_anything = True
            continue

        m = _RE_OFFLOAD_GPU_BACKEND.search(line)
        if m:
            gpu_n, total_n = int(m.group(1)), int(m.group(2))
            gpu_offload[None] = max(gpu_offload.get(None, 0), gpu_n)
            gpu_total_candidates.append((gpu_n, total_n))
            saw_anything = True
            continue

        m = _RE_OFFLOAD_CPU.search(line)
        if m:
            val = m.group(1) or m.group(2)
            if val is not None:
                cpu_layers_explicit = int(val)
                saw_anything = True
            continue

        m = _RE_KV_INLINE.search(line)
        if m and not _is_noise(line):
            val = _to_float(m.group(1))
            # varias coincidencias (por-slot y unificada): prevalece la mayor
            if val is not None and (kv_inline is None or val > kv_inline[0]):
                kv_inline = (val, int(m.group(2)))
            saw_anything = True

        m = _RE_KV_SIZE.search(line)
        if m and not _is_noise(line) and "size:" not in line:
            val = _to_float(m.group(1))
            if val is not None:
                kv_size_candidates.append(val)
                saw_anything = True

        m = _RE_COMPUTE_MIB.search(line)
        if m and not _is_noise(line):
            val = _to_float(m.group(1))
            if val is not None and (stats.compute_mib is None or val > stats.compute_mib):
                stats.compute_mib = val
            saw_anything = True

        m = _RE_CPU_BUFFER_MIB.search(line)
        if m and not _is_noise(line):
            val = _to_float(m.group(1))
            if val is not None and (stats.cpu_buffer_mib is None or val > stats.cpu_buffer_mib):
                stats.cpu_buffer_mib = val
            saw_anything = True

        m = _RE_MODEL_BUFFER_MIB.search(line)
        if m and not _is_noise(line):
            val = _to_float(m.group(2))
            if val is not None:
                kind, idx = _model_buffer_target(m.group(1))
                if kind == "cpu":
                    if cpu_weights_mib is None or val > cpu_weights_mib:
                        cpu_weights_mib = val
                else:
                    prev = gpu_weights.get(idx)
                    gpu_weights[idx] = max(prev, val) if prev is not None else val
                saw_anything = True
            continue

        m = _RE_METAL_DEVICE.search(line)
        if m:
            device_names.setdefault(None, m.group(1).strip())
            saw_anything = True
            continue

        m = _RE_CUDA_DEVICE.search(line)
        if m and "device" in line.lower():
            device_names[int(m.group(1))] = m.group(2).strip()
            saw_anything = True

    # --- consolidación ---
    if gpu_offload:
        for idx, layers in sorted(gpu_offload.items(), key=lambda kv: (kv[0] is None, kv[0])):
            stats.devices.append(
                DeviceOffload(
                    device_index=idx,
                    device_name=device_names.get(idx),
                    layers=layers,
                )
            )
        stats.gpu_layers = sum(gpu_offload.values())

    if gpu_total_candidates and stats.total_layers is None:
        # el "x/y" del offload también declara el total de capas
        stats.total_layers = max(total for _gpu, total in gpu_total_candidates)

    if stats.total_layers is not None and stats.gpu_layers is not None:
        stats.cpu_layers = max(stats.total_layers - stats.gpu_layers, 0)
    elif cpu_layers_explicit is not None:
        stats.cpu_layers = cpu_layers_explicit

    # pesos por dispositivo: se acoplan a los devices ya declarados por el
    # offload. Con un único device y un único bucket de pesos son el mismo
    # dispositivo aunque el offload no numere (era b11009: "offloaded 66/66
    # layers to GPU" + "CUDA0 model buffer size"). Sin líneas de offload el
    # device queda con layers=None: el log declaró pesos, no capas.
    if gpu_weights:
        if len(stats.devices) == 1 and len(gpu_weights) == 1:
            stats.devices[0].model_buffer_mib = max(gpu_weights.values())
        else:
            by_index = {d.device_index: d for d in stats.devices}
            for idx, val in sorted(gpu_weights.items(), key=lambda kv: (kv[0] is None, kv[0])):
                dev = by_index.get(idx)
                if dev is None:
                    stats.devices.append(
                        DeviceOffload(
                            device_index=idx,
                            device_name=device_names.get(idx),
                            model_buffer_mib=val,
                        )
                    )
                elif dev.model_buffer_mib is None or val > dev.model_buffer_mib:
                    dev.model_buffer_mib = val

    if cpu_weights_mib is not None:
        stats.cpu_model_buffer_mib = cpu_weights_mib

    if kv_inline is not None:
        stats.kv_cache_mib, stats.kv_layers = kv_inline
    elif kv_size_candidates:
        stats.kv_cache_mib = max(kv_size_candidates)

    if not saw_anything:
        # Código estable, no texto: la UI lo traduce (launcher.loadCardNoParse).
        stats.notes.append("unrecognized_format")
    return stats
