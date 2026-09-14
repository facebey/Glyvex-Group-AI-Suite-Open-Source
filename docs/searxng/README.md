# Búsqueda web

La tool `web_search` del chat soporta cuatro proveedores. El que se usa se
elige en `tools.search_provider` de `data/config.json`.

| Proveedor | Necesita | Cuándo conviene |
|---|---|---|
| `ddgs` (default) | nada | Funciona apenas instalás la app |
| `searxng` | instancia propia | Sin límites de tasa, sin exponer tu identidad |
| `brave` | API key | Volumen y calidad consistente |
| `tavily` | API key | Snippets largos, pensada para agentes |

No hay fallback automático entre proveedores. Si elegiste SearXNG por
privacidad, caer en silencio a DuckDuckGo sería lo contrario de lo que
pediste — cuando el proveedor falla, el error dice qué pasó y cómo cambiarlo.

El diagnóstico está en `GET /api/chat/tools/status`, que hace una búsqueda
real y descartable. Es lo que usa la UI para decidir si el toggle del globo
está disponible y qué explicar en el tooltip cuando no lo está.

## DuckDuckGo (default)

Nada que configurar. Viene con `pip install -r requirements.txt`.

Scrapea, así que con uso intensivo puede recibir límites de tasa temporales.
Si te pasa seguido, pasate a SearXNG o a un proveedor con API key.

Para acotar los resultados a una región, `tools.region`: `wt-wt` es global,
`ar-es` Argentina, `es-es` España.

## Brave o Tavily

Conseguí una key en el proveedor y ponela en una variable de entorno:

```
setx BRAVE_API_KEY "tu-key"     # Windows
export BRAVE_API_KEY="tu-key"   # Linux
```

También se puede dejar en `tools.brave_api_key` / `tools.tavily_api_key`,
pero eso la escribe en `config.json`. La variable de entorno tiene
precedencia.

Después, `tools.search_provider` a `brave` o `tavily`.

## SearXNG (opcional)

Requiere Docker, así que es una dependencia externa a la app: **no viene
incluida en el paquete de Glyvex**. Es la mejor opción si ya tenés una
instancia corriendo o si te importa que las búsquedas no salgan con tu
identidad.

1. Generá un secret y ponelo en `settings.yml`:

   ```
   openssl rand -hex 32
   ```

2. Levantá el contenedor:

   ```
   cd docs/searxng
   docker compose up -d
   ```

3. Verificá que el formato JSON quedó habilitado:

   ```
   curl "http://127.0.0.1:8888/search?q=test&format=json"
   ```

   Si devuelve JSON, está listo. Si devuelve **403 Forbidden** o HTML, falta
   `json` en `search.formats` de `settings.yml` — reiniciá el contenedor
   después de agregarlo.

4. En `data/config.json`, poné `tools.search_provider` en `searxng`.

En Windows, Docker Desktop necesita WSL2. Si no lo tenés y no querés
instalarlo, quedate con el proveedor por defecto.

## Resto de la configuración

| Clave | Default | Qué hace |
|---|---|---|
| `search_provider` | `ddgs` | Cuál de los cuatro se usa |
| `region` | `wt-wt` | Región de DuckDuckGo |
| `searxng_url` | `http://127.0.0.1:8888` | Dónde escucha SearXNG |
| `max_results` | `5` | Resultados por búsqueda |
| `fetch_max_chars` | `8000` | Tope de texto por página leída |
| `max_rounds` | `5` | Idas y vueltas modelo↔tool por turno |
| `allow_private_hosts` | `false` | Permite que `fetch_url` lea direcciones de red interna |

`allow_private_hosts` está apagado a propósito. La URL de `fetch_url` la
elige el modelo, no el usuario, y un resultado de búsqueda manipulado podría
intentar dirigirlo a un servicio de tu red. Activalo solo si querés que el
modelo pueda leer documentación interna.

## Requisitos del modelo

El tool-calling necesita que el chat template del modelo lo soporte. Con
llama-server hay que levantarlo con `--jinja`; sin eso el endpoint rechaza
los requests que traen `tools`. La app detecta el soporte leyendo
`chat_template_caps` de `/props` y deshabilita el toggle cuando no está.
