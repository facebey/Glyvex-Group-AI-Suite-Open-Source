# Quickstart

> **Language:** [Español](QUICKSTART.es.md)

The shortest way to get Glyvex-AI-Suite running on your machine. This is the
minimum; the [README](../README.md) has the full detail (multi-instance,
environment variables, security, release).

> **Just want to use it on Windows?** None of this is needed: download the
> installer `GlyvexAI Suite_<version>_x64-setup.exe` from
> [Releases → latest](https://github.com/facebey/Glyvex-Group-AI-Suite-Open-Source/releases/latest),
> run it and follow the wizard. If Windows SmartScreen warns you, it is
> because the installer is not signed: **More info → Run anyway**.
> This guide is for running it from source.

## Prerequisites

- **Python 3.11+**
- **Node.js 20 LTS+** (Vite/React frontend)
- An inference backend, depending on what you use:
  - **Windows:** the [embedded runtime](../README.md) — the suite downloads
     its own tested build of llama.cpp (pin b11349, CUDA) without installing
     anything — **or** [`llama-server`](https://github.com/ggml-org/llama.cpp),
     [Ollama](https://ollama.com) or [LM Studio](https://lmstudio.ai) in server mode.
  - **Other platforms:** [`llama-server`](https://github.com/ggml-org/llama.cpp),
     [Ollama](https://ollama.com) or [LM Studio](https://lmstudio.ai) in server mode.
- **NVIDIA drivers + CUDA** — *optional*. Without an NVIDIA GPU everything runs
  on CPU; the GPU only accelerates the large models.
- **Voice** — *optional*. Local dictation uses whisper.cpp (downloaded from
  the app, on Windows); on any OS you can also install `faster-whisper` with
  `pip install -r requirements-optional.txt`. See
  [speech-to-text.md](speech-to-text.md).

## 1. Install

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

## 2. Start

**Option A — all-in-one (local production):**

```bat
start.cmd      # Windows (CMD or PowerShell: .\start.ps1)
```
```bash
./start.sh     # Linux/macOS
```

Compiles the frontend to `frontend/dist` (if it does not exist) and brings
everything up on `127.0.0.1:7981`. Open `http://127.0.0.1:7981`.

**Option B — development (2 processes, with hot reload):**

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

Open `http://localhost:5173`.

## 3. First time (4 steps)

With an empty `config.json`, the onboarding screen appears with these same
steps and live checkmarks:

1. **Configure** — add at least one directory with `.gguf` models and choose
   the inference backend:
   - **Windows:** in the onboarding (or in `/config` → **Runtime**) click
     **Download runtime** and the suite installs its own tested build of
     llama.cpp (pin b11349). You never touch `binary_path`.
   - **Other platforms / expert mode:** in `/config`, point `binary_path` to
     `llama-server` (and/or Ollama/LM Studio).
2. **Scan** — click "Scan now"; the inventory shows up in the Launcher.
3. **Launch** — in `/launcher`, pick a model and click **LAUNCH**.
4. **Chat** — in `/` the launched model's endpoint appears in the selector;
   type and the response arrives in streaming.

## Next

- [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) — how backend and frontend
  connect, data flows and where state lives.
- [`docs/modules.md`](modules.md) — technical detail per module (M0–M8).
- [`docs/launcher-params.md`](launcher-params.md) — `llama-server` launch
  parameters the Launcher handles.
- [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md) — if something does not start.
