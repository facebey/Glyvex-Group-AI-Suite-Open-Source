"""
launcher.py — Lanzamiento y control de modelos LLM locales (módulo M2).

Responsabilidades:
- Schema Pydantic v2 de parámetros de lanzamiento (LaunchConfig) con
  validación cruzada (gpu_mode <-> n_gpu_layers, rangos de lora_scale/n_parallel)
  y parámetros avanzados (rope scaling, NUMA, cache reuse, group attention).
- Sampling parameters de arranque (temp, top_p, top_k, min_p, penalties) con
  presets "thinking" / "instruct": llama-server los usa como defaults para
  todo cliente que no mande los suyos en el request.
- Templates de hardware predefinidos + custom, persistidos en SQLite
  (tabla hw_templates, ver database.py). El JSON data/templates/hw_templates.json
  queda solo como semilla de la primera carga.
- Construcción del comando de llama-server / ollama a partir de LaunchConfig.
- Gestión de procesos con asyncio.create_subprocess_exec (NUNCA
  subprocess.Popen dentro de código async), incluyendo:
    - captura de stdout/stderr combinados hacia un log en disco + buffer
      en memoria + fan-out a suscriptores WebSocket ("tail -f" en vivo).
    - health check async (httpx) con polling cada 2s / timeout total 30s.
    - stop con SIGTERM y escalamiento a SIGKILL si no responde en 5s.
    - detección de caída inesperada del proceso -> estado "error".
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import aiofiles
import httpx
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, model_validator

from config import config
from database import (
    db_delete_template,
    db_get_template,
    db_list_templates,
    db_upsert_template,
)
from models import inventory as model_inventory

logger = logging.getLogger("glyvex.launcher")

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_PATH = BASE_DIR / "data" / "templates" / "hw_templates.json"
LOGS_DIR = BASE_DIR / "data" / "logs"

LOG_BUFFER_MAXLEN = 2000
HEALTH_CHECK_INTERVAL_S = 2.0
HEALTH_CHECK_TIMEOUT_TOTAL_S = 30.0
STOP_GRACE_PERIOD_S = 5.0

# llama-server escribe esta línea cada vez que atiende una tarea sin nada que
# generar, incluida cada lectura de /metrics que hace llm_metrics.py. Se deja
# pasar la primera (marca el fin de un request) y se descartan las repetidas.
IDLE_LOG_MARK = "update_slots: all slots are idle"


def should_forward_log_line(line: str, previous_was_idle: bool) -> tuple[bool, bool]:
    """(reenviar, esta_línea_es_idle). Colapsa idles consecutivos en uno."""
    is_idle = IDLE_LOG_MARK in line
    return (not (is_idle and previous_was_idle), is_idle)


# Rotación por tamaño de data/logs/*.log: desde que llama-server no recibe
# --log-file, _pump_output es el único que escribe esos logs, y un proceso
# que vive mucho tiempo los dejaría crecer sin bound. Se rota a <nombre>.1
# (se pisa la rotación anterior) al abrir el pump y, durante la escritura,
# cada LOG_ROTATE_CHECK_BYTES escritos.
LOG_ROTATE_MAX_BYTES = 10 * 1024 * 1024
LOG_ROTATE_CHECK_BYTES = 256 * 1024


def rotate_log_if_needed(log_path: Path) -> bool:
    """Rota log -> log.1 si supera el tope. Devuelve True si rotó."""
    try:
        if log_path.stat().st_size <= LOG_ROTATE_MAX_BYTES:
            return False
        backup = log_path.with_name(log_path.name + ".1")
        backup.unlink(missing_ok=True)
        log_path.rename(backup)
        return True
    except OSError:
        logger.warning("no se pudo rotar %s", log_path)
        return False


# Nivel de verbosidad de llama-server. 4 = todo el detalle de carga del modelo,
# offload de capas y timings por request, que es lo que se ve en el terminal
# de logs del Launcher.
LLAMA_SERVER_VERBOSITY = 4

# Presets de sampling. Son la fuente de verdad del backend; el frontend tiene
# una copia con los mismos valores para poder previsualizarlos sin round-trip.
SAMPLING_PRESETS: dict[str, dict[str, float | int]] = {
    "thinking": {
        "temperature": 1.0,
        "top_p": 0.95,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 0.0,
        "repeat_penalty": 1.0,
    },
    "instruct": {
        "temperature": 0.7,
        "top_p": 0.80,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 1.5,
        "repeat_penalty": 1.5,
    },
}

# --------------------------------------------------------------------------
# Schema (Pydantic v2)
# --------------------------------------------------------------------------

BackendName = Literal["llama_server", "ollama", "lm_studio"]
GpuMode = Literal["gpu_only", "cpu_only", "hybrid"]
CacheType = Literal["f16", "q8_0", "q4_0", "q4_1"]
RopeScalingType = Literal["none", "linear", "yarn"]
ProcessState = Literal["starting", "running", "stopped", "error"]


class LaunchConfig(BaseModel):
    model_id: str
    backend: BackendName = "llama_server"

    n_ctx: int = 65536
    n_batch: int = 512
    n_ubatch: int = 512

    n_gpu_layers: int = -1
    gpu_mode: GpuMode = "gpu_only"

    cache_type_k: CacheType = "q4_0"
    cache_type_v: CacheType = "q4_0"
    flash_attn: bool = True
    use_mlock: bool = False
    use_mmap: bool = True

    # -- Speculative decoding (MTP / NextN) ------------------------------
    # Dos formas de tener MTP:
    #   - sidecar: un .gguf de draft aparte (mtp-*.gguf) -> mtp_draft_model
    #   - embebido: el propio modelo trae los tensores blk.N.nextn.*
    #     (lo detecta el scanner en models.py) -> mtp_embedded=True, sin path
    mtp_draft_model: str | None = None
    mtp_embedded: bool = False
    n_draft: int = 5
    # None = no se pasa el flag, llama-server usa su default (f16) para el
    # draft. Solo tiene efecto si mtp_draft_model o mtp_embedded están activos.
    cache_type_k_draft: CacheType | None = None
    cache_type_v_draft: CacheType | None = None

    mmproj_path: str | None = None

    lora_path: str | None = None
    lora_scale: float = 1.0

    thinking_enabled: bool = False
    budget_tokens: int = 8192
    
    # -- Jinja + reasoning effort ---------------------------------------
    jinja: bool = True
    reasoning_effort: str = "none"   # "none"|"low"|"medium"|"high"|"xhigh"

    # -- Sampling parameters (defaults del servidor) --------------------
    # Cuando se pasan como flags de arranque, llama-server los usa como
    # defaults para todos los clientes que no especifiquen sus propios valores.
    sampling_preset: str = "instruct"   # "thinking" | "instruct" | "custom"
    temperature: float = 0.7
    top_p: float = 0.80
    top_k: int = 20
    min_p: float = 0.0
    presence_penalty: float = 0.0
    repeat_penalty: float = 1.5

    # -- Rope scaling ---------------------------------------------------
    rope_freq_base: float = 0.0          # 0 = auto
    rope_scaling_type: RopeScalingType = "none"
    yarn_ext_factor: float = -1.0        # -1 = auto

    # -- Optimizaciones avanzadas ---------------------------------------
    numa: bool = False
    no_kv_offload: bool = False
    cache_reuse: int = 0                 # 0-256
    defrag_thold: float = -1.0           # -1 = deshabilitado

    # -- Group attention (sliding window) -------------------------------
    grp_attn_n: int = 1                  # 1 = deshabilitado
    grp_attn_w: int = 512

    host: str = "127.0.0.1"
    port: int = 8080
    n_threads: int = -1
    n_parallel: int = 1
    api_key: str = ""
    log_file: str = "data/logs/llama-server.log"
    template_name: str | None = None

    @model_validator(mode="after")
    def _validate_cross_fields(self) -> "LaunchConfig":
        if self.gpu_mode == "cpu_only" and self.n_gpu_layers != 0:
            # cpu_only siempre implica cero capas en GPU, sin importar lo
            # que haya llegado en n_gpu_layers (evita configs contradictorias).
            self.n_gpu_layers = 0
        if self.gpu_mode == "gpu_only" and self.n_gpu_layers == 0:
            raise ValueError(
                "gpu_mode='gpu_only' es incompatible con n_gpu_layers=0 "
                "(usá -1 para todas las capas, o gpu_mode='hybrid'/'cpu_only')"
            )
        if not (0.0 <= self.lora_scale <= 2.0):
            raise ValueError("lora_scale debe estar entre 0.0 y 2.0")
        if not (1 <= self.n_parallel <= 8):
            raise ValueError("n_parallel debe estar entre 1 y 8")
        if self.lora_path and self.lora_scale is None:
            raise ValueError("lora_scale es obligatorio si se especifica lora_path")
        if not (0 <= self.cache_reuse <= 256):
            raise ValueError("cache_reuse debe estar entre 0 y 256")
        if self.grp_attn_n < 1:
            raise ValueError("grp_attn_n debe ser >= 1 (1 = deshabilitado)")
        if self.grp_attn_n > 1 and self.grp_attn_w < 1:
            raise ValueError("grp_attn_w debe ser >= 1 cuando grp_attn_n > 1")
        # -- rangos de sampling (los mismos que expone la UI) ------------
        if not (0.0 <= self.temperature <= 2.0):
            raise ValueError("temperature debe estar entre 0.0 y 2.0")
        if not (0.0 <= self.top_p <= 1.0):
            raise ValueError("top_p debe estar entre 0.0 y 1.0")
        if self.top_k < 0:
            raise ValueError("top_k debe ser >= 0 (0 = deshabilitado)")
        if not (0.0 <= self.min_p <= 1.0):
            raise ValueError("min_p debe estar entre 0.0 y 1.0")
        return self


class HWTemplate(BaseModel):
    name: str
    builtin: bool = False
    params: dict[str, Any] = Field(default_factory=dict)


class ProcessInfo(BaseModel):
    process_id: str
    model_id: str
    model_name: str
    backend: BackendName
    state: ProcessState
    pid: int | None = None
    host: str
    port: int
    started_at: str | None = None
    error_message: str | None = None
    launch_config: dict[str, Any]


# --------------------------------------------------------------------------
# Templates (persistidos en SQLite, tabla hw_templates)
# --------------------------------------------------------------------------


class TemplateStore:
    """
    Fachada fina sobre las funciones de templates de database.py.

    Mantiene la misma superficie de API que la versión anterior (basada en
    hw_templates.json) para no romper a quien la llame, pero ya no guarda
    estado en memoria: cada método va a la DB. `self.path` se conserva solo
    por compatibilidad con código/tests que lo referencian.
    """

    def __init__(self, path: Path = TEMPLATES_PATH) -> None:
        self.path = path

    async def ensure_loaded(self) -> None:
        """No-op: la semilla de templates la hace init_db() al arrancar."""
        return None

    async def list(self) -> list[HWTemplate]:
        return [HWTemplate(**row) for row in await db_list_templates()]

    async def get(self, name: str) -> HWTemplate | None:
        row = await db_get_template(name)
        return HWTemplate(**row) if row is not None else None

    async def upsert(self, tpl: HWTemplate) -> HWTemplate:
        saved = await db_upsert_template(tpl.name, tpl.builtin, tpl.params)
        return HWTemplate(**saved)

    async def delete(self, name: str) -> bool:
        # db_delete_template ya retorna False para builtin o inexistente.
        return await db_delete_template(name)


templates = TemplateStore()

# --------------------------------------------------------------------------
# Construcción de comandos
# --------------------------------------------------------------------------


def resolve_log_path(log_file: str, process_id: str) -> Path:
    # C3: log_file es el archivo donde _pump_output guarda la salida del
    # proceso (el launcher es el único que lo escribe: ver
    # build_llama_server_command), así que se valida en start() antes de usarlo. Se resuelve contra BASE_DIR para bloquear path traversal
    # ("../../etc/x" o absolutos fuera del proyecto) -> 400.
    if not log_file:
        return LOGS_DIR / f"{process_id}.log"
    log_path = (BASE_DIR / log_file).resolve()
    try:
        log_path.relative_to(BASE_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"log_file debe quedar dentro de {BASE_DIR}: {log_file}",
        ) from exc
    return log_path


def build_llama_server_command(cfg: LaunchConfig, binary_path: str, model_path: str) -> list[str]:
    cmd: list[str] = [
        binary_path,
        "--model", model_path,
        "--ctx-size", str(cfg.n_ctx),
        "--batch-size", str(cfg.n_batch),
        "--ubatch-size", str(cfg.n_ubatch),
        "--n-gpu-layers", str(cfg.n_gpu_layers),
        "--cache-type-k", cfg.cache_type_k,
        "--cache-type-v", cfg.cache_type_v,
    ]

    if cfg.flash_attn:
        cmd.extend(["--flash-attn", "on"])
    else:
        cmd.extend(["--flash-attn", "off"])
    if cfg.jinja:
        cmd.append("--jinja")
    if cfg.reasoning_effort != "none":
        import json
        cmd += ["--chat-template-kwargs",
            json.dumps({"reasoning_effort": cfg.reasoning_effort})]
    if cfg.use_mlock:
        cmd.append("--mlock")
    if not cfg.use_mmap:
        cmd.append("--no-mmap")
    # -- speculative decoding (MTP / NextN) ------------------------------
    # Nombres de flags según `llama-server --help` (build 2026-09):
    #   --spec-type draft-mtp     activa el modo de especulación MTP
    #   --spec-draft-model FNAME  modelo de draft (alias -md / --model-draft);
    #                             SOLO cuando la cabeza viene en un archivo aparte
    #   --spec-draft-n-max N      tokens a especular por paso
    # Ojo: --draft-model nunca existió y --draft/--draft-n/--draft-max fueron
    # removidos del binario ("use --spec-draft-n-max"), por eso no se usan.
    if cfg.mtp_draft_model or cfg.mtp_embedded:
        cmd += ["--spec-type", "draft-mtp"]
        if cfg.mtp_draft_model:
            cmd += ["--spec-draft-model", cfg.mtp_draft_model]
        # Con mtp_embedded y sin path, el propio --model ya trae los tensores
        # nextn: no se pasa ningún archivo extra.
        cmd += ["--spec-draft-n-max", str(cfg.n_draft)]
        # cache_type_k/v del DRAFT son flags distintos de los del modelo
        # principal (--spec-draft-type-k/-v, alias -ctkd/-ctvd) — por default
        # llama-server usa f16 para el draft aunque el principal esté en q4_0.
        if cfg.cache_type_k_draft:
            cmd += ["--spec-draft-type-k", cfg.cache_type_k_draft]
        if cfg.cache_type_v_draft:
            cmd += ["--spec-draft-type-v", cfg.cache_type_v_draft]
    if cfg.mmproj_path:
        cmd += ["--mmproj", cfg.mmproj_path]
    if cfg.lora_path:
        cmd += ["--lora", cfg.lora_path, "--lora-scale", str(cfg.lora_scale)]

    # -- sampling defaults del servidor ---------------------------------
    # Van siempre: son los valores que llama-server aplica a cualquier
    # request que no traiga los suyos (el Chat sí manda los propios).
    cmd += ["--temp", str(cfg.temperature)]
    cmd += ["--top-p", str(cfg.top_p)]
    cmd += ["--top-k", str(cfg.top_k)]
    cmd += ["--min-p", str(cfg.min_p)]
    if cfg.presence_penalty != 0.0:
        cmd += ["--presence-penalty", str(cfg.presence_penalty)]
    cmd += ["--repeat-penalty", str(cfg.repeat_penalty)]

    # -- parámetros avanzados (solo si difieren del default "apagado") ---
    if cfg.rope_freq_base > 0:
        cmd += ["--rope-freq-base", str(cfg.rope_freq_base)]
    if cfg.rope_scaling_type != "none":
        cmd += ["--rope-scaling", cfg.rope_scaling_type]
    if cfg.rope_scaling_type == "yarn" and cfg.yarn_ext_factor >= 0:
        cmd += ["--yarn-ext-factor", str(cfg.yarn_ext_factor)]
    if cfg.numa:
        cmd.append("--numa")
    if cfg.no_kv_offload:
        cmd.append("--no-kv-offload")
    if cfg.cache_reuse > 0:
        cmd += ["--cache-reuse", str(cfg.cache_reuse)]
    if cfg.defrag_thold >= 0:
        cmd += ["--defrag-thold", str(cfg.defrag_thold)]
    if cfg.grp_attn_n > 1:
        cmd += ["--grp-attn-n", str(cfg.grp_attn_n)]
        cmd += ["--grp-attn-w", str(cfg.grp_attn_w)]

    cmd += [
        "--threads", str(cfg.n_threads),
        "--parallel", str(cfg.n_parallel),
        "--host", cfg.host,
        "--port", str(cfg.port),
    ]

    if cfg.api_key:
        cmd += ["--api-key", cfg.api_key]
    # Sin --log-file a propósito: _pump_output ya guarda la salida en
    # resolve_log_path(cfg.log_file). Con el flag, llama-server escribía el
    # mismo archivo en paralelo (líneas duplicadas, sin el filtro de idle) y
    # además resolvía la ruta relativa contra su propio directorio de trabajo.

    cmd += ["--verbosity", str(LLAMA_SERVER_VERBOSITY)]

    # Endpoint Prometheus /metrics: lo lee llm_metrics.py para la tira de
    # vitales del Launcher y el histórico del Monitor. Ollama y LM Studio no
    # lo tienen; hoy esta función solo se llama para llama_server, pero la
    # condición queda explícita por si se reutiliza.
    if cfg.backend == "llama_server":
        cmd.append("--metrics")

    return cmd


def build_ollama_command(binary_path: str, model_name: str) -> list[str]:
    # Equivalente pedido por el spec. En un uso real de servidor se separaría
    # `ollama serve` (daemon) de `ollama run <modelo>` (cliente interactivo),
    # pero seguimos la instrucción tal cual para este módulo.
    return [binary_path, "run", model_name]


# --------------------------------------------------------------------------
# Gestión de procesos (asyncio nativo)
# --------------------------------------------------------------------------


class ModelProcessManager:
    def __init__(self) -> None:
        self._info: dict[str, ProcessInfo] = {}
        self._handles: dict[str, asyncio.subprocess.Process] = {}
        self._log_buffers: dict[str, deque[str]] = {}
        self._log_subscribers: dict[str, list[asyncio.Queue]] = {}
        self._start_monotonic: dict[str, float] = {}
        self._stopping: set[str] = set()  # ids detenidos intencionalmente
        self._background_tasks: set[asyncio.Task] = set()

    def _spawn_background(self, coro) -> asyncio.Task:
        """asyncio.create_task + tracking, para poder cancelar todo en shutdown()
        (evita 'Task was destroyed but it is pending' al cerrar el event loop,
        tanto en producción como en tests)."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def shutdown(self) -> None:
        """Cancela y espera todas las tasks de background activas (pump/watch/health).
        Pensado para el lifespan de la app y para el teardown de tests."""
        tasks = list(self._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # -- consultas -----------------------------------------------------

    def status(self, process_id: str) -> ProcessInfo | None:
        return self._info.get(process_id)

    def status_all(self) -> list[ProcessInfo]:
        return list(self._info.values())

    def uptime_s(self, process_id: str) -> float | None:
        start = self._start_monotonic.get(process_id)
        if start is None:
            return None
        return round(time.monotonic() - start, 1)

    def get_logs(self, process_id: str, last_n_lines: int = 100) -> list[str]:
        buf = self._log_buffers.get(process_id)
        if buf is None:
            return []
        return list(buf)[-last_n_lines:]

    # -- suscripción de logs en vivo (para el WebSocket) ---------------

    def subscribe_logs(self, process_id: str) -> tuple[asyncio.Queue, list[str]]:
        queue: asyncio.Queue = asyncio.Queue()
        self._log_subscribers.setdefault(process_id, []).append(queue)
        snapshot = list(self._log_buffers.get(process_id, []))
        return queue, snapshot

    def unsubscribe_logs(self, process_id: str, queue: asyncio.Queue) -> None:
        subs = self._log_subscribers.get(process_id)
        if subs and queue in subs:
            subs.remove(queue)

    def _publish_log_line(self, process_id: str, line: str) -> None:
        buf = self._log_buffers.setdefault(process_id, deque(maxlen=LOG_BUFFER_MAXLEN))
        buf.append(line)
        for queue in self._log_subscribers.get(process_id, []):
            queue.put_nowait(line)

    # -- health check ----------------------------------------------------

    @staticmethod
    async def health_check(host: str, port: int) -> bool:
        url = f"http://{host}:{port}/health"
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                res = await client.get(url)
                return res.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError):
            return False

    async def _monitor_health(self, process_id: str) -> None:
        info = self._info.get(process_id)
        if info is None:
            return
        elapsed = 0.0
        while elapsed < HEALTH_CHECK_TIMEOUT_TOTAL_S:
            if process_id not in self._info or self._info[process_id].state == "stopped":
                return
            if await self.health_check(info.host, info.port):
                current = self._info.get(process_id)
                if current and current.state == "starting":
                    current.state = "running"
                return
            await asyncio.sleep(HEALTH_CHECK_INTERVAL_S)
            elapsed += HEALTH_CHECK_INTERVAL_S

        current = self._info.get(process_id)
        if current and current.state == "starting":
            current.state = "error"
            current.error_message = (
                f"Health check no respondió 200 en {HEALTH_CHECK_TIMEOUT_TOTAL_S:.0f}s"
            )

    # -- lectura de stdout/stderr combinados -----------------------------

    async def _pump_output(self, process_id: str, process: asyncio.subprocess.Process, log_path: Path) -> None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        assert process.stdout is not None
        previous_was_idle = False
        log_fh = None
        written_since_check = 0
        try:
            while True:
                if log_fh is None:
                    rotate_log_if_needed(log_path)
                    log_fh = await aiofiles.open(log_path, "a", encoding="utf-8")
                raw_line = await process.stdout.readline()
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
                forward, previous_was_idle = should_forward_log_line(line, previous_was_idle)
                if not forward:
                    continue
                self._publish_log_line(process_id, line)
                await log_fh.write(line + "\n")
                written_since_check += len(line) + 1
                if written_since_check >= LOG_ROTATE_CHECK_BYTES:
                    written_since_check = 0
                    try:
                        if log_path.stat().st_size > LOG_ROTATE_MAX_BYTES:
                            await log_fh.flush()
                            await log_fh.close()
                            log_fh = None  # rota y reabre en la siguiente vuelta
                    except OSError:
                        pass
        finally:
            if log_fh is not None:
                await log_fh.close()

    async def _watch_exit(self, process_id: str, process: asyncio.subprocess.Process) -> None:
        returncode = await process.wait()
        info = self._info.get(process_id)
        if info is None:
            return
        self._handles.pop(process_id, None)
        if process_id in self._stopping:
            info.state = "stopped"
            info.pid = None
            self._stopping.discard(process_id)
            logger.info(
                "proceso detenido: id=%s backend=%s model=%s",
                process_id, info.backend, info.model_name,
            )
        else:
            info.state = "error"
            info.pid = None
            info.error_message = f"El proceso terminó inesperadamente (código {returncode})"
            self._publish_log_line(
                process_id, f"[glyvex] proceso terminado inesperadamente, código {returncode}"
            )
            logger.error(
                "proceso termino inesperadamente: id=%s backend=%s model=%s codigo=%s",
                process_id, info.backend, info.model_name, returncode,
            )

    # -- start / stop / restart ------------------------------------------

    async def start(self, cfg: LaunchConfig, process_id: str | None = None) -> ProcessInfo:
        model = model_inventory.get(cfg.model_id)
        if model is None:
            raise HTTPException(status_code=404, detail="Modelo no encontrado en el inventario")

        process_id = process_id or str(uuid4())

        if cfg.backend == "lm_studio":
            # LM Studio corre su propio servidor: solo verificamos que responda.
            is_up = await self.health_check(cfg.host, cfg.port)
            info = ProcessInfo(
                process_id=process_id,
                model_id=cfg.model_id,
                model_name=model.name,
                backend=cfg.backend,
                state="running" if is_up else "error",
                pid=None,
                host=cfg.host,
                port=cfg.port,
                started_at=datetime.now(timezone.utc).isoformat(),
                error_message=None if is_up else "LM Studio no responde en ese host/puerto",
                launch_config=cfg.model_dump(),
            )
            logger.info(
                "start lm_studio: model=%s host=%s port=%s up=%s",
                model.name, cfg.host, cfg.port, is_up,
            )
            self._info[process_id] = info
            self._start_monotonic[process_id] = time.monotonic()
            return info

        # C3: validar ANTES de lanzar: _pump_output escribe en log_path.
        log_path = resolve_log_path(cfg.log_file, process_id)

        if cfg.backend == "llama_server":
            binary_path = config.get("backends.llama_server.binary_path")
            if not binary_path:
                raise HTTPException(
                    status_code=400,
                    detail="No hay binary_path configurado para llama_server (ver Config)",
                )
            cmd = build_llama_server_command(cfg, binary_path, model.path)
        elif cfg.backend == "ollama":
            binary_path = config.get("backends.ollama.binary_path") or "/usr/bin/ollama"
            cmd = build_ollama_command(binary_path, model.name)
        else:
            raise HTTPException(status_code=400, detail=f"Backend desconocido: {cfg.backend}")

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except (FileNotFoundError, PermissionError, NotImplementedError, OSError) as exc:
            raise HTTPException(
                status_code=400, detail=f"No se pudo lanzar el proceso: {exc}"
            ) from exc

        logger.info(
            "start %s: model=%s pid=%s",
            cfg.backend, model.name, process.pid,
        )

        info = ProcessInfo(
            process_id=process_id,
            model_id=cfg.model_id,
            model_name=model.name,
            backend=cfg.backend,
            state="starting",
            pid=process.pid,
            host=cfg.host,
            port=cfg.port,
            started_at=datetime.now(timezone.utc).isoformat(),
            error_message=None,
            launch_config=cfg.model_dump(),
        )
        self._info[process_id] = info
        self._handles[process_id] = process
        self._start_monotonic[process_id] = time.monotonic()
        self._log_buffers[process_id] = deque(maxlen=LOG_BUFFER_MAXLEN)

        self._spawn_background(self._pump_output(process_id, process, log_path))
        self._spawn_background(self._watch_exit(process_id, process))
        self._spawn_background(self._monitor_health(process_id))

        return info

    async def stop(self, process_id: str) -> ProcessInfo:
        info = self._info.get(process_id)
        if info is None:
            raise HTTPException(status_code=404, detail="Proceso no encontrado")

        process = self._handles.get(process_id)
        logger.info(
            "stop: id=%s pid=%s", process_id, process.pid if process else None
        )
        if process is None:
            # lm_studio (no administramos el proceso) u otro ya finalizado.
            info.state = "stopped"
            info.pid = None
            return info

        self._stopping.add(process_id)
        try:
            process.terminate()  # SIGTERM (en Windows: TerminateProcess)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=STOP_GRACE_PERIOD_S)
        except asyncio.TimeoutError:
            try:
                process.kill()  # SIGKILL
            except ProcessLookupError:
                pass
            await process.wait()

        # _watch_exit (corriendo en paralelo) es quien setea el estado final
        # a "stopped" al detectar que process_id está en self._stopping.
        return self._info[process_id]

    async def restart(self, process_id: str) -> ProcessInfo:
        info = self._info.get(process_id)
        if info is None:
            raise HTTPException(status_code=404, detail="Proceso no encontrado")
        logger.info("restart: id=%s", process_id)
        cfg = LaunchConfig(**info.launch_config)
        if info.pid is not None:
            await self.stop(process_id)
        return await self.start(cfg, process_id=process_id)


manager = ModelProcessManager()

# --------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------

router = APIRouter()


@router.get("/sampling-presets")
async def list_sampling_presets() -> dict[str, dict[str, float | int]]:
    """Presets de sampling del backend (fuente de verdad de los valores)."""
    return SAMPLING_PRESETS


@router.get("/templates", response_model=list[HWTemplate])
async def list_templates() -> list[HWTemplate]:
    await templates.ensure_loaded()
    return await templates.list()


@router.post("/templates", response_model=HWTemplate)
async def save_template(template: HWTemplate) -> HWTemplate:
    await templates.ensure_loaded()
    return await templates.upsert(template)


@router.delete("/templates/{name}")
async def delete_template(name: str) -> dict[str, bool]:
    await templates.ensure_loaded()
    existing = await templates.get(name)
    if existing is None:
        raise HTTPException(status_code=404, detail="Template no encontrado")
    if existing.builtin:
        raise HTTPException(
            status_code=403,
            detail="Los templates predefinidos no se pueden eliminar",
        )
    deleted = await templates.delete(name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Template no encontrado")
    return {"deleted": True}


@router.post("/launch", response_model=ProcessInfo)
async def launch(cfg: LaunchConfig) -> ProcessInfo:
    await model_inventory.ensure_loaded()
    return await manager.start(cfg)


@router.post("/stop/{process_id}", response_model=ProcessInfo)
async def stop(process_id: str) -> ProcessInfo:
    return await manager.stop(process_id)


@router.post("/restart/{process_id}", response_model=ProcessInfo)
async def restart(process_id: str) -> ProcessInfo:
    return await manager.restart(process_id)


@router.get("/status", response_model=list[ProcessInfo])
async def status_all() -> list[ProcessInfo]:
    return manager.status_all()


@router.get("/status/{process_id}", response_model=ProcessInfo)
async def status_one(process_id: str) -> ProcessInfo:
    info = manager.status(process_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Proceso no encontrado")
    return info


@router.get("/logs/{process_id}")
async def get_logs(process_id: str) -> dict[str, Any]:
    if manager.status(process_id) is None:
        raise HTTPException(status_code=404, detail="Proceso no encontrado")
    return {"process_id": process_id, "lines": manager.get_logs(process_id, last_n_lines=200)}


@router.websocket("/logs/{process_id}/stream")
async def logs_stream(websocket: WebSocket, process_id: str) -> None:
    await websocket.accept()
    if manager.status(process_id) is None:
        await websocket.send_json({"error": "Proceso no encontrado"})
        await websocket.close()
        return
    queue, buffered = manager.subscribe_logs(process_id)
    try:
        for line in buffered:
            await websocket.send_text(line)
        while True:
            line = await queue.get()
            await websocket.send_text(line)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        manager.unsubscribe_logs(process_id, queue)
