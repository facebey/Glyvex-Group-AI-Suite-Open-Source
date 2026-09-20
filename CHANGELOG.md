# Changelog

Formato: [Keep a Changelog](https://keepachangelog.com/es/1.1.0/).
Versionado: semántico `X.Y.Z` (ver README, sección "Versionado").

## [0.4.0] — 2026-09-20

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
