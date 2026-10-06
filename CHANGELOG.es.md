# Changelog

> **Idioma:** [English](CHANGELOG.md)

Formato: [Keep a Changelog](https://keepachangelog.com/es/1.1.0/).
Versionado: semántico `X.Y.Z` (ver README, sección "Versionado").

## [Unreleased]

## [0.7.7-beta] — 2026-10-05

### Añadido
- **Monitor: gráficas acumuladas de cache hit rate y MTP acceptance** (B5):
  dos métricas persistidas nuevas (`llm.cache_hit_pct_total`,
  `llm.spec_accept_pct_total`) calculadas desde los contadores `/metrics` de
  llama-server, mostradas como cuarta gráfica.
- **Launcher: parámetro `--n-cpu-moe`** (F2): offload de expertos MoE a CPU
  vía `--n-cpu-moe` de llama-server; el probe del binario lo descarta en
  builds viejas que no conocen el flag.
- **Botones Browse nativos** (PUB-3): selector de archivos/carpeta (diálogo
  Tauri) para las rutas de los binarios de backend, los directorios de
  modelos y los archivos GGUF/draft/mmproj/LORA; en modo navegador cae a
  entrada manual.

### Cambiado
- **Nombre de display unificado a "GlyvexAI Suite"** (sin espacio) en el
  título de ventana, onboarding, diálogo Acerca de, instalador y menú Inicio
  (carpeta "GlyvexAI"); el ejecutable ahora es `glyvexai.exe`.
- **Ollama ya no preselecciona `/usr/bin/ollama`** (PUB-4): el default ahora
  queda vacío en Windows y el launcher resuelve `ollama` desde PATH.

## [0.7.6-beta] — 2026-10-05

### Cambiado
- **Fuente de marca: Glyvex Sans (variable, wght 100-900)** — reemplaza a
  Exo 2 en toda la UI. Misma base de Exo 2 con glifos de marca (rombos) en
  `i`, `j`, `0`, `o` y `X`, manteniendo el look mientras cada carácter lleva
  la marca.
- **Tipografía 100% local**: Exo 2, JetBrains Mono y Glyvex Sans viajan como
  WOFF2 self-hosted dentro de la app — cero peticiones a Google Fonts,
  funciona sin conexión.

### Corregido
- Activos muertos de Exo 2 fuera de `frontend/public/fonts/` (los sustituye
  la fuente de marca; una copia de referencia sigue en `assets/brand/fonts/`).

## [0.7.5-beta] — 2026-10-05

### Corregido
- **Auto-update: el updater ahora apunta al repo público** (`Glyvex-Group-AI-Suite-Open-Source`). El endpoint y la URL de descarga de `latest.json` seguían refiriendo al repo privado de origen, así que las instalaciones de la release pública nunca podían encontrar ni descargar una actualización.
- **Los archivos de versión ya no se desfasan** (PUB-7): `frontend/package.json`, `src-tauri/Cargo.toml` y ambos lockfiles ahora llevan 0.7.5-beta junto con `APP_VERSION` y `tauri.conf.json` (se habían quedado en 0.7.1-beta).

## [0.7.4-beta] — 2026-10-04

### Cambiado
- **Runtime embebido: pin llama.cpp b11146 → b11349**: superficie de flags idéntica (329 long-flags, 0 agregados / 0 removidos, verificado contra el `--help` de ambos binarios); trae mejoras de CPU para k-quants, carga de modelo más rápida y correcciones de seguridad (BoringSSL, cpp-httplib, overflow GGUF).
- **Runtime: el update de pin ya no deja la build anterior huérfana**: al descargar el pin nuevo se eliminan las builds gestionadas previas (`llama.cpp-*`) para no duplicar el disco (~700 MB); `keep_previous=True` las conserva (base del rollback, feature empresarial — ver PENDIENTES).

### Corregido
- **Versión unificada en 0.7.1-beta**: `APP_VERSION` (`/api/health`,
  `/api/info`), `frontend/package.json` y `src-tauri/Cargo.toml` seguían en
  0.6.0 mientras el instalador ya era 0.7.1-beta.
- **Pie de la app**: "Todos los derechos reservados" contradecía la licencia
  Apache 2.0; ahora dice "© Glyvex Group · Apache 2.0".
- **i18n**: el estado del encabezado ("Sin modelo activo"), los rótulos de
  Backends en Config, Power/Clock del Monitor y 3 errores del Launcher
  estaban fijos en un idioma; ahora pasan por los locales EN/ES.
- **Config**: los ejemplos de ruta siguen al SO (rutas Windows en Windows) y
  ya no muestran una ruta personal.
- **Monitor**: el badge de alerta dice qué lo disparó (temperatura o VRAM) y
  explica los umbrales en el tooltip; "GPU NVIDIA no detectada" respeta el
  idioma de la UI.
- **Chat**: el selector de modelo muestra el nombre del archivo en vez de la
  ruta completa (que queda en el tooltip).
- `SyntaxWarning` por una secuencia de escape en el docstring de `paths.py`.

### Documentación
- README EN/ES: sección **Descarga** con link al instalador, badges de CI y
  release, sección de **voz** (STT whisper.cpp / faster-whisper / navegador y
  TTS Kokoro / Piper / SAPI), instalador NSIS (no MSI), cantidad de tests.
- `docs/speech-to-text.md` reescrito: cubría solo faster-whisper y decía que no
  había TTS.
- QUICKSTART con `git clone` y atajo al instalador; SECURITY soporta 0.7.x.

### CI
- El job de frontend corre en Node 20 y 22 y ejecuta los tests de vitest.

## [0.7.1-beta] — 2026-09-30

### Corregido
- **TTS**: la reproducción vuelve al elemento `<audio>` (el rewrite a WebAudio
  dejaba el audio mudo en algunos entornos Chrome/Edge/WebView2). Un fallo de
  reproducción o autoplay se muestra ahora en un aviso, en vez de silencio o
  botón atascado.
- **TTS**: cache de WAV en el backend (LRU 32): re-escuchar la misma respuesta
  es instantáneo y no resintetiza.
- **Config**: la detección de Web Search / STT / TTS en Opciones ya no tarda
  30-45 s (caché TTL 60 s por firma de config; el botón "Comprobar" fuerza el
  re-probe).
- **Chat**: los últimos 5 endpoints personalizados se persisten (localStorage)
  y aparecen en el selector para no volver a tipear la URL.

## [0.7.0-beta] — 2026-09-30

### Agregado
- **TTS neuronal (Piper + Kokoro) empaquetado en el sidecar**: la cadena
  neural (piper, kokoro-onnx, onnxruntime, espeak-ng) ahora viaja dentro del
  `glyvex-backend` (Fase 1b del empaquetado Tauri). Voces y modelo se descargan
  en Ajustes → TTS; el motor `auto` elige Kokoro → Piper → SAPI según qué esté
  listo.
- **Pantalla de provisión — paso TTS**: además del check de voces SAPI, un
  puntero directo para configurar el TTS neuronal en Ajustes.

### Cambiado
- **Bumps de dependencias**: uvicorn 0.54, sqlalchemy 2.1.1, nvidia-ml-py
  13.615.71 (backend) y vite 8.3.1 (frontend).
- **Piso de numpy 2.2** en `requirements.txt`: el 2.5 exigía Python >=3.12 y
  rompía la install del job de CI 3.11.

### Corregido
- **CI en Linux**: 3 tests asumían Windows (status STT/TTS) y ahora fuerzan la
  plataforma vía monkeypatch; la suite pasa en 3.11 y 3.12.

## [0.6.3-beta] — 2026-09-28

### Agregado
- **Empaquetado nativo Windows (Tauri 2 + NSIS)** per-user (sin UAC):
  bootstrapper de WebView2 (auto-download en Win10 antiguos), branding del
  wizard (header 150x57 + sidebar 164x314), shortcut en Inicio → "Glyvex".
- **Ciclo de vida del sidecar**: la app arranca `glyvex-backend` (PyInstaller),
  health-check con auto-reintento, kill al cerrar la ventana, y overlay
  splash/error con botón Reintentar en la UI.
- **Origen SPA estable (7981)**: el release arranca siempre en 7981 (con
  fallback secuencial 7982…7990 solo si está ocupado); la SPA la sirve el
  backend con rutas relativas `/api/...`.
- **Arranque `--provision`**: la app abre directo en la pantalla de
  provisión.
- **Pantalla de provisión (T5.3)**: 3 pasos — runtime llama.cpp (obligatorio),
  STT whisper.cpp + modelo base, TTS (check de voces SAPI) — con progreso
  real de las descargas, reintentos y "Entrar a la app".
- **STT whisper.cpp (nativo)**: runtime descargable (binario + modelo
  ggml-base), dictado del micrófono a WAV 16 kHz y selector de motor
  (auto / navegador / whisper.cpp / faster-whisper) en Opciones.
- **TTS SAPI (local de Windows)**: botón "escuchar" en las respuestas del
  chat y sección en Opciones con estado, activar/desactivar, voz y velocidad.
- **Idioma de transcripción unificado**: un solo selector para todos los
  motores (automático / es / en / pt / fr / de / it) en Opciones.
- **Paneles colapsables** en Launcher (los 11, incluidos Opciones básicas y
  Avanzadas) y Monitor (GPU, CPU, RAM, Historial, LLM Server, Procesos):
  header clicable, estado por panel persistido (localStorage).

### Corregido
- **Descarga de modelo STT en Opciones**: el crash `Cannot read properties
  of null (reading 'aborted')` cortaba el progreso en la UI (la descarga
  seguía en el backend y el reintento caía en "ya hay una descarga en
  curso"); ahora el stream se lee con signal válido y la pantalla se
  refresca sola hasta que el modelo queda listo.
- **Mensaje de faster-whisper en la app empaquetada**: antes mandaba a
  `pip install` (no aplica al bundle); ahora el estado STT reporta
  `packaged` y la UI deja de mostrar la tarjeta de advertencia de ese motor
  con motor `auto` (y lo ofrece deshabilitado en el selector), porque en el
  bundle nunca se puede instalar.
- **Idioma heredado en STT local**: whisper.cpp y faster-whisper ignoraban
  `stt.language` y transcribían en inglés (el default de los motores); ahora
  lo heredan salvo override explícito.
- **host/port en templates**: el puerto configurado se perdía al
  guardar/editar un template (faltaban esos campos en `TEMPLATE_FIELDS`).
- **App empaquetada: tema/opciones no persistían** (PERS-1): el backend
  release pedía un puerto libre aleatorio al SO, el `localStorage` era por
  origen y cada arranque caía en uno distinto; ahora el release arranca
  siempre en 7981 (con fallback secuencial 7982…7990 si está ocupado) y la
  persistencia sobrevive al cierre/abrir de la app.
- **App empaquetada: `ERR_CONNECTION_REFUSED` / pantalla de error al
  abrir**: el shell Tauri redirigía a la SPA antes de que el backend
  escuchara (race de bootstrap); ahora el shell emite el estado del sidecar
  por eventos (con heartbeat de 5 s) y la UI redirige solo cuando el
  health-check pasa, con fallback a recargar a los 180 s.
- **Launcher: "build desconocida" con llama.cpp b11146**: el probe de
  `--version` leía solo la primera línea (que en esa build es un log de
  inicialización sin número); ahora recorre todas las líneas hasta
  encontrar el build.
- **Config → Runtime → Reinstalar: aviso falso "Windows-only"**: el
  endpoint de reset no devolvía `platform` (solo lo devolvía el de status);
  ahora el reset incluye la plataforma y la UI muestra la pantalla de
  descarga correcta.
- **STT: fallback automático a faster-whisper**: con el motor `auto` y el
  runtime whisper.cpp no disponible, antes quedaba sin transcripción;
  ahora cae a faster-whisper si está instalado (y la UI empaquetada no
  ofrece ese motor, ya que en el bundle no se puede instalar).
- **Ventanas CMD parpadeantes al abrir el Launcher y al lanzar modelos**:
  los hijos de consola (probe de `--help`/`--version` del launcher y
  whisper-cli, arranque de llama-server) se crean ahora sin ventana
  (`CREATE_NO_WINDOW` en Windows).

### Cambiado
- **Layout responsive**: max-w de la app 1600 px (2100 en ruta Launcher),
  sidebar 360 px en 2xl+ y drawer de historial 360 px (antes se cortaba en
  pantallas de 1280 px).

### Baseline
- Suite completa: **471 tests en verde**; build de producción OK; instalador
  NSIS verificado en máquina (install silencioso → sidecar → SPA → shortcut),
  re-empaquetado 2026-09-28 con los fixes de arriba validado en el exe.

## [0.6.2-beta] — 2026-09-26

### Cambiado
- **Migración mayor de dependencias del frontend** (una rama por
  librería, build y tests en verde en cada paso):
  | Dependencia | Antes | Después |
  |---|---|---|
  | `react` / `react-dom` | ^18.3.0 | ^19.3.0 |
  | `lucide-react` | ^0.400.0 | ^1.47.0 |
  | `react-markdown` | ^9.0.1 | ^10.1.0 |
  | `recharts` | ^2.12.0 | ^3.10.0 |
- **lucide-react 1.x** eliminó los iconos de marca: el ícono `Github` del
  footer pasó a `ExternalLink` (`frontend/src/App.jsx`).

### Baseline
- Suite completa: **358 tests en verde**; build de producción OK; smoke
  manual de las 6 vistas.

## [0.6.1-beta1] — 2026-09-26

### Agregado
- **Branding del instalador MSI v3**: banner del wizard (sobrescribe el
  default de WixUI), imagen personalizada en el ExitDialog e **ícono
  molécula** en wizard, Panel de Control (`ProductIcon`) y
  `ARPPRODUCTICON`.
- Instalador **per-user** para Windows x64 (sin UAC): la data de la app
  vive en `%LOCALAPPDATA%\Glyvex-AI-Suite\data`, independiente del
  directorio de instalación.

## [0.6.0] — 2026-09-25

### Agregado
- **Sistema de temas (5)**: carbon (textura fibra), metallic (por
  superficie + textura metal) y matrix (lluvia digital en canvas), más el
  modo claro rediseñado a **lavanda**. Ciclo desde el botón de paleta del
  header o desde Config; texturas generadas por código (sin licencias de
  terceros).
- **Runtime embebido b11146 (llama.cpp v0.5.0)**: bump del pin b11009
  (sha256 + feature-detect por probe).
- **Packaging MSI WiX per-user** (`build.ps1 -MakeInstaller`): wizard,
  atajos y upgrade; sidecar PyInstaller sin consola; bundle recortado
  **143.6→88 MB** (excluye gguf/numpy y recorta babel locale-data).
- **`GLYVEX_NO_BROWSER`**: el bundle FROZEN no abre el navegador solo.
- **requires-python >= 3.11** declarado (pyproject + test de guarda).

### Cambiado
- **Floors de dependencias** (pip) a versiones probadas +
  `react-router-dom` 7.18.4.
- Empaquetado: Nuitka eliminado — la decisión fue PyInstaller (T-2).
- Ícono Glyvex en el exe y el MSI (wxs Icon + WixUI_Banner).
- Paleta light: tokens de contraste y fondo gris suave; temperatura en
  recuadro de estado con tokens `-800` legibles en modo claro.

### Corregido
- MSI: fila `WIXUI_INSTALLDIR` faltante (error 2819) y `WIXUI_INSTALLDIR`
  apuntando a `APPDIR` (error 2343, ERROR_BAD_PATH).
- **TDP toggle**: se restaura desde el estado real de la GPU al cargar.
- **fit toggle**: ON ahora envía `fit=true` en el payload.
- Selector de tema de Config aplica el tema al instante.

### Baseline
- Suite completa: **356 tests en verde**.

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