# Módulos — detalle técnico

Detalle por módulo. El README trae la tabla resumen; acá está el cómo.

## M0 — Skeleton

Base del proyecto: FastAPI + React + config persistida en `data/config.json`
con dot-notation.

## M1 — Inventario

Escanea los `model_dirs` configurados (GGUF/GGML/safetensors), extrae
familia/parámetros/cuantización del nombre de archivo y lee **metadata GGUF**
(arch, vocab, context, chat template, BOS/EOS), cachea en `data/models.json`.

- **Detecta módulos embebidos** en el propio `.gguf` (cabeza MTP/NextN
  `*.nextn.*` y encoder de visión) leyendo el header una sola vez.
- Scan incremental por `path+mtime`, progreso vía SSE.
- **Agrupación por carpeta** con variantes (base + MTP + mmproj), tags por
  modelo.
- Si cambia el esquema de campos derivados del header
  (`MODELS_CACHE_SCHEMA_VERSION`), el cache se invalida una sola vez y el
  próximo scan recalcula todo.

## M2 — Launcher

Lanza `llama-server`/Ollama/LM Studio con `asyncio.subprocess`, templates de
hardware predefinidos, logs en vivo por WebSocket (colapsa las líneas "slots
idle" repetidas para no enterrar la terminal), health check async, stop/
restart limpios (SIGTERM→SIGKILL).

- **Speculative decoding MTP/NextN:** origen del draft embebido en el modelo,
  sidecar detectado o path manual (UI de origen), flags `--spec-type
  draft-mtp` / `--spec-draft-model` / `--spec-draft-n-max` y cache types
  propios del draft (`--spec-draft-type-k/-v`).
- Arranca `llama-server` con `--metrics` (endpoint Prometheus `/metrics`)
  para alimentar los vitales del proceso (M8).
- **Probe de capacidades del binario:** se lee `--help` una vez por build y
  se descartan los flags que esa build no conoce (con warning), para que la
  app funcione contra builds distintas sin romperse. Ver
  [launcher-params.md](launcher-params.md) para la referencia completa de
  parámetros.

## M3 — Chat

Interfaz de chat OpenAI-compatible con streaming (SSE), separación de bloques
`thinking` (razonamiento) del contenido de respuesta, métricas en vivo de t/s,
TTFT y tokens generados (MetricsBar, calculadas en `stream_metrics.py` desde
los `timings` de llama-server: cuenta el razonamiento y las rondas de tools
sin inflar ni cortar las tasas), cancelación de generación (Esc), export a
JSON/Markdown.

- **Conversaciones en SQLite** con árbol de mensajes (regeneraciones =
  ramas), búsqueda e historial.
- **Adjuntos:** archivos e imágenes (PDF, DOCX, PPTX, XLSX) con límites
  configurables y estimación de tokens.
- **Tool calling nativo:** loop de `tool_calls` del modelo con rondas
  configurables (`tools.max_rounds`) y visualización de actividad
  (ToolActivity).
- **Voz a texto:** micrófono en el composer (Web Speech API en el browser,
  fallback a **faster-whisper** en el backend, idioma configurable, default
  es-AR). Ver [voz-a-texto.md](voz-a-texto.md).

## M4 — Benchmark

Suite de 58 prompts en 6 categorías (programación, matemática, ciencias,
lógica, español, infraestructura de redes) — extensible con **sets propios**
(CRUD) — corridos como `asyncio.Task` cancelable, con progreso por WebSocket,
scoring por prompt (keywords automáticas, **judge LLM** opcional, score
manual), reporte HTML autónomo, historial de runs en SQLite, comparación de
runs y vista `/reports` con gráficos Recharts.

## M5 — Monitor

GPU (pynvml)/CPU/RAM (psutil) en tiempo real vía WebSocket con buffer
circular de 300 muestras, degrada a `null` sin crashear si no hay GPU NVIDIA
o falta psutil.

- **Histórico persistente** (`metrics_store.py`): poller en segundo plano
  desde que arranca la app que guarda series en `data/metrics.db` (SQLite)
  en ventanas de 5 s, con rollup a 1 min y 1 h y retención configurable
  (`monitor.retention_raw_h/1m_d/1h_d`, default 48 h / 30 d / 365 d);
  `GET /api/metrics/series` y `/api/metrics/query` eligen la resolución
  según el rango pedido.

## M6 — Integración final

Widget de estado en la navbar (modelo activo + mini GPU), sistema de toasts
(Context + `useReducer`), shortcuts de teclado en Chat, estado persistido en
`localStorage`, onboarding para instalaciones nuevas, `/api/info` +
`/api/state`.

## M7 — Base de datos

SQLAlchemy async + aiosqlite (WAL) en `data/glyvex.db`: conversaciones/
mensajes (árbol), benchmark runs/results/summaries, templates de hardware,
sets de prompts. Dual con JSON (`config.json`, `models.json`) para
portabilidad de reportes.

## M8 — Vitales del LLM server

Poller que lee el endpoint Prometheus `/metrics` de cada `llama-server` en
ejecución (`llm_metrics.py`): generación t/s, prompt processing, contexto
ocupado, % de tokens de prompt reutilizados del caché
(`prompt_tokens_cached_total`), % de aceptación de la decodificación
especulativa MTP/draft (`spec_decode_num_*`), pico de secuencia y cola de
requests, con detección de reinicio (no genera picos falsos). Intervalo
adaptativo: 1 s con clientes WS conectados, 5 s en segundo plano (se despierta
enseguida al abrir el panel). REST `/api/llm-metrics` (procesos vivos +
históricos, history por proceso, WS stream).

Se muestra como **tira de vitales** en el Launcher (por proceso) y como
**sección LLM Server** en el Monitor (tiles en vivo + gráficos históricos
desde `metrics.db`, separados por proceso). Requiere que el launcher arranque
`llama-server` con `--metrics` (Ollama/LM Studio no lo exponen).
