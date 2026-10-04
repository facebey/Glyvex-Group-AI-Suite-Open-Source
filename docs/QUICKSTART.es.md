# Quickstart

> **Idioma:** [English](QUICKSTART.md)

El camino más corto para tener Glyvex-AI-Suite corriendo en tu máquina. Acá va
el mínimo; el [README](../README.md) trae el detalle completo (multi-instancia,
variables de entorno, seguridad, release).

> **¿Solo querés usarla en Windows?** No hace falta nada de esto: bajá el
> instalador `Glyvex AI Suite_<versión>_x64-setup.exe` desde
> [Releases → latest](https://github.com/facebey/Glyvex-Group-AI-Suite-Open-Source/releases/latest),
> ejecutalo y seguí el asistente. Si Windows SmartScreen avisa, es porque el
> instalador no está firmado: **Más información → Ejecutar de todas formas**.
> Esta guía es para correrla desde el código fuente.

## Prerrequisitos

- **Python 3.11+**
- **Node.js 20 LTS+** (frontend Vite/React)
- Un backend de inferencia, según qué uses:
  - **Windows:** el [runtime embebido](../README.md) — la suite descarga su
     propia build probada de llama.cpp (pin b11349, CUDA), sin instalar nada —
     **o** [`llama-server`](https://github.com/ggml-org/llama.cpp),
     [Ollama](https://ollama.com) o [LM Studio](https://lmstudio.ai) en modo servidor.
  - **Otras plataformas:** [`llama-server`](https://github.com/ggml-org/llama.cpp),
     [Ollama](https://ollama.com) o [LM Studio](https://lmstudio.ai) en modo servidor.
- **NVIDIA drivers + CUDA** — *opcional*. Sin GPU NVIDIA todo corre en CPU; la
  GPU solo acelera los modelos grandes.
- **Voz** — *opcional*. El dictado local usa whisper.cpp (se descarga desde
  la app, en Windows); en cualquier SO también podés instalar `faster-whisper`
  con `pip install -r requirements-optional.txt`. Ver
  [speech-to-text.md](speech-to-text.md).

## 1. Instalar

Backend:

```bash
git clone https://github.com/facebey/Glyvex-Group-AI-Suite-Open-Source.git
cd Glyvex-Group-AI-Suite-Open-Source
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Frontend:

```bash
cd frontend
npm install
```

## 2. Arrancar

**Opción A — todo-en-uno (producción local):**

```bat
start.cmd      # Windows (CMD o PowerShell: .\start.ps1)
```
```bash
./start.sh     # Linux/macOS
```

Compila el frontend a `frontend/dist` (si no existe) y levanta todo en
`127.0.0.1:7981`. Abrí `http://127.0.0.1:7981`.

**Opción B — desarrollo (2 procesos, con hot-reload):**

```bash
# Terminal 1 — backend
cd backend
uvicorn main:app --reload --port 7981
```
```bash
# Terminal 2 — frontend
cd frontend
npm run dev
```

Abrí `http://localhost:5173`.

## 3. Primera vez (4 pasos)

Con `config.json` vacío aparece la pantalla de onboarding con estos mismos
pasos y checkmarks en vivo:

1. **Configurar** — agregá al menos un directorio con modelos `.gguf` y elegí
   el backend de inferencia:
   - **Windows:** en el onboarding (o en `/config` → **Runtime**) tocá
     **Descargar runtime** y la suite instala su propia build probada de
      llama.cpp (pin b11349). No tocás `binary_path`.
   - **Otras plataformas / modo experto:** en `/config`, indicá el
     `binary_path` de `llama-server` (y/o Ollama/LM Studio).
2. **Escanear** — tocá "Escanear ahora"; el inventario queda en el Launcher.
3. **Lanzar** — en `/launcher`, elegí un modelo y tocá **LAUNCH**.
4. **Chatear** — en `/` el endpoint del modelo lanzado aparece en el selector;
   escribí y la respuesta llega en streaming.

## Siguiente

- [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) — cómo se conectan backend y
  frontend, flujos de datos y dónde vive el estado.
- [`docs/modules.md`](modules.md) — detalle técnico por módulo (M0–M8).
- [`docs/launcher-params.md`](launcher-params.md) — parámetros de lanzamiento
  de `llama-server` que maneja el Launcher.
- [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md) — si algo no arranca.
