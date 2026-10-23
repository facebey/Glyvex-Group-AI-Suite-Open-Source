"""
config.py — Manejo de configuración persistente para Glyvex-AI-Suite.

Usa un modelo Pydantic v2 como esquema/validación y expone acceso por
dot-notation (config.get("app.port"), config.set("app.port", 7981)) sobre
un dict interno, con persistencia async en JSON vía aiofiles.
"""

from __future__ import annotations

import json
from pathlib import Path
from paths import DATA_DIR
from typing import Any, Literal

import aiofiles
from pydantic import BaseModel, Field, ValidationError

# Ruta del archivo de configuración: <DATA_DIR>/config.json
# DATA_DIR sale de paths.py (respeta GLYVEX_DATA_DIR), así que cada instancia
# tiene su propio config.json.
CONFIG_PATH = DATA_DIR / "config.json"


class BackendConfig(BaseModel):
    binary_path: str = ""
    default_port: int = 8080
    enabled: bool = True


class UnslothConfig(BaseModel):
    python_env: str = ""
    enabled: bool = False


class BackendsConfig(BaseModel):
    llama_server: BackendConfig = Field(
        default_factory=lambda: BackendConfig(default_port=8080, enabled=True)
    )
    # binary_path vacío = resolver "ollama" contra el PATH del SO.
    ollama: BackendConfig = Field(
        default_factory=lambda: BackendConfig(
            binary_path="", default_port=11434, enabled=False
        )
    )
    lm_studio: BackendConfig = Field(
        default_factory=lambda: BackendConfig(default_port=1234, enabled=False)
    )
    unsloth: UnslothConfig = Field(default_factory=UnslothConfig)


class HardwareConfig(BaseModel):
    gpu_model: str = ""
    vram_gb: int = 0
    ram_gb: int = 0
    cpu_model: str = ""
    cpu_cores: int = 0


class AppConfig(BaseModel):
    port: int = 7981
    theme: str = "dark"
    language: str = "es"


class AttachmentsConfig(BaseModel):
    """Límites del chat para adjuntos (módulo M3, Bloque 1)."""

    max_file_mb: int = 16
    max_files_per_message: int = 10
    # Tope de texto extraído por archivo. Se guarda dentro del mensaje, así
    # que también acota cuánto crece la fila de chat_conversations.
    max_text_chars: int = 50_000
    # Costo estimado de una imagen para el aviso de contexto. Varía mucho
    # entre modelos de visión, por eso es configurable y no una constante.
    image_tokens_estimate: int = 1024


class ToolsConfig(BaseModel):
    """Búsqueda y navegación web del chat (módulo M3, Bloque 2)."""

    # Proveedor de búsqueda: "ddgs" | "searxng" | "brave" | "tavily".
    # El default no necesita servidor ni key, así que funciona dentro del
    # bundle de Tauri sin que el usuario instale nada aparte.
    search_provider: str = "ddgs"
    # Región de DuckDuckGo: "wt-wt" es global, "ar-es" Argentina, etc.
    region: str = "wt-wt"
    # Solo para search_provider = "searxng". La imagen Docker escucha en 8080
    # adentro del contenedor — el mismo puerto que llama-server — así que el
    # mapeo habitual es -p 8888:8080.
    searxng_url: str = "http://127.0.0.1:8888"
    # Se leen antes las variables de entorno BRAVE_API_KEY y TAVILY_API_KEY:
    # dejar un secreto acá lo escribe en config.json.
    brave_api_key: str = ""
    tavily_api_key: str = ""

    max_results: int = 5
    fetch_max_chars: int = 8000
    # Tope de idas y vueltas modelo↔tool dentro de un mismo turno.
    max_rounds: int = 5
    timeout_s: float = 20.0
    # fetch_url recibe la URL del modelo, no del usuario: un resultado de
    # búsqueda podría intentar apuntarlo a la red interna. Habilitalo solo si
    # querés que pueda leer páginas de tu propia LAN.
    allow_private_hosts: bool = False
    user_agent: str = "Glyvex-AI-Suite/0.1"


class STTConfig(BaseModel):
    """Voz a texto del chat (módulo M3, Bloque 3)."""

    # "auto" | "whispercpp" | "whisper" | "browser". Con "auto": whisper.cpp
    # nativo si su runtime está listo, si no faster-whisper si está
    # instalado, si no la Web Speech API (que no existe en el WebView de
    # Tauri). Ver stt.py y docs/voz-a-texto.md.
    # La variable de entorno STT_ENGINE tiene precedencia sobre esto.
    engine: str = "auto"
    # Idioma que se le pasa a la Web Speech API (formato BCP-47).
    language: str = "es-AR"
    # tiny | base | small | medium | large-v3 | large-v3-turbo | distil-large-v3.
    # base es el default para no asumir GPU: anda en CPU a velocidad razonable.
    whisper_model: str = "base"
    # "auto" | "cpu" | "cuda"
    whisper_device: str = "auto"
    # int8 anda en cualquier CPU. Con GPU, float16 es más rápido.
    whisper_compute_type: str = "int8"
    # "" = que Whisper detecte el idioma solo.
    whisper_language: str = ""
    # True = los motores locales transcriben directo a inglés (whisper.cpp
    # --translate / faster-whisper task="translate"); el idioma de origen se
    # detecta solo. El motor del navegador no lo soporta y lo ignora.
    translate_english: bool = False


class TTSConfig(BaseModel):
    """Texto a voz de las respuestas del chat (100% local: SAPI, Piper o Kokoro)."""

    # False desactiva el botón de lectura en voz alta sin tocar nada más.
    enabled: bool = True
    # "auto" | "sapi" | "piper" | "kokoro". auto: Kokoro si está listo (motor
    # importable + modelo descargado), si no Piper listo (voz descargada), si
    # no SAPI. piper/kokoro: siempre ese motor (error claro si no).
    engine: str = "auto"
    # "" = automático: cadena es-AR → es-MX → es-ES → voz por defecto del
    # sistema (Windows no trae es-AR por defecto). Si se fija un nombre de
    # voz instalada (ver /api/tts/status), esa voz gana. Solo engine SAPI.
    voice: str = ""
    # Velocidad: SAPI -10 (lento) a 10 (rápido), 0 = natural. En Piper se
    # traduce a length_scale (rate 10 ≈ 2x más rápido, -10 ≈ más lento) y en
    # Kokoro a speed (rate 10 ≈ 1.5x más rápido, -10 ≈ más lento).
    rate: int = 0
    # Voz neuronal Piper (ver /api/tts/piper/voices). Solo engines auto/piper.
    piper_voice: str = "es_AR-daniela-high"
    # Voz neuronal Kokoro (ver /api/tts/kokoro/voices). Solo engines auto/kokoro.
    kokoro_voice: str = "ef_dora"


class MonitorConfig(BaseModel):
    """Histórico del Monitor (MetricsService, <DATA_DIR>/metrics.db)."""
    # Poller en segundo plano desde que arranca la app. En false, el Monitor
    # en vivo sigue andando pero no se guarda histórico.
    history_enabled: bool = True
    # Retención por nivel de resolución: ventanas de 5 s, 1 minuto y 1 hora.
    retention_raw_h: int = 48
    retention_1m_d: int = 30
    retention_1h_d: int = 365


# Elementos ocultos por defecto (plantilla "Predeterminada"). Tiene que
# coincidir con PRESETS.default de frontend/src/lib/metricsDisplay.js; lo
# verifica tests/test_config.py.
DEFAULT_HIDDEN_METRICS = ["monitor.gpu.power_clocks", "monitor.cpu.freq"]


class DisplayConfig(BaseModel):
    """
    Qué métricas se ven en Monitor, Chat y Launcher.

    Se guarda lo OCULTO, no lo visible: una métrica que se agregue en una
    versión futura aparece sola, sin que el usuario tenga que habilitarla.
    Ocultar es solo visual (el histórico y las exportaciones siguen con
    todo), salvo "monitor.processes": oculto, el backend deja de recorrer
    los procesos del sistema.
    """

    preset: str = "default"  # default | minimal | full | custom
    hidden: list[str] = Field(default_factory=lambda: list(DEFAULT_HIDDEN_METRICS))


class InfluxExportConfig(BaseModel):
    enabled: bool = False
    # "v2": /api/v2/write con org + bucket + token (InfluxDB 2.x y 3).
    # "v1": /write?db= con usuario/contraseña opcionales (InfluxDB 1.x,
    # VictoriaMetrics, QuestDB).
    version: str = "v2"
    url: str = "http://127.0.0.1:8086"
    # Se lee antes la variable de entorno GLYVEX_INFLUX_TOKEN: dejar el token
    # acá lo escribe en config.json.
    token: str = ""
    org: str = ""
    bucket: str = "glyvex"
    database: str = "glyvex"
    username: str = ""
    password: str = ""
    interval_s: int = 10
    measurement_prefix: str = "glyvex"


class ExportsConfig(BaseModel):
    """Salidas opcionales de métricas hacia sistemas externos."""

    # GET /api/metrics/prometheus. Apagado: responde 404.
    prometheus_enabled: bool = False
    # Opcional: si tiene valor, el endpoint exige "Authorization: Bearer <token>".
    prometheus_token: str = ""
    influx: InfluxExportConfig = Field(default_factory=InfluxExportConfig)


class CloudPricingProvider(BaseModel):
    """Precios de referencia de un proveedor cloud (moneda por millón de tokens)."""

    id: str
    name: str
    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0
    # None = el proveedor no diferencia la lectura de caché: los tokens
    # cacheados se facturan a la tarifa normal de entrada.
    cache_read_per_mtok: float | None = None


def _default_cloud_pricing_providers() -> list[CloudPricingProvider]:
    # Valores de referencia: lista cerrada de Fabian (Files/Pricing
    # Providers/pricing.json, verificada 2026-10). Cambian seguido, así que
    # son editables desde Config → "Precios de nube" sin tocar código.
    # Los nombres con notas de vigencia ("desde 1-dic-2026", "horario pico")
    # se copian tal cual: la vigencia es información para el usuario, no
    # lógica de la app (la app no hace billing).
    return [
        CloudPricingProvider(id="openai-gpt-6-astra", name="OpenAI GPT-6 Astra",
                             input_per_mtok=10.0, output_per_mtok=50.0, cache_read_per_mtok=1.0),
        CloudPricingProvider(id="openai-gpt-6.1-sol", name="OpenAI GPT-6.1 Sol",
                             input_per_mtok=2.0, output_per_mtok=10.0, cache_read_per_mtok=0.1),
        CloudPricingProvider(id="openai-gpt-6-luna", name="OpenAI GPT-6 Luna",
                             input_per_mtok=0.1, output_per_mtok=0.5, cache_read_per_mtok=0.01),
        CloudPricingProvider(id="openai-gpt-4o", name="OpenAI GPT-4o",
                             input_per_mtok=2.5, output_per_mtok=10.0, cache_read_per_mtok=1.25),
        CloudPricingProvider(id="openai-gpt-4.1", name="OpenAI GPT-4.1",
                             input_per_mtok=2.0, output_per_mtok=8.0, cache_read_per_mtok=0.5),
        CloudPricingProvider(id="anthropic-claude-fable-5.1", name="Anthropic Claude Fable 5.1",
                             input_per_mtok=10.0, output_per_mtok=50.0, cache_read_per_mtok=0.25),
        CloudPricingProvider(id="anthropic-claude-opus-5.5", name="Anthropic Claude Opus 5.5",
                             input_per_mtok=4.0, output_per_mtok=20.0, cache_read_per_mtok=0.2),
        CloudPricingProvider(id="anthropic-claude-opus-4.8", name="Anthropic Claude Opus 4.8",
                             input_per_mtok=5.0, output_per_mtok=25.0, cache_read_per_mtok=0.5),
        CloudPricingProvider(id="anthropic-claude-sonnet-5.5", name="Anthropic Claude Sonnet 5.5",
                             input_per_mtok=2.0, output_per_mtok=10.0, cache_read_per_mtok=0.1),
        CloudPricingProvider(id="anthropic-claude-sonnet-4.6", name="Anthropic Claude Sonnet 4.6",
                             input_per_mtok=3.0, output_per_mtok=15.0, cache_read_per_mtok=0.3),
        CloudPricingProvider(id="anthropic-claude-haiku-5.5", name="Anthropic Claude Haiku 5.5 (prompts hasta 100k tokens)",
                             input_per_mtok=0.1, output_per_mtok=0.5, cache_read_per_mtok=0.01),
        CloudPricingProvider(id="google-gemini-4-argon", name="Google Gemini 4 Argon (tarifa vigente hasta 30-nov-2026)",
                             input_per_mtok=2.0, output_per_mtok=10.0, cache_read_per_mtok=0.1),
        CloudPricingProvider(id="google-gemini-4-argon-dec2026", name="Google Gemini 4 Argon (desde 1-dic-2026)",
                             input_per_mtok=4.0, output_per_mtok=20.0, cache_read_per_mtok=0.2),
        CloudPricingProvider(id="google-gemini-3.8-flash", name="Google Gemini 3.8 Flash (tarifa introductoria hasta 31-dic-2026)",
                             input_per_mtok=0.75, output_per_mtok=3.75, cache_read_per_mtok=0.075),
        CloudPricingProvider(id="google-gemini-3.8-flash-2027", name="Google Gemini 3.8 Flash (desde 1-ene-2027)",
                             input_per_mtok=1.5, output_per_mtok=7.5, cache_read_per_mtok=0.15),
        CloudPricingProvider(id="google-gemini-3.5-flash-lite", name="Google Gemini 3.5 Flash-Lite",
                             input_per_mtok=0.3, output_per_mtok=2.5, cache_read_per_mtok=0.03),
        CloudPricingProvider(id="google-gemini-3.1-pro", name="Google Gemini 3.1 Pro Preview (prompts hasta 200k tokens)",
                             input_per_mtok=2.0, output_per_mtok=12.0, cache_read_per_mtok=0.2),
        CloudPricingProvider(id="google-gemini-2.5-pro", name="Google Gemini 2.5 Pro (prompts hasta 200k tokens)",
                             input_per_mtok=1.25, output_per_mtok=10.0, cache_read_per_mtok=0.125),
        CloudPricingProvider(id="google-gemini-2.5-flash", name="Google Gemini 2.5 Flash",
                             input_per_mtok=0.3, output_per_mtok=2.5, cache_read_per_mtok=0.03),
        CloudPricingProvider(id="deepseek-v4.1-flash", name="DeepSeek V4.1 Flash (horario pico)",
                             input_per_mtok=0.3, output_per_mtok=1.2, cache_read_per_mtok=0.006),
        CloudPricingProvider(id="deepseek-v4.1-flash-offpeak", name="DeepSeek V4.1 Flash (fuera de pico)",
                             input_per_mtok=0.15, output_per_mtok=0.6, cache_read_per_mtok=0.003),
        CloudPricingProvider(id="deepseek-r1", name="DeepSeek R1",
                             input_per_mtok=0.55, output_per_mtok=2.19, cache_read_per_mtok=0.14),
        CloudPricingProvider(id="deepseek-v3", name="DeepSeek V3",
                             input_per_mtok=0.27, output_per_mtok=1.1, cache_read_per_mtok=0.07),
        CloudPricingProvider(id="meta-llama-3.3-70b-instruct", name="Meta Llama 3.3 70B Instruct",
                             input_per_mtok=0.88, output_per_mtok=0.88, cache_read_per_mtok=0.0),
        CloudPricingProvider(id="meta-llama-3.1-405b-instruct", name="Meta Llama 3.1 405B Instruct",
                             input_per_mtok=3.5, output_per_mtok=3.5, cache_read_per_mtok=0.0),
        CloudPricingProvider(id="meta-llama-3.1-8b-instruct", name="Meta Llama 3.1 8B Instruct",
                             input_per_mtok=0.1, output_per_mtok=0.1, cache_read_per_mtok=0.0),
        CloudPricingProvider(id="mistral-large-2", name="Mistral Large 2",
                             input_per_mtok=2.0, output_per_mtok=6.0, cache_read_per_mtok=0.5),
        CloudPricingProvider(id="mistral-small-3", name="Mistral Small 3",
                             input_per_mtok=0.2, output_per_mtok=0.6, cache_read_per_mtok=0.05),
        CloudPricingProvider(id="mistral-codestral", name="Mistral Codestral",
                             input_per_mtok=0.3, output_per_mtok=0.9, cache_read_per_mtok=0.08),
        CloudPricingProvider(id="xai-grok-2", name="xAI Grok 2",
                             input_per_mtok=2.0, output_per_mtok=10.0, cache_read_per_mtok=0.2),
        CloudPricingProvider(id="xai-grok-vision-beta", name="xAI Grok Vision Beta",
                             input_per_mtok=5.0, output_per_mtok=15.0, cache_read_per_mtok=0.5),
        CloudPricingProvider(id="cohere-command-r-plus", name="Cohere Command R+",
                             input_per_mtok=2.5, output_per_mtok=10.0, cache_read_per_mtok=0.25),
        CloudPricingProvider(id="cohere-command-r", name="Cohere Command R",
                             input_per_mtok=0.15, output_per_mtok=0.6, cache_read_per_mtok=0.015),
        CloudPricingProvider(id="amazon-nova-pro", name="Amazon Nova Pro",
                             input_per_mtok=0.8, output_per_mtok=3.2, cache_read_per_mtok=0.2),
        CloudPricingProvider(id="amazon-nova-lite", name="Amazon Nova Lite",
                             input_per_mtok=0.06, output_per_mtok=0.24, cache_read_per_mtok=0.015),
        CloudPricingProvider(id="amazon-nova-micro", name="Amazon Nova Micro",
                             input_per_mtok=0.035, output_per_mtok=0.14, cache_read_per_mtok=0.009),
    ]


class CloudPricingConfig(BaseModel):
    """
    Precios de nube para la comparativa "Costo de sesión" del Monitor (MON-2).

    Son valores de referencia para la demo (¿cuánto habría costado esta
    sesión en la nube?), no facturación: la app no hace billing.
    """

    currency: str = "USD"
    default_provider_id: str = "openai-gpt-4o"
    providers: list[CloudPricingProvider] = Field(
        default_factory=_default_cloud_pricing_providers
    )


class RuntimeConfig(BaseModel):
    """Selección de aceleración GPU para el runtime gestionado (RT-10 B2).

    Solo tiene sentido para familias con más de una ruta (AMD: vulkan/rocm).
    None = default del catálogo (orden de ACCEL_SOURCES: Vulkan primero por
    cubrir toda la familia). Es una ELECCIÓN, no un estado: se aplica en la
    próxima descarga/reinstall; la backend realmente instalada vive en la meta
    del runtime y se expone en /api/runtime/status como accel_source.
    """

    accel: Literal["vulkan", "rocm"] | None = None


class AgentBridgeConfig(BaseModel):
    """(AG-1) Integracion con OpenCode: mientras un server lanzado por la suite
    esta 'running', su bloque de provider se publica en el config de OpenCode
    (~/.config/opencode/) y se retira al stop/crash/shutdown. Default OFF: el
    bridge no toca ese archivo si no se activa explicitamente. Diseno completo:
    Files/Docs/design/ag-1-opencode-bridge.md."""

    enabled: bool = False


class ConfigSchema(BaseModel):
    """Esquema completo de config.json, usado solo para validar/generar defaults."""

    app: AppConfig = Field(default_factory=AppConfig)
    hardware: HardwareConfig = Field(default_factory=HardwareConfig)
    backends: BackendsConfig = Field(default_factory=BackendsConfig)
    attachments: AttachmentsConfig = Field(default_factory=AttachmentsConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    agent_bridge: AgentBridgeConfig = Field(default_factory=AgentBridgeConfig)
    monitor: MonitorConfig = Field(default_factory=MonitorConfig)
    display: DisplayConfig = Field(default_factory=DisplayConfig)
    exports: ExportsConfig = Field(default_factory=ExportsConfig)
    cloud_pricing: CloudPricingConfig = Field(default_factory=CloudPricingConfig)
    model_dirs: list[str] = Field(default_factory=list)
    last_used_model: str | None = None
    auto_start_last: bool = False


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Merge recursivo: patch pisa a base, sin borrar claves no incluidas en patch."""
    result = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class Config:
    """
    Wrapper de configuración con acceso por dot-notation y persistencia async.

    Uso:
        config = Config()
        await config.load()
        config.get("app.port")          # -> 7981
        config.set("app.port", 8000)
        await config.save()
    """

    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self.path = path
        self._data: dict[str, Any] = json.loads(ConfigSchema().model_dump_json())

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    def defaults(self) -> dict[str, Any]:
        return json.loads(ConfigSchema().model_dump_json())

    def get(self, key: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        # Un null guardado (p. ej. un campo numérico que llegó mal por la
        # API) se trata como "no existe": de lo contrario el default no
        # aplicaba y un int() en el reader crasheaba con TypeError.
        return default if node is None else node

    def set(self, key: str, value: Any) -> None:
        parts = key.split(".")
        node = self._data
        for part in parts[:-1]:
            if part not in node or not isinstance(node[part], dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value

    def update(self, patch: dict[str, Any]) -> None:
        """Merge parcial (recursivo) sobre la config actual."""
        self._data = _deep_merge(self._data, patch)

    async def load(self) -> dict[str, Any]:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            await self.save()
            return self._data

        async with aiofiles.open(self.path, "r", encoding="utf-8") as f:
            raw = await f.read()

        try:
            loaded = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            loaded = {}

        # Merge sobre los defaults para tolerar config.json parcial o de una
        # versión anterior del esquema.
        self._data = _deep_merge(self.defaults(), loaded)
        # Migración: el default viejo de Ollama era una ruta fija de Linux;
        # vacío significa "resolver contra el PATH" (Windows/Mac/Linux).
        if self.get("backends.ollama.binary_path") == "/usr/bin/ollama":
            self.set("backends.ollama.binary_path", "")
        return self._data

    async def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(self.path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(self._data, indent=2, ensure_ascii=False))

    async def reset(self) -> dict[str, Any]:
        self._data = self.defaults()
        await self.save()
        return self._data


# Instancia única compartida por toda la app (importada por main.py y routers)
config = Config()


def get_cloud_pricing() -> CloudPricingConfig:
    """
    Los precios de nube validados. POST /api/config no valida el patch
    (deep-merge abierto), así que la garantía vive en la lectura: cualquier
    consumidor recibe el esquema completo, o los defaults si la sección quedó
    rota o incompleta.
    """
    try:
        return CloudPricingConfig.model_validate(config.get("cloud_pricing") or {})
    except (ValidationError, TypeError):
        return CloudPricingConfig()


def get_runtime_accel_choice() -> str | None:
    """(RT-10 B2) Aceleración elegida por el usuario para el runtime (config.
    runtime.accel). POST /api/config no valida el patch (deep-merge abierto), así
    que la garantía vive en la lectura: lo que no valida como RuntimeConfig se
    trata como "sin elección" → default del catálogo (Vulkan primero en AMD)."""
    try:
        return RuntimeConfig.model_validate(config.get("runtime") or {}).accel
    except (ValidationError, TypeError):
        return None
