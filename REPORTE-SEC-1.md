# REPORTE-SEC-1 — Auditoría manual: exposición de red y telemetría

Fecha: 2026-10-05 (sección a) · 2026-10-06 (sección b) · Pin de binario: `b11349`
Método: auditoría semántica manual (grep + lectura de código). CodeQL/Dependabot
quedan fuera de este reporte (SEC-3, cerrado, 0 abiertos).

## Modelo de amenazas

App de escritorio 100% local, un usuario, **sin autenticación** (por diseño).
El modelo de amenazas real NO es el de una web app: los riesgos reales son
exposición a LAN (bind `0.0.0.0`), inyección de comandos en el launcher
(subprocess a llama-server), path traversal, SSRF en descargas y XSS en el
render de markdown del LLM. Lo que este reporte cubre (sección a) es el
perímetro de red: qué escucha la app, qué orígenes acepta y a dónde sale.

## Hallazgos (sección a — exposición de red)

| # | Hallazgo | Severidad | Estado |
|---|----------|-----------|--------|
| F-a1 | Fuentes de Google (Exo 2 + JetBrains Mono) cargadas remotamente en cada arranque | Media | Remediado y merged a main (2026-10-05) |
| F-a2 | Sin autenticación en la API al exponer a LAN (`GLYVEX_HOST=0.0.0.0`) | Info (aceptado) | Documentado |
| F-a3 | CORS con `allow_credentials=True` y wildcards permitidos en configuración de usuario | Baja | Documentado |
| F-a4 | Descargas con `follow_redirects=True` | Info (aceptado) | Documentado |
| F-a5 | Modelos whisper de fuente GitHub de terceros (NoMercy-Entertainment) | Info (aceptado) | Documentado |

### F-a1 — Google Fonts en runtime (Media)

`frontend/index.html:8-10` (preconnects + `<link rel="stylesheet">`) y
`frontend/src/index.css:2` (`@import url(...)`) cargan Exo 2 (300-800) y
JetBrains Mono (400,600) desde `fonts.googleapis.com` / `fonts.gstatic.com`.

Impacto:
- Contradice el claim "100% local, sin nube" (AGENTS.md, `docs/PRIVACY.md`).
- Cada arranque envía el IP público del usuario a Google (request + user agent),
  incluso si la app "no usa nube": es una salida de red automática e involuntaria.
- Dependencia blanda: sin internet las fuentes caen a fallback de sistema
  (no rompe la app), pero la solicitud de red igual se intenta.

Recomendación: self-host los woff2 en `frontend/public/fonts/` (el patrón ya
existe: Poppins del splash, con `OFL.txt`) y borrar los `<link>`/`@import`
remotos. Mitiga F-a1 completamente y deja el frontend 100% offline.

**Remediación implementada** (merged a main el 2026-10-05): las dos fuentes del brand kit (Exo 2 y JetBrains Mono, ambas OFL 1.1)
son *variable fonts* en Google Fonts — un solo woff2 por family cubre todos los
pesos. Se descargó el subset latin de cada una (`frontend/public/fonts/`:
`exo-2-latin.woff2` 40 KB, `jetbrains-mono-latin.woff2` 31 KB) con su
`OFL.txt`, se declararon con `@font-face` local en `frontend/src/index.css`
(reemplazando el `@import` remoto) y se borraron los 3 `<link>` de
`frontend/index.html`. Ojo con un gotcha del build: el minifier de CSS de
Vite 8 / Tailwind 4 (lightningcss) corrompe `unicode-range: U+0000-00FF` a
`U+??` (los caracteres de control 0x00-0x1F se serializan como `?`), invalidando
la declaración; la solución fue declarar el rango desde `U+0020` (space).
Verificado: `npm run build` sin ninguna referencia a `googleapis`/`gstatic` en
el bundle, `npm run test` 14/14, pytest 604/604. Las fuentes también quedaron
versionadas en el brand kit (`assets/brand/fonts/` y kit oficial fuera del
repo) y su doc actualizada. El sitio web `glyvex/web` sigue usando Google
Fonts remoto (proyecto aparte, fuera de este repo).

### F-a2 — Sin auth en exposición LAN (Info, riesgo aceptado)

Por defecto la app solo escucha en loopback (ver inventario). Exponer a LAN es
opt-in explícito (`GLYVEX_HOST=0.0.0.0`) y la documentación es honesta al
respecto: `docs/PRIVACY.md:74-77` dice que en ese caso "requires authentication
that you would have to implement; it is not included". No hay claim falso de
seguridad. Riesgo residual aceptado: quien exponga la API a LAN, cualquier
dispositivo de la red puede usarla (chat, TTS/STT, tools). Sin acción
requerida; mejora opcional: warn en el log de arranque cuando el host no sea
loopback (hoy solo se warns CORS con `*`).

### F-a3 — CORS con credentials (Baja)

`backend/main.py:128-159`: `allow_credentials=True`, `allow_methods=["*"]`,
`allow_headers=["*"]`, y orígenes desde `GLYVEX_CORS_ORIGINS` (default
`http://localhost:5173` para dev). En producción la shell Tauri fija el
allowlist a `https://tauri.localhost,tauri://localhost`
(`src-tauri/src/lib.rs:145-148`), sin wildcards. El único camino a wildcard es
configuración manual del usuario, y el código lo detecta y lo loguea como
advertencia (`main.py:156-159`). Adicionalmente los navegadores rechazan
`*` con credentials, así que el impacto real es nulo. Sin cambio.

### F-a4 — follow_redirects en descargas (Info, riesgo aceptado)

`backend/runtime.py:360-361`: `httpx.AsyncClient(..., follow_redirects=True)`.
Un redirect apuntaría a otro host, pero la integridad del archivo está
garantizada por verificación sha256 incremental obligatoria (sin sha256 la
fuente "no existe", `runtime.py:137-148`). Peor caso: se envía un request a un
host inesperado; el binario descargado no puede ser adulterado. Los redirects
reales (GitHub/HF) son infraestructura propia de esas CDNs. Sin cambio.

### F-a5 — Modelos whisper de terceros (Info, riesgo aceptado)

`backend/stt_runtime.py:86-103`: los modelos ggml-* vienen de la release
`NoMercy-Entertainment/nomercy-whisper-models` v2026.08.23 (GitHub de
terceros, no ggml-org), por desviación documentada de D6 (HF bloqueado en la
IP del usuario; `stt_runtime.py:88-93`). Mitigación existente: sha256 por
archivo (y por parte, en large-v3) verificado antes de usar. Cadena de
confianza = el pin sha256, no el autor del repo. Sin cambio; el pin es la
garantía y ya está en el catálogo.

## Inventario verificado — entrada (bind)

| Punto | Bind | Evidencia |
|---|---|---|
| Backend (FastAPI/uvicorn) | `127.0.0.1:7981`, fallback 7981→7990→dinámico | `backend/main.py:265-298` (`_bind_with_fallback`), `main.py:338` |
| Launcher CLI | `127.0.0.1:7981` | `start.cmd:65-66`, `start.ps1:49-50` |
| Shell Tauri | spawnea el backend con `GLYVEX_HOST=127.0.0.1`, `GLYVEX_PORT=7981` | `src-tauri/src/lib.rs:138-139` (`SPAWN_PORT` línea 23) |

Exposición a red: opt-in explícito vía `GLYVEX_HOST`, documentado en
README/PRIVACY con advertencia de que no hay auth incluido.

## Inventario verificado — salida (outbound)

| Destino | Cuándo | Transporte | Integridad | Opt-in |
|---|---|---|---|---|
| ~~`fonts.googleapis.com` / `fonts.gstatic.com`~~ | ~~Cada arranque, automático~~ | — | — | — (eliminado por F-a1: self-host) |
| `github.com/ggml-org/llama.cpp` (b11349) | Botón "Download runtime" | HTTPS (httpx, TLS verificado) | sha256 obligatorio | Sí |
| `github.com/ggml-org/whisper.cpp` (b5130) | Botón STT | HTTPS | sha256 obligatorio | Sí |
| `github.com/NoMercy-Entertainment/nomercy-whisper-models` | Botón STT (modelos) | HTTPS | sha256 por archivo | Sí (F-a5) |
| `github.com/thewh1teagle/kokoro-onnx` (model-files-v1.1) | Botón TTS | HTTPS | sha256 + tamaño | Sí |
| `huggingface.co/rhasspy/piper-voices` (v1.0.0) | Botón TTS (voces) | HTTPS | sha256 + tamaño | Sí |
| `github.com/facebey/.../releases/latest.json` (updater) | Check de update del bundle Tauri | HTTPS | firma ed25519 (pubkey en `tauri.conf.json`) | Sí |
| `api.search.brave.com` / `api.tavily.com` | Tool de búsqueda, solo con API key del usuario | HTTPS | — | Sí (clave en `data/config.json`, fuera de versionado) |
| SearXNG (`127.0.0.1:8888`) | Tool de búsqueda, self-hosted del usuario | HTTP local | — | Sí |
| Endpoints chat/benchmark | Solo al endpoint que configura el usuario (default `127.0.0.1:8080`) | HTTP | — | Sí |
| `*.glyvexgroup.com`, `github.com/facebey/...` (enlaces About) | Clic explícito del usuario | HTTPS | — | Sí |

## Ausencia de telemetría — verificada

- Sin librerías de analytics/telemetry en backend ni frontend: no hay
  sentry, posthog, mixpanel, segment, plausible, umami, hotjar ni
  `navigator.sendBeacon` en el código de la app.
- Todos los WebSocket (`Monitor`, `Benchmark`, `Launcher`, `useLlmStream`) y
  los streams SSE salen a orígenes propios vía `wsUrlFor(...)` / fetch
  same-origin. Ninguno apunta a host externo.
- `@opentelemetry/api` aparece en `package-lock.json` **solo como
  peerDependency de vitest** (dev-only, no se instala ni se bundtea; es un
  paquete de interfaces sin exporter y sin red). No es telemetría.
- Conclusión: la única salida de red automática e involuntaria de la app era
  Google Fonts (F-a1), ya remediado y merged a main (self-host). La app queda
  sin salida de red automática: todo lo demás es descargas explícitas por
  botón, endpoints configurados por el usuario o navegación iniciada por clic.

## Hallazgos (sección b — backend: inyección de comandos, path traversal, SQL)

Fecha: 2026-10-06 · Baseline: pytest 610 en verde antes del cambio.

| # | Hallazgo | Severidad | Estado |
|---|----------|-----------|--------|
| F-b1 | Contención del zip-slip en `_extract_zip` con `str.startswith` (bypass por directorio hermano) | Baja | Remediado en esta rama |

### F-b1 — zip-slip: `startswith` vs `is_relative_to` (Baja)

`backend/runtime.py:342-344` (antes del fix): el guard validaba
`str(dest).startswith(str(target.resolve()))`. Una entrada que resuelve en un
directorio *hermano* cuyo nombre empiece con el del destino
(`../out-evil/x` con target `.../out`) pasa el `startswith` (`out-evil`
empieza por `out`) y se escribe fuera del target. Remediado con
`dest.is_relative_to(target.resolve())` + test de regresión
(`tests/test_runtime.py::test_extract_zip_rejects_sibling_dir_bypass`).
Explotabilidad real: baja — todos los zips que pasan por aquí (runtime
llama.cpp, modelos whisper) provienen de URLs fijas en el manifest con
verificación sha256 obligatoria; el guard es segunda línea de defensa, no la
primera. Cubre los 3 call sites (`runtime.py` ×2, `stt_runtime.py` ×1).

### Auditoría — inyección de comandos (sin hallazgos)

- Todos los subprocess usan `asyncio.create_subprocess_exec` / lista argv,
  **nunca** `shell=True` ni `Popen` con string: `launcher.py:634` (probe
  `--help`/`--version`), `launcher.py:1331` (launch), `stt.py:182` (probe
  whisper-cli), `stt_runtime.py:530`, `tts.py:161` (PowerShell SAPI).
- El comando de launch (`build_llama_server_command`, `launcher.py:770`) es
  una lista de argv con cada valor como entrada independiente y prefijado por
  su flag; la única concatenación es `--lora-scaled PATH:SCALE` (una sola
  entrada, sin shell). No hay inyección de comandos de SO.
- `tts.py`: el script PowerShell se escribe en un temp dir con contenido fijo
  (constantes `_VOICES_SCRIPT`/`_SPEAK_SCRIPT`) y los args van como lista.
- Nota informativa (sin cambio): valores de config del usuario
  (`lora_path`, `mmproj_path`, `mtp_draft_model`, `host`, `cache_type_k`,
  `load_mode`, `reasoning_effort`, `rope_scaling_type`) se pasan como
  entradas de argv; un valor que empiece por `-` podría reinterpretarse como
  flag por llama-server (argument injection en el propio binario). Impacto
  nulo en la práctica: la config es del propio usuario de la máquina local.

### Auditoría — path traversal (sin hallazgos nuevos)

| Punto | Contención | Evidencia |
|---|---|---|
| Adjuntos (`/api/chat/attachments/{id}/raw`) | id hex de 32 chars: `isalnum()` + longitud exacta → 400 | `attachments.py:625-631` |
| Escritura de adjuntos | id generado por la app (`uuid4().hex`) | `attachments.py:559-561` |
| Fallback SPA | siempre `index.html` fijo; el path del request no toca el FS | `main.py:265-271` |
| `/assets` | `StaticFiles` de Starlette (sanitiza el path) | `main.py:263` |
| `log_file` del launcher | `relative_to(DATA_DIR)` → 400 si sale | `launcher.py:541-574` (C3) |
| Descargas runtime/STT/TTS | destinos fijos en `runtime_dir()` y dirs propios; URL del manifest con sha256 obligatorio | `runtime.py:350-384` |
| Voz Piper por nombre | `name` debe existir en el registro fijo `PIPER_VOICES` → 404 | `piper_runtime_api.py:42-47` |
| Lectura de header GGUF | paths desde `models.json` (configurados por el usuario) — lee archivos de la propia máquina local | `models.py:511` (informativo) |

### Auditoría — SQL (sin hallazgos)

- Todo el acceso a datos pasa por SQLAlchemy ORM con parámetros
  (`select()`, `.ilike()`, `.order_by(column)`): `database.py:399-412`,
  `benchmark.py:630/1097/1192`.
- Las únicas f-strings SQL (`metrics_store.py:245/287/322-324`) interpolan
  nombres de tabla de un diccionario interno fijo (`TABLE[src]`) y
  placeholders `?`; los valores van siempre como parámetros bound.

## Hallazgos (sección c — secretos y SSRF)

Fecha: 2026-10-06 · Sin cambios de código (auditoría solo).

| # | Hallazgo | Severidad | Estado |
|---|----------|-----------|--------|
| F-c1 | API keys en claro en la API local (loopback) | Baja (aceptado) | Documentado |
| F-c2 | `--api-key` en la línea de comandos del hijo llama-server | Baja (informativo) | Documentado |

### F-c1 — API keys en claro en la API local (Baja, riesgo aceptado)

`GET /api/config` (`main.py:191-193`) devuelve `config.data` completo, con
`tools.brave_api_key` / `tools.tavily_api_key` en claro; `GET
/api/launcher/status` (y `/launch`, `/stop`, `/restart`) devuelven
`ProcessInfo.launch_config` con `api_key` en claro (`launcher.py:484,1356`).
El frontend las necesita para poblar/editar los campos de configuración
(`Config.jsx`, `Launcher.jsx:1273-1279`). Mismo trust boundary que F-a2:
por defecto todo escucha en loopback y la LAN es opt-in sin auth (aceptado).
Lo que SÍ está protegido: `command` expuesto por la API y escrito a logs pasa
por `mask_command` (`launcher.py:754-767`, `--api-key` → `********`) y los
logs de config usan `_redact_secrets` (`main.py:70-86`). Los mensajes de error
nunca incluyen la key (`ProviderError` genérico en `tools.py`). Sin cambio.

### F-c2 — `--api-key` en argv del hijo (Baja, informativo)

`launcher.py:978-979`: la key va como argumento de línea de comandos de
llama-server, visible para cualquier proceso local (tasklist/WMI/Process
Explorer) mientras el server corre. llama-server no ofrece alternativa por
variable de entorno; en una máquina de un solo usuario la key es del propio
usuario. Informativo; sin cambio.

### Verificado limpio — secretos

- `data/config.json` y `models.json` en `.gitignore`;
  `data/config.example.json` versionado sin valores (todas las keys/token/
  password en `""`).
- Token Prometheus: comparación timing-safe con `hmac.compare_digest`
  (`metrics_export.py:395-402`), endpoint deshabilitado por defecto
  (`exports.prometheus_enabled=false`) y 404 si no está activado.
- Keys de tools con prioridad env > config.json (`tools.py:151-154`):
  `BRAVE_API_KEY`/`TAVILY_API_KEY` evitan dejarla en disco.
- `GET /api/llm-metrics/processes` expone solo id/nombre/estado/host/puerto
  (`llm_metrics.py:808-812`), sin `launch_config`.
- Updater: el ed25519 en `tauri.conf.json` es la CLAVE PÚBLICA de verificación
  (no hay secreto que proteger).

### Verificado limpio — SSRF

| Superficie | Riesgo | Evidencia |
|---|---|---|
| Tool `fetch_url` (URL sugerida por el LLM) | Guard SSRF completo: resuelve el DNS localmente (no confía en el literal) y bloquea `is_private \| is_loopback \| is_link_local \| is_reserved \| is_multicast` — cubre 169.254.169.254 (metadata cloud) — aplicado a la URL original Y en cada redirect | `tools.py:436-463,534-570`; opt-out explícito `tools.allow_private_hosts` (default `false`) |
| Brave / Tavily search | URLs fijas en el código (`https://api.search.brave.com`, `https://api.tavily.com`); la key va en header/body y nunca en la URL | `tools.py:284,323-331` |
| Endpoints chat/benchmark | El usuario configura el endpoint (su propia infraestructura; default `127.0.0.1:8080`) — por diseño, no SSRF | `chat.py:107,1266-1270` |
| Descargas runtime/STT/TTS | URLs fijas en el manifest + sha256 obligatorio | `runtime.py:350-384` (F-a4) |
| SearXNG | URL configurada por el usuario (default `127.0.0.1:8888`, self-hosted propio) | `tools.py:143` |

Nota (sin cambio): entre el `getaddrinfo` del guard y la resolución que hace
httpx existe una ventana teórica de DNS rebinding; explotarla requiere control
del DNS de un dominio que la víctima no administra, fuera del alcance real de
una app local de un solo usuario.

## Hallazgos (sección d — XSS en el frontend)

Sin hallazgos que remediar. La superficie de render de contenido
LLM-controlado quedó verificada limpia capa por capa.

### Verificado limpio — render de markdown

| Superficie | Riesgo | Evidencia |
|---|---|---|
| `dangerouslySetInnerHTML` / `eval` / `document.write` / `window.open` | Ausentes en todo `frontend/src` | búsqueda directa (0 matches) |
| HTML crudo en markdown | `react-markdown` **sin** `rehype-raw` → el HTML literal en la respuesta del LLM se escapa y se pinta como texto | `MarkdownMessage.jsx:153` (solo se sobrepone `components`, no plugins de HTML crudo) |
| URLs peligrosas en `href`/`src` | `urlTransform` **no sobrepuesto** → rige `defaultUrlTransform` de react-markdown 10.1.0: cualquier protocolo fuera de la allowlist (`javascript:`, `file:`, `data:`…) se vacía a `""` ANTES de llegar a los componentes custom | `react-markdown/lib/index.js:320,421-444`; `MarkdownMessage.jsx` no define `urlTransform` |
| Links externos (componente `a` custom) | Modo navegador: `target="_blank" rel="noopener noreferrer"` (sin reverse tabnabbing). Modo Tauri: `preventDefault()` + `openExternal(href)` | `ExternalLink.jsx:1-27` (únicamente usado por `MarkdownMessage`) |
| `openExternal` → OS handler | Con permiso `shell:allow-open` concedido (`src-tauri/capabilities/default.json`) PERO sin `plugins.shell.open` en `tauri.conf.json`, el plugin aplica su allowlist por defecto: solo `mailto:`, `tel:`, `https?://` llegan al handler del SO. `file://`, `javascript:` y rutas relativas (p. ej. `/C:/Windows/System32/...`) son **rechazadas por el plugin** antes de ShellExecute — sin vector de ejecución de binarios locales | `tauri-plugin-shell-2.4.0/src/lib.rs:178-196` (`ShellAllowlistOpen::Unset` → regex `^((mailto:\w+)\|(tel:\w+)\|(https?://\w+)).+`) |
| Reporte HTML de benchmark | En el frontend solo se **descarga** a disco (`downloadBlob`), nunca se renderiza en el browser. El reporte servido por el backend escapa todo valor dinámico con `_esc` | `Benchmark.jsx:446-451`; `benchmark.py:455,460-529` |

### Verificado limpio — almacenamiento local

- `localStorage`/`sessionStorage` solo guardan: idioma, tema, estado de
  paneles colapsables y el `run_id` del benchmark activo (UUID interno,
  re-validado contra el backend al cargar). Ningún contenido LLM o del
  usuario pasa por ahí → sin vector de stored XSS.

Observación (sin cambio, defense-in-depth opcional): dejar explícito
`"plugins": { "shell": { "open": "^https?://..." } }` en `tauri.conf.json`
hace visible la allowlist que hoy aplica por defecto, y un chequeo de
esquema en `openExternal` (`lib/tauri.js:45`) protegería también a un
futuro segundo caller. No es necesario con las dos capas actuales
(`defaultUrlTransform` + scope del plugin).

## Hallazgos (sección e — errores funcionales por módulo)

Verificados con file:line. El detalle (hallazgos y fixes propuestos) se
mantiene en un anexo interno, fuera del repo público.

## Estado del epic SEC-1

- a) red/telemetría: ✅ — a.1 (bind) loopback por defecto en todos los
  puntos; a.2 (CORS) allowlist explícita, sin wildcards en producción;
  a.3 (outbound + telemetría) 1 hallazgo (F-a1, merged a main), resto
  documentado/aceptado; a.4 (reporte) este documento.
- b) comandos/paths/SQL: ✅ (arriba) — 1 hallazgo (F-b1, remediado en rama), resto verificado limpio.
- c) secretos y SSRF: ✅ — 2 hallazgos documentados (F-c1 aceptado, F-c2
  informativo), resto verificado limpio. Sin cambios de código.
- d) XSS en frontend: ✅ — 0 hallazgos que remediar; superficie verificada
  limpia capa por capa (urlTransform + scope del plugin shell + sin
  rehype-raw + localStorage sin contenido LLM). 1 observación
  defense-in-depth opcional documentada.
- e) errores funcionales por módulo: ✅ — verificados con file:line; el
  detalle (hallazgos y fixes propuestos) queda en un anexo interno, fuera
  del repo público.
- f) cierre: ✅ — documento completo (secciones a–e).
