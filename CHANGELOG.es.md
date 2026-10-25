# Changelog

> **Idioma:** [English](CHANGELOG.md)

Formato: [Keep a Changelog](https://keepachangelog.com/es/1.1.0/).
Versionado: semántico `X.Y.Z` (ver README, sección "Versionado").

## [Unreleased]

## [0.11.0-beta] — 2026-10-25

Primera beta de la serie 0.11.x y release completa del conjunto 0.11.0:
soporte de aceleración AMD de punta a punta (RT-10 — selección in-app de
Vulkan/ROCm, validada en la RX 7600 del tester a lo largo de las previews
alpha1–alpha4, con el runtime llama.cpp **b11827** embebido y telemetría
de GPU AMD), mejoras de flujo en Benchmark (K2) y métricas de tokens de
largo plazo. A diferencia de las previews alpha, esta versión **sí** se
publica en el canal de actualización: las instalaciones en 0.10.0-beta la
reciben.

### Añadido
- **Benchmark: juzgar corridas guardadas desde Reports** (K2.c): el paso de
  juzgado ahora vive en un componente compartido (`JudgePanel`) usado tanto
  por el flujo de Benchmark como por la página Reports — cualquier corrida
  persistida en el historial puede juzgarse o volver a juzgarse sin
  repetir la corrida.
- **Benchmark: endpoints externos arbitrarios** (K2.a): Benchmark ahora
  puede probar cualquier endpoint OpenAI-compatible por URL con API key y
  selector de modelo, además de los servidores locales. Las requests van
  solo al endpoint elegido; las corridas locales se comportan igual que
  antes.
- **Benchmark: modo avanzado con sampling fino** (K2.b): el paso 3 agrega
  un modo avanzado opcional (`top_p`, `top_k`, `min_p`, `repeat_penalty`,
  `seed`). Los parámetros no configurados nunca se envían, así cada
  endpoint conserva sus propios defaults; un endpoint que no entiende un
  parámetro simplemente lo ignora.
- **Exportación de métricas: contadores de por vida de la app a
  InfluxDB/Prometheus**: `llm.app_prompt_tokens_total`,
  `llm.app_prompt_tokens_cached_total` y `llm.app_tokens_predicted_total`
  — totales acumulados de la propia app (sin tag por servidor), basados en
  los contadores lifetime no reseteables (MON-5), por lo que son
  verificables contra los números del propio motor.
- **Docs: guía de exportación de métricas (ES/EN)** con un dashboard de
  ejemplo de Grafana: paneles de CPU/GPU/memoria, series de consumo
  chat-vs-externo y una nota sobre la trampa MB/GB→bytes de Flux al
  graficar memoria.

### Cambiado
- **Monitor: división chat vs externo en el total de consumo de por vida**
  (MON-5): el panel de consumo del servidor ahora separa el total lifetime
  en tokens de chat de la app y consumo de servidores externos (nueva
  división en el endpoint de consumo), y las tarjetas de comparación de
  costo cloud quedan destacadas visualmente.

### Corregido
- **Desktop: versiones de plugins Tauri alineadas entre npm y Rust** (fix
  de CI): la CI de Linux (AppImage) fallaba en los tags alpha porque
  `tauri-plugin-shell` (Rust 2.4.0) y `@tauri-apps/plugin-shell` (npm
  2.5.0) — igual que `tauri-plugin-updater` (2.13.1 vs 2.14.0) — estaban
  desalineados; `tauri build` aborta ante plugins desparejados. Ambos
  lados resuelven ahora 2.5.0 / 2.14.0 y quedan fijados con precisión
  (Cargo `=x.y.z`, npm save-exact) para que una regeneración futura del
  lockfile no los separe otra vez. Sin cambio de comportamiento para el
  usuario.

## [0.11.0-alpha4] — 2026-10-23 (preview de prueba en hardware)

Cuarta build alpha de prueba para validación en hardware real. Trae el
runtime actualizado (llama.cpp **b11827**, primera build en probarlo en la
RX 7600 del tester — Parte 3 del protocolo) **y** la nueva telemetría de
GPU AMD vía LibreHardwareMonitor embebido (MON-6, verificación E2E agregada
al protocolo). No publicada en el canal de actualización: las instalaciones
en 0.10.0-beta no se ven afectadas.

### Añadido
- **Monitor: telemetría de GPU AMD con LibreHardwareMonitor embebido**
  (MON-6): en máquinas sin NVIDIA el Monitor ahora inicia
  LibreHardwareMonitor 0.9.6 (MPL-2.0) como sidecar local — la app viaja sin
  modificar dentro del instalador (vía `datas` de PyInstaller) y se extrae a
  DATA_DIR/tools/ en el primer uso con verificación SHA-256, sin descargar
  nada. corre en un puerto dedicado (127.0.0.1:8185, para no chocar con un
  LHM del usuario en 8085; si 8185 ya está sirviendo, se adopta en vez de
  spawnear un segundo proceso) y se configura con un preset propio de su
  .exe.config escrito antes de cada arranque: servidor web activado, solo
  la categoría GPU, minimizado a la bandeja. Temperatura del core, uso de
  core/VRAM, relojes gráficos/memoria, % de fan, potencia de package y VRAM
  usada/libre/total alimentan el schema GpuMetrics existente — el frontend no
  distingue la fuente. El fallback solo corre cuando la vía NVML no encuentra
  nada: equipos solo-NVIDIA o sin GPU se comportan exactamente igual que
  antes. Un LHM spawneado por la app se termina en el apagado (lifespan y el
  hook pre-kill de Tauri `/gpu/fan/restore-all`) para no quedar huérfano.

### Cambiado
- **Runtime embebido: pin llama.cpp b11349 → b11827** (v0.7.0): los nombres
  de assets son idénticos, solo cambian el tag y los pines SHA-256 (todos
  verificados localmente). Gate de probe ejecutado contra el binario real:
  superficie de flags de 257 → 262 long-flags con **cero removidas**
  (+5: `--moe-cache-mib`, `--spec-draft-n-ubatch`, `--spec-draft-sampling`,
  `--connect`, `--connect-code`); el formato de `--version` no cambió
  (0.7.0), así que el parser del probe no se toca. Lo relevante del rango:
  fix de invalidación de checkpoints SWA para modelos híbridos (#30666),
  fix del undercount de `usage.completion_tokens` con n>1 (#29486, alimenta
  MON-4/MON-5), fix de memoria host de `--gpu-visible` en Vulkan (#30468,
  ruta AMD), MMVQ más rápido en low-bit CUDA (#30517) y patches de
  dependencias cpp-httplib → 0.62.0 y BoringSSL → 0.20261020.0. El bundle
  de NVIDIA crece en consecuencia (runtime en disco ~710.8 MB → ~1157.8 MB,
  sobre todo el ggml-cuda.dll de CUDA 12.4), y las estimaciones de descarga
  de la UI se recalibraron con los tamaños reales de los archives de b11827
  (base ~23 MB, NVIDIA ~656 MB, AMD Vulkan ~58 MB, AMD ROCm ~274 MB).
  `--moe-cache-mib` queda **fuera** del launcher a propósito (feature
  separada; upstream cambió su política de eviction a SLRU 50/50 en este
  rango).

- **Precios de nube por defecto actualizados a la lista cerrada 2026-10**
  (defaults de MON-2): la lista base incorporada detrás de "Precios de
  nube" pasa de 7 proveedores a 36, tomada de la lista cerrada de Fabian
  (Files/Pricing Providers/pricing.json, verificada 2026-10). Los
  proveedores con tarifas con vigencia se mantienen como entradas
  separadas con la nota en su nombre (ej. "Gemini 4 Argon hasta
  30-nov-2026" / "desde 1-dic-2026"). Solo cambian los valores por
  defecto: las configuraciones que ya guardaron precios propios no se
  tocan, y la lista sigue siendo editable desde Configuración.

## [0.11.0-alpha3] — 2026-10-22 (preview de prueba en hardware)

Tercera build alpha de prueba para la validación en hardware real del
soporte a GPU AMD (RT-10). El flujo de runtime queda igual que en alpha2
(llama.cpp b11349 pinnado, misma oferta Vulkan/ROCm), así que esta build
no interfiere con el protocolo del tester. No se publica en el canal de
actualización: las instalaciones en 0.10.0-beta no se ven afectadas.

### Añadido
- **Monitor: consumo total de por vida** (MON-5): el panel de consumo del
  server suma ahora una sección "Total de por vida" que agrega todos los
  procesos llama-server que la suite haya monitoreado alguna vez
  (reinicios combinados), con gran total, desglose por modelo y costo
  estimado en la nube para comparar; servida por un nuevo
  `GET /api/llm-metrics/consumption/all`. El conteo empieza en el primer
  uso con 0.11.x — no hay backfill de versiones anteriores.
- **Integración con agentes OpenCode** (AG-1): mientras la integración
  con OpenCode esté habilitada en Configuración, los servers lanzados por
  la suite se publican como agentes listos para usar en la configuración
  de OpenCode y se retiran cuando el server se detiene. Una nueva tarjeta
  "Agentes" en el lanzador lista los agentes publicados y puede abrir
  OpenCode apuntado a uno de ellos.
- **Monitor: consumo por server** (MON-4, enviado en alpha2 pero no
  documentado hasta ahora): el Monitor muestra el uso de tokens por
  proceso llama-server monitoreado (prompt/cached/completado) con series
  de tiempo y costo estimado. Se agrega acá para que el changelog sea
  honesto sobre lo que alpha2 realmente incluía.

### Corregido
- **Seguridad: CORS ya no permite credenciales** (E7, del análisis de
  seguridad SEC-1): ningún endpoint usa cookies ni autenticación y el
  frontend nunca manda credenciales, así que `allow_credentials=True` era
  superficie de ataque muerta; ahora está deshabilitado. Como efecto
  secundario, `GLYVEX_CORS_ORIGINS='*'` pasa a ser una configuración
  válida en lugar de una combinación advertida en el log.

## [0.11.0-alpha2] — 2026-10-22 (preview de prueba en hardware)

Segunda build alpha de prueba para la validación en hardware real del
soporte a GPU AMD (RT-10 B1+B2). No se publica en el canal de
actualización: las instalaciones en 0.10.0-beta no se ven afectadas.

### Añadido
- **Runtime: aceleración ROCm seleccionable para GPU AMD** (RT-10 B2,
  preview): las placas AMD ahora tienen una elección Vulkan/ROCm en el
  panel de runtime. Se ofrece como segunda fuente de aceleración la build
  oficial ggml-org/llama.cpp b11349 con ROCm 10.0 (Windows, pinnada por
  SHA-256); la elección queda guardada en la configuración. No hay
  fallback implícito: si la ruta elegida no está disponible, el runtime
  degrada a CPU con una nota explícita, para que las mediciones sean
  honestas. Vulkan sigue siendo el valor por defecto hasta que lleguen
  los datos de la comparativa en hardware (protocolo
  PRUEBA-VULKAN-AMD-b11349.md).
- **El estado del runtime muestra el backend de aceleración activo** —
  el panel y la API de estado del runtime ahora informan qué backend está
  en uso (Vulkan/ROCm/CUDA) y qué opciones hay para la familia de GPU
  detectada.

### Cambiado
- **Tamaño de descarga según backend elegido** — el tamaño estimado de
  descarga del runtime que muestra la app ahora refleja la elección de
  aceleración seleccionada (AMD: Vulkan ~51 MB / ROCm ~263 MB). Sin
  cambio de comportamiento en NVIDIA.

## [0.11.0-alpha1] — 2026-10-20 (preview de prueba en hardware)

Build alpha de prueba para la validación en hardware real del soporte a
GPU AMD (RT-10 B1). No se publica en el canal de actualización: las
instalaciones en 0.10.0-beta no se ven afectadas. Esta versión vive en
una rama de feature hasta que el reporte del tester externo sea
aprobado.

### Añadido
- **Runtime: oferta de aceleración Vulkan para GPU AMD** (RT-10 B1,
  preview): las placas AMD reciben ahora el mismo flujo de runtime en dos
  etapas que ya tiene NVIDIA — la suite detecta la familia de GPU y ofrece
  la build oficial ggml-org/llama.cpp b11349 con soporte Vulkan (Windows),
  pinnada por SHA-256. Si la etapa de aceleración falla, el runtime sigue
  degradando a CPU, nunca a error. Requiere validación en hardware real
  (protocolo PRUEBA-VULKAN-AMD-b11349.md) antes de llegar al canal
  estable.

### Cambiado
- **Ajustes de runtime: tamaños y notas según la familia de GPU** — el
  tamaño estimado de descarga, las descripciones de etapas y las notas del
  panel de runtime ahora se derivan de la familia de GPU detectada en vez
  de constantes duplicadas por marca. Sin cambio de comportamiento en
  NVIDIA.

### Corregido
- **Runtime: los archivos sueltos de un zip/tar.gz ahora se extraen** —
  el heurístico de recorte de raíz trataba un archivo plano de un solo
  archivo como prefijo de carpeta y no extraía nada; el archivo de
  aceleración descargado quedaba registrado en la meta pero ausente en
  disco. Tests de regresión agregados para zip y tar.gz.

## [0.10.0-beta] — 2026-10-19

Release de features sobre v0.9.1-beta: seguimiento del costo de sesión con
comparativa contra la nube, temas externos cargados desde carpetas, selección
de prompts individuales en Benchmark, un flujo de actualización del runtime
pulido, un panel de parámetros avanzados reorganizado y un fix de caché
post-actualización.

### Añadido
- **Monitor: costo de sesión** (MON-2): un panel en el Monitor lleva el
  conteo de tokens de prompt/completado/caché usados por los chats de la
  propia suite y estima el ahorro contra siete proveedores de nube (OpenAI,
  Anthropic, Google, DeepSeek). Las tarifas se editan en una nueva sección
  "Precios de nube" de Configuración (ES/EN).
- **Temas: temas externos desde carpetas** (TH-2): cada tema es una carpeta
  con uno o más archivos `.css` (más texturas opcionales) bajo el directorio
  `themes/` de la carpeta de datos. Los temas se autodetectan y aparecen en
  el selector junto a los cinco integrados, se aplican en vivo sin
  reinstalar ni reiniciar, y el botón de ciclo del header los incluye. El CSS
  de un tema solo necesita redefinir las variables de color de la app — lo que
  no se redefine hereda del tema base.
- **Benchmark: selección por prompt** (K3): se pueden activar/desactivar
  prompts individuales dentro de cada set (antes solo se filtraban categorías
  completas); los chips de categoría ahora actúan como interruptores de grupo
  de sus propios prompts, cada set tiene un checkbox maestro "Todos", y las
  categorías de prompts y las etiquetas de dificultad están totalmente
  localizadas (ES/EN).
- **Runtime: flujo actualizar / reinstalar / desinstalar** (RT-12): cuando
  hay una build más nueva de llama.cpp disponible, el panel de runtime muestra
  una vista "desactualizado" — Actualizar como acción primaria con opción de
  conservar la build anterior, una línea explícita de delta de tamaño que
  reacciona a esa opción, y acciones de reinstalar / desinstalar; el Launcher
  muestra un chip "Runtime: desactualizado".

### Cambiado
- **Launcher: "Parámetros avanzados" reorganizado** (E10): el panel plano de
  ~30 controles se divide en tres grupos colapsables (RoPE/Escalado,
  Memoria/Atención, Checkpoints y memoria); la apertura de cada grupo
  persiste. Sin cambios de comportamiento — mismos campos, toggles y
  deshabilitaciones por probe.

### Corregido
- **Actualizaciones: se acabó el F5 manual después de un update interno**
  (PUB-13): el redirigido de arranque de la app ahora lleva la versión
  instalada como parámetro de consulta, así que la caché de disco de WebView2
  no puede servir nunca un `index.html` viejo de una versión anterior.

## [0.9.1-beta] — 2026-10-16

Release de parche sobre v0.9.0-beta: las actualizaciones internas muestran la
UI nueva de inmediato (sin recargar a mano), la barra de descarga siempre
muestra progreso y la app instalada sirve sus propias fuentes empaquetadas.

### Corregido
- **Actualizaciones: la UI nueva aparece justo después de un update interno.**
  Los assets embebidos ahora llevan cabeceras de caché correctas — los assets
  con hash se cachean un año, `index.html` y los archivos sin hash siempre se
  revalidan — y la app compara su propia construcción contra la que sirve el
  backend: si aún queda un bundle cacheado de una instalación anterior, se
  recarga una vez en vez de quedarse con la UI vieja hasta un F5 manual.
- **Actualizaciones: la barra de descarga nunca queda clavada.** Cuando el
  servidor de actualización no informa el tamaño del archivo, la barra anima un
  estado indeterminado en vez de congelarse en 0%.
- **Monitor: el panel de curvas de ventilador GPU carga incluso con el backend
  aún levantando** (justo tras un update/rearranque): la probe ahora reintenta
  con un backoff acotado mientras el panel está abierto.
- **Escritorio: la app instalada usa de verdad sus fuentes empaquetadas.**
  Glyvex Sans, JetBrains Mono y Poppins nunca eran servidas por el backend
  embebido (caían en el shell de la app y la webview las descartaba por tipo
  de contenido); ahora se sirven con el tipo correcto.
- **CI: el job `windows-nsis` de release vuelve a construir y firmar el
  instalador en los tags** (el override `--config` de build debe pasarse como
  archivo; nombres de artifact endurecidos para refs con barra). Sin impacto
  en los assets de la ya publicada v0.9.0-beta.

## [0.9.0-beta] — 2026-10-16

Release de features sobre v0.8.1-beta: curvas de ventilador de GPU con
restauración automática al salir, bandeja del sistema, nuevos templates
builtin y fixes de Chat/Launcher.

### Añadido
- **Monitor: curvas de ventilador por temperatura** (MON-3): curvas
  temperatura→ventilador % por GPU aplicadas vía NvAPI, con editor de curva
  y botón AUTO del ventilador en la card de GPU (EN/ES). Un hook de parada en
  Rust restaura los ventiladores a AUTO antes del kill duro del backend al
  salir de la app (cierre ordenado, salir desde la bandeja o SIGTERM), y
  `POST /api/metrics/gpu/fan/restore-all` restaura todas las placas desde la
  API. Validado end-to-end en una RTX 3090.
- **Escritorio: bandeja del sistema** (T5.4): ícono en la bandeja con menú
  abrir/minimizar/salir; cerrar la ventana minimiza a la bandeja en vez de
  salir.
- **Launcher: templates builtin para 128K híbrido GPU y NUMA multi-socket**,
  dimensionados según el hardware detectado.
- **Modelos: un preset de sampling `auto`** que lee los valores de sampling
  que el cuantizador embebió en el GGUF (metadata `general.sampling`) y los
  aplica al lanzar.
- **Benchmark: filtro de categorías** en los runs — se elige qué categorías
  de prompts de un set se ejecutan.

### Cambiado
- **Launcher: el KV cache por defecto ahora es `q8_0`** — mejor relación
  calidad/VRAM en las instancias llama-server lanzadas.
- **Fuente de marca: Glyvex Sans v2.010 (full coverage)**: la build de
  subset latin se sustituye por la variable full coverage (235 glifos,
  wght 100–900, opción C con `o`/`0` limpios); la app y el brand kit
  distribuyen ahora `glyvex-sans-wght.woff2` (+ `.ttf` en el kit).

### Corregido
- **Chat: detección de modelos en endpoints protegidos** (CHAT-3): la API
  key configurada se reenvía como header Bearer por el proxy de listado de
  modelos, así que los llama-server iniciados con `--api-key` ya no dan 401
  ni dejan el modelo sin detectar; la petición de detección va con un
  debounce de 300 ms.
- **Chat: el streaming sobrevive a navegar a otros módulos** (CHAT-2): el
  fetch+SSE pasó a un store de nivel de módulo (`chatStreamStore.js`), por lo
  que desmontar Chat ya no aborta la generación; al volver se reattacha al
  stream en curso y las respuestas parciales se guardan al desmontar.
- **Launcher: los templates ahora persisten la activación de MTP, los floors
  de draft y LoRA**.
- **Chat: los chips de adjuntos muestran la miniatura desde el preview
  local** (`previewUrl`) antes de que termine la subida.
- **Frontend en Linux: texto visible en los `<select>`** — se fuerza el
  renderizado CSS, corrigiendo el texto invisible en WebKitGTK.
- **Frontend: `tauri.js` se importa estáticamente en el bootstrap** (el
  import dinámico no tenía efecto en builds empaquetadas).

### Build
- Nuevo workflow CI `windows-nsis`: construye el instalador NSIS y el
  manifest firmado del updater en un runner de Windows (T7.4).

## [0.8.1-beta] — 2026-10-11

Release de mantenimiento sobre v0.8.0-beta para las dos regresiones de UX
post-actualización. El AppImage de Linux se añadió a esta release tras la
primera publicación (construido por CI; validado en Ubuntu 26.04, funciona
en 22.04+ con `libfuse2`).

### Añadido
- **Runtime: AppImage Linux x64 + updater** (RT-14 L2): `build-linux-appimage.sh`
  construye el AppImage de Tauri firmado en Linux (sidecar + bundle de
  webview); `latest.json` ahora usa el formato estático `platforms` para que
  un solo manifiesto sirva las actualizaciones de Windows y Linux; nuevo
  workflow `linux-appimage` que construye el AppImage en tags `v*` (se
  salta si no hay secret de firma, de modo que el espejo público no se vea
  afectado).

### Corregido
- **Barra de progreso del actualizador**: ahora refleja el avance real
  (corregida la forma del callback `DownloadEvent` de
  `@tauri-apps/plugin-updater` 2.13.1).
- **Watchdog de WebSocket de métricas** (F5): descarta la conexión
  zombie/silenciosa tras el relanzamiento de una actualización in-app, para
  que el control de ventilador y las métricas GPU vuelvan sin recargar.
- **Búsqueda del sidecar en Linux**: el proceso backend solo se buscaba como
  `glyvex-backend.exe`; ahora resuelve el nombre de binario según la
  plataforma.

## [0.8.0-beta] — 2026-10-10

### Añadido
- **Runtime: soporte para Linux x64** (RT-14): el runtime gestionado ahora
  incluye un bundle nativo Linux x64 (sidecar PyInstaller construido por
  `build-linux.sh`), con symlinks SONAME recreados en los tarballs y una
  fuente CUDA 12.8 para Linux; el launcher confía en el estado del runtime del
  backend en vez de la cadena de plataforma del navegador.
- **Monitor: multi-GPU** (MON-1): una card por GPU con TDP por GPU y alertas
  por índice, un `privilege_hint` por SO en el endpoint de límite de potencia,
  lectura de fan por GPU con control 0–100% y verificación por lectura, y
  control de fan en Windows vía NvAPI Direct; el multi-GPU también se muestra
  en el navbar y la barra de alertas de Monitor.
- **Runtime: bundle CUDA elegido según la arquitectura de la GPU** (RT-15):
  el bundle de aceleración se elige por la compute capability de la GPU
  (consultada vía NVML), de modo que las tarjetas Pascal (GTX 10-series) y
  las más nuevas (Turing/Ampere/Ada) cargan cada una su build CUDA correcta.
- **Modelos: el escaneo de directorios usa los model dirs actuales del panel**
  sin necesidad de guardar la config antes.

### Corregido
- **Seguridad (SEC-1 / SEC-5)**: extracción de zip contenida con
  `is_relative_to`; los locks de descarga de Piper/Kokoro pasaron de
  `threading.Lock` a `asyncio.Lock`; `CancelledError` re-lanzado en la etapa
  de aceleración; el health polling degrada en cargas lentas en vez de dar
  error a los 30s; los frames de error WS sin tipar se tratan como
  terminales; el manejo de errores se endureció en Chat, Monitor, Launcher,
  Benchmark, Reports, App y SpeakButton (nuevas keys i18n).
- **Chat: la lista de modelos del endpoint ahora va por proxy del backend**
  (E4): el navegador no puede llamar `/v1/models` directo a un endpoint
  ajeno (CORS), así que Chat hace `GET /api/chat/endpoint-models?url=...`; el
  backend valida la URL (solo http/https), aplica timeout de 3s y devuelve
  los ids de modelos.
- **Launcher: parada ordenada de llama-server** (E6): `stop()` ahora envía
  primero `POST /exit` (shutdown ordenado, unload del modelo) y espera un
  grace period antes de caer a terminate/kill — en Windows `terminate()` es
  un kill inmediato que podía saltarse el unload.
- **Chat: el debounce del autosave mantiene identidad estable** (E8):
  `scheduleSave` lee `open`/`search` por referencia (deps `[save, load]`), de
  modo que `onFinish` del chat ya no reprograma el debounce en cada cambio de
  open/search y el recargo de lista tras guardar usa los valores actuales.
- **Árbol de conversaciones: `getPath` construye el índice de hijos una
  vez** (E3): el Map `kidsOf` hace cada salto del camino O(1) en vez de
  rescanear el array (O(n) por salto en árboles profundos); mismo resultado,
  incluido el fallback al último hijo creado. Con test unitario nuevo.
- **Arranque: silenciado el warning de deprecation de
  `WindowsProactorEventLoopPolicy`** (E11) y la llamada se salta en Python
  ≥3.15, donde el selector ya soporta subprocess.
- **Monitor: la lluvia de matriz siempre se anima** — `prefers-reduced-motion`
  ya no congela el efecto de fondo.

### Docs
- **Auditoría de seguridad (SEC-1)**: reporte completo con secciones a–e
  (red/telemetría, backend, secrets/SSRF, XSS de frontend, errores
  funcionales).
- **Checklist de release** (`docs/RELEASE-CHECKLIST.md` + `.es.md`): el
  procedimiento completo de release — bump de versión en 6 archivos juntos,
  tag/push, build (sidecar + NSIS desde la raíz del repo con override
  mínimo), `latest.json`, sync público, GitHub Release no-prerelease,
  verificación — más los gotchas aprendidos. Enlazado desde la sección de
  versionado del README.
- **Export público OSS**: el reporte SEC-1 está redactado en el repo público
  (nombres de ramas internas y detalle de errores funcionales movidos a un
  anexo privado), la ruta del intérprete del checklist se generalizó y se
  agregó una nota de arquitectura CUDA a los README públicos.

### Build / dependencias
- Tauri 2.12.0 → 2.12.1, tauri-build 2.7.0 → 2.7.1.
- source-map-js 1.2.1 → 1.2.2 (GHSA-68fv-2mgg-jv7q).

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
  título de ventana, onboarding y diálogo Acerca de; el ejecutable ahora es
  `glyvexai.exe`. La identidad del instalador NSIS se mantiene como
  `"Glyvex AI Suite"` (con espacio, carpeta "Glyvex-AI-Suite") para que los
  upgrades in-place desde 0.7.6 sigan funcionando — ver Corregido.
- **Ollama ya no preselecciona `/usr/bin/ollama`** (PUB-4): el default ahora
  queda vacío en Windows y el launcher resuelve `ollama` desde PATH.

### Corregido (re-release 2026-10-06, misma versión — assets reemplazados)
- **El cambio de nombre rompía el upgrade in-place de NSIS**: el build
  inicial de 0.7.7-beta renombró la identidad del instalador ("Glyvex AI
  Suite" → "GlyvexAI Suite"), lo que mueve el directorio de instalación por
  defecto de NSIS y la key de desinstalación del registro, de modo que el
  updater pasivo no encontraba la instalación 0.7.6 y colocaba 0.7.7
  lado a lado. `productName` revertido a "Glyvex AI Suite" y
  `startMenuFolder` a "Glyvex-AI-Suite"; el branding "GlyvexAI Suite" se
  mantiene en el título de ventana y en la UI.
- **Crash por chunks stale tras un update**: un `errorElement` del router
  (`ChunkReloadError`) detecta "Failed to fetch dynamically imported module"
  (chunks hasheados en 404 cuando una instancia vieja sirve los assets de la
  build anterior) y hace un hard reload único con guarda en vez de mostrar
  una página rota.

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