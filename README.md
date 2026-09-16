# Glyvex-AI-Suite

**Glyvex-AI-Suite** es una aplicación local — parte de **Glyvex Group** — para
gestionar, lanzar, chatear con, y evaluar modelos LLM locales (GGUF/GGML vía
llama-server, Ollama, LM Studio) sin depender de ningún servicio en la nube.
Todo corre en tu propia máquina: el inventario de modelos, los procesos, las
conversaciones y los benchmarks quedan en tu disco.

Pensada originalmente para una estación de trabajo con **NVIDIA RTX 3090
(24GB) + Intel i9-12900K (16 cores) + 64GB RAM sobre Linux**, pero el monitor
de hardware y el launcher degradan de forma segura si no hay GPU NVIDIA
presente (ver sección de Módulos).

## Screenshots

<!-- Screenshot: Chat.jsx — conversación con streaming, panel de razonamiento colapsado y MetricsBar mostrando t/s en vivo -->
<!-- Screenshot: Launcher.jsx en vista Group — cards colapsables por carpeta con selector de cuantización, MTP y mmproj -->
<!-- Screenshot: Benchmark.jsx post-run — tabla de resultados expandible junto a los gráficos de Recharts (t/s por prompt y TTFT vs tokens) -->
<!-- Screenshot: Monitor.jsx — card de GPU con barra de VRAM, heatmap de cores y sparklines de los últimos 60s -->

## Requisitos

- **Python 3.11+**
- **Node.js 20 LTS+** (para el frontend Vite/React)
- **NVIDIA drivers + CUDA** — *opcional*. Sin GPU NVIDIA, el Monitor (M5)
  muestra "GPU NVIDIA no detectada" en vez de crashear, y el resto de la
  suite funciona igual (podés correr modelos 100% en CPU).
- Al menos uno de estos backends de inferencia instalado aparte, según qué
  vayas a usar: [`llama-server`](https://github.com/ggml-org/llama.cpp)
  (de llama.cpp), [Ollama](https://ollama.com), o [LM Studio](https://lmstudio.ai)
  corriendo en modo servidor.

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
uvicorn main:app --reload --port 7860
```

Terminal 2 — frontend:

```bash
cd frontend
npm run dev
```

Abrí `http://localhost:5173`.

### 4. Arranque todo-en-uno (producción local)

```bash
./start.sh
```

Compila el frontend a `frontend/dist` (si no existe) y levanta `uvicorn` en
`:7860`, sirviendo la SPA compilada desde `/`.

## Uso básico (4 pasos)

Si es la primera vez que abrís la app con `config.json` vacío, vas a ver una
pantalla de onboarding con estos mismos pasos y checkmarks en vivo.

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

## Módulos

| Módulo | Qué hace |
|--------|----------|
| **M0 — Skeleton** | Base del proyecto: FastAPI + React + config persistida en `data/config.json` con dot-notation. |
| **M1 — Inventario** | Escanea los `model_dirs` configurados (GGUF/GGML/safetensors), extrae familia/parámetros/cuantización del nombre de archivo, cachea en `data/models.json`. Scan incremental por `path+mtime`, progreso vía SSE. |
| **M2 — Launcher** | Lanza `llama-server`/Ollama/LM Studio con `asyncio.subprocess`, templates de hardware predefinidos, logs en vivo por WebSocket, health check async, stop/restart limpios (SIGTERM→SIGKILL). |
| **M3 — Chat** | Interfaz de chat OpenAI-compatible con streaming (SSE), separación de bloques `<think>` (razonamiento) del contenido de respuesta, métricas de t/s y TTFT en vivo, export a JSON/Markdown. |
| **M4 — Benchmark** | Suite de 58 prompts en 6 categorías (programación, matemática, ciencias, lógica, español, infraestructura de redes) corridos como `asyncio.Task` cancelable, con progreso por WebSocket, reporte HTML autónomo y comparación de runs. |
| **M5 — Monitor** | GPU (pynvml)/CPU/RAM (psutil) en tiempo real vía WebSocket con buffer circular de 300 muestras, degrada a `null` sin crashear si no hay GPU NVIDIA o falta psutil. Incluye agrupación de modelos por carpeta en el Launcher (cuantización base + MTP + mmproj). |
| **M6 — Integración final** | Widget de estado en la navbar (modelo activo + mini GPU), sistema de toasts (Context + `useReducer`), shortcuts de teclado en Chat, estado persistido en `localStorage`, onboarding para instalaciones nuevas, `/api/info` + `/api/state`. |

## Shortcuts de teclado (Chat)

| Atajo | Acción |
|-------|--------|
| `Enter` | Enviar mensaje |
| `Shift+Enter` | Nueva línea |
| `Ctrl+Enter` (o `Cmd+Enter`) | Enviar mensaje (alternativa global) |
| `Esc` | Cancelar la generación en curso |
| `Ctrl+L` | Limpiar la conversación (pide confirmación) |
| `Ctrl+E` | Mostrar/ocultar el panel de exportar |

## Roadmap

- [x] **M0** — Skeleton del proyecto
- [x] **M1** — Gestión de modelos (scanner e inventario)
- [x] **M2** — Lanzador de modelos
- [x] **M3** — Interfaz de chat
- [x] **M4** — Benchmark suite
- [x] **M5** — Monitor de recursos de hardware
- [x] **M6** — Integración final y pulido

## Tests

Suite de tests automatizados (pytest + pytest-asyncio + httpx, sin unittest,
sin requests, sin GPU/modelos/red real — todo mockeado):

```bash
# Instalar dependencias de test
pip install pytest>=8.0 pytest-asyncio>=0.24 pytest-mock>=3.14 httpx>=0.27 anyio>=4.0 pytest-cov

# Correr todos los tests
pytest tests/ -v

# Un módulo específico
pytest tests/test_models.py -v

# Con coverage
pytest tests/ --cov=backend --cov-report=term-missing --cov-report=html
```

## Licencia

Por definir (se agregará próximamente).
