# Voz a texto

El micrófono del chat transcribe con uno de dos motores. El texto cae en el
textarea y queda editable: nunca se envía solo.

| Motor | Necesita | Privacidad | Dónde funciona |
|---|---|---|---|
| `browser` | nada | El audio sale a internet | Chrome, Edge, Safari |
| `whisper` | `faster-whisper` | Todo local | En cualquier lado |

Se elige con `stt.engine` en `data/config.json`, o con la variable de entorno
`STT_ENGINE`, que tiene precedencia.

## El default es `auto`, y no por comodidad

`auto` usa la Web Speech API cuando el navegador la expone y cae a Whisper
local cuando no. Hace falta que sea así porque **el WebView de Tauri no
expone la Web Speech API**: ni WebView2 en Windows ni WebKitGTK en Linux la
tienen habilitada. Es una función que Chrome implementa contra servicios de
Google, no algo que venga con el motor de renderizado.

O sea que el mismo código se comporta distinto según cómo lo corras:

- **Desarrollo, en Chrome** → motor del navegador, sin instalar nada.
- **Producción, dentro del .exe de Tauri** → Whisper local, obligatorio.

Por eso `faster-whisper` es opcional en el repo pero deja de serlo en el
bundle. Ver la sección de empaquetado más abajo.

## Web Speech API no es dictado local

Conviene decirlo explícito porque el nombre engaña: en Chrome el audio se
manda a servidores de Google para transcribirlo. Para una herramienta que se
presenta como local, es la excepción. El tooltip del micrófono lo aclara
cuando este motor está activo.

Si querés dictado que no salga de la máquina, poné `stt.engine` en
`whisper`.

## Whisper local

```
pip install -r requirements-optional.txt
```

El modelo se descarga en el primer uso. La app lo dispara desde
`POST /api/stt/warmup` **antes** de empezar a grabar, para que la espera no
quede después de que hablaste. El botón muestra un ícono de descarga la
primera vez.

| Modelo | Descarga | Cuándo |
|---|---|---|
| `tiny` | ~75 MB | Máquinas modestas, dictado corto |
| `base` (default) | ~145 MB | Equilibrio razonable en CPU |
| `small` | ~484 MB | Notablemente mejor, todavía viable en CPU |
| `large-v3-turbo` | ~1.6 GB | Con GPU |

Configuración en `data/config.json`, bajo `stt`:

| Clave | Default | Qué hace |
|---|---|---|
| `engine` | `auto` | `auto` \| `browser` \| `whisper` |
| `language` | `es-AR` | Idioma para la Web Speech API (BCP-47) |
| `whisper_model` | `base` | Cuál de los de arriba |
| `whisper_device` | `auto` | `auto` \| `cpu` \| `cuda` |
| `whisper_compute_type` | `int8` | `int8` en CPU, `float16` con GPU |
| `whisper_language` | `""` | Vacío = detección automática |

No hace falta tener ffmpeg instalado: el navegador graba en webm/opus y
faster-whisper decodifica con PyAV, que trae sus propios decoders.

## Contexto seguro

`getUserMedia` y la Web Speech API solo funcionan en `https://` o
`localhost`. `main.py` escucha en `127.0.0.1` por defecto, así que el acceso
es local y el micrófono funciona; si se expone a la red a propósito
(`GLYVEX_HOST=0.0.0.0`) y se entra desde otra máquina por IP
(`http://192.168.x.x:7981`), el micrófono va a aparecer deshabilitado. El
tooltip lo explica. No es un bug de la app: es una restricción del navegador.

## Empaquetado con Tauri

Whisper pasa de opcional a obligatorio, pero no conviene meter los pesos en
el instalador: son cientos de MB para una función que no todos usan. La
opción razonable es empaquetar `faster-whisper` (la librería, ~30 MB con
CTranslate2) y dejar que el modelo se descargue en el primer uso, que es
exactamente lo que ya hace `/api/stt/warmup`.

TTS (texto a voz) queda fuera de esta versión a propósito.
