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
from typing import Any

import aiofiles
from pydantic import BaseModel, Field

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
    ollama: BackendConfig = Field(
        default_factory=lambda: BackendConfig(
            binary_path="/usr/bin/ollama", default_port=11434, enabled=False
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

    # "auto" | "browser" | "whisper". Con "auto" el frontend usa la Web Speech
    # API si el navegador la tiene y cae a Whisper local si no — que es el
    # caso del WebView de Tauri, donde la API del navegador no existe.
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


class ConfigSchema(BaseModel):
    """Esquema completo de config.json, usado solo para validar/generar defaults."""

    app: AppConfig = Field(default_factory=AppConfig)
    hardware: HardwareConfig = Field(default_factory=HardwareConfig)
    backends: BackendsConfig = Field(default_factory=BackendsConfig)
    attachments: AttachmentsConfig = Field(default_factory=AttachmentsConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    monitor: MonitorConfig = Field(default_factory=MonitorConfig)
    display: DisplayConfig = Field(default_factory=DisplayConfig)
    exports: ExportsConfig = Field(default_factory=ExportsConfig)
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
