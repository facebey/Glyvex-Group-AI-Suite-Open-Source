# Changelog

> **Language:** [Español](CHANGELOG.es.md)

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: semantic `X.Y.Z` (see README, "Versioning" section).

## [Unreleased]

### Fixed
- **Unified version in 0.7.1-beta**: `APP_VERSION` (`/api/health`,
  `/api/info`), `frontend/package.json` and `src-tauri/Cargo.toml` were
  still on 0.6.0 while the installer was already 0.7.1-beta.
- **App footer**: "All rights reserved" contradicted the Apache 2.0
  license; it now says "© Glyvex Group · Apache 2.0".
- **i18n**: the header state ("No active model"), the Backends labels in
  Config, the Monitor's Power/Clock and 3 Launcher errors were fixed in one
  language; they now go through the EN/ES locales.
- **Config**: the path examples follow the OS (Windows paths on Windows) and
  no longer show a personal path.
- **Monitor**: the alert badge says what triggered it (temperature or VRAM)
  and explains the thresholds in the tooltip; "NVIDIA GPU not detected"
  respects the UI language.
- **Chat**: the model selector shows the filename instead of the full path
  (which stays in the tooltip).
- `SyntaxWarning` for an escape sequence in the `paths.py` docstring.

### Documentation
- README EN/ES: **Download** section with link to the installer, CI and
  release badges, **voice** section (STT whisper.cpp / faster-whisper /
  browser and TTS Kokoro / Piper / SAPI), NSIS installer (not MSI), test
  count.
- `docs/speech-to-text.md` rewritten: it only covered faster-whisper and
  said there was no TTS.
- QUICKSTART with `git clone` and shortcut to the installer; SECURITY
  supports 0.7.x.

### CI
- The frontend job runs on Node 20 and 22 and executes the vitest tests.

## [0.7.1-beta] — 2026-09-30

### Fixed
- **TTS**: playback goes back to the `<audio>` element (the rewrite to
  WebAudio left the audio silent in some Chrome/Edge/WebView2
  environments). A playback or autoplay failure is now shown in a notice,
  instead of silence or a stuck button.
- **TTS**: WAV cache in the backend (LRU 32): re-listening to the same
  response is instant and does not re-synthesize.
- **Config**: the Web Search / STT / TTS detection in Options no longer
  takes 30-45 s (TTL cache 60 s per config signature; the "Check" button
  forces the re-probe).
- **Chat**: the last 5 custom endpoints are persisted (localStorage) and
  appear in the selector so you do not have to type the URL again.

## [0.7.0-beta] — 2026-09-30

### Added
- **Neural TTS (Piper + Kokoro) packaged in the sidecar**: the neural chain
  (piper, kokoro-onnx, onnxruntime, espeak-ng) now travels inside
  `glyvex-backend` (Phase 1b of the Tauri packaging). Voices and model are
  downloaded in Settings → TTS; the `auto` engine picks Kokoro → Piper →
  SAPI according to what is ready.
- **Provisioning screen — TTS step**: in addition to the SAPI voices
  check, a direct pointer to configure the neural TTS in Settings.

### Changed
- **Dependency bumps**: uvicorn 0.54, sqlalchemy 2.1.1, nvidia-ml-py
  13.615.71 (backend) and vite 8.3.1 (frontend).
- **numpy floor 2.2** in `requirements.txt`: 2.5 required Python >=3.12 and
  broke the install of the CI job 3.11.

### Fixed
- **CI on Linux**: 3 tests assumed Windows (STT/TTS status) and now force
  the platform via monkeypatch; the suite passes on 3.11 and 3.12.

## [0.6.3-beta] — 2026-09-28

### Added
- **Native Windows packaging (Tauri 2 + NSIS)** per-user (no UAC):
  WebView2 bootstrapper (auto-download on old Win10), wizard branding
  (header 150x57 + sidebar 164x314), shortcut in Start → "Glyvex".
- **Sidecar lifecycle**: the app starts `glyvex-backend` (PyInstaller),
  health-check with auto-retry, kill when the window closes, and splash/error
  overlay with a Retry button in the UI.
- **Stable SPA origin (7981)**: the release always starts on 7981 (with
  sequential fallback 7982…7990 only if it is busy); the SPA is served by
  the backend with relative `/api/...` routes.
- **`--provision` startup**: the app opens directly in the provisioning
  screen.
- **Provisioning screen (T5.3)**: 3 steps — llama.cpp runtime (required),
  STT whisper.cpp + base model, TTS (SAPI voices check) — with real download
  progress, retries and "Enter the app".
- **STT whisper.cpp (native)**: downloadable runtime (binary + ggml-base
  model), microphone dictation to WAV 16 kHz and engine selector (auto /
  browser / whisper.cpp / faster-whisper) in Options.
- **TTS SAPI (Windows local)**: "listen" button on chat responses and a
  section in Options with state, enable/disable, voice and speed.
- **Unified transcription language**: a single selector for all engines
  (auto / es / en / pt / fr / de / it) in Options.
- **Collapsible panels** in Launcher (the 11, including Basic and Advanced
  Options) and Monitor (GPU, CPU, RAM, History, LLM Server, Processes):
  clickable header, per-panel state persisted (localStorage).

### Fixed
- **STT model download in Options**: the crash `Cannot read properties of
  null (reading 'aborted')` cut the progress in the UI (the download kept
  going in the backend and the retry fell into "a download is already in
  progress"); now the stream is read with a valid signal and the screen
  refreshes on its own until the model is ready.
- **faster-whisper message in the packaged app**: before it sent you to
  `pip install` (does not apply to the bundle); now the STT state reports
  `packaged` and the UI stops showing that engine's warning card with the
  `auto` engine (and offers it disabled in the selector), because it can
  never be installed in the bundle.
- **Inherited language in local STT**: whisper.cpp and faster-whisper
  ignored `stt.language` and transcribed in English (the engines' default);
  now they inherit it unless there is an explicit override.
- **host/port in templates**: the configured port was lost when saving/
  editing a template (those fields were missing in `TEMPLATE_FIELDS`).
- **Packaged app: theme/options did not persist** (PERS-1): the release
  backend asked the OS for a random free port, `localStorage` was per origin
  and each startup landed on a different one; now the release always starts
  on 7981 (with sequential fallback 7982…7990 if it is busy) and the
  persistence survives app close/open.
- **Packaged app: `ERR_CONNECTION_REFUSED` / error screen on open**: the
  Tauri shell redirected to the SPA before the backend was listening
  (bootstrap race); now the shell emits the sidecar state via events (with a
  5 s heartbeat) and the UI redirects only when the health-check passes,
  with a reload fallback at 180 s.
- **Launcher: "unknown build" with llama.cpp b11146**: the `--version` probe
  only read the first line (which in that build is an initialization log
  without a number); now it walks all lines until it finds the build.
- **Config → Runtime → Reinstall: false "Windows-only" notice**: the reset
  endpoint did not return `platform` (only the status one did); now the
  reset includes the platform and the UI shows the correct download screen.
- **STT: automatic fallback to faster-whisper**: with the `auto` engine and
  the whisper.cpp runtime unavailable, before it stayed without
  transcription; now it falls to faster-whisper if it is installed (and the
  packaged UI does not offer that engine, since it cannot be installed in
  the bundle).
- **Flickering CMD windows when opening the Launcher and launching models**:
  the console children (launcher `--help`/`--version` probes and whisper-cli,
  llama-server startup) are now created without a window
  (`CREATE_NO_WINDOW` on Windows).

### Changed
- **Responsive layout**: app max-w 1600 px (2100 on the Launcher route),
  sidebar 360 px on 2xl+ and history drawer 360 px (before it was cut on
  1280 px screens).

### Baseline
- Full suite: **471 tests green**; production build OK; NSIS installer
  verified on machine (silent install → sidecar → SPA → shortcut),
  re-packaging 2026-09-28 with the fixes above validated in the exe.

## [0.6.2-beta] — 2026-09-26

### Changed
- **Major frontend dependency migration** (one branch per library, build
  and tests green at each step):
  | Dependency | Before | After |
  |---|---|---|
  | `react` / `react-dom` | ^18.3.0 | ^19.3.0 |
  | `lucide-react` | ^0.400.0 | ^1.47.0 |
  | `react-markdown` | ^9.0.1 | ^10.1.0 |
  | `recharts` | ^2.12.0 | ^3.10.0 |
- **lucide-react 1.x** removed the brand icons: the `Github` icon in the
  footer became `ExternalLink` (`frontend/src/App.jsx`).

### Baseline
- Full suite: **358 tests green**; production build OK; manual smoke of the
  6 views.

## [0.6.1-beta1] — 2026-09-26

### Added
- **MSI installer branding v3**: wizard banner (overrides the WixUI
  default), custom image in the ExitDialog and **molecule icon** in the
  wizard, Control Panel (`ProductIcon`) and `ARPPRODUCTICON`.
- **Per-user installer** for Windows x64 (no UAC): the app's data lives in
  `%LOCALAPPDATA%\Glyvex-AI-Suite\data`, independent of the installation
  directory.

## [0.6.0] — 2026-09-25

### Added
- **Theme system (5)**: carbon (fiber texture), metallic (per surface +
  metal texture) and matrix (digital rain on canvas), plus the light mode
  redesigned as **lavender**. Cycle from the header's palette button or from
  Config; textures generated by code (no third-party licenses).
- **Embedded runtime b11146 (llama.cpp v0.5.0)**: pin bump from b11009
  (sha256 + feature-detect by probe).
- **Per-user WiX MSI packaging** (`build.ps1 -MakeInstaller`): wizard,
  shortcuts and upgrade; console-less PyInstaller sidecar; trimmed bundle
  **143.6→88 MB** (excludes gguf/numpy and trims babel locale-data).
- **`GLYVEX_NO_BROWSER`**: the FROZEN bundle does not open the browser on
  its own.
- **requires-python >= 3.11** declared (pyproject + guard test).

### Changed
- **Dependency floors** (pip) to tested versions + `react-router-dom`
  7.18.4.
- Packaging: Nuitka removed — the decision was PyInstaller (T-2).
- Glyvex icon in the exe and the MSI (wxs Icon + WixUI_Banner).
- Light palette: contrast tokens and soft gray background; temperature in
  the status box with `-800` tokens readable in light mode.

### Fixed
- MSI: missing `WIXUI_INSTALLDIR` row (error 2819) and `WIXUI_INSTALLDIR`
  pointing to `APPDIR` (error 2343, ERROR_BAD_PATH).
- **TDP toggle**: restored from the GPU's real state on load.
- **fit toggle**: ON now sends `fit=true` in the payload.
- Config's theme selector applies the theme instantly.

### Baseline
- Full suite: **356 tests green**.

## [0.5.0] — 2026-09-22

### Added
- **Embedded inference runtime**: on Windows the suite downloads its own
  tested llama.cpp build (pin **b11009**), **sha256**-verified, without
  installing anything else. Split in 2 levels: **base engine** (~19 MB,
  runs on any hardware) + **NVIDIA acceleration** (~531 MB, CUDA 13.4),
  chosen by the detected GPU family (`nvidia|amd|intel|cpu`). Download in 2
  stages with progress over SSE; if the acceleration fails, it degrades to
  CPU (state "degraded"), never an error. Sources with preference: the
  suite's own asset (v0.5.0 release of the open source repo) → official
  ggml-org/llama.cpp build (fallback).
- **Binary cascade at launch**: `binary_path` (expert mode) always wins →
  managed runtime if it is `ready` → otherwise the launch is rejected with
  `runtime_missing` and the UI offers to download it.
- **Runtime API**: `GET /api/runtime/status` (GPU + family, state,
  version), `POST /api/runtime/download` (SSE with progress),
  `POST /api/runtime/reset`.
- **Runtime UI**: "Download runtime" step in the onboarding (detected GPU,
  estimated size by family, progress, states), "Runtime" card in Config
  (state, reinstall) and runtime chip in the Launcher. On Windows the base
  engine is offered to any GPU; AMD/Intel run on CPU with a note "the
  acceleration is coming soon".
- **M5 — Monitor: TDP control** (power limit) of the GPU in the GPU card
  (nvidia-ml-py).
- **Footer** with link to the public GitHub repo (OSS Apache 2.0).
- Full ES/EN i18n for the new runtime views.

### Changed
- **Dependencies**: `pynvml` (deprecated) replaced by `nvidia-ml-py`
  (NVIDIA's official library).
- The runtime archives are resolved from the release of the public open
  source repo (`Glyvex-Group-AI-Suite-Open-Source`).

### Tests
- New runtime test suite: core `runtime.py` (download verified against a
  fake server, extract, states, source cascade), API (status/download
  SSE/reset) and the launcher cascade. Full suite: **356 tests green**.

## [0.4.3] — 2026-09-21

### Added
- **A3 — ES/EN i18n**: react-i18next with a language selector in the UI
  (default Spanish). Covers Launcher, Chat, Monitor, Benchmark, Reports and
  Config (706 keys per language); benchmark sets' names and descriptions
  bilingual (fallback to JSON); `frontend/package-lock.json` versioned for
  reproducible installs.
- **P8 — Flags reference**: `docs/LAUNCH-FLAGS.md` (EN) +
  `docs/LAUNCH-FLAGS.es.md` (ES) — ~45 flags in 14 groups (what it does,
  impact, app default, when to change it); official labels per flag in the
  Launcher UI via the probe (`flag_help`).
- **Docs (P7)**: central "Documentation" index in the README (EN/ES),
  `docs/QUICKSTART.md`, `docs/ARQUITECTURA.md`, `docs/PRIVACIDAD.md`,
  `.github` templates (issue bug/feature, config, PR template) and
  `CONTRIBUTORS.md` + up-for-grabs in `CONTRIBUTING.md`.
- **Dependabot**: `.github/dependabot.yml` (pip + npm, weekly, cap of 10
  open PRs per ecosystem).

### Fixed
- **K3**: benchmark persistence race — the final status (completed/error)
  is saved in the DB before being exposed in memory.
- **`--fit` toggle**: OFF now emits `--fit off` instead of omitting the
  flag (which left the build's default `on`).

### Baseline
- 297 tests green.

## [0.4.2] — 2026-09-20

### Fixed
- **Native flag thinking/reasoning** (D8): `thinking_enabled` is now wired
  with `--reasoning on|off` instead of the `enable_thinking` kwarg in
  `--chat-template-kwargs`, deprecated in llama.cpp (warning in the server
  log). `budget_tokens` moves to the native `--reasoning-budget` (only with
  thinking on and if `reasoning_budget` is not explicit, which has
  priority); the `thinking_budget` kwarg was removed (no-op in templates
  like Qwen3).

### Updated
- `docs/TROUBLESHOOTING.md`: "context exceeded" case aligned with the real
  code (prior UI notice, `prompt + max_tokens > n_ctx` check, `max_tokens`
  default 4096 and "lower max_tokens" fix).

### Baseline
- 297 tests green.

## [0.4.1] — 2026-09-20

### Added
- **Public bilingual README**: `README.md` in English (primary, the one
  GitHub renders) + `README.es.md` in Spanish, with a language line in both.
- **New docs**: `docs/modulos.md` (technical detail M0–M8),
  `docs/launcher-params.md` (launch parameters reference),
  `docs/TROUBLESHOOTING.md` (common problems verified against the llama.cpp
  source), `CONTRIBUTING.md` and `screenshots/` (10 captures in the README).
- README improvements: requirements (NVIDIA/CPU-only, faster-whisper),
  security (network exposure opt-in), Docs section, 296 tests.

### Fixed
- Outdated README data (tests, versioning, hardware).

### Added
- **Apache 2.0 license** (Glyvex Group): official LICENSE + READMEs.
- **P1.5 — Toggles per flag**: 17 toggles per group in the Launcher
  (`None` field = flag not emitted = build default), **automatic mode**
  (strict command: only model + port), **official help per flag**
  (`_parse_flag_help` + `flag_help` in probe and `/backend-info`), `n_ubatch`
  with toggle, snapshot of toggles and automatic mode in templates.
- **Build 11009**: `--load-mode` (replaces `--mlock`/`--no-mmap`),
  bidirectional jinja, **Tier 1 VRAM** (`--fit`, `--fit-target`) and
  **Tier 2 reasoning** (`--reasoning-effort`, `--reasoning-budget`,
  `--no-reasoning-preserve`).
- **Build 11003 compat**: binary capabilities probe (reads `--help` and
  discards unknown flags with a warning), flag filtering, launch command
  preview (`/preview-command` + `/backend-info`), `--ctx-checkpoints`/
  `--checkpoint-min-step`, `--cache-ram`, `--kv-unified` (+46 tests).
- **VRAM estimator** (B6) + blob-based GGUF parser and cached metadata.
- **Thinking-aware**: `enable_thinking` detection by the GGUF header's
  `chat_template` and kwarg port in command, preview and UI.
- **Multiinstance**: `GLYVEX_DATA_DIR` per instance, CORS by env var,
  multi-port scripts and busy-port notice.
- Dead process cleanup in Monitor: bounded selector, purge of short-lived
  process history and freed memory buffers.

### Fixed
- **SSRF**: each `fetch_url` redirect is validated against private hosts.
- Benchmark: DELETE no longer cancels a finished run — it waits for the
  final persistence.
- Launcher: pre-fills the name when choosing a template and blocks
  overwriting predefined templates.
- `AGENTS.md` stops being versioned (local documentation).

### Baseline
- 296 tests green.

## [0.3.1] — 2026-09-17

### Added
- **Visible metrics and Prometheus/InfluxDB export** (A1/T4b).
- **Chat — filename convention**: parsing of `language:name.ext` in code
  fences + download button in CodeBlock (`rehypeFilename` transform before
  `rehype-highlight`).
- **Chat — bidirectional attachments**: convention toggle, preview with
  object URLs, thumbnails of unsent files, picker without filter.
- **LLM**: last measured tg with age in the vitals strip/Monitor +
  `/slots` failure notice only on change.

### Fixed
- Chat: image bytes are always saved (even if the model has no vision) +
  HEIC/AVIF/TIFF normalization to PNG.

## [0.3.0] — 2026-09-15

### Added
- **Current context from `/slots`**: in current builds `kv_cache_tokens`
  was removed from the `/metrics` endpoint; context usage now comes from
  the sum of the slots' sequences (`slots_total`/`slots_busy`, an idle slot
  keeps the last conversation), with fallback to `kv_cache_tokens` in old
  builds.

### Fixed
- Caps by `process_id` in `llm_metrics` and size-based rotation of
  `data/logs`.
- `useLlmStream` shared with reconnection + stable selection in Monitor.

## [0.2.1] — 2026-09-15

### Added
- **Monitor**: live LLM data over WebSocket + queue of the last 5 minutes.

### Fixed
- Launcher: no longer passes `--log-file` to llama-server (the app manages
  the logs).
- `llm_metrics`: compatibility with current llama-server builds.
- Launcher: collapses repeated "slots idle" lines in the log.

## [0.2.0] — 2026-09-15

First versioned release: complete suite M0–M8.

- **M0** FastAPI + React skeleton, config persisted with dot-notation.
- **M1** Inventory: `model_dirs` scan, GGUF metadata, embedded modules
  (MTP/vision), grouping by folder, tags.
- **M2** Launcher: `llama-server`/Ollama/LM Studio, hardware templates,
  live logs over WS, MTP/NextN, clean stop/restart.
- **M3** Chat: SSE streaming, separated reasoning, live metrics,
  conversation tree in SQLite, attachments, tool calling, speech to text.
- **M4** Benchmark: 58 prompts in 6 categories + custom sets, scoring,
  HTML reports, history and comparison.
- **M5** Monitor: live GPU/CPU/RAM + persistent history with configurable
  retention.
- **M6** Integration: status widget in navbar, toasts, shortcuts,
  onboarding.
- **M7** Database: async SQLite (WAL), conversations, benchmarks,
  templates, sets.
- **M8** LLM server vitals from `/metrics` (t/s, context, cache, MTP
  acceptance %), in the Launcher strip and the Monitor's LLM Server
  section.
