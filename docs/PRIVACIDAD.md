# Privacidad y telemetría

Glyvex-AI-Suite está **100% local por diseño**. Esta página dice, sin
eufemismos, qué sale de tu máquina y qué no.

## Lo que NO hacemos

- **Sin telemetría saliente.** La app no envía métricas de uso, analytics,
  identificadores ni "llamadas a casa" a ningún servidor. No existe ningún
  endpoint de *phone home*.
- **Sin nube.** Conversaciones, inventario de modelos, benchmarks, métricas y
  configuración quedan en tu disco.
- **Sin cuentas ni licencias.** Nada pide usuario, token de autenticación
  propia ni activación.

## Dónde vive cada cosa (todo local)

| Dato | Dónde |
|---|---|
| Conversaciones y mensajes | `data/glyvex.db` (SQLite) |
| Inventario de modelos | `data/models.json` |
| Runs y resultados de benchmark | `data/glyvex.db` + reportes HTML en `data/benchmarks/` |
| Métricas de HW y del LLM server | `data/metrics.db` |
| Adjuntos | `data/attachments/` |
| Configuración | `data/config.json` (no versionado) |

## Lo que SÍ sale a red (y por qué)

La única salida a red es cuando **el modelo invoca una herramienta** del chat,
y solo con los proveedores que tenés activos:

- **Búsqueda web** — por defecto DuckDuckGo (sin API key, sin Docker).
  Opcionales: SearXNG (local), Brave y Tavily (requieren API key).
- **Fetch de URLs** — descarga el contenido de la URL que el modelo pida.
  Bloquea hosts privados por defecto (`tools.allow_private_hosts`).

Si no usás esas herramientas, no hay salida a red. Ver
[`searxng/README.md`](searxng/README.md).

### Aclaración sobre `/metrics`

El endpoint Prometheus `/metrics` que aparece en la documentación **no es
telemetría saliente**: es el que expone cada `llama-server` **localmente**, y lo
lee el propio Monitor de la app para mostrarte los vitales del proceso
(t/s, contexto, spec decoding). No se envía a ningún lado.

## Excepción: runtime embebido (llama.cpp)

En **Windows** la suite puede **descargar** su propia build probada de
[llama.cpp](https://github.com/ggml-org/llama.cpp) (el runtime de inferencia,
~550 MB) para que no tengas que instalar nada aparte. Es la única excepción a
"todo es local", y viene con garantías:

- **Explicita** — la app no descarga nada por su cuenta; la descarga la
  iniciás vos con el botón **Descargar runtime** (onboarding o
  Config → **Runtime**).
- **Verificada por sha256** — cada build de la lista de "versiones probadas"
  declara su URL + sha256; si el archivo descargado no casa, se **descarta**
  (se borra el parcial) y el runtime queda en `error`. No verificable = no se
  usa.
- **Solo fuentes fijas** — el canal propio de la suite (asset del release) y,
  como fallback, la build oficial de `ggml-org/llama.cpp`. Ningún endpoint
  configurable por el usuario.
- **Sin telemetría** — la descarga no envía nada de tu máquina: es un `GET` al
  release.

Si no tocás **Descargar runtime**, no hay ninguna descarga: la app funciona
igual con el modo experto (`binary_path`) u Ollama/LM Studio.

## Configuración y red

- **Bind por defecto `127.0.0.1`** — la app escucha solo en la máquina local.
  Exponerla a la red es opt-in (`GLYVEX_HOST=0.0.0.0`) y en ese caso **requiere
  autenticación** que vos tengas que implementar; no viene incluida.
- **CORS restringido** a los orígenes que listes en `GLYVEX_CORS_ORIGINS`.
- **API keys locales** — `BRAVE_API_KEY`/`TAVILY_API_KEY` se leen de las
  variables de entorno o de `data/config.json`, que no se commitea.
- **`data/config.json` no versionado** — la plantilla pública es
  `data/config.example.json`.

## Cómo verificarlo vos

- Corré la app sin internet: todo funciona menos la búsqueda/fetch de las
  herramientas del modelo.
- Revisá `backend/tools.py` (las únicas llamadas HTTP salientes) y
  `backend/main.py` (no hay pollers de envío a servidores externos).
