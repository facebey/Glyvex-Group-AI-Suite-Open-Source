# Modules — technical detail

> **Language:** [Español](modules.es.md)

Per-module detail. The README carries the summary table; this is the how.

## M0 — Skeleton

Project base: FastAPI + React + configuration persisted in `data/config.json`
with dot-notation.

## M1 — Inventory

Scans the configured `model_dirs` (GGUF/GGML/safetensors), extracts
family/parameters/quantization from the filename and reads **GGUF metadata**
(arch, vocab, context, chat template, BOS/EOS), caches in `data/models.json`.

- **Detects embedded modules** in the `.gguf` itself (MTP/NextN head
  `*.nextn.*` and the vision encoder) by reading the header once.
- Incremental scan by `path+mtime`, progress via SSE.
- **Folder grouping** with variants (base + MTP + mmproj), tags per model.
- If the schema of header-derived fields changes
  (`MODELS_CACHE_SCHEMA_VERSION`), the cache is invalidated once and the next
  scan recalculates everything.

## M2 — Launcher

Starts `llama-server`/Ollama/LM Studio with `asyncio.subprocess`, predefined
hardware templates, live logs over WebSocket (collapses repeated "slots idle"
lines so the terminal does not get buried), async health check, clean
stop/restart (SIGTERM→SIGKILL).

- **Speculative decoding MTP/NextN:** origin of the draft embedded in the
  model, detected sidecar, or manual path (origin UI), flags `--spec-type
  draft-mtp` / `--spec-draft-model` / `--spec-draft-n-max` and the draft's own
  cache types (`--spec-draft-type-k/-v`).
- Starts `llama-server` with `--metrics` (Prometheus endpoint `/metrics`)
  to feed the process vitals (M8).
- **Binary capability probe:** reads `--help` once per build and drops the
  flags that build does not know (with a warning), so the app works against
  different builds without breaking. See
  [launcher-params.md](launcher-params.md) for the full parameter reference.

## M3 — Chat

OpenAI-compatible chat interface with streaming (SSE), separation of
`thinking` blocks (reasoning) from response content, live t/s, TTFT and
generated tokens metrics (MetricsBar, computed in `stream_metrics.py` from
llama-server's `timings`: counts reasoning and tool rounds without inflating
or cutting the rates), generation cancellation (Esc), export to
JSON/Markdown.

- **Conversations in SQLite** with a message tree (regenerations =
  branches), search and history.
- **Attachments:** files and images (PDF, DOCX, PPTX, XLSX) with configurable
  limits and token estimation.
- **Native tool calling:** loop of the model's `tool_calls` with configurable
  rounds (`tools.max_rounds`) and activity visualization (ToolActivity).
- **Speech to text:** microphone in the composer (Web Speech API in the
  browser, fallback to **faster-whisper** in the backend, configurable
  language, default es-AR). See [speech-to-text.md](speech-to-text.md).

## M4 — Benchmark

Suite of 58 prompts in 6 categories (programming, math, science,
logic, Spanish, network infrastructure) — extensible with **custom sets**
(CRUD) — run as a cancelable `asyncio.Task`, with progress over WebSocket,
per-prompt scoring (automatic keywords, optional **LLM judge**, manual
score), self-contained HTML report, run history in SQLite, run comparison
and `/reports` view with Recharts graphs.

## M5 — Monitor

GPU (pynvml)/CPU/RAM (psutil) in real time via WebSocket with a 300-sample
circular buffer, degrades to `null` without crashing if there is no NVIDIA
GPU or psutil is missing.

- **Persistent history** (`metrics_store.py`): background poller from app
  start that stores series in `data/metrics.db` (SQLite) in 5 s windows,
  with rollup to 1 min and 1 h and configurable retention
  (`monitor.retention_raw_h/1m_d/1h_d`, default 48 h / 30 d / 365 d);
  `GET /api/metrics/series` and `/api/metrics/query` pick the resolution
  according to the requested range.

## M6 — Final integration

Status widget in the navbar (active model + mini GPU), toast system
(Context + `useReducer`), keyboard shortcuts in Chat, state persisted in
`localStorage`, onboarding for new installations, `/api/info` +
`/api/state`.

## M7 — Database

SQLAlchemy async + aiosqlite (WAL) in `data/glyvex.db`: conversations/
messages (tree), benchmark runs/results/summaries, hardware templates,
prompt sets. Dual with JSON (`config.json`, `models.json`) for report
portability.

## M8 — LLM server vitals

Poller that reads the Prometheus endpoint `/metrics` of each running
`llama-server` (`llm_metrics.py`): generation t/s, prompt processing, context
used, % of prompt tokens reused from cache
(`prompt_tokens_cached_total`), % of acceptance of MTP/draft speculative
decoding (`spec_decode_num_*`), sequence peak and request queue, with
restart detection (does not generate false spikes). Adaptive interval: 1 s
with connected WS clients, 5 s in background (wakes up immediately when the
panel opens). REST `/api/llm-metrics` (live + historical processes, history
per process, WS stream).

Shown as a **vitals strip** in the Launcher (per process) and as an
**LLM Server section** in the Monitor (live tiles + historical graphs from
`metrics.db`, separated per process). Requires the launcher to start
`llama-server` with `--metrics` (Ollama/LM Studio do not expose it).
