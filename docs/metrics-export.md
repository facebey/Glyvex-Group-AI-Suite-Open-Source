# Exporting metrics to InfluxDB (external monitoring)

The suite can send everything the Monitor measures to an external InfluxDB so
you can view it in Grafana or any other tool. This option is **off by
default**: nothing leaves the machine unless you enable it and set a destination.

Related docs: [PRIVACY.md](PRIVACY.md) · example dashboard:
[`docs/examples/grafana-suite-example.json`](examples/grafana-suite-example.json)

## What is exported and what is not

**Exported:**

- Hardware metrics (GPU, CPU, RAM) and the metrics of running `llama-server`
  instances (speeds, context, requests, cache, engine counters).
- The **same 5 s windows (average, min, max)** the Monitor history stores.
- **All** metrics, including the ones you hid on screen (Config → hidden in the
  Monitor). What you see in the app and what is exported are separate decisions.

**Not exported:**

- The app's own accounting: the "Session cost" and the chat/external split of
  the Monitor's "Lifetime total" live only in the app. The engine `*_total`
  counters that do travel include **all** clients hitting that `llama-server`
  (the app and others).

No conversation or prompt content ever leaves the machine: the export carries
numbers only. LLM series travel with the **model name** as a tag (`model=...`);
hardware carries no machine identifiers.

## Prerequisites: an available InfluxDB

The exporter writes InfluxDB line protocol and works with:

- **InfluxDB 1.x** → `/write` endpoint (`v1` mode in Config).
- **InfluxDB 2.x / 3.x** → `/api/v2/write` endpoint (`v2` mode in Config).
- QuestDB or VictoriaMetrics also accept this line protocol on their
  Influx-compatible endpoints.

For a local instance, InfluxDB 2 set up in advance is the quickest (Docker):

```powershell
docker run -d --name influxdb -p 8086:8086 `
  -e DOCKER_INFLUXDB_INIT_MODE=setup `
  -e DOCKER_INFLUXDB_INIT_USERNAME=glyvex `
  -e DOCKER_INFLUXDB_INIT_PASSWORD="<your password>" `
  -e DOCKER_INFLUXDB_INIT_ORG=glyvex `
  -e DOCKER_INFLUXDB_INIT_BUCKET=glyvex `
  influxdb:2
```

The exporter **does not create buckets**: on v2/v3 you must first create the
bucket (e.g. `glyvex`) and an API token with write permissions in InfluxDB.
InfluxDB 1.x usually auto-creates the database on first write unless the server
has auto-create disabled.

## Configure it in the app

Config → **Export metrics** → InfluxDB. Form fields:

| Field | Default | What it does |
|---|---|---|
| Enable | off | Turns sending on/off. Applied when saving. |
| Version | `v2` | `v2` for InfluxDB 2.x/3.x (`/api/v2/write`); `v1` for 1.x (`/write`). |
| URL | `http://127.0.0.1:8086` | Where your InfluxDB lives. Must start with `http://` or `https://`. |
| Organization / Bucket | empty / `glyvex` | v2/v3. The bucket must exist. |
| Token | empty | v2/v3 API token with write permissions. **The `GLYVEX_INFLUX_TOKEN` environment variable takes priority** over what you type here. Typing it in the UI stores it in `data/config.json` (not versioned). |
| Database / Username / Password | `glyvex` / empty / empty | v1 only. |
| Send interval (s) | `10` | How often the backend flushes the pending line queue (minimum 1 s). It does not change resolution: windows are always 5 s. |
| Measurement prefix | `glyvex` | Writes go to `<prefix>_hw` and `<prefix>_llm`. |

Before saving you can use **"Test connection"**: the backend attempts to write a
test line (`<prefix>_test,source=glyvex ok=1i`) using the form values as they
are, without needing a save. "Write accepted" means the URL, org/bucket and
credentials are correct. Leaving a `<prefix>_test` measurement in the bucket is
expected; you can delete it.

The UI itself reminds you: **if you enable InfluxDB with the Monitor history
turned off, restart the backend** so it starts measuring (the poller that builds
the windows starts at backend startup).

Manual alternative: the `exports` section of `data/config.json` (template in
[`data/config.example.json`](../data/config.example.json)):

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

## What happens if the destination is down

The exporter never interrupts the app:

- Lines accumulate in a bounded queue (50,000 lines). When it fills, the oldest
  lines are dropped and the backend log warns once.
- If the destination rejects or does not answer, it retries with growing waits
  (`interval_s × 2^consecutive_failures`, capped at 5 minutes). Once it
  recovers, it drains the backlog in batches of 5,000 lines without waiting
  between batches.
- It logs a warning at the first failure of a failing streak ("no se pudo
  escribir en InfluxDB…") and an info line when writes are accepted again.
- If you turn export off in Config, the pending queue is discarded. On backend
  shutdown a final send is attempted (up to 5 s).

## Data model in InfluxDB

One line per (series, 5 s window), with timestamps aligned to the clock:

```
glyvex_hw,series=gpu.0.temp_c,unit=°C avg=65.2,min=64,max=68 1789525000
glyvex_llm,series=llm.tg_tps,process_id=1234,model=Qwen3-8B,unit=t/s avg=41.3,min=40.1,max=42.8 1789525000
```

- **Measurement**: `<prefix>_hw` (hardware) or `<prefix>_llm` (models).
- **Tags**: `series` (the name, in the tables below), `unit` (when the series
  has one), and `process_id` + `model` on llm series only.
- **Fields**: the window's `avg`, `min`, `max`. Use `max` for the worst case
  (temperature, context peak) and `min` for the conservative reading.
- **Timestamp**: Unix seconds.
- An unavailable sensor **produces no point**: in Grafana it appears as a gap,
  not a zero.

### Hardware series (`<prefix>_hw`)

| Series | Unit | What it is |
|---|---|---|
| `gpu.<N>.vram_used_mb` | MB | VRAM in use on GPU N |
| `gpu.<N>.vram_pct` | % | VRAM used on GPU N |
| `gpu.<N>.util_pct` | % | GPU N core utilization |
| `gpu.<N>.mem_util_pct` | % | GPU N memory bandwidth utilization |
| `gpu.<N>.temp_c` | °C | GPU N temperature |
| `gpu.<N>.power_w` | W | GPU N power draw |
| `cpu.total_pct` | % | Total CPU utilization |
| `cpu.freq_mhz` | MHz | Current CPU frequency |
| `cpu.temp_c` | °C | CPU temperature (when the sensor exists) |
| `ram.used_gb` | GB | System RAM in use |
| `ram.pct` | % | RAM used |
| `swap.used_gb` | GB | Swap in use |

### LLM series (`<prefix>_llm`)

All series in this table carry `process_id` and `model` tags. These are the
same series the Monitor history stores. (App chat counters, which carry none,
are documented below.)

| Series | Unit | What it is |
|---|---|---|
| `llm.tg_tps` | t/s | Current generation speed (no value when the engine is idle) |
| `llm.pp_tps` | t/s | Prompt processing speed |
| `llm.throughput_tps` | t/s | Tokens generated per wall-clock second |
| `llm.ctx_used` | tokens | Context in use by the process |
| `llm.ctx_pct` | % | Context used (of the configured size) |
| `llm.ctx_peak` | tokens | Observed context peak |
| `llm.requests_processing` | — | Requests in flight |
| `llm.requests_deferred` | — | Requests waiting for a slot |
| `llm.cache_hit_pct` | % | Prompt cache reuse within the window |
| `llm.spec_accept_pct` | % | Speculative token acceptance within the window |
| `llm.cache_hit_pct_total` | % | Prompt cache, run-wide cumulative |
| `llm.spec_accept_pct_total` | % | Speculative decoding, run-wide cumulative |
| `llm.prompt_tokens_total` | tokens | Prompt tokens processed since the process started |
| `llm.prompt_tokens_cached_total` | tokens | Of those, served from cache |
| `llm.tokens_predicted_total` | tokens | Tokens generated since the process started |

### App chat counters (`llm.app_*`)

Three more series in the same measurement, **without `process_id` or `model`
tags**: they are the app's own chat counters, **lifetime** totals (the Monitor's
session reset never touches them), in llama.cpp format:

| Series | Unit | What it is |
|---|---|---|
| `llm.app_prompt_tokens_total` | tokens | App chat prompt tokens, **excluding** cache |
| `llm.app_prompt_tokens_cached_total` | tokens | Of those, served from cache |
| `llm.app_tokens_predicted_total` | tokens | Tokens generated by the app chat |

They are written with `avg = min = max` equal to the current total, and **only
when the value changes**, so the last published point is always the current
total. With them a dashboard can separate the app's usage from that of other
clients of the same model: `external = engine totals − app`. It is the same
chat-vs-external conversion the Monitor's "Lifetime total" view shows.

## Viewing it in Grafana

The repo ships a ready-to-import example dashboard:
[`docs/examples/grafana-suite-example.json`](examples/grafana-suite-example.json)
(speeds, context, cache, per-process counters, GPU, CPU and RAM). In Grafana:
**Dashboards → Import** → paste the JSON or upload the file. Panels come with no
datasource assigned: Grafana asks you to pick your InfluxDB datasource at import
time.

The dashboard ships with an **InfluxDB bucket** dropdown at the top: every query
reads from the `v.bucket` variable, so you just pick the bucket your export
writes to (the exporter default is `glyvex`; any other bucket works). The
dropdown is filled with `buckets()`, i.e. it lists the real buckets on the
server. Measurements are fixed in the JSON (`glyvex_hw` / `glyvex_llm`, the
prefix defaults): if you changed `measurement_prefix`, replace them in each
panel (Edit panel → Query). If the dropdown shows up empty, assign the datasource
to the variable in **Dashboard settings → Variables** (the JSON leaves the
datasource unassigned on purpose, just like the panels).

Typical Flux query (average of a series):

```flux
from(bucket: "glyvex")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "glyvex_llm")
  |> filter(fn: (r) => r._field == "avg")
  |> filter(fn: (r) => r.series =~ /^llm\.tg_tps$/)
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> yield(name: "mean")
```

### Units: convert MB/GB to bytes in the query

The `unit` tag states the unit each series arrives in: `gpu.<N>.vram_used_mb`
comes in MB, `ram.used_gb` / `swap.used_gb` in GB. If a panel uses Grafana's
`bytes` unit, convert the values to bytes **inside the query itself**
(× 1048576 for MB, × 1073741824 for GB), ending the chain with the `map`:

```flux
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> map(fn: (r) => ({_time: r._time, _value: r._value * 1048576.0}))
```

Gotcha: **never put `yield()` before a `map()`**. Flux then returns two tables
(the raw one with `series`/`unit` tags and the converted one) and the panel
shows the same value twice with different units ("51.8 B" and "51.8 GiB").
Without a `map()`, a trailing `yield()` is optional: Grafana receives the last
table either way.

To inspect what is arriving to a measurement:

```flux
from(bucket: "glyvex")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "glyvex_llm" and r._field == "avg")
```

### Absolute counters (`*_total`)

`llm.prompt_tokens_total`, `llm.prompt_tokens_cached_total` and
`llm.tokens_predicted_total` are cumulative **since the `llama-server` process
started**: they are not a machine-wide or app-wide total. To see consumption
within the panel's time range, compute the per-window delta:

```flux
from(bucket: "glyvex")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "glyvex_llm")
  |> filter(fn: (r) => r._field == "max")
  |> filter(fn: (r) => r.series == "llm.tokens_predicted_total")
  |> aggregateWindow(every: v.windowPeriod, fn: max, createEmpty: false)
  |> difference()
```

When the process restarts the counter goes back to zero and `difference()`
yields a negative point at that moment: filter it out or ignore it.

### Lifetime total: app chat vs other clients

For the all-time total (not the time range's), keep the same delta pattern but
with `range(start: 0)`: it covers every process, absorbs restarts, and reaches
back to the first point exported to Influx:

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

The app chat total is simpler: its points are the current total, so taking the
last one is enough. External is then built by subtracting in Flux (union the
engine total with `-1.0 × app` and sum, floored at 0 so a shorter engine
history than the app's never goes negative):

```flux
e = /* the engine query above */
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

Coverage: the Influx history starts when export was enabled, and `llm.app_*`
exists only from the suite version that publishes it. Before that the split is
still visible in the app's Monitor, which does have the full lifetime total.

## Prometheus export (pull), in brief

The same Config section offers a *pull* alternative: enabling it exposes
`GET http://127.0.0.1:<app port>/api/metrics/prometheus` in Prometheus
exposition format with the latest hardware reading and each running
`llama-server` (disabled, it answers 404). With "Token (optional)" set, the
endpoint requires `Authorization: Bearer <token>` — useful if the backend is
exposed on the network. The `# HELP` lines in the output describe each metric.
Alongside the per-process `glyvex_llm_*` counters it also publishes
`glyvex_app_*`, the app's own lifetime chat counters (no labels). Telegraf
example (the UI shows it with a "Copy" button):

```toml
[[inputs.prometheus]]
  urls = ["http://127.0.0.1:7981/api/metrics/prometheus"]
```

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| The bucket receives no data | You enabled Influx with the history off without restarting the backend (see the note above). Check the backend log: "no se pudo escribir en InfluxDB…" means the destination is rejecting. |
| HTTP 401 / 403 | Wrong token or organization. Remember `GLYVEX_INFLUX_TOKEN` (environment) **overrides** the token stored in Config. |
| HTTP 404 | The bucket does not exist (create it in Influx) or URL/version mismatch: `v2` posts to `/api/v2/write`, `v1` to `/write`. |
| "no se pudo contactar al backend" when testing | The suite backend is not running on that port. |
| `<prefix>_test` appears in the bucket | It comes from "Test connection". Safe to delete. |
| You changed prefix/bucket after building the dashboard | Change the selection in the **InfluxDB bucket** dropdown; the prefix is fixed per panel (Edit panel → Query). |
| The bucket does not show up in the dropdown | The dropdown queries the datasource assigned to the variable: if your bucket lives on another InfluxDB server, that datasource points to the wrong place (or the bucket has not been created yet). |
| Gaps in the graphs | Expected: unavailable sensor or idle engine (e.g. `llm.tg_tps` has no value without generation in progress). They are not filled with zeros. |
