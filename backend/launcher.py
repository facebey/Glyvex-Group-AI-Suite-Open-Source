"""
launcher.py — Lanzamiento y control de modelos LLM locales (módulo M2).

Responsabilidades:
- Schema Pydantic v2 de parámetros de lanzamiento (LaunchConfig) con
  validación cruzada (gpu_mode <-> n_gpu_layers, rangos de lora_scale/n_parallel,
  combinaciones de KV cache válidas con Flash Attention) y parámetros
  avanzados (rope scaling, NUMA, cache reuse, checkpoints de contexto).
- Sampling parameters de arranque (temp, top_p, top_k, min_p, penalties) con
  presets "thinking" / "instruct": llama-server los usa como defaults para
  todo cliente que no mande los suyos en el request.
- Templates de hardware predefinidos + custom, persistidos en SQLite
  (tabla hw_templates, ver database.py). El JSON data/templates/hw_templates.json
  queda solo como semilla de la primera carga.
- Construcción del comando de llama-server / ollama a partir de LaunchConfig,
  con PROBE de capacidades del binario: se lee `--help` una vez por build y se
  descartan los flags que esa build no conoce (con warning), para que la app
  funcione contra builds distintas sin romperse.
- Gestión de procesos con asyncio.create_subprocess_exec (NUNCA
  subprocess.Popen dentro de código async), incluyendo:
    - captura de stdout/stderr combinados hacia un log en disco + buffer
      en memoria + fan-out a suscriptores WebSocket ("tail -f" en vivo).
    - health check async (httpx) con polling cada 2s / timeout total 30s.
    - stop con SIGTERM y escalamiento a SIGKILL si no responde en 5s.
    - detección de caída inesperada del proceso -> estado "error".

Notas de compatibilidad verificadas contra las builds b11003-b11009 (2026) y
re-probadas contra b11146 (v0.5.0, 2026-09-24, PLAN-LLAMA-BUMP-V0-5-0.md T2.1):
- b11146: los 53 flags que emite el launcher siguen presentes, EXCEPTO:
  - --lora-scale REMOVIDO → --lora-scaled PATH:SCALE (feature-detect por
    probe; ver build_llama_server_command).
  - --grp-attn-n/--grp-attn-w REMOVIDOS del server (solo se emiten con
    grp_attn_n > 1, default 1 = off; el probe los descarta con warning).
- Desde b11003-b11009:
- --flash-attn acepta on|off|auto (NO 1/0).
- Con Flash Attention, el KV cache solo admite pares simétricos:
  q4_0-q4_0, q8_0-q8_0, f16-f16, bf16-bf16 (línea FA_QUANTS del log).
- --numa EXIGE valor: distribute|isolate|numactl.
- Checkpoints de contexto: -ctxcp/--ctx-checkpoints (default 32) y
  -cms/--checkpoint-min-step (default 8192). Cada checkpoint cuesta
  ~150 MiB de VRAM en arquitecturas híbridas (estado recurrente SSM);
  en ctx largos son el componente que domina el consumo post-carga.
- --cache-ram <MiB> limita el prompt cache en RAM (0 lo desactiva).
- --fit-target <MiB> reserva margen de VRAM y auto-ajusta el ctx.
- b11007 arregla la recaptura de CUDA graph en speculative decoding MTP
  (~4-5% en decode con --spec-type draft-mtp). Es un fix interno: no cambia
  ni agrega flags, así que basta con actualizar el binario.
- El puerto default del server migrará de 8080 a 9931 en el futuro:
  nunca hardcodear, siempre salir de cfg.port / config de backends.
"""

from __future__ import annotations

import asyncio
import logging
import re
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
from paths import BUNDLE_DIR, DATA_DIR
from database import (
    db_delete_template,
    db_get_template,
    db_list_templates,
    db_upsert_template,
)
from models import get_entry_metadata, inventory as model_inventory
from runtime import resolve_binary

logger = logging.getLogger("glyvex.launcher")

# Semilla versionada en el repo (no está en .gitignore): son los templates
# predefinidos que init_db() inserta en la DB de CADA instancia al arrancar.
# Es código, no estado, así que cuelga de BUNDLE_DIR y se comparte. Si colgara
# de DATA_DIR, una instancia nueva arrancaría sin ningún template predefinido.
TEMPLATES_PATH = BUNDLE_DIR / "data" / "templates" / "hw_templates.json"
LOGS_DIR = DATA_DIR / "logs"

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
# "instruct" usa penalties moderados: repeat_penalty 1.5 / presence 1.5
# degeneran la salida en la familia Qwen (especialmente código).
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
        "presence_penalty": 0.3,
        "repeat_penalty": 1.1,
    },
}

# Combinaciones de KV cache que el kernel de Flash Attention acepta en las
# builds recientes (FA_QUANTS del log de arranque). Con flash_attn=on, K y V
# deben ser simétricos y estar en esta lista; cualquier otra combinación
# termina en crash o fallback silencioso a f16.
FA_KV_WHITELIST = frozenset({
    ("q4_0", "q4_0"),
    ("q8_0", "q8_0"),
    ("f16", "f16"),
    ("bf16", "bf16"),
})

# ---------------------------------------------------------------------------
# Schema (Pydantic v2)
# ---------------------------------------------------------------------------

BackendName = Literal["llama_server", "ollama", "lm_studio"]
GpuMode = Literal["gpu_only", "cpu_only", "hybrid"]
# bf16 agregado: está en FA_QUANTS. q4_1 NO: con Flash Attention rechazado.
CacheType = Literal["f16", "bf16", "q8_0", "q4_0"]
RopeScalingType = Literal["none", "linear", "yarn"]
ProcessState = Literal["starting", "running", "stopped", "error"]
# Reemplaza a los viejos flags --mlock/--no-mmap/--mmap, que ya no existen
# como tales en builds recientes (unificados en --load-mode, ver
# build_llama_server_command). Valores tal cual los acepta el binario.
LoadMode = Literal["auto", "none", "mmap", "mlock", "mmap+mlock", "dio"]
# Lectura bajo demanda de ciertos tensores (p. ej. embeddings por capa) para
# ahorrar VRAM residente. auto = solo tensores >4GiB (default del binario).
LazyMode = Literal["auto", "on", "off"]

# Migración de configs/templates guardados con tipos de KV que ya no existen
# en el schema. Sin esto, un hw_template viejo en SQLite con cache_type_k
# "q4_1" hace fallar el POST /launch con un 422 de Pydantic que el usuario no
# puede arreglar desde la UI. Se corrige en silencio (con warning) en vez de
# romper. OJO: debe vivir a nivel de módulo — como atributo de clase con
# guion bajo Pydantic lo convierte en ModelPrivateAttr y no es indexable.
LEGACY_CACHE_TYPES: dict[str, str] = {"q4_1": "q4_0", "q5_0": "q8_0", "q5_1": "q8_0"}


class LaunchConfig(BaseModel):
    model_id: str
    backend: BackendName = "llama_server"
    # P1.5: campos con toggle. None = toggle OFF -> el builder no emite el
    # flag -> llama-server usa el default de la build (para benchmarks finos).
    n_ctx: int | None = 65536
    # 2048 (default de llama.cpp) rinde bastante más en prompt processing que
    # 512 en GPUs anchas (medido: pp2048 ~1400 t/s con batch 2048). El ubatch
    # se queda en 512 para no inflar el compute buffer.
    n_batch: int | None = 2048
    # Extensión del alcance P1.5 (2026-09-20): también con toggle. None = no
    # se emite --ubatch-size -> default de la build.
    n_ubatch: int | None = 512
    n_gpu_layers: int = -1
    gpu_mode: GpuMode = "gpu_only"
    cache_type_k: CacheType | None = "q4_0"
    cache_type_v: CacheType | None = "q4_0"
    flash_attn: bool = True
    load_mode: LoadMode = "auto"

    # -- Speculative decoding (MTP / NextN) ------------------------------
    # Dos formas de tener MTP:
    #   - sidecar: un .gguf de draft aparte (mtp-*.gguf) -> mtp_draft_model
    #   - embebido: el propio modelo trae los tensores blk.N.nextn.*
    #     (lo detecta el scanner en models.py) -> mtp_embedded=True, sin path
    mtp_draft_model: str | None = None
    mtp_embedded: bool = False
    n_draft: int = 5
    # KV cache del DRAFT. None = no se pasa el flag (llama-server usa f16,
    # que quema VRAM sin necesidad). Default q8_0: mitad de VRAM que f16 con
    # aceptación prácticamente idéntica. Solo aplica con MTP activo.
    cache_type_k_draft: CacheType | None = "q8_0"
    cache_type_v_draft: CacheType | None = "q8_0"
    mmproj_path: str | None = None
    lora_path: str | None = None
    lora_scale: float = 1.0

    # -- Reasoning / thinking --------------------------------------------
    # thinking_enabled se cablea vía el flag nativo --reasoning on/off.
    # El viejo kwarg enable_thinking en --chat-template-kwargs quedó deprecado
    # en llama.cpp (warning en el log del server) y hacía lo mismo.
    # El prefijo "/think" del chat/benchmark es el otro lever, a nivel de prompt.
    thinking_enabled: bool = False
    # -1 = sin límite (no se envía). >0 se pasa al flag nativo
    # --reasoning-budget (solo con thinking enabled y si reasoning_budget
    # es -1: ese campo, al ser explícito, tiene prioridad).
    budget_tokens: int = 8192
    jinja: bool = True
    reasoning_effort: str = "none"   # "none"|"low"|"medium"|"high"|"xhigh"
    # Si el template soporta reasoning preservado, la build 11003 lo activa
    # por defecto y gasta tokens extra re-emitiendo el razonamiento en cada
    # turno. Este flag lo apaga.
    no_reasoning_preserve: bool = False
    # Token budget nativo del server para el razonamiento (b11009).
    # -1 = sin límite (no se envía el flag), 0 = fin inmediato del thinking.
    # Es el control por flag, distinto de budget_tokens (que va por
    # chat_template_kwargs solo con thinking enabled).
    reasoning_budget: int = -1           # --reasoning-budget

    # -- Sampling parameters (defaults del servidor) --------------------
    # Cuando se pasan como flags de arranque, llama-server los usa como
    # defaults para todos los clientes que no especifiquen sus propios valores.
    sampling_preset: str = "instruct"   # "thinking" | "instruct" | "custom"
    temperature: float = 0.7
    top_p: float = 0.80
    top_k: int = 20
    min_p: float = 0.0
    presence_penalty: float = 0.3
    repeat_penalty: float = 1.1

    # -- Rope scaling ---------------------------------------------------
    # Toggle agrupado (subsistema RoPE): OFF = los tres None -> ni un solo
    # flag --rope-*/--yarn-*.
    rope_freq_base: float | None = 0.0          # 0 = auto
    rope_scaling_type: RopeScalingType | None = "none"
    yarn_ext_factor: float | None = -1.0        # -1 = auto

    # -- Checkpoints de contexto (VRAM en arquitecturas híbridas/SSM) ----
    # Cada checkpoint = ~150 MiB de VRAM (copia del estado recurrente).
    # Defaults de la build 11003: 32 / 8192 -> hasta 16 checkpoints en un
    # ctx de 128k (~2.4 GiB). Estos defaults los acotan: con -cms 16384 el
    # reproceso máximo al regenerar un mensaje viejo es de ~16k tokens
    # (~12-16 s a pp ~1300 t/s), casi nunca perceptible.
    ctx_checkpoints: int | None = 8      # -ctxcp / --ctx-checkpoints
    checkpoint_min_step: int | None = 16384     # -cms  / --checkpoint-min-step

    # -- Prompt cache (RAM del sistema, no VRAM) -------------------------
    # Límite del caché de prompts ociosos en RAM. 0 lo desactiva. Con 64 GB
    # de RAM sobra para subirlo y acelerar el switch entre conversaciones.
    cache_ram_mib: int | None = 8192     # --cache-ram

    # -- Ajuste automático de VRAM ----------------------------------------
    # >0: le pide a llama.cpp reservar ese margen de VRAM por dispositivo y
    # auto-reducir lo que haga falta (ctx incluido). Ideal para una app que
    # carga modelos arbitrarios sin conocer su tamaño de antemano.
    fit_target_mib: int | None = 0       # --fit-target (0 = off)
    # --fit [on|off]: llama.cpp ajusta los argumentos que el usuario NO fijó
    # para caber en la memoria del dispositivo (default de b11009: on).
    # True -> --fit on, False -> --fit off (enviar el flag es obligatorio:
    # si falta, la build usa su default 'on' y fit queda activo igual).
    # None = no se emite el flag (modo manual).
    fit: bool | None = True              # --fit on|off

    # -- Optimizaciones avanzadas ---------------------------------------
    numa: bool = False
    no_kv_offload: bool = False
    cache_reuse: int | None = 0          # 0-256
    defrag_thold: float | None = -1.0    # -1 = deshabilitado
    # Unifica el KV cache entre slots. Con n_parallel > 1 y kv_unified en
    # false, CADA slot reserva su propio ctx completo (x N de VRAM de KV).
    kv_unified: bool = False             # --kv-unified
    # Límite de contexto por slot paralelo cuando kv_unified está activo.
    # 0 = sin límite (comportamiento previo, cada slot usa el n_ctx global).
    kv_unified_per_slot: int = 0         # --kv-unified-per-slot
    # El server se "duerme" (libera VRAM) tras N segundos de inactividad.
    # 0 = deshabilitado. Útil para soltar VRAM y cargar otro modelo.
    sleep_idle_seconds: int = 0          # --sleep-idle-seconds
    # Warmup con una corrida vacía al arrancar (default del binario: on).
    warmup: bool = True                  # --warmup / --no-warmup
    # Lectura bajo demanda de tensores grandes (ver LazyMode). auto = default.
    lazy_mode: LazyMode = "auto"         # --lazy-mode

    # -- Group attention (sliding window) -------------------------------
    # Ojo: flag deprecado/removido en varias builds. El probe de flags lo
    # descarta con warning si el binario no lo conoce.
    grp_attn_n: int | None = 1           # 1 = deshabilitado (agrupado con w)
    grp_attn_w: int | None = 512
    # -- Modo automático (P1.5 F4) ----------------------------------------
    # Comando estricto: solo modelo + puerto (+ host si difiere de
    # 127.0.0.1). Todo lo demás usa el default de la build. Se conserva la
    # infra de la app: --verbosity (logs) y --metrics (Monitor).
    auto_mode: bool = False
    host: str = "127.0.0.1"
    port: int = 8080
    n_threads: int | None = -1           # -1 o 0 = auto (no se pasa el flag)
    n_parallel: int | None = 1
    api_key: str = ""
    log_file: str = "data/logs/llama-server.log"
    template_name: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_cache_types(cls, data: Any) -> Any:
        """Migra tipos de KV que ya no existen en el schema (ver
        LEGACY_CACHE_TYPES): un template viejo no debe romper el lanzamiento."""
        if not isinstance(data, dict):
            return data
        for field in ("cache_type_k", "cache_type_v",
                      "cache_type_k_draft", "cache_type_v_draft"):
            value = data.get(field)
            replacement = LEGACY_CACHE_TYPES.get(value) if isinstance(value, str) else None
            if replacement:
                logger.warning(
                    "config guardada con %s=%s (ya no soportado con Flash "
                    "Attention): se migra a %s", field, value, replacement,
                )
                data[field] = replacement
        return data

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_load_mode(cls, data: Any) -> Any:
        """Migra use_mlock/use_mmap (campos removidos, ver LoadMode) a un
        único load_mode: un template guardado antes de este cambio no debe
        romper el lanzamiento ni perder silenciosamente la intención del
        usuario (ej. alguien que había activado mlock a propósito)."""
        if not isinstance(data, dict):
            return data
        if "use_mlock" in data or "use_mmap" in data:
            use_mlock = bool(data.pop("use_mlock", False))
            use_mmap = bool(data.pop("use_mmap", True))
            if use_mlock and use_mmap:
                load_mode = "mmap+mlock"
            elif use_mlock and not use_mmap:
                load_mode = "mlock"
            elif not use_mlock and not use_mmap:
                load_mode = "none"
            else:
                load_mode = "auto"
            logger.warning(
                "config guardada con use_mlock=%s/use_mmap=%s (flags "
                "removidos del binario): se migra a load_mode=%s",
                use_mlock, use_mmap, load_mode,
            )
            data.setdefault("load_mode", load_mode)
        return data

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
        # Los campos con toggle pueden ser None (OFF -> default de build):
        # solo se valida el rango cuando hay valor.
        if self.n_parallel is not None and not (1 <= self.n_parallel <= 8):
            raise ValueError("n_parallel debe estar entre 1 y 8")
        if self.cache_reuse is not None and not (0 <= self.cache_reuse <= 256):
            raise ValueError("cache_reuse debe estar entre 0 y 256")
        if self.grp_attn_n is not None:
            if self.grp_attn_n < 1:
                raise ValueError("grp_attn_n debe ser >= 1 (1 = deshabilitado)")
            if self.grp_attn_n > 1 and (self.grp_attn_w is None or self.grp_attn_w < 1):
                raise ValueError("grp_attn_w debe ser >= 1 cuando grp_attn_n > 1")
        # -- KV cache vs Flash Attention (FA_QUANTS de la build) ---------
        # Con FA on solo existen los pares simétricos de FA_KV_WHITELIST.
        # Si algún tipo es None (toggle OFF) la build usa f16, que es
        # FA-compatible: solo se valida con ambos fijados.
        if (self.flash_attn and self.cache_type_k is not None
                and self.cache_type_v is not None
                and (self.cache_type_k, self.cache_type_v) not in FA_KV_WHITELIST):
            raise ValueError(
                f"Con flash_attn=on el KV cache debe ser simétrico y estar en "
                f"{sorted(FA_KV_WHITELIST)}; recibiste k={self.cache_type_k} "
                f"v={self.cache_type_v}. Desactivá flash_attn o igualá los tipos."
            )
        # El KV del draft cae bajo la misma restricción (None = f16 default).
        draft_pair = (self.cache_type_k_draft or "f16", self.cache_type_v_draft or "f16")
        if self.flash_attn and draft_pair not in FA_KV_WHITELIST:
            raise ValueError(
                f"Con flash_attn=on el KV del draft debe ser simétrico y estar "
                f"en {sorted(FA_KV_WHITELIST)}; recibiste k={draft_pair[0]} v={draft_pair[1]}"
            )
        # -- rangos de los flags nuevos ----------------------------------
        if self.ctx_checkpoints is not None and self.ctx_checkpoints < 0:
            raise ValueError("ctx_checkpoints debe ser >= 0 (0 = sin checkpoints)")
        if self.checkpoint_min_step is not None and self.checkpoint_min_step < 512:
            raise ValueError("checkpoint_min_step debe ser >= 512 tokens")
        if self.cache_ram_mib is not None and self.cache_ram_mib < 0:
            raise ValueError("cache_ram_mib debe ser >= 0 (0 = desactivado)")
        if self.fit_target_mib is not None and self.fit_target_mib < 0:
            raise ValueError("fit_target_mib debe ser >= 0 (0 = off)")
        if self.budget_tokens < -1:
            raise ValueError("budget_tokens debe ser >= -1 (-1 = sin límite)")
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
    # argv realmente ejecutado, YA filtrado por el probe y con la api-key
    # enmascarada (ver mask_command). Es lo que la UI muestra en "Estado del
    # proceso": sirve para reproducir el lanzamiento a mano en una terminal.
    command: list[str] | None = None
    # Aviso de pre-vuelo: el puerto de destino ya estaba ocupado por algo
    # que esta instancia no lanzó (otra instancia, huérfano de un --reload).
    # El proceso igual se crea; la UI muestra esto junto al estado.
    warning: str | None = None


# ---------------------------------------------------------------------------
# Templates (persistidos en SQLite, tabla hw_templates)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Construcción de comandos
# ---------------------------------------------------------------------------


def resolve_log_path(log_file: str, process_id: str) -> Path:
    # C3: log_file es el archivo donde _pump_output guarda la salida del
    # proceso (el launcher es el único que lo escribe: ver
    # build_llama_server_command), así que se valida en start() antes de
    # usarlo, para bloquear path traversal ("../../etc/x" o absolutos fuera
    # del proyecto) -> 400.
    #
    # Se ancla a DATA_DIR, NO a BASE_DIR. Los logs de llama-server son estado
    # de la instancia: con GLYVEX_DATA_DIR apuntando afuera del repo, anclar a
    # BASE_DIR mandaría los logs de la instancia de testing al data/ de
    # producción (se pisarían), y además rechazaría con 400 cualquier ruta
    # dentro del DATA_DIR nuevo por quedar fuera de BASE_DIR.
    #
    # log_file se acepta relativo al DATA_DIR ("logs/llama-server.log") y
    # también con el prefijo histórico "data/" para no romper los templates y
    # las configs ya guardadas de instalaciones previas.
    if not log_file:
        return LOGS_DIR / f"{process_id}.log"

    candidate = Path(log_file)
    if not candidate.is_absolute():
        parts = candidate.parts
        if parts and parts[0] == "data":
            candidate = Path(*parts[1:]) if len(parts) > 1 else Path()
        candidate = DATA_DIR / candidate

    log_path = candidate.resolve()
    try:
        log_path.relative_to(DATA_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"log_file debe quedar dentro de {DATA_DIR}: {log_file}",
        ) from exc
    return log_path


# Flags que, si el binario no los conoce, indican que estamos ante un
# llama.cpp incompatible: mejor error claro que un comando mutilado.
CRITICAL_FLAGS = frozenset({"--model", "--host", "--port", "--ctx-size"})

# Long-flags que NO llevan valor. filter_command los necesita para poder
# descartar un flag desconocido sin comerse por error el token siguiente:
# la heurística "el próximo token no empieza con -- => es su valor" falla
# justo en los booleanos (p. ej. --kv-unified seguido de --metrics estaría
# bien, pero --no-jinja seguido de un valor posicional no).
# OJO: --load-mode SÍ lleva valor (auto|none|mmap|mlock|mmap+mlock|dio), no
# va acá — reemplaza a los viejos --mlock/--no-mmap que eran booleanos.
BOOLEAN_FLAGS = frozenset({
    "--jinja", "--no-jinja", "--metrics", "--kv-unified",
    "--no-kv-offload", "--no-reasoning-preserve", "--spec-type-mtp",
    "--warmup", "--no-warmup",
})


class BinaryInfo(BaseModel):
    """Capacidades detectadas de un binario de llama-server."""
    path: str
    build: str | None = None          # p. ej. "b11146"
    version_line: str | None = None   # primera línea cruda de --version
    flags: list[str] = []
    flag_help: dict[str, str] = {}    # P1.5: ayuda oficial de cada long-flag
    probed: bool = False              # False = el probe falló (no se filtra nada)


# Cache del probe. La clave incluye mtime+size del archivo, no solo la ruta:
# con auto-update/rollback del llama.cpp embebido el binario se reemplaza
# EN LA MISMA RUTA, y cachear por ruta dejaría filtrando contra los flags de
# la build vieja hasta reiniciar la app.
_BINARY_CACHE: dict[tuple[str, int, int], BinaryInfo] = {}

_BUILD_RE = re.compile(r"\bb?(\d{4,6})\b")


def _binary_cache_key(binary_path: str) -> tuple[str, int, int]:
    try:
        st = Path(binary_path).stat()
        return (binary_path, int(st.st_mtime), st.st_size)
    except OSError:
        return (binary_path, 0, 0)


async def _run_capture(binary_path: str, arg: str, timeout: float = 15.0) -> str | None:
    """Corre `binary --<arg>` y devuelve su salida combinada, o None si falla."""
    try:
        proc = await asyncio.create_subprocess_exec(
            binary_path, arg,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (OSError, asyncio.TimeoutError, NotImplementedError) as exc:
        logger.warning("no se pudo ejecutar %s %s: %s", binary_path, arg, exc)
        return None
    return out.decode("utf-8", errors="replace")


def _parse_flag_help(help_text: str) -> dict[str, str]:
    """
    Parsea la salida de --help (formato llama-gen-docs) a {long-flag: ayuda
    oficial}. Cada línea de opción: spec de la opción, un salto de 2+
    espacios y la descripción; los aliases (-m, --model) apuntan a la misma
    ayuda. Una opción sin salto ancho (sin descripción) queda con "".
    """
    help_map: dict[str, str] = {}
    for line in help_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("-"):
            continue
        parts = re.split(r"\s{2,}", stripped, maxsplit=1)
        desc = parts[1].strip() if len(parts) == 2 else ""
        for flag in re.findall(r"--[a-z0-9][a-z0-9-]*", parts[0]):
            help_map.setdefault(flag, desc)
    return help_map


async def probe_binary(binary_path: str) -> BinaryInfo:
    """
    Lee `--help` y `--version` una vez por build (cache invalidada por
    mtime+size) y devuelve los long-flags soportados más el número de build.
    Si el probe falla, `probed` queda en False: en ese caso NO se filtra nada
    y el error lo dará el propio llama-server con su mensaje en el log.
    """
    key = _binary_cache_key(binary_path)
    cached = _BINARY_CACHE.get(key)
    if cached is not None:
        return cached

    help_text = await _run_capture(binary_path, "--help")
    if help_text is None:
        return BinaryInfo(path=binary_path, probed=False)

    flags = sorted(set(re.findall(r"--[a-z0-9][a-z0-9-]*", help_text)))
    flag_help = _parse_flag_help(help_text)

    build = None
    version_line = None
    version_text = await _run_capture(binary_path, "--version", timeout=10.0)
    if version_text:
        version_line = version_text.strip().splitlines()[0] if version_text.strip() else None
        if version_line:
            m = _BUILD_RE.search(version_line)
            if m:
                build = f"b{m.group(1)}"

    info = BinaryInfo(
        path=binary_path,
        build=build,
        version_line=version_line,
        flags=flags,
        flag_help=flag_help,
        probed=True,
    )
    _BINARY_CACHE[key] = info
    logger.info(
        "probe de binario %s: build=%s, %d long-flags soportados",
        binary_path, build or "desconocida", len(flags),
    )
    return info


async def probe_supported_flags(binary_path: str) -> set[str]:
    """Compatibilidad hacia atrás: solo el set de flags. Set vacío = sin probe."""
    info = await probe_binary(binary_path)
    return set(info.flags) if info.probed else set()


def filter_command(cmd: list[str], supported: set[str]) -> tuple[list[str], list[str]]:
    """
    Devuelve (comando_filtrado, flags_descartados). Descarta los long-flags
    opcionales que el binario no conoce, junto con su valor si lo llevan.
    Un flag de BOOLEAN_FLAGS nunca consume el token siguiente; para el resto
    se toma como valor el próximo token que no empiece con '--' (así siguen
    cubiertos los negativos como -1 y el JSON de --chat-template-kwargs).
    Con `supported` vacío no se toca nada.
    """
    if not supported:
        return cmd, []
    out: list[str] = [cmd[0]]  # el binario nunca se filtra
    dropped: list[str] = []
    i = 1
    while i < len(cmd):
        arg = cmd[i]
        if arg.startswith("--") and arg not in supported:
            dropped.append(arg)
            takes_value = (
                arg not in BOOLEAN_FLAGS
                and i + 1 < len(cmd)
                and not cmd[i + 1].startswith("--")
            )
            i += 2 if takes_value else 1
            continue
        out.append(arg)
        i += 1
    return out, dropped


SECRET_FLAGS = frozenset({"--api-key"})


def mask_command(cmd: list[str]) -> list[str]:
    """
    Copia del argv con los valores sensibles reemplazados. El comando se
    expone por la API y se escribe en el log, así que la api-key nunca debe
    salir en claro de acá.
    """
    out = list(cmd)
    for i, arg in enumerate(out):
        if arg in SECRET_FLAGS and i + 1 < len(out) and out[i + 1]:
            out[i + 1] = "********"
    return out


def build_llama_server_command(
    cfg: LaunchConfig,
    binary_path: str,
    model_path: str,
    enable_thinking_supported: bool = True,
    supported_flags: set[str] | None = None,
) -> list[str]:
    # P1.5: solo --model y --port van siempre. Todo lo que tiene toggle es
    # Optional: None (toggle OFF) -> no se emite el flag -> default de build.
    cmd: list[str] = [
        binary_path,
        "--model", model_path,
    ]
    if cfg.auto_mode:
        # Modo automático: ningún flag de tuning — mandarlo sería pisar el
        # default de la build, que es justo lo que este modo quiere probar.
        # host/port son el mínimo para llegar al server; verbosity y metrics
        # son infra de la app (logs del _pump_output y el Monitor).
        if cfg.host != "127.0.0.1":
            cmd += ["--host", cfg.host]
        cmd += ["--port", str(cfg.port), "--verbosity", str(LLAMA_SERVER_VERBOSITY)]
        if cfg.backend == "llama_server":
            cmd.append("--metrics")
        return cmd
    if cfg.n_ctx is not None:
        cmd += ["--ctx-size", str(cfg.n_ctx)]
    if cfg.n_batch is not None:
        cmd += ["--batch-size", str(cfg.n_batch)]
    if cfg.n_ubatch is not None:
        cmd += ["--ubatch-size", str(cfg.n_ubatch)]
    cmd += ["--n-gpu-layers", str(cfg.n_gpu_layers)]
    if cfg.cache_type_k is not None:
        cmd += ["--cache-type-k", cfg.cache_type_k]
    if cfg.cache_type_v is not None:
        cmd += ["--cache-type-v", cfg.cache_type_v]
    if cfg.flash_attn:
        cmd.extend(["--flash-attn", "on"])
    else:
        cmd.extend(["--flash-attn", "off"])
    # -- Checkpoints de contexto y prompt cache (build 11003+) -----------
    # Van siempre: son los dos grandes consumidores de VRAM/RAM que la UI
    # ahora permite acotar. El probe los descarta en builds viejas.
    if cfg.ctx_checkpoints is not None:
        cmd += ["--ctx-checkpoints", str(cfg.ctx_checkpoints)]
    if cfg.checkpoint_min_step is not None:
        cmd += ["--checkpoint-min-step", str(cfg.checkpoint_min_step)]
    if cfg.cache_ram_mib is not None:
        cmd += ["--cache-ram", str(cfg.cache_ram_mib)]
    # fit: el flag es on|off y el default de la build es 'on', así que faltar
    # no lo apaga. False -> --fit off explícito; True -> --fit on; None -> no
    # se emite (modo manual, la build usa su default).
    if cfg.fit is True:
        cmd += ["--fit", "on"]
    elif cfg.fit is False:
        cmd += ["--fit", "off"]
    if cfg.fit_target_mib is not None and cfg.fit_target_mib > 0:
        cmd += ["--fit-target", str(cfg.fit_target_mib)]
    if cfg.kv_unified:
        # En algunas builds pide valor explícito; si el probe lo deja pasar
        # y el server lo rechaza, el log lo muestra al instante.
        cmd.append("--kv-unified")
    # El binario trae --jinja en 'enabled' por default: si no mandamos nada
    # cuando cfg.jinja=False, el toggle de la UI en "apagado" no hace nada
    # (el server sigue con Jinja prendido). Por eso las dos ramas, no solo
    # el append condicional.
    cmd.append("--jinja" if cfg.jinja else "--no-jinja")
    # -- reasoning effort / budget: flags nativos del server (b11009) ----
    # --reasoning-effort pasa "reasoning effort level given to the chat
    # template": es la versión nativa del server del viejo kwarg
    # reasoning_effort (no depende de que el template lea un kwarg concreto).
    # 'default'/'none' no se envía (mantiene el default del template).
    if cfg.reasoning_effort != "none":
        cmd += ["--reasoning-effort", cfg.reasoning_effort]
    if cfg.reasoning_budget != -1:
        cmd += ["--reasoning-budget", str(cfg.reasoning_budget)]
    # -- reasoning (thinking) --------------------------------------------
    # --reasoning on/off es el flag nativo: el kwarg enable_thinking en
    # --chat-template-kwargs quedó deprecado en llama.cpp y hacía lo mismo.
    # Solo si el chat_template del modelo lee enable_thinking (detectado del
    # header GGUF por read_gguf_metadata): si no lo lee, no hay qué togglear
    # y --reasoning on solo mutaría el parseo (reasoning_content) sin motivo.
    if enable_thinking_supported:
        if cfg.jinja:
            cmd += ["--reasoning", "on" if cfg.thinking_enabled else "off"]
            # budget_tokens → --reasoning-budget nativo: el viejo kwarg
            # "thinking_budget" era no-op en templates como Qwen3. El campo
            # explícito reasoning_budget se emitió arriba y tiene prioridad.
            if cfg.thinking_enabled and cfg.budget_tokens > 0 and cfg.reasoning_budget == -1:
                cmd += ["--reasoning-budget", str(cfg.budget_tokens)]
        else:
            logger.warning(
                "thinking_enabled/budget_tokens requieren --jinja: --reasoning no se envía"
            )
    if cfg.no_reasoning_preserve:
        cmd.append("--no-reasoning-preserve")
    # --mlock/--no-mmap/--mmap ya no existen en builds recientes; se
    # unificaron en --load-mode (confirmado contra --help real: auto es el
    # default del binario, así que no hace falta mandarlo salvo que el
    # usuario haya elegido otra cosa).
    if cfg.load_mode != "auto":
        cmd += ["--load-mode", cfg.load_mode]
    # -- speculative decoding (MTP / NextN) ------------------------------
    # Nombres de flags según `llama-server --help` (build 11003):
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
        if supported_flags and "--lora-scaled" in supported_flags:
            # b11146+ (v0.5.0): --lora-scale fue removido; la escala va
            # dentro del mismo flag (PATH:SCALE).
            cmd += ["--lora-scaled", f"{cfg.lora_path}:{cfg.lora_scale}"]
        else:
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
    # Grupo RoPE: toggle OFF = rope_scaling_type None -> nada del subsistema,
    # aunque freq_base/yarn traigan valores.
    if cfg.rope_scaling_type is not None:
        if cfg.rope_freq_base is not None and cfg.rope_freq_base > 0:
            cmd += ["--rope-freq-base", str(cfg.rope_freq_base)]
        if cfg.rope_scaling_type != "none":
            cmd += ["--rope-scaling", cfg.rope_scaling_type]
        if (cfg.rope_scaling_type == "yarn" and cfg.yarn_ext_factor is not None
                and cfg.yarn_ext_factor >= 0):
            cmd += ["--yarn-ext-factor", str(cfg.yarn_ext_factor)]
    if cfg.numa:
        # Build 11003: --numa EXIGE modo (distribute|isolate|numactl).
        cmd += ["--numa", "distribute"]
    if cfg.no_kv_offload:
        cmd.append("--no-kv-offload")
    if cfg.cache_reuse is not None and cfg.cache_reuse > 0:
        cmd += ["--cache-reuse", str(cfg.cache_reuse)]
    # -- VRAM / multi-modelo (b11009) -----------------------------------
    # warmup: el default del binario es "on", así que solo se envía
    # --no-warmup para desactivarlo (coherente con "solo si difiere").
    if not cfg.warmup:
        cmd.append("--no-warmup")
    if cfg.sleep_idle_seconds > 0:
        cmd += ["--sleep-idle-seconds", str(cfg.sleep_idle_seconds)]
    if cfg.kv_unified_per_slot > 0:
        cmd += ["--kv-unified-per-slot", str(cfg.kv_unified_per_slot)]
    if cfg.lazy_mode != "auto":
        cmd += ["--lazy-mode", cfg.lazy_mode]
    # --defrag-thold figura como DEPRECATED en el --help de builds recientes.
    # Por ahora el binario lo sigue aceptando (no está en la lista de flags
    # críticos), así que lo dejamos: si algún día lo remueven del todo, el
    # probe de capacidades lo descarta solo y esto no rompe el arranque.
    if cfg.defrag_thold is not None and cfg.defrag_thold >= 0:
        cmd += ["--defrag-thold", str(cfg.defrag_thold)]
    if cfg.grp_attn_n is not None and cfg.grp_attn_n > 1:
        cmd += ["--grp-attn-n", str(cfg.grp_attn_n)]
        if cfg.grp_attn_w is not None:
            cmd += ["--grp-attn-w", str(cfg.grp_attn_w)]
    if cfg.n_parallel is not None and cfg.n_parallel > 1 and not cfg.kv_unified:
        logger.warning(
            "n_parallel=%d sin kv_unified: cada slot reserva su propio KV cache "
            "completo (x%d la VRAM de contexto); considerá kv_unified=True o "
            "reducir n_ctx",
            cfg.n_parallel, cfg.n_parallel,
        )
    # --threads solo con valor positivo: -1/0 significa "auto" y pasarlo
    # explícitamente depende de cómo lo trate cada build.
    if cfg.n_threads is not None and cfg.n_threads > 0:
        cmd += ["--threads", str(cfg.n_threads)]
    if cfg.n_parallel is not None:
        cmd += ["--parallel", str(cfg.n_parallel)]
    # 127.0.0.1 es el default de la build: solo se envía si difiere.
    if cfg.host != "127.0.0.1":
        cmd += ["--host", cfg.host]
    cmd += ["--port", str(cfg.port)]
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


def _require_llama_binary() -> str:
    """Binario de llama_server vía cascada (modo experto → runtime
    gestionado, ver runtime.resolve_binary). Sin auto-download: si no hay
    ninguno usable, 400 con code `runtime_missing` para que la UI ofrezca
    descargarlo (RT-4/RT-5) en vez de lanzar a ciegas."""
    binary_path = resolve_binary(config.get("backends.llama_server.binary_path"))
    if not binary_path:
        raise HTTPException(
            status_code=400,
            detail=(
                "runtime_missing: no hay runtime de llama.cpp instalado "
                "(descargalo desde Config → Runtime, o fijá un binary_path propio)"
            ),
        )
    return binary_path


# ---------------------------------------------------------------------------
# Gestión de procesos (asyncio nativo)
# ---------------------------------------------------------------------------


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

    async def _port_in_use(self, host: str, port: int) -> bool:
        """Conector TCP efímero: ¿algo escucha en host:port?"""
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=1.0
            )
        except (OSError, asyncio.TimeoutError):
            return False
        writer.close()
        await writer.wait_closed()
        return True

    async def _identify_listener(self, host: str, port: int) -> str:
        """
        Heurística de qué hay escuchando: 'llama-server' (/props es exclusivo
        de llama.cpp), 'ollama' (/api/version) u 'otro'. Cualquier fallo se
        lee como 'otro'.
        """
        base = f"http://{host}:{port}"
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                if (await client.get(f"{base}/props")).status_code == 200:
                    return "llama-server"
                if (await client.get(f"{base}/api/version")).status_code == 200:
                    return "ollama"
        except (httpx.HTTPError, OSError, ValueError):
            pass
        return "otro"

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
                # LM Studio lo lanza el usuario por fuera: no hay argv propio.
                command=None,
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
        port_warning = None
        if cfg.backend == "llama_server":
            # Pre-vuelo: si algo ya escucha en ese puerto, el server que
            # lanzamos muere al bindear y el ocupante original sigue
            # sirviendo sin que ninguna UI lo muestre (huérfano de un
            # --reload u otra instancia). No bloqueamos: el proceso nace
            # con el aviso y la UI lo muestra junto al estado.
            if await self._port_in_use(cfg.host, cfg.port):
                kind = await self._identify_listener(cfg.host, cfg.port)
                if kind == "llama-server":
                    port_warning = (
                        f"El puerto {cfg.port} ya está en uso por un "
                        "llama-server que esta instancia no lanzó (otra "
                        "instancia o proceso huérfano). El server nuevo no "
                        f"podrá subir: chateá con el existente usando "
                        f"http://{cfg.host}:{cfg.port} como endpoint de "
                        "Chat, o terminalo antes de relanzar."
                    )
                elif kind == "ollama":
                    port_warning = (
                        f"El puerto {cfg.port} ya está en uso por Ollama: "
                        "el server nuevo no podrá subir en ese puerto."
                    )
                else:
                    port_warning = (
                        f"El puerto {cfg.port} ya está en uso por otro "
                        "proceso: el server nuevo no podrá subir en ese "
                        "puerto."
                    )
            binary_path = _require_llama_binary()
            # Puerta de thinking: usa la metadata persistida en el inventario
            # (extraída en el scan) si el archivo no cambió, o relee el
            # header con cache. Si no se puede leer, se conserva el
            # comportamiento previo (enviar el kwarg) para no cambiar el
            # launch por metadata ilegible.
            meta = await get_entry_metadata(model)
            enable_thinking_supported = (
                True if meta.get("error") else bool(meta.get("enable_thinking_kwarg"))
            )
            # Probe de capacidades ANTES de armar el comando: el build lo usa
            # para feature-detect (ej. --lora-scaled de b11146+) y para
            # descartar los flags que la build no conoce (ej. --grp-attn-n
            # removido) en vez de morir con "unknown argument". Los críticos
            # faltantes son error duro.
            info = await probe_binary(binary_path)
            supported = set(info.flags) if info.probed else set()
            cmd = build_llama_server_command(
                cfg,
                binary_path,
                model.path,
                enable_thinking_supported=enable_thinking_supported,
                supported_flags=supported,
            )
            if supported:
                # Solo los críticos que el comando emite realmente: si
                # --host no va (default 127.0.0.1), una build sin --host
                # no es un problema para este launch.
                missing = sorted((CRITICAL_FLAGS & set(cmd)) - supported)
                if missing:
                    raise HTTPException(
                        status_code=400,
                        detail=f"El binario no soporta flags críticos: {missing}",
                    )
            cmd, dropped = filter_command(cmd, supported)
            if dropped:
                logger.warning(
                    "build %s: flags no soportados por este binario, se omiten: %s",
                    info.build or "desconocida", ", ".join(dropped),
                )
            # Queda en el log del proceso: al abrir el log se ve contra qué
            # build corrió realmente, sin tener que adivinarlo después.
            logger.info(
                "lanzando contra llama-server build %s (%s)",
                info.build or "desconocida", binary_path,
            )
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
            command=mask_command(cmd),
            warning=port_warning,
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

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter()


class CommandPreview(BaseModel):
    """Comando que se ejecutaría con esta config, sin lanzar nada."""
    command: list[str]
    dropped: list[str] = []      # flags que esta build no soporta
    build: str | None = None
    probed: bool = False


@router.post("/preview-command", response_model=CommandPreview)
async def preview_command(cfg: LaunchConfig) -> CommandPreview:
    """
    Arma el comando exactamente como lo haría start() —mismo probe, mismo
    filtrado, misma máscara de secretos— pero sin ejecutarlo. La UI lo usa
    para mostrar qué se va a correr antes de apretar LAUNCH, y para avisar
    qué flags va a descartar esta build.
    """
    model = model_inventory.get(cfg.model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="Modelo no encontrado en el inventario")

    if cfg.backend == "lm_studio":
        return CommandPreview(
            command=[],
            dropped=[],
            build=None,
            probed=False,
        )

    if cfg.backend == "ollama":
        binary_path = config.get("backends.ollama.binary_path") or "/usr/bin/ollama"
        return CommandPreview(
            command=mask_command(build_ollama_command(binary_path, model.name)),
            dropped=[],
            build=None,
            probed=False,
        )

    binary_path = _require_llama_binary()
    meta = await get_entry_metadata(model)
    enable_thinking_supported = (
        True if meta.get("error") else bool(meta.get("enable_thinking_kwarg"))
    )
    info = await probe_binary(binary_path)
    supported = set(info.flags) if info.probed else set()
    cmd = build_llama_server_command(
        cfg,
        binary_path,
        model.path,
        enable_thinking_supported=enable_thinking_supported,
        supported_flags=supported,
    )
    cmd, dropped = filter_command(cmd, supported)
    return CommandPreview(
        command=mask_command(cmd),
        dropped=dropped,
        build=info.build,
        probed=info.probed,
    )


@router.get("/backend-info", response_model=BinaryInfo)
async def backend_info() -> BinaryInfo:
    """
    Build y capacidades del binario de llama_server configurado. La UI lo usa
    para mostrar contra qué build está corriendo y avisar si el probe falló,
    en vez de tener que comparar changelogs a mano en cada actualización.
    """
    binary_path = _require_llama_binary()
    return await probe_binary(binary_path)


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
    existing = await templates.get(template.name)
    if existing is not None and existing.builtin:
        # La UI prellena el nombre al elegir un template: sin esta guarda,
        # guardar sin cambiar el nombre pisaría un predefinido (y le
        # quitaría el flag builtin, dejándolo eliminable).
        raise HTTPException(
            status_code=409,
            detail="El template predefinido no se puede sobrescribir: usá otro nombre.",
        )
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
