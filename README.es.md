# Glyvex-AI-Suite

🌐 *Languages / Idiomas:* [English](README.md) | **Español**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![Node.js 20](https://img.shields.io/badge/Node.js-20%20LTS-green.svg)](https://nodejs.org/)

**Glyvex-AI-Suite** es una aplicación local — parte de **Glyvex Group** — para
gestionar, lanzar, chatear con, y evaluar modelos LLM locales (GGUF/GGML vía
llama-server, Ollama, LM Studio) sin depender de ningún servicio en la nube.
Todo corre en tu propia máquina: el inventario de modelos, los procesos, las
conversaciones y los benchmarks quedan en tu disco.

Optimizada para **GPU NVIDIA (CUDA)**, pero no es un requisito: corre en
CPU pura en cualquier máquina moderna, la GPU solo acelera los modelos
grandes, y los templates de hardware del Launcher ajustan la carga a lo
que la máquina tiene. Sin GPU NVIDIA, el Monitor y el Launcher degradan
de forma segura (ver sección de Módulos).

**Sitio de producto:** [ai-suite.glyvexgroup.com](https://ai-suite.glyvexgroup.com/) — [División Glyvex AI](https://ai.glyvexgroup.com/)

## Screenshots

| | |
|---|---|
| ![Chat — conversación con streaming y métricas en vivo](screenshots/chat.png)<br><sub>Chat: streaming, razonamiento y métricas en vivo (t/s, TTFT)</sub> | ![Launcher — modelos detectados](screenshots/launcher-modelos-detectados.png)<br><sub>Launcher: inventario de modelos agrupado por carpeta</sub> |
| ![Launcher — opciones de lanzamiento](screenshots/launcher-opciones.png)<br><sub>Launcher: opciones de lanzamiento</sub> | ![Launcher — opciones avanzadas](screenshots/launcher-opciones-avanzadas.png)<br><sub>Launcher: opciones avanzadas</sub> |
| ![Launcher — opciones avanzadas (2)](screenshots/launcher-opciones-avanzadas-2.png)<br><sub>Launcher: opciones avanzadas (MTP, contexto)</sub> | ![Launcher — backends en línea](screenshots/launcher-online.png)<br><sub>Launcher: backends en línea</sub> |
| ![Benchmark — resultados](screenshots/benchmark.png)<br><sub>Benchmark: resultados del run</sub> | ![Monitor — GPU y vitales](screenshots/monitor.png)<br><sub>Monitor: GPU, CPU y vitales del LLM server</sub> |
| ![Reports — historial](screenshots/reports.png)<br><sub>Reports: historial de benchmarks</sub> | ![Config — configuración](screenshots/config.png)<br><sub>Config: configuración general</sub> |

## Requisitos

- **Python 3.11+**
- **Node.js 20 LTS+** (para el frontend Vite/React)
- **NVIDIA drivers + CUDA** — *opcional*. Sin GPU NVIDIA, el Monitor (M5)
  muestra "GPU NVIDIA no detectada" en vez de crashear, y el resto de la
  suite funciona igual (podés correr modelos 100% en CPU).
- `faster-whisper` — *opcional*, para la transcripción local de voz del
  micrófono del chat: `pip install -r requirements-optional.txt`
  (ver [`docs/voz-a-texto.md`](docs/voz-a-texto.md)).
- Al menos uno de estos backends de inferencia instalado aparte, según qué
  vayas a usar: [`llama-server`](https://github.com/ggml-org/llama.cpp)
  (de llama.cpp), [Ollama](https://ollama.com), o [LM Studio](https://lmstudio.ai)
   corriendo en modo servidor.

## Plataformas

| Plataforma | Estado | Notas |
|---|---|---|
| **Windows** | ✅ Desarrollo y pruebas | Scripts `start.cmd` (CMD) y `start.ps1` (PowerShell). |
| **Linux** | ⏳ Próximamente | `start.sh` ya existe; el soporte formal (pruebas completas y CI) está en el roadmap. |
| **macOS** | ⏳ Próximamente | Usa el mismo `start.sh`; no se prueba en el ciclo actual. |

## Instalación

### 1. Backend

```bash
# Desde la raíz del repositorio
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Frontend

```bash
cd frontend
npm install
```

### 3. Arranque en desarrollo (2 procesos, con hot-reload)

Terminal 1 — backend:

```bash
cd backend
uvicorn main:app --reload --port 7981
```

Terminal 2 — frontend:

```bash
cd frontend
npm run dev
```

Abrí `http://localhost:5173`.

### 4. Arranque todo-en-uno (producción local)

Linux/macOS:

```bash
./start.sh
```

Windows (CMD o PowerShell):

```bat
start.cmd
```
```powershell
.\start.ps1
```

Compilan el frontend a `frontend/dist` (si no existe) y levantan `uvicorn` en
`127.0.0.1:7981`, sirviendo la SPA compilada desde `/`. Los tres scripts
respetan `GLYVEX_PORT` y `GLYVEX_HOST` (el bind por defecto es `127.0.0.1`;
exponerlo a la red es opt-in a propósito, con `GLYVEX_HOST=0.0.0.0`, y
requiere autenticación — ver sección Seguridad).

### 5. Varias instancias en la misma máquina

Se puede correr una segunda instancia (por ejemplo, para probar cambios sin
tocar los datos reales) con el flag `--env`:

```bash
./start.sh --env testing        # Linux/macOS
start.cmd --env testing         # Windows CMD
.\start.ps1 -Env testing        # PowerShell
```

Cada entorno se define en un archivo de `environments/`:

| | `production.env` | `testing.env` |
|---|---|---|
| Backend | `127.0.0.1:7981` | `127.0.0.1:22222` |
| Dev server (Vite) | `:5173` | `:25173` |
| Datos | `./data` | `./data-testing` |

**Para mover una instancia de puerto se toca un solo archivo.** `vite.config.js`
lee el mismo `environments/<nombre>.env`, así que cambiar `GLYVEX_PORT` y
`GLYVEX_DEV_PORT` ahí alinea solo el backend, el proxy `/api` del dev server y
el CORS. (Acordate de actualizar también `GLYVEX_CORS_ORIGINS`, que tiene que
listar el `GLYVEX_DEV_PORT` de esa instancia.)

Variables que definen cada entorno:

| Variable | Para qué |
|---|---|
| `GLYVEX_ENV` | Nombre del entorno. Informativo: aparece en el log de arranque. |
| `GLYVEX_HOST` / `GLYVEX_PORT` | Bind de uvicorn. |
| `GLYVEX_DEV_PORT` | Puerto del dev server de Vite. No lo usa el backend. |
| `GLYVEX_DATA_DIR` | Dónde vive el estado de la instancia (relativo a la raíz del repo, o absoluto). |
| `GLYVEX_CORS_ORIGINS` | Orígenes permitidos, separados por comas. |

Todo el estado de una instancia cuelga de su `GLYVEX_DATA_DIR`: `config.json`,
`glyvex.db`, `metrics.db`, `attachments/`, `logs/`, `benchmarks/`. Lo único
que se comparte entre instancias es lo que está versionado en el repo y es de
solo lectura: los sets de prompts (`backend/prompts/`) y la semilla de
templates de hardware (`data/templates/hw_templates.json`).

En modo desarrollo, el frontend de la instancia de testing se levanta con el
sistema de modos de Vite:

```bash
cd frontend && npm run dev -- --mode testing
```

Sin ninguna variable `GLYVEX_*` definida, todo se comporta como antes:
`DATA_DIR` es `./data` y el puerto es 7981.

## Uso básico (4 pasos)

Si es la primera vez que abrís la app con `config.json` vacío (o sin el
archivo, que se genera con los defaults; ver `data/config.example.json` para
la referencia completa), vas a ver una pantalla de onboarding con estos
mismos pasos y checkmarks en vivo.

1. **Configurar** — en `/config`, indicá el `binary_path` de `llama-server`
   (y/o Ollama/LM Studio) y agregá al menos un directorio donde tengas
   modelos `.gguf`/`.safetensors`.
2. **Escanear** — en la misma pantalla, tocá "Escanear ahora". El inventario
   se guarda en `data/models.json` y queda disponible en el Launcher.
3. **Lanzar** — en `/launcher`, elegí un modelo (vista Group agrupa por
   carpeta con sus variantes MTP/mmproj, o cambiá a vista List), ajustá los
   parámetros de contexto/GPU si hace falta, y tocá **LAUNCH**.
4. **Chatear** — en `/` (Chat), el endpoint del modelo recién lanzado
   aparece automático en el selector. Escribí y listo — la respuesta llega
   en streaming token a token.

Desde ahí también podés correr un benchmark completo (`/benchmark`) contra
el modelo activo, o ver su consumo de HW en vivo (`/monitor`).

El Launcher gestiona el ciclo de vida completo del proceso: arranca,
monitorea y detiene `llama-server` por vos. Por ahora no se adjunta a
servers que estén corriendo afuera de la app.

## Vistas

| Ruta | Vista | Contenido |
|------|-------|-----------|
| `/` | Chat | Streaming, ramas (regeneraciones), adjuntos, mic, razonamiento, métricas, export, historial |
| `/launcher` | Launcher | Vistas Group/List, templates de hardware, presets de sampling, launch/stop, logs en vivo, tira de vitales del proceso (t/s, contexto, cola) |
| `/benchmark` | Benchmark | Selección de sets (incluidos propios), run cancelable, progreso por WS, resultados |
| `/monitor` | Monitor | Card de GPU, heatmap de cores, sparklines 60 s, sección **LLM Server** (vitales en vivo + histórico de t/s, contexto y cola por proceso), gráfico histórico HW con retención, indicador de conexión WS |
| `/reports` | Reports | Historial de benchmarks, gráficos Recharts (t/s por prompt, TTFT vs tokens), comparación, reportes HTML |
| `/config` | Config | Backend, model_dirs, scan, tools, STT, adjuntos, templates, monitor (histórico y retenciones) |

## Módulos

| Módulo | Qué hace |
|--------|----------|
| **M0 — Skeleton** | Base del proyecto: FastAPI + React + config persistida. |
| **M1 — Inventario** | Escanea tus `.gguf`, extrae familia/cuantización, lee metadata GGUF, detecta módulos embebidos (MTP/visión), agrupa por carpeta. |
| **M2 — Launcher** | Lanza `llama-server`/Ollama/LM Studio con templates de hardware, logs en vivo, MTP/draft y stop/restart limpios. |
| **M3 — Chat** | Streaming con razonamiento separado, métricas en vivo, ramas, adjuntos, tool calling, voz a texto. |
| **M4 — Benchmark** | 58 prompts en 6 categorías + sets propios, scoring, reportes HTML, comparación de runs. |
| **M5 — Monitor** | GPU/CPU/RAM en tiempo real + histórico persistente con retención configurable. |
| **M6 — Integración** | Widget de estado en navbar, toasts, shortcuts, onboarding. |
| **M7 — Base de datos** | SQLite async: conversaciones (árbol), benchmarks, templates, sets. |
| **M8 — Vitales del LLM server** | t/s, prompt processing, caché, % de aceptación MTP desde `/metrics` de `llama-server`. |

Detalle técnico por módulo: [`docs/modulos.md`](docs/modulos.md) ·
Parámetros de lanzamiento: [`docs/launcher-params.md`](docs/launcher-params.md)

## Shortcuts de teclado (Chat)

| Atajo | Acción |
|-------|--------|
| `Enter` | Enviar mensaje |
| `Shift+Enter` | Nueva línea |
| `Ctrl+Enter` (o `Cmd+Enter`) | Enviar mensaje (alternativa global) |
| `Esc` | Cancelar la generación en curso |
| `Ctrl+L` | Limpiar la conversación (pide confirmación) |
| `Ctrl+E` | Mostrar/ocultar el panel de exportar |

## Herramientas del modelo (chat)

El chat expone al modelo dos herramientas, invocadas de forma nativa vía
`tool_calls` (rondas limitadas por `tools.max_rounds` en `/config`):

- **Búsqueda web** — proveedor por defecto **DuckDuckGo (ddgs)**: sin
  servidor, sin API key, sin Docker. Proveedores opcionales: **SearXNG**
  (local, vía HTTP), **Brave** y **Tavily** (API keys por env var
  `BRAVE_API_KEY`/`TAVILY_API_KEY` o por config).
- **Fetch de URLs** — límites de caracteres, timeout y user-agent
  configurables, extracción de contenido con trafilatura/readability, y
  **bloqueo de hosts privados por defecto** (`tools.allow_private_hosts`).
- `GET /api/chat/tools/status` informa al frontend qué proveedores están
  activos.

## Documentación

| A dónde ir | Qué vas a encontrar |
|---|---|
| [README](README.md) | Panorama completo: módulos (M0–M8), instalación, uso, seguridad y release. |
| [`docs/QUICKSTART.md`](docs/QUICKSTART.md) | El camino más corto a tenerla corriendo: prerrequisitos, install, arranque y primera vez. |
| [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md) | Mapa del código: procesos, flujos de datos y dónde vive el estado. |
| [`docs/PRIVACIDAD.md`](docs/PRIVACIDAD.md) | Qué sale de la máquina y qué no: 100% local, sin telemetría saliente. |
| [`docs/modulos.md`](docs/modulos.md) | Detalle técnico por módulo (M0–M8). |
| [`docs/launcher-params.md`](docs/launcher-params.md) | Referencia de parámetros de lanzamiento de `llama-server` (los que maneja el Launcher). |
| [`docs/voz-a-texto.md`](docs/voz-a-texto.md) | Micrófono del chat: Web Speech API vs Whisper local, modelos, seguridad y empaquetado. |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | Problemas frecuentes y cómo resolverlos. |
| [`docs/searxng/README.md`](docs/searxng/README.md) | Proveedores de búsqueda web (DuckDuckGo, SearXNG, Brave, Tavily) y `tools.*`. |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Cómo contribuir: ramas, commits, tests y conventions. |

## Seguridad

- **100% local por diseño**: conversaciones, inventario y benchmarks no
  salen de la máquina; la única salida a red es la búsqueda/fetch de las
  herramientas del modelo.
- CORS restringido a lo que liste `GLYVEX_CORS_ORIGINS` en el entorno activo
  (default `http://localhost:5173`). Con `allow_credentials=True`, un `*` no
  funciona: los navegadores rechazan esa combinación, así que hay que listar
  los orígenes uno por uno.
- `fetch_url` bloquea hosts privados por defecto.
- `data/config.json` **no se commitea** (está en `.gitignore`); la plantilla
  está en `data/config.example.json`. Las API keys (`brave_api_key`,
  `tavily_api_key`) se leen primero de las variables de entorno
  `BRAVE_API_KEY`/`TAVILY_API_KEY`; si las escribís en `config.json` quedan
  solo en tu máquina.
- El servidor escucha en `127.0.0.1` por defecto (app local, sin
  autenticación). Para exponerlo a la red hay que pasarlo a propósito:
   `GLYVEX_HOST=0.0.0.0 ./start.sh` — y en ese caso, **no lo hagas sin
   autenticación**.
- `log_file` del launcher validado contra path traversal (400 si queda
  fuera de `BASE_DIR`).

## Roadmap

- [x] **M0** — Skeleton del proyecto
- [x] **M1** — Gestión de modelos (scanner e inventario)
- [x] **M2** — Lanzador de modelos
- [x] **M3** — Interfaz de chat
- [x] **M4** — Benchmark suite
- [x] **M5** — Monitor de recursos de hardware
- [x] **M6** — Integración final y pulido
- [x] **M7** — Base de datos SQLite (conversaciones, benchmarks, templates, sets)
- [x] **M8** — Vitales del LLM server (Prometheus `/metrics`, histórico en `metrics.db`)
- [ ] Soporte para GPU AMD (el stack es hoy optimizado para NVIDIA/CUDA)
- [ ] Soporte formal para Linux (y macOS): pruebas completas y CI

## Tests

Suite de tests automatizados (pytest + pytest-asyncio + httpx, 297 tests,
sin unittest, sin requests, sin GPU/modelos/red real — todo mockeado: mock
LLM server uvicorn en `:18080`, binarios fake, SQLite en memoria):

```bash
# Instalar dependencias de test
pip install pytest>=8.0 pytest-asyncio>=0.24 pytest-mock>=3.14 httpx>=0.27 anyio>=4.0 pytest-cov

# Correr todos los tests (desde la raíz del repositorio)
pytest tests/ -v

# Un módulo específico
pytest tests/test_models.py -v

# Con coverage
pytest tests/ --cov=backend --cov-report=term-missing --cov-report=html
```

## Versionado

La suite usa [SemVer](https://semver.org/lang/es/). La versión vive en
`backend/main.py` (`APP_VERSION`, expuesta en `/api/health` y `/api/info`) y
se mantiene en sync con tags anotadas de git `vX.Y.Z`:

- **MAJOR** — breaking changes en esquemas de datos (`config.json`, `glyvex.db`, `metrics.db`) o API interna.
- **MINOR** — módulos/features nuevos (M8, herramientas, backends).
- **PATCH** — fixes y pulido.

Para armar un release:

```bash
# 1. Subir APP_VERSION en backend/main.py y commitear
# 2. Suite completa en verde
pytest tests/ -q
# 3. Tag anotada + push
git tag -a vX.Y.Z -m "<resumen del release>"
git push origin main vX.Y.Z
```

## Licencia

Apache License 2.0 — ver [LICENSE](LICENSE).

Copyright (c) 2026 Glyvex Group

La suite se distribuye bajo **Apache 2.0** para la comunidad. Las ediciones
**Enterprise** (Glyvex Suite) se licencian por separado bajo un acuerdo
comercial de Glyvex Group.
