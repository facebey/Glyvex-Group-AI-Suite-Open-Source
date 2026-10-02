# Architecture — code map

> **Language:** [Español](ARCHITECTURE.es.md)

This page is the map: which process is which, how they talk, where everything
lives and how data flows. The functional *why* of each module is in
[modules.md](modules.md); this is the *where*.

## Overview

Two processes talking over HTTP on `127.0.0.1`:

```
┌─────────────────────────────┐         ┌──────────────────────────────┐
│  Frontend (SPA React/Vite)  │  REST   │  Backend (FastAPI / Python)  │
│  frontend/                  │ ──────▶ │  backend/                    │
│  pages + hooks + components │ ◀────── │  routers /api/* + WS + SSE   │
└─────────────────────────────┘  WS/SSE └──────────────┬───────────────┘
                                                        │ asyncio.subprocess
                                                        ▼
                                         llama-server / Ollama / LM Studio
```

- **Development mode:** Vite (`:5173`) serves the SPA and proxies `/api` to
  the backend (`:7981`). Two processes, hot reload.
- **Local production mode:** `start.sh`/`start.cmd`/`start.ps1` compile the SPA
  to `frontend/dist` and the backend serves it from `/` (`StaticFiles`),
  together with the API. A single process on `:7981`.

The backend also orchestrates the inference processes via
`asyncio.subprocess` and, for live monitoring, reads the Prometheus endpoint
`/metrics` of each `llama-server`.

### Communication channels

| Channel | For what | Example |
|---|---|---|
| **REST `/api/*`** | CRUD, state, actions | launch/stop, config, scan, benchmarks |
| **WebSocket** | low-level live streams | launcher logs, GPU/CPU/RAM, LLM server vitals |
| **SSE** | text/reasoning streaming | chat response token by token, scan progress |

## Backend — `backend/`

FastAPI. `main.py` assembles the app: `lifespan` (pollers start/stop),
routers under `/api`, SPA mount and `/api/info` + `/api/health`.

| File | Role |
|---|---|
| `main.py` | FastAPI app, routers, `APP_VERSION`, lifespan, static mount. |
| `config.py` | Loads `data/config.json` with dot-notation, defaults, validation. |
| `paths.py` | `BASE_DIR` and path helpers (validates against path traversal). |
| `database.py` | SQLAlchemy async + aiosqlite (WAL) over `data/glyvex.db`. |
| `models.py` | **M1** — model scanner and inventory, GGUF metadata, cache. |
| `launcher.py` | **M2** — inference launch/stop, `probe_binary`, spec decoding. |
| `chat.py` | **M3** — OpenAI-compatible streaming, conversation tree, tools. |
| `benchmark.py` | **M4** — prompt suites, cancelable runs, scoring, reports. |
| `metrics.py` | **M5** — real-time GPU/CPU/RAM (pynvml/psutil). |
| `metrics_store.py` | **M5** — history in `data/metrics.db` with 5s/1m/1h rollups. |
| `metrics_export.py` | Metric series export. |
| `llm_metrics.py` | **M8** — poller of `llama-server`'s `/metrics` (t/s, ctx, spec). |
| `stream_metrics.py` | t/s, TTFT and tokens from `llama-server`'s `timings`. |
| `stt.py` | Local speech-to-text (faster-whisper). |
| `tools.py` | Model tools: web search + `fetch_url`. |
| `attachments.py` | Attachments (files/images) and token estimation. |
| `vram_estimate.py` | VRAM estimation per model/config. |
| `logsetup.py` | Logging configuration. |

Routers mounted in `main.py` (`api_router`): `/models`, `/launcher`, `/chat`,
`/benchmark`, `/metrics` (+`/metrics-export`), `/llm-metrics`, `/stt`.

## Frontend — `frontend/src/`

React + Vite SPA. One page per view, hooks for data/streams, reusable
components.

- **`main.jsx`** — entry; mounts the router.
- **`App.jsx`** — layout, navbar, `StatusWidget` (active model + mini GPU),
  global toasts, onboarding.
- **`pages/`** — one per view:
  - `Chat.jsx` (`/`) — streaming, branches, attachments, mic, reasoning, tools.
  - `Launcher.jsx` (`/launcher`) — Group/List, HW templates, launch/stop,
    vitals strip. `DEFAULT_LAUNCH_CONFIG` is the mirror of the backend's
    `LaunchConfig`.
  - `Benchmark.jsx` (`/benchmark`) — set selection, cancelable run, progress.
  - `Monitor.jsx` (`/monitor`) — GPU/CPU/RAM + LLM Server section.
  - `Reports.jsx` (`/reports`) — history, Recharts graphs, comparison.
  - `Config.jsx` (`/config`) — backend, model_dirs, tools, STT, templates, monitor.
- **`components/`** — `ChatComposer`, `MessageBubble`, `MetricsBar`,
  `ToolActivity`, `MicButton`, `StatusWidget`, `ToastNotification`, etc.
- **`hooks/`** — `useChatStream`, `useLlmStream` (SSE/WS), `useConversations`,
  `useSpeechRecognition`, `useAudioRecorder`, `useAutoScroll`, `useLocalStorage`.
- **`lib/`** — `sse.js` (SSE reader), `conversationTree.js`, `export.js`,
  `metricsDisplay.js`, `payload.js`.

## Data flows

**Launching a model:** `Launcher.jsx` → `POST /api/launcher/launch` →
`launcher.py` runs `probe_binary` (drops flags the build does not know) →
`asyncio.subprocess` starts `llama-server --metrics` → logs over WS → health
check → the endpoint is available for the Chat.

**Chatting:** `Chat.jsx` → `POST /api/chat` (SSE) → `chat.py` talks to the
model's OpenAI-compatible endpoint in streaming → separates
`thinking` blocks from content → `stream_metrics.py` computes t/s/TTFT → if the
model emits `tool_calls`, it runs the tool loop (search/fetch) and goes back
to the model up to `tools.max_rounds`.

**Monitoring:** `metrics.py` samples GPU/CPU/RAM → circular buffer + the
`metrics_store.py` poller persists to `metrics.db` (5s→1m→1h rollup). In
parallel, `llm_metrics.py` reads each `llama-server`'s `/metrics` → process
vitals over WS → shown in the Launcher strip and the Monitor's LLM Server
section.

## Persistence — where state lives

All the state of an instance hangs off `GLYVEX_DATA_DIR` (default `./data`):

| Path | What it stores |
|---|---|
| `config.json` | Configuration (backend, model_dirs, tools, STT, monitor). **Not versioned.** |
| `models.json` | Cached inventory of the scan (M1). |
| `glyvex.db` | SQLite: conversations/messages (tree), benchmark runs/results, HW templates, prompt sets. |
| `metrics.db` | Metrics history with 5s/1m/1h rollup and configurable retention. |
| `attachments/` | Files/images uploaded to the chat. |
| `benchmarks/` | Self-contained HTML reports. |
| `logs/` | Launcher logs. |

Read-only and shared across instances (versioned in the repo):
`backend/prompts/` (prompt sets) and `data/templates/hw_templates.json`
(hardware templates seed).

## Multi-instance

`start.* --env <name>` loads `environments/<name>.env`, which defines
`GLYVEX_HOST/PORT`, `GLYVEX_DEV_PORT`, `GLYVEX_DATA_DIR` and
`GLYVEX_CORS_ORIGINS`. Each environment has its own `GLYVEX_DATA_DIR`, so
two instances do not overwrite each other. `vite.config.js` reads the same
file to align the dev server and the proxy.

## Key invariants

- **100% local by design**: the only outbound network is the search/fetch of
  the model's tools. The default bind is `127.0.0.1`.
- **Binary probe** (`probe_binary` + `filter_command` in `launcher.py`):
  flags the `llama-server` build does not know are dropped and
  reported, the launch is not broken. This is what allows running against
  different builds.
- **Dual JSON/SQLite**: `config.json` and `models.json` in JSON for
  portability/inspection; the rest in SQLite.
- **100% mocked tests**: no GPU, no models, no real network (mock LLM server
  uvicorn on `:18080`, fake binaries, in-memory SQLite). See `tests/`.

## Links

- [`modules.md`](modules.md) — technical detail per module (M0–M8).
- [`launcher-params.md`](launcher-params.md) — `llama-server` parameters.
- [`CONTRIBUTING.md`](../CONTRIBUTING.md) — how to contribute.
