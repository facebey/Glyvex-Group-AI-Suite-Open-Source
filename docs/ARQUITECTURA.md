# Arquitectura — mapa del código

Esta página es el mapa: qué proceso es qué, cómo se hablan, dónde vive cada
cosa y cómo fluye los datos. El *por qué* funcional de cada módulo está en
[modulos.md](modulos.md); acá está el *dónde*.

## Visión general

Dos procesos que se hablan por HTTP sobre `127.0.0.1`:

```
┌─────────────────────────────┐         ┌──────────────────────────────┐
│  Frontend (SPA React/Vite)  │  REST   │  Backend (FastAPI / Python)  │
│  frontend/                  │ ──────▶ │  backend/                    │
│  páginas + hooks + componentes │ ◀────── │  routers /api/* + WS + SSE  │
└─────────────────────────────┘  WS/SSE └──────────────┬───────────────┘
                                                       │ asyncio.subprocess
                                                       ▼
                                        llama-server / Ollama / LM Studio
```

- **Modo desarrollo:** Vite (`:5173`) sirve la SPA y hace proxy de `/api` al
  backend (`:7981`). Dos procesos, hot-reload.
- **Modo producción local:** `start.sh`/`start.cmd`/`start.ps1` compilan la SPA
  a `frontend/dist` y el backend la sirve desde `/` (`StaticFiles`), junto con
  la API. Un solo proceso en `:7981`.

El backend también orquesta los procesos de inferencia vía
`asyncio.subprocess` y, para el monitoreo en vivo, lee el endpoint Prometheus
`/metrics` de cada `llama-server`.

### Canales de comunicación

| Canal | Para qué | Ejemplo |
|---|---|---|
| **REST `/api/*`** | CRUD, estado, acciones | lanzar/stop, config, escanear, benchmarks |
| **WebSocket** | streams en vivo de bajo nivel | logs del launcher, GPU/CPU/RAM, vitales del LLM server |
| **SSE** | streaming de texto/razonamiento | respuesta del chat token a token, progreso del scan |

## Backend — `backend/`

FastAPI. `main.py` arma la app: `lifespan` (arranque/parada de pollers),
routers bajo `/api`, mount de la SPA y `/api/info` + `/api/health`.

| Archivo | Rol |
|---|---|
| `main.py` | App FastAPI, routers, `APP_VERSION`, lifespan, static mount. |
| `config.py` | Carga `data/config.json` con dot-notation, defaults, validación. |
| `paths.py` | `BASE_DIR` y helpers de rutas (valida contra path traversal). |
| `database.py` | SQLAlchemy async + aiosqlite (WAL) sobre `data/glyvex.db`. |
| `models.py` | **M1** — scanner e inventario de modelos, metadata GGUF, cache. |
| `launcher.py` | **M2** — launch/stop de inferencia, `probe_binary`, spec decoding. |
| `chat.py` | **M3** — streaming OpenAI-compatible, árbol de conversaciones, tools. |
| `benchmark.py` | **M4** — suite de prompts, runs cancelables, scoring, reportes. |
| `metrics.py` | **M5** — GPU/CPU/RAM en tiempo real (pynvml/psutil). |
| `metrics_store.py` | **M5** — histórico en `data/metrics.db` con rollup 5s/1m/1h. |
| `metrics_export.py` | Export de series de métricas. |
| `llm_metrics.py` | **M8** — poller del `/metrics` de `llama-server` (t/s, ctx, spec). |
| `stream_metrics.py` | t/s, TTFT y tokens desde los `timings` de `llama-server`. |
| `stt.py` | Voz a texto local (faster-whisper). |
| `tools.py` | Herramientas del modelo: búsqueda web + `fetch_url`. |
| `attachments.py` | Adjuntos (archivos/imágenes) y estimación de tokens. |
| `vram_estimate.py` | Estimación de VRAM por modelo/config. |
| `logsetup.py` | Configuración de logging. |

Routers montados en `main.py` (`api_router`): `/models`, `/launcher`, `/chat`,
`/benchmark`, `/metrics` (+`/metrics-export`), `/llm-metrics`, `/stt`.

## Frontend — `frontend/src/`

SPA React + Vite. Una página por vista, hooks para datos/streams, componentes
reutilizables.

- **`main.jsx`** — entry; monta el router.
- **`App.jsx`** — layout, navbar, `StatusWidget` (modelo activo + mini GPU),
  toasts globales, onboarding.
- **`pages/`** — una por vista:
  - `Chat.jsx` (`/`) — streaming, ramas, adjuntos, mic, razonamiento, tools.
  - `Launcher.jsx` (`/launcher`) — Group/List, templates HW, launch/stop,
    tira de vitales. `DEFAULT_LAUNCH_CONFIG` es el espejo del `LaunchConfig`
    de backend.
  - `Benchmark.jsx` (`/benchmark`) — selección de sets, run cancelable, progreso.
  - `Monitor.jsx` (`/monitor`) — GPU/CPU/RAM + sección LLM Server.
  - `Reports.jsx` (`/reports`) — historial, gráficos Recharts, comparación.
  - `Config.jsx` (`/config`) — backend, model_dirs, tools, STT, templates, monitor.
- **`components/`** — `ChatComposer`, `MessageBubble`, `MetricsBar`,
  `ToolActivity`, `MicButton`, `StatusWidget`, `ToastNotification`, etc.
- **`hooks/`** — `useChatStream`, `useLlmStream` (SSE/WS), `useConversations`,
  `useSpeechRecognition`, `useAudioRecorder`, `useAutoScroll`, `useLocalStorage`.
- **`lib/`** — `sse.js` (reader de SSE), `conversationTree.js`, `export.js`,
  `metricsDisplay.js`, `payload.js`.

## Flujos de datos

**Lanzar un modelo:** `Launcher.jsx` → `POST /api/launcher/launch` →
`launcher.py` hace `probe_binary` (descarta flags que la build no conoce) →
`asyncio.subprocess` levanta `llama-server --metrics` → logs por WS → health
check → el endpoint queda disponible para el Chat.

**Chatear:** `Chat.jsx` → `POST /api/chat` (SSE) → `chat.py` habla con el
endpoint OpenAI-compatible del modelo en streaming → separa bloques
`thinking` del contenido → `stream_metrics.py` calcula t/s/TTFT → si el modelo
emite `tool_calls`, corre el loop de tools (búsqueda/fetch) y vuelve al modelo
hasta `tools.max_rounds`.

**Monitoreo:** `metrics.py` muestrea GPU/CPU/RAM → buffer circular + poller de
`metrics_store.py` persiste a `metrics.db` (rollup 5s→1m→1h). En paralelo,
`llm_metrics.py` lee el `/metrics` de cada `llama-server` → vitales del proceso
por WS → se ven en la tira del Launcher y la sección LLM Server del Monitor.

## Persistencia — dónde vive el estado

Todo el estado de una instancia cuelga de `GLYVEX_DATA_DIR` (default `./data`):

| Ruta | Qué guarda |
|---|---|
| `config.json` | Configuración (backend, model_dirs, tools, STT, monitor). **No versionado.** |
| `models.json` | Inventario cacheado del scan (M1). |
| `glyvex.db` | SQLite: conversaciones/mensajes (árbol), benchmark runs/results, templates HW, sets de prompts. |
| `metrics.db` | Histórico de métricas con rollup 5s/1m/1h y retención configurable. |
| `attachments/` | Archivos/imágenes subidos al chat. |
| `benchmarks/` | Reportes HTML autónomos. |
| `logs/` | Logs del launcher. |

De solo lectura y compartido entre instancias (versionado en el repo):
`backend/prompts/` (sets de prompts) y `data/templates/hw_templates.json`
(semilla de templates de hardware).

## Multi-instancia

`start.* --env <nombre>` carga `environments/<nombre>.env`, que define
`GLYVEX_HOST/PORT`, `GLYVEX_DEV_PORT`, `GLYVEX_DATA_DIR` y
`GLYVEX_CORS_ORIGINS`. Cada entorno tiene su propio `GLYVEX_DATA_DIR`, así que
dos instancias no se pisan. `vite.config.js` lee el mismo archivo para alinear
el dev server y el proxy.

## Invariantes clave

- **100% local por diseño**: la única salida a red es la búsqueda/fetch de las
  herramientas del modelo. El bind por defecto es `127.0.0.1`.
- **Probe de binario** (`probe_binary` + `filter_command` en `launcher.py`):
  los flags que la build de `llama-server` no conoce se descartan y se
  reportan, no se rompe el launch. Esto es lo que permite correr contra builds
  distintas.
- **Dual JSON/SQLite**: `config.json` y `models.json` en JSON para
  portabilidad/inspección; el resto en SQLite.
- **Tests 100% mockeados**: sin GPU, sin modelos, sin red real (mock LLM server
  uvicorn en `:18080`, binarios fake, SQLite en memoria). Ver `tests/`.

## Enlaces

- [`modulos.md`](modulos.md) — detalle técnico por módulo (M0–M8).
- [`launcher-params.md`](launcher-params.md) — parámetros de `llama-server`.
- [`CONTRIBUTING.md`](../CONTRIBUTING.md) — cómo contribuir.
