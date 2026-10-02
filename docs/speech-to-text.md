# Voice: dictation and text-to-speech

> **Language:** [Español](speech-to-text.es.md)

The suite has both directions of voice, both 100% local unless you
deliberately choose the browser engine:

- **Speech to text (STT)** — the chat's microphone. The text lands in the
  textarea and stays editable: it is never sent on its own.
- **Text to speech (TTS)** — the "listen" button of each model response.

---

## Speech to text

| Engine | Needs | Privacy | Where it works |
|---|---|---|---|
| `whispercpp` | whisper.cpp runtime (downloaded from the app) | Everything local | Packaged app and development, on Windows |
| `whisper` | `faster-whisper` (`requirements-optional.txt`) | Everything local | From source code, on any OS |
| `browser` | nothing | The audio goes out to the internet | Chrome, Edge, Safari (not in Tauri or Firefox) |

Chosen with `stt.engine` in `data/config.json` (or in Settings), or with the
`STT_ENGINE` environment variable, which takes precedence.

### The default is `auto`, and not for convenience

`auto` chooses, in this order:

1. **whisper.cpp** if its runtime is ready,
2. otherwise **faster-whisper** if it is installed,
3. otherwise the browser's Web Speech API.

It has to be this way because **Tauri's WebView does not expose the Web Speech
API**: neither WebView2 on Windows nor WebKitGTK on Linux has it enabled. It
is a function Chrome implements against Google's services, not something
that comes with the rendering engine. Inside the packaged app, local
dictation is mandatory.

### whisper.cpp (native, recommended)

The `whisper-cli` binary and the model are downloaded on demand: from the
first-run provisioning screen (STT step) or from Settings → STT. It does not
need Python or pip, which is why it is the only engine that works inside the
Tauri bundle. The audio and the transcription go through files (not through
stdout), so accents are not broken by the console codepage.

### faster-whisper (from source code)

```
pip install -r requirements-optional.txt
```

It is not in `requirements.txt`: it drags in CTranslate2 and downloads the
weights on first use. The app triggers that download from
`POST /api/stt/warmup` **before** starting to record, so the wait does not
come after you already spoke. The button shows a download icon the first
time.

ffmpeg is not needed: the browser records in webm/opus and faster-whisper
decodes with PyAV, which carries its own decoders.

### Models

| Model | Download | When |
|---|---|---|
| `tiny` | ~75 MB | Modest machines, short dictation |
| `base` (default) | ~145 MB | Reasonable balance on CPU |
| `small` | ~484 MB | Notably better, still viable on CPU |
| `large-v3-turbo` | ~1.6 GB | With GPU |

### Web Speech API is not local dictation

It is worth saying explicitly because the name misleads: in Chrome the audio
is sent to Google's servers to be transcribed. For a tool that presents
itself as local, it is the exception. The microphone's tooltip clarifies it
when this engine is active. If you want dictation that does not leave the
machine, use `auto`, `whispercpp` or `whisper`.

### Configuration (`stt`)

| Key | Default | What it does |
|---|---|---|
| `engine` | `auto` | `auto` \| `whispercpp` \| `whisper` \| `browser` |
| `language` | `es-AR` | Dictation language (BCP-47); the local engines inherit it |
| `whisper_model` | `base` | Which of the models above |
| `whisper_device` | `auto` | `auto` \| `cpu` \| `cuda` (faster-whisper) |
| `whisper_compute_type` | `int8` | `int8` on CPU, `float16` with GPU (faster-whisper) |
| `whisper_language` | `""` | Language override; empty = uses `language` (or the app's language) |
| `translate_english` | `false` | The local engines transcribe directly into English |

### Secure context

`getUserMedia` and the Web Speech API only work on `https://` or
`localhost`. `main.py` listens on `127.0.0.1` by default, so access is
local and the microphone works; if it is deliberately exposed to the network
(`GLYVEX_HOST=0.0.0.0`) and you enter from another machine by IP
(`http://192.168.x.x:7981`), the microphone will appear disabled. The
tooltip explains it. It is not an app bug: it is a browser restriction.

---

## Text to speech

| Engine | Needs | Quality |
|---|---|---|
| `kokoro` | Kokoro model (`.onnx` + `voices.bin`), downloaded in Settings → TTS | Neural, the most natural |
| `piper` | a Piper voice (`.onnx` + `.json`), downloaded in Settings → TTS | Neural, lightweight |
| `sapi` | nothing: the voices Windows ships with | Classic synthetic |

The `auto` engine (default) chooses **Kokoro** if it is ready, otherwise
**Piper**, otherwise **SAPI**. With `piper` or `kokoro` fixed, the app always
uses that engine and shows a clear error if it is not downloaded.

In the packaged app the neural chain (piper, kokoro-onnx, onnxruntime,
espeak-ng) travels inside the `glyvex-backend` sidecar; only the voices and
the model are downloaded.

- **Cache:** the generated WAV is kept in memory (LRU of 32 entries, by
  engine + voice + speed + text). Listening to a response again is
  instant.
- **SAPI voice:** if none is fixed, it looks es-AR → es-MX → es-ES → the
  system's default voice (Windows does not ship es-AR out of the box).
- **Playback:** with the `<audio>` element; if the browser blocks autoplay
  or playback fails, a notice is shown instead of staying silent.

### Configuration (`tts`)

| Key | Default | What it does |
|---|---|---|
| `enabled` | `true` | `false` hides the "listen" button |
| `engine` | `auto` | `auto` \| `kokoro` \| `piper` \| `sapi` |
| `voice` | `""` | Fixed SAPI voice (empty = es-AR → es-MX → es-ES chain) |
| `rate` | `0` | Speed from -10 (slow) to 10 (fast) |
| `piper_voice` | `es_AR-daniela-high` | Piper voice |
| `kokoro_voice` | `ef_dora` | Kokoro voice |
