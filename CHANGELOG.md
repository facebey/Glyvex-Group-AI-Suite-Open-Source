# Changelog

Formato: [Keep a Changelog](https://keepachangelog.com/es/1.1.0/).
Versionado: semántico `X.Y.Z` (ver README, sección "Versionado").

## [0.5.0] — 2026-09-22

### Agregado
- **Runtime de inferencia embebido**: en Windows la suite descarga su propia
  build probada de llama.cpp (pin **b11009**), verificada por **sha256**,
  sin instalar nada aparte. Split en 2 niveles: **motor base** (~19 MB,
  corre en cualquier hardware) + **aceleración NVIDIA** (~531 MB, CUDA
  13.4), elegida por familia de GPU detectada (`nvidia|amd|intel|cpu`).
  Download en 2 etapas con progreso por SSE; si la aceleración falla,
  degrada a CPU (estado "degradado"), nunca es error. Fuentes con
  preferencia: asset propio de la suite (release v0.5.0 del repo open
  source) → build oficial de ggml-org/llama.cpp (fallback).
- **Cascada de binario al lanzar**: `binary_path` (modo experto) gana
  siempre → runtime gestionado si está `ready` → si no, el launch se
  rechaza con `runtime_missing` y la UI ofrece descargarlo.
- **API runtime**: `GET /api/runtime/status` (GPU + familia, estado,
  versión), `POST /api/runtime/download` (SSE con progreso),
  `POST /api/runtime/reset`.
- **UI runtime**: paso "Descargar runtime" en el onboarding (GPU
  detectada, tamaño estimado por familia, progreso, estados), card
  "Runtime" en Config (estado, reinstalar) y chip de runtime en el
  Launcher. En Windows el motor base se ofrece a toda la GPU; AMD/Intel
  corren en CPU con nota "la aceleración llega próximamente".
- **M5 — Monitor: control TDP** (power limit) de la GPU en la tarjeta
  GPU (nvidia-ml-py).
- **Footer** con link al repo público de GitHub (OSS Apache 2.0).
- i18n ES/EN completo para las vistas nuevas de runtime.

### Cambiado
- **Dependencias**: `pynvml` (deprecated) reemplazado por `nvidia-ml-py`
  (lib oficial de NVIDIA).
- Los archives del runtime se resuelven desde la release del repo open
  source público (`Glyvex-Group-AI-Suite-Open-Source`).

### Tests
- Nuevo test suite del runtime: núcleo `runtime.py` (download verificado
  contra server fake, extract, estados, cascada de fuentes), API
  (status/download SSE/reset) y cascada del launcher. Suite completa:
  **356 tests en verde**.

## [0.4.3] — 2026-09-21

### Agregado
- **A3 — i18n ES/EN**: react-i18next con selector de idioma en la UI
  (default español). Cubre Launcher, Chat, Monitor, Benchmark, Reports y
  Config (706 keys por idioma); nombres y descripciones de sets de
  benchmark bilingües (fallback al JSON); `frontend/package-lock.json`
  versionado para installs reproducibles.
- **P8 — Referencia de flags**: `docs/LAUNCH-FLAGS.md` (EN) +
  `docs/LAUNCH-FLAGS.es.md` (ES) — ~45 flags en 14 grupos (qué hace,
  impacto, default de la app, cuándo cambiarlo); etiquetas oficiales por
  flag en la UI del Launcher vía el probe (`flag_help`).
- **Docs (P7)**: índice central "Documentación" en README (EN/ES),
  `docs/QUICKSTART.md`, `docs/ARQUITECTURA.md`, `docs/PRIVACIDAD.md`,
  plantillas `.github` (issue bug/feature, config, PR template) y
  `CONTRIBUTORS.md` + up-for-grabs en `CONTRIBUTING.md`.
- **Dependabot**: `.github/dependabot.yml` (pip + npm, semanal, tope de 10
  PRs abiertas por ecosistema).

### Corregido
- **K3**: race de persistencia del benchmark — el status final
  (completed/error) se guarda en la DB antes de exponerlo en memoria.
- **`--fit` toggle**: OFF ahora emite `--fit off` en vez de omitir el flag
  (que dejaba el default `on` de la build).

### Baseline
- 297 tests en verde.


## [0.4.2] — 2026-09-20

### Corregido
- **Thinking/reasoning por flag nativo** (D8): `thinking_enabled` ahora se
  cablea con `--reasoning on|off` en vez del kwarg `enable_thinking` en
  `--chat-template-kwargs`, deprecado en llama.cpp (warning en el log del
  server). `budget_tokens` pasa a `--reasoning-budget` nativo (solo con
  thinking on y si `reasoning_budget` no está explícito, que tiene
  prioridad); se eliminó el kwarg `thinking_budget` (no-op en templates
  como Qwen3).

### Actualizado
- `docs/TROUBLESHOOTING.md`: caso "contexto excedido" alineado al código
  real (aviso previo de la UI, chequeo `prompt + max_tokens > n_ctx`,
  default de `max_tokens` 4096 y fix "bajar max_tokens").

### Baseline
- 297 tests en verde.


## [0.4.1] — 2026-09-20

### Agregado
- **README público bilingue**: `README.md` en inglés (principal, el que
  renderiza GitHub) + `README.es.md` en español, con línea de idiomas en
  ambos.
- **Docs nuevas**: `docs/modulos.md` (detalle técnico M0–M8),
  `docs/launcher-params.md` (referencia de parámetros de lanzamiento),
  `docs/TROUBLESHOOTING.md` (problemas frecuentes verificados contra el
  source de llama.cpp), `CONTRIBUTING.md` y `screenshots/` (10 capturas en
  el README).
- Mejoras al README: requisitos (NVIDIA/CPU-only, faster-whisper), seguridad
  (exposición de red opt-in), sección Docs, 296 tests.

### Corregido
- Datos desactualizados del README (tests, versionado, hardware).


### Agregado
- **Licencia Apache 2.0** (Glyvex Group): LICENSE oficial + READMEs.
- **P1.5 — Toggles por flag**: 17 toggles por grupo en el Launcher
  (campo `None` = flag no se emite = default de la build), **modo
  automático** (comando estricto: solo modelo + puerto), **ayuda oficial
  por flag** (`_parse_flag_help` + `flag_help` en probe y `/backend-info`),
  `n_ubatch` con toggle, snapshot de toggles y modo automático en
  templates.
- **Build 11009**: `--load-mode` (reemplaza a `--mlock`/`--no-mmap`),
  jinja bidireccional, **Tier 1 VRAM** (`--fit`, `--fit-target`) y
  **Tier 2 reasoning** (`--reasoning-effort`, `--reasoning-budget`,
  `--no-reasoning-preserve`).
- **Compat build 11003**: probe de capacidades del binario (lee `--help`
  y descarta flags desconocidos con warning), filtrado de flags, preview
  del comando de lanzamiento (`/preview-command` + `/backend-info`),
  `--ctx-checkpoints`/`--checkpoint-min-step`, `--cache-ram`,
  `--kv-unified` (+46 tests).
- **Estimador de VRAM** (B6) + parser GGUF blob-based y metadata cacheada.
- **Thinking-aware**: detección de `enable_thinking` por `chat_template`
  del header GGUF y porte del kwarg en comando, preview y UI.
- **Multiinstancia**: `GLYVEX_DATA_DIR` por instancia, CORS por env var,
  scripts multi-puerto y aviso de puerto ocupado.
- Limpieza de procesos muertos en Monitor: selector acotado, purga del
  histórico de procesos cortos y buffers liberados de memoria.

### Corregido
- **SSRF**: cada redirección de `fetch_url` se valida contra hosts
  privados.
- Benchmark: DELETE ya no cancela un run terminado — espera la
  persistencia final.
- Launcher: prellena el nombre al elegir template y bloquea la
  sobrescritura de templates predefinidos.
- `AGENTS.md` deja de versionarse (documentación local).

### Baseline
- 296 tests en verde.

## [0.3.1] — 2026-09-17

### Agregado
- **Métricas visibles y exportación Prometheus/InfluxDB** (A1/T4b).
- **Chat — convención de nombres de archivos**: parseo de
  `lenguaje:nombre.ext` en fences de código + botón de descarga en
  CodeBlock (transform `rehypeFilename` antes de `rehype-highlight`).
- **Chat — adjuntos bidireccionales**: toggle de convención, preview con
  object URLs, miniaturas de archivos no enviados, picker sin filtro.
- **LLM**: última tg medida con antigüedad en la tira de vitales/Monitor
  + aviso de fallos de `/slots` solo al cambiar.

### Corregido
- Chat: los bytes de imagen se guardan siempre (aunque el modelo no tenga
  visión) + normalización HEIC/AVIF/TIFF a PNG.

## [0.3.0] — 2026-09-15

### Agregado
- **Contexto actual desde `/slots`**: en builds actuales
  `kv_cache_tokens` fue removido del endpoint `/metrics`; el uso de
  contexto ahora sale de la suma de secuencias de los slots
  (`slots_total`/`slots_busy`, en slot ocioso conserva la última
  conversación), con fallback a `kv_cache_tokens` en builds viejas.

### Corregido
- Topes por `process_id` en `llm_metrics` y rotación por tamaño de
  `data/logs`.
- `useLlmStream` compartido con reconexión + selección estable en
  Monitor.

## [0.2.1] — 2026-09-15

### Agregado
- **Monitor**: datos en vivo del LLM por WebSocket + cola de los últimos
  5 minutos.

### Corregido
- Launcher: ya no pasa `--log-file` a llama-server (los logs los gestiona
  la app).
- `llm_metrics`: compatibilidad con builds actuales de llama-server.
- Launcher: colapsa las líneas "slots idle" repetidas en el log.

## [0.2.0] — 2026-09-15

Primer release versionado: suite completa M0–M8.

- **M0** Skeleton FastAPI + React, config persistida con dot-notation.
- **M1** Inventario: scan de `model_dirs`, metadata GGUF, módulos
  embebidos (MTP/visión), agrupación por carpeta, tags.
- **M2** Launcher: `llama-server`/Ollama/LM Studio, templates de
  hardware, logs en vivo por WS, MTP/NextN, stop/restart limpios.
- **M3** Chat: streaming SSE, razonamiento separado, métricas en vivo,
  árbol de conversaciones en SQLite, adjuntos, tool calling, voz a texto.
- **M4** Benchmark: 58 prompts en 6 categorías + sets propios, scoring,
  reportes HTML, historial y comparación.
- **M5** Monitor: GPU/CPU/RAM en vivo + histórico persistente con
  retención configurable.
- **M6** Integración: widget de estado en navbar, toasts, shortcuts,
  onboarding.
- **M7** Base de datos: SQLite async (WAL), conversaciones, benchmarks,
  templates, sets.
- **M8** Vitales del LLM server desde `/metrics` (t/s, contexto, caché,
  % de aceptación MTP), en la tira del Launcher y sección LLM Server del
  Monitor.
