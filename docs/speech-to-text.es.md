# Voz: dictado y lectura en voz alta

> **Idioma:** [English](speech-to-text.md)

La suite tiene las dos direcciones de voz, las dos 100% locales salvo que
elijas a propósito el motor del navegador:

- **Voz a texto (STT)** — el micrófono del chat. El texto cae en el textarea y
  queda editable: nunca se envía solo.
- **Texto a voz (TTS)** — el botón "escuchar" de cada respuesta del modelo.

---

## Voz a texto

| Motor | Necesita | Privacidad | Dónde funciona |
|---|---|---|---|
| `whispercpp` | runtime whisper.cpp (se descarga desde la app) | Todo local | App empaquetada y desarrollo, en Windows |
| `whisper` | `faster-whisper` (`requirements-optional.txt`) | Todo local | Desde el código fuente, en cualquier SO |
| `browser` | nada | El audio sale a internet | Chrome, Edge, Safari (no en Tauri ni Firefox) |

Se elige con `stt.engine` en `data/config.json` (o en Ajustes), o con la
variable de entorno `STT_ENGINE`, que tiene precedencia.

### El default es `auto`, y no por comodidad

`auto` elige, en este orden:

1. **whisper.cpp** si su runtime está listo,
2. si no **faster-whisper** si está instalado,
3. si no la Web Speech API del navegador.

Hace falta que sea así porque **el WebView de Tauri no expone la Web Speech
API**: ni WebView2 en Windows ni WebKitGTK en Linux la tienen habilitada. Es
una función que Chrome implementa contra servicios de Google, no algo que
venga con el motor de renderizado. Dentro de la app empaquetada, el dictado
local es obligatorio.

### whisper.cpp (nativo, recomendado)

El binario `whisper-cli` y el modelo se descargan bajo pedido: desde la
pantalla de provisión del primer arranque (paso STT) o desde Ajustes → STT.
No necesita Python ni pip, por eso es el único motor que funciona dentro del
bundle de Tauri. El audio y la transcripción pasan por archivos (no por
stdout), así los acentos no se rompen con la codepage de la consola.

### faster-whisper (desde el código fuente)

```
pip install -r requirements-optional.txt
```

No está en `requirements.txt`: arrastra CTranslate2 y descarga los pesos en
el primer uso. La app dispara esa descarga desde `POST /api/stt/warmup`
**antes** de empezar a grabar, para que la espera no quede después de que
hablaste. El botón muestra un ícono de descarga la primera vez.

No hace falta ffmpeg: el navegador graba en webm/opus y faster-whisper
decodifica con PyAV, que trae sus propios decoders.

### Modelos

| Modelo | Descarga | Cuándo |
|---|---|---|
| `tiny` | ~75 MB | Máquinas modestas, dictado corto |
| `base` (default) | ~145 MB | Equilibrio razonable en CPU |
| `small` | ~484 MB | Notablemente mejor, todavía viable en CPU |
| `large-v3-turbo` | ~1.6 GB | Con GPU |

### Web Speech API no es dictado local

Conviene decirlo explícito porque el nombre engaña: en Chrome el audio se
manda a servidores de Google para transcribirlo. Para una herramienta que se
presenta como local, es la excepción. El tooltip del micrófono lo aclara
cuando este motor está activo. Si querés dictado que no salga de la máquina,
usá `auto`, `whispercpp` o `whisper`.

### Configuración (`stt`)

| Clave | Default | Qué hace |
|---|---|---|
| `engine` | `auto` | `auto` \| `whispercpp` \| `whisper` \| `browser` |
| `language` | `es-AR` | Idioma de dictado (BCP-47); los motores locales lo heredan |
| `whisper_model` | `base` | Cuál de los modelos de arriba |
| `whisper_device` | `auto` | `auto` \| `cpu` \| `cuda` (faster-whisper) |
| `whisper_compute_type` | `int8` | `int8` en CPU, `float16` con GPU (faster-whisper) |
| `whisper_language` | `""` | Override de idioma; vacío = usa `language` (o el idioma de la app) |
| `translate_english` | `false` | Los motores locales transcriben directo a inglés |

### Contexto seguro

`getUserMedia` y la Web Speech API solo funcionan en `https://` o
`localhost`. `main.py` escucha en `127.0.0.1` por defecto, así que el acceso
es local y el micrófono funciona; si se expone a la red a propósito
(`GLYVEX_HOST=0.0.0.0`) y se entra desde otra máquina por IP
(`http://192.168.x.x:7981`), el micrófono va a aparecer deshabilitado. El
tooltip lo explica. No es un bug de la app: es una restricción del navegador.

---

## Texto a voz

| Motor | Necesita | Calidad |
|---|---|---|
| `kokoro` | modelo Kokoro (`.onnx` + `voices.bin`), se descarga en Ajustes → TTS | Neuronal, la más natural |
| `piper` | una voz Piper (`.onnx` + `.json`), se descarga en Ajustes → TTS | Neuronal, liviana |
| `sapi` | nada: las voces que trae Windows | Sintética clásica |

El motor `auto` (default) elige **Kokoro** si está listo, si no **Piper**,
si no **SAPI**. Con `piper` o `kokoro` fijados, la app usa siempre ese motor
y muestra un error claro si no está descargado.

En la app empaquetada la cadena neuronal (piper, kokoro-onnx, onnxruntime,
espeak-ng) viaja dentro del sidecar `glyvex-backend`; solo se descargan las
voces y el modelo.

- **Caché:** el WAV generado se guarda en memoria (LRU de 32 entradas, por
  motor + voz + velocidad + texto). Volver a escuchar una respuesta es
  instantáneo.
- **Voz SAPI:** si no se fija una, se busca es-AR → es-MX → es-ES → la voz por
  defecto del sistema (Windows no trae es-AR de fábrica).
- **Reproducción:** con el elemento `<audio>`; si el navegador bloquea el
  autoplay o falla la reproducción, se muestra un aviso en vez de quedar en
  silencio.

### Configuración (`tts`)

| Clave | Default | Qué hace |
|---|---|---|
| `enabled` | `true` | `false` oculta el botón "escuchar" |
| `engine` | `auto` | `auto` \| `kokoro` \| `piper` \| `sapi` |
| `voice` | `""` | Voz SAPI fija (vacío = cadena es-AR → es-MX → es-ES) |
| `rate` | `0` | Velocidad de -10 (lento) a 10 (rápido) |
| `piper_voice` | `es_AR-daniela-high` | Voz Piper |
| `kokoro_voice` | `ef_dora` | Voz Kokoro |