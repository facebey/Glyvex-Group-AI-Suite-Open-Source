# Exportar métricas a InfluxDB (monitoreo externo)

La suite puede enviar a un InfluxDB externo todo lo que el Monitor mide, para
verlo con Grafana o cualquier otra herramienta. Es una opción **apagada por
defecto**: nada sale de la máquina si no la activás y le indicás un destino.

Docs relacionadas: [PRIVACY.es.md](PRIVACY.es.md) · dashboard de ejemplo:
[`docs/examples/grafana-suite-example.json`](examples/grafana-suite-example.json)

## Qué se exporta y qué no

**Se exporta:**

- Las métricas de hardware (GPU, CPU, RAM) y las de los `llama-server` en
  ejecución (velocidades, contexto, requests, caché, contadores del motor).
- Las **ventanas de 5 s con promedio, mínimo y máximo**, las mismas que guarda
  el histórico del Monitor.
- **Todas** las métricas, incluidas las que ocultaste en pantalla (Config →
  ocultas en el Monitor). Lo que se ve en la app y lo que se exporta son
  decisiones distintas.

**No se exporta:**

- La contabilidad propia de la app: el "Costo de sesión" y el desglose
  chat/externo del "Total de por vida" del Monitor viven solo en la app.
  Los contadores `*_total` del motor que sí viajan incluyen a **todos** los
  clientes que toquen ese `llama-server` (la app y otros).

Ninguna conversación ni contenido de prompt sale de la máquina: la exportación
lleva solo números. Las series del LLM viajan con el **nombre del modelo** como
etiqueta (`model=...`); el hardware no lleva identificadores de la máquina.

## Prerrequisitos: un InfluxDB disponible

El exporter escribe con InfluxDB line protocol y sirve para:

- **InfluxDB 1.x** → endpoint `/write` (modo `v1` en Config).
- **InfluxDB 2.x / 3.x** → endpoint `/api/v2/write` (modo `v2` en Config).
- QuestDB o VictoriaMetrics también aceptan este line protocol en sus endpoints
  compatibles con Influx.

Para una instancia local, InfluxDB 2 con todo creado por adelantado es lo más
rápido (Docker):

```powershell
docker run -d --name influxdb -p 8086:8086 `
  -e DOCKER_INFLUXDB_INIT_MODE=setup `
  -e DOCKER_INFLUXDB_INIT_USERNAME=glyvex `
  -e DOCKER_INFLUXDB_INIT_PASSWORD="<tu password>" `
  -e DOCKER_INFLUXDB_INIT_ORG=glyvex `
  -e DOCKER_INFLUXDB_INIT_BUCKET=glyvex `
  influxdb:2
```

El exporter **no crea buckets**: en v2/v3 el bucket (p. ej. `glyvex`) y un token
con permiso de escritura tenés que crearlos primero en InfluxDB. InfluxDB 1.x
suele crear la base automáticamente en la primera escritura si el servidor no
tiene el autocrateado desactivado.

## Configurar en la app

Config → **Exportar métricas** → InfluxDB. Campos del formulario:

| Campo | Default | Para qué sirve |
|---|---|---|
| Activar | apagado | Enciende/apaga el envío. Se aplica al guardar. |
| Versión | `v2` | `v2` para InfluxDB 2.x/3.x (`/api/v2/write`); `v1` para 1.x (`/write`). |
| URL | `http://127.0.0.1:8086` | Dónde está tu InfluxDB. Tiene que empezar con `http://` o `https://`. |
| Organización / Bucket | vacío / `glyvex` | v2/v3. El bucket debe existir. |
| Token | vacío | Token de API v2/v3 con permiso de escritura. **La variable de entorno `GLYVEX_INFLUX_TOKEN` tiene prioridad** sobre lo escrito acá. Escribirlo en la UI lo guarda en `data/config.json` (archivo no versionado). |
| Base de datos / Usuario / Contraseña | `glyvex` / vacío / vacío | Solo v1. |
| Intervalo de envío (s) | `10` | Cada cuánto vacía el backend la cola de líneas pendientes (mínimo 1 s). No cambia la resolución: las ventanas son siempre de 5 s. |
| Prefijo de measurement | `glyvex` | Se escribe en `<prefijo>_hw` y `<prefijo>_llm`. |

Antes de guardar podés usar **"Probar conexión"**: el backend intenta escribir
una línea de prueba (`<prefijo>_test,source=glyvex ok=1i`) con los valores del
formulario tal como están, sin necesitar que hayas guardado. "Escritura
aceptada" significa que URL, org/bucket y credenciales son correctos. Que quede
un measurement `<prefijo>_test` en el bucket es esperado; lo podés borrar.

La propia UI lo recuerda: **si activás InfluxDB con el histórico del Monitor
apagado, reiniciá el backend** para que empiece a medir (el poller que arma las
ventanas se levanta al arrancar el backend).

Alternativa manual: la sección `exports` de `data/config.json` (el modelo está
en [`data/config.example.json`](../data/config.example.json)):

```json
"exports": {
  "prometheus_enabled": false,
  "prometheus_token": "",
  "influx": {
    "enabled": false,
    "version": "v2",
    "url": "http://127.0.0.1:8086",
    "token": "",
    "org": "",
    "bucket": "glyvex",
    "database": "glyvex",
    "username": "",
    "password": "",
    "interval_s": 10,
    "measurement_prefix": "glyvex"
  }
}
```

## Qué pasa si el destino no responde

El exporter nunca interrumpe la app:

- Las líneas se acumulan en una cola acotada (50 000 líneas). Si se llena, se
  descartan las más viejas y se avisa en el log del backend una sola vez.
- Si el destino rechaza o no contesta, reintenta con espera creciente
  (`interval_s × 2^fallos consecutivos`, tope 5 minutos). Al recuperarse,
  vacía lo acumulado en lotes de 5 000 líneas sin esperar entre lotes.
- Avisa en el log al primer fallo de una racha ("no se pudo escribir en
  InfluxDB…") y cuando vuelve a aceptar escrituras.
- Si apagás la exportación desde Config, lo pendiente en la cola se descarta.
  Al apagar el backend se intenta un último envío (hasta 5 s).

## Modelo de datos en InfluxDB

Una línea por (serie, ventana de 5 s), con marcas de tiempo alineadas al reloj:

```
glyvex_hw,series=gpu.0.temp_c,unit=°C avg=65.2,min=64,max=68 1789525000
glyvex_llm,series=llm.tg_tps,process_id=1234,model=Qwen3-8B,unit=t/s avg=41.3,min=40.1,max=42.8 1789525000
```

- **Measurement**: `<prefijo>_hw` (hardware) o `<prefijo>_llm` (modelos).
- **Tags**: `series` (el nombre, en las tablas de abajo), `unit` (si la serie
  tiene unidad), y `process_id` + `model` solo en las series llm.
- **Fields**: `avg`, `min`, `max` de la ventana. Usá `max` para lo peor
  (temperatura, pico de contexto) y `min` para la lectura conservadora.
- **Timestamp**: segundos Unix.
- Un sensor no disponible **no genera punto**: en Grafana aparece como hueco,
  no como cero.

### Series de hardware (`<prefijo>_hw`)

| Serie | Unidad | Qué es |
|---|---|---|
| `gpu.<N>.vram_used_mb` | MB | VRAM en uso de la GPU N |
| `gpu.<N>.vram_pct` | % | VRAM ocupada de la GPU N |
| `gpu.<N>.util_pct` | % | Uso de núcleos de la GPU N |
| `gpu.<N>.mem_util_pct` | % | Uso de ancho de banda de memoria de la GPU N |
| `gpu.<N>.temp_c` | °C | Temperatura de la GPU N |
| `gpu.<N>.power_w` | W | Consumo de la GPU N |
| `cpu.total_pct` | % | Uso total de CPU |
| `cpu.freq_mhz` | MHz | Frecuencia actual de CPU |
| `cpu.temp_c` | °C | Temperatura de CPU (si el sensor existe) |
| `ram.used_gb` | GB | RAM del sistema en uso |
| `ram.pct` | % | RAM ocupada |
| `swap.used_gb` | GB | Swap en uso |

### Series de LLM (`<prefijo>_llm`)

Todas las de esta tabla llevan tags `process_id` y `model`. Son las mismas que
guarda el histórico del Monitor. (Las de chat de la app, que no los llevan,
están más abajo.)

| Serie | Unidad | Qué es |
|---|---|---|
| `llm.tg_tps` | t/s | Velocidad de generación actual (sin valor si el motor está ocioso) |
| `llm.pp_tps` | t/s | Velocidad de procesamiento de prompt |
| `llm.throughput_tps` | t/s | Tokens generados por segundo de reloj |
| `llm.ctx_used` | tokens | Contexto en uso del proceso |
| `llm.ctx_pct` | % | Contexto ocupado (del tamaño configurado) |
| `llm.ctx_peak` | tokens | Pico de contexto observado |
| `llm.requests_processing` | — | Requests en curso |
| `llm.requests_deferred` | — | Requests esperando un slot |
| `llm.cache_hit_pct` | % | Reutilización del caché de prompt en la ventana |
| `llm.spec_accept_pct` | % | Aceptación de tokens especulativos en la ventana |
| `llm.cache_hit_pct_total` | % | Caché de prompt, acumulado de la corrida |
| `llm.spec_accept_pct_total` | % | Speculative decoding, acumulado de la corrida |
| `llm.prompt_tokens_total` | tokens | Tokens de prompt procesados desde que arrancó el proceso |
| `llm.prompt_tokens_cached_total` | tokens | De esos, servidos desde el caché |
| `llm.tokens_predicted_total` | tokens | Tokens generados desde que arrancó el proceso |

### Contadores de chat de la app (`llm.app_*`)

Tres series más en la misma measurement **sin tags `process_id` ni `model`**:
son contadores del chat de la propia app, de **por vida** (el `reset` de la
sesión del Monitor no los toca), en formato llama.cpp:

| Serie | Unidad | Qué es |
|---|---|---|
| `llm.app_prompt_tokens_total` | tokens | Prompt del chat de la app, **sin** contar el caché |
| `llm.app_prompt_tokens_cached_total` | tokens | De esos, servidos desde el caché |
| `llm.app_tokens_predicted_total` | tokens | Tokens generados por el chat de la app |

Se escriben con `avg = min = max` igual al total corriente y **solo cuando el
valor cambia**, así que el último punto publicado es siempre el total actual.
Con ellas un dashboard puede separar el uso de la app del de otros clientes del
mismo modelo: `externo = totales del motor − app`. Es la misma conversión
chat-vs-externo que muestra el "Total de por vida" del Monitor.

## Verlo en Grafana

El repo incluye un dashboard de ejemplo listo para importar:
[`docs/examples/grafana-suite-example.json`](examples/grafana-suite-example.json)
(velocidades, contexto, caché, contadores por proceso, GPU, CPU y RAM). En
Grafana: **Dashboards → Import** → pegá el JSON o cargá el archivo. Los paneles
vienen sin datasource asignado: Grafana te pide elegir tu datasource de InfluxDB
al importar.

Las consultas usan los valores por defecto (bucket `glyvex`, measurements
`glyvex_hw` / `glyvex_llm`). Si cambiaste el bucket o el prefijo, reemplazalos
en cada panel (Editar panel → Query).

Consulta Flux típica (promedio de una serie):

```flux
from(bucket: "glyvex")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "glyvex_llm")
  |> filter(fn: (r) => r._field == "avg")
  |> filter(fn: (r) => r.series =~ /^llm\.tg_tps$/)
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> yield(name: "mean")
```

### Unidades: convertir MB/GB a bytes en la consulta

El tag `unit` indica la unidad en la que viene cada serie: `gpu.<N>.vram_used_mb`
llega en MB y `ram.used_gb` / `swap.used_gb` en GB. Si el panel usa la unidad
`bytes` de Grafana, convertí los valores a bytes **dentro de la propia consulta**
(× 1048576 para MB, × 1073741824 para GB), terminando la cadena con el `map`:

```flux
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> map(fn: (r) => ({_time: r._time, _value: r._value * 1048576.0}))
```

Gotcha: **no intercales `yield()` antes de un `map()`**. Flux devuelve entonces
dos tablas (la cruda con tags `series`/`unit` y la convertida) y el panel muestra
el valor dos veces con unidades distintas ("51.8 B" y "51.8 GiB" del mismo dato).
Sin `map()`, un `yield()` al final de la cadena es opcional: Grafana recibe la
última tabla de todos modos.

Para inspeccionar qué está llegando a un measurement:

```flux
from(bucket: "glyvex")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "glyvex_llm" and r._field == "avg")
```

### Contadores absolutos (`*_total`)

`llm.prompt_tokens_total`, `llm.prompt_tokens_cached_total` y
`llm.tokens_predicted_total` son acumulativos **desde que arrancó el
`llama-server`**: no son un total de la máquina ni de la app. Para ver el
consumo dentro del rango del panel, calculá el delta por ventana:

```flux
from(bucket: "glyvex")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "glyvex_llm")
  |> filter(fn: (r) => r._field == "max")
  |> filter(fn: (r) => r.series == "llm.tokens_predicted_total")
  |> aggregateWindow(every: v.windowPeriod, fn: max, createEmpty: false)
  |> difference()
```

Cuando el proceso reinicia, el contador vuelve a cero y `difference()` da un
punto negativo en ese instante: filtralo o ignoralo.

### Total de por vida: chat de la app vs otros clientes

Para el total acumulado (no del período), el truco es `range(start: 0)` con el
mismo patrón de delta: cubre todos los procesos y absorbe los reinicios, desde
el primer punto exportado a Influx:

```flux
from(bucket: "glyvex")
  |> range(start: 0)
  |> filter(fn: (r) => r._measurement == "glyvex_llm")
  |> filter(fn: (r) => r._field == "avg")
  |> filter(fn: (r) => r.series == "llm.tokens_predicted_total")
  |> aggregateWindow(every: v.windowPeriod, fn: last, createEmpty: false)
  |> difference()
  |> filter(fn: (r) => r._value >= 0.0)
  |> group(columns: [])
  |> sum(column: "_value")
```

El total del chat de la app es más simple: sus puntos son el total corriente,
así que alcanza con el último. Y el externo se arma restando en Flux (sumar
`-1.0 × app` al motor con `union`, con piso 0 para que un histórico del motor
más corto que el de la app no dé negativo):

```flux
e = /* consulta del motor de arriba */
  |> map(fn: (r) => ({v: r._value}))
c = from(bucket: "glyvex")
  |> range(start: 0)
  |> filter(fn: (r) => r._measurement == "glyvex_llm")
  |> filter(fn: (r) => r._field == "avg")
  |> filter(fn: (r) => r.series == "llm.app_tokens_predicted_total")
  |> last()
  |> map(fn: (r) => ({v: r._value * -1.0}))
union(tables: [e, c])
  |> group()
  |> sum(column: "v")
  |> map(fn: (r) => ({_value: if r.v > 0.0 then r.v else 0.0}))
```

Cobertura: el histórico de Influx empieza cuando activaste la exportación, y
las `llm.app_*` existen desde la versión de la suite que las publica. Antes de
eso el desglose sigue viéndose solo en el Monitor de la app, que sí tiene el
total de por vida completo.

## Exportación Prometheus (pull), en breve

En la misma sección de Config hay una alternativa *pull*: activarla expone
`GET http://127.0.0.1:<puerto de la app>/api/metrics/prometheus` en formato de
exposición Prometheus con la última lectura de hardware y de cada `llama-server`
(apagada, responde 404). Con "Token (opcional)" el endpoint exige
`Authorization: Bearer <token>` — útil si el backend queda expuesto en la red.
Las líneas `# HELP` de la salida describen cada métrica. Junto a los
contadores por proceso `glyvex_llm_*` también publica `glyvex_app_*`, los
contadores de chat de por vida de la propia app (sin labels). Ejemplo para
Telegraf (la UI lo muestra con botón "Copiar"):

```toml
[[inputs.prometheus]]
  urls = ["http://127.0.0.1:7981/api/metrics/prometheus"]
```

## Troubleshooting

| Síntoma | Causa probable y arreglo |
|---|---|
| El bucket no recibe datos | Activaste Influx con el histórico apagado sin reiniciar el backend (ver la nota más arriba). Revisá el log del backend: "no se pudo escribir en InfluxDB…" indica que el destino rechaza. |
| HTTP 401 / 403 | Token u organización incorrectos. Recordá que `GLYVEX_INFLUX_TOKEN` (entorno) **pisa** el token guardado en Config. |
| HTTP 404 | El bucket no existe (crealo en Influx) o la URL Versión no coincide: `v2` apunta a `/api/v2/write`, `v1` a `/write`. |
| "no se pudo contactar al backend" al probar conexión | El backend de la suite no está corriendo en ese puerto. |
| Aparece `<prefijo>_test` en el bucket | Es de "Probar conexión". Es seguro borrarlo. |
| Cambiaste prefijo/bucket después de armar el dashboard | Actualizá las consultas de los paneles (Editar panel → Query). |
| Huecos en los gráficos | Esperado: sensor no disponible o motor ocioso (p. ej. `llm.tg_tps` no tiene valor sin generación en curso). No se rellenan con ceros. |
