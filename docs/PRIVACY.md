# Privacy and telemetry

> **Language:** [Español](PRIVACY.es.md)

Glyvex-AI-Suite is **100% local by design**. This page tells you, without
euphemisms, what leaves your machine and what does not.

## What we do NOT do

- **No outbound telemetry.** The app does not send usage metrics, analytics,
  identifiers or "phone home" calls to any server. There is no *phone home*
  endpoint.
- **No cloud.** Conversations, model inventory, benchmarks, metrics and
  configuration stay on your disk.
- **No accounts or licenses.** Nothing asks for a username, its own
  authentication token or activation.

## Where each thing lives (all local)

| Data | Where |
|---|---|
| Conversations and messages | `data/glyvex.db` (SQLite) |
| Model inventory | `data/models.json` |
| Benchmark runs and results | `data/glyvex.db` + HTML reports in `data/benchmarks/` |
| HW and LLM server metrics | `data/metrics.db` |
| Attachments | `data/attachments/` |
| Configuration | `data/config.json` (not versioned) |

## What DOES go out to the network (and why)

The only outbound network traffic is when **the model invokes a chat tool**,
and only with the providers you have active:

- **Web search** — DuckDuckGo by default (no API key, no Docker).
  Optional: SearXNG (local), Brave and Tavily (require an API key).
- **URL fetch** — downloads the content of the URL the model asks for.
  Blocks private hosts by default (`tools.allow_private_hosts`).

If you do not use those tools, there is no outbound network traffic. See
[`searxng/README.md`](searxng/README.md).

### Clarification about `/metrics`

The Prometheus endpoint `/metrics` that appears in the documentation **is not
outbound telemetry**: it is the one each `llama-server` exposes
**locally**, and the app's own Monitor reads it to show you the process
vitals (t/s, context, spec decoding). It is not sent anywhere.

## Exception: embedded runtime (llama.cpp)

On **Windows** the suite can **download** its own tested build of
[llama.cpp](https://github.com/ggml-org/llama.cpp) (the inference runtime,
~550 MB) so you do not have to install anything else. It is the only
exception to "everything is local", and it comes with guarantees:

- **Explicit** — the app does not download anything on its own; you start
  the download with the **Download runtime** button (onboarding or
  Config → **Runtime**).
- **sha256-verified** — each build in the "tested versions" list declares
  its URL + sha256; if the downloaded file does not match, it is
  **discarded** (the partial is deleted) and the runtime is left in `error`.
  Not verifiable = not used.
- **Fixed sources only** — the suite's own channel (release asset) and, as a
  fallback, the official `ggml-org/llama.cpp` build. No user-configurable
  endpoint.
- **No telemetry** — the download sends nothing from your machine: it is a
  `GET` to the release.

If you do not touch **Download runtime**, no download happens: the app works
the same in expert mode (`binary_path`) or with Ollama/LM Studio.

## Configuration and network

- **Default bind `127.0.0.1`** — the app listens only on the local machine.
  Exposing it to the network is opt-in (`GLYVEX_HOST=0.0.0.0`) and in that
  case it **requires authentication** that you would have to implement; it is
  not included.
- **Restricted CORS** to the origins you list in `GLYVEX_CORS_ORIGINS`.
- **Local API keys** — `BRAVE_API_KEY`/`TAVILY_API_KEY` are read from the
  environment variables or from `data/config.json`, which is not committed.
- **`data/config.json` not versioned** — the public template is
  `data/config.example.json`.

## How to verify it yourself

- Run the app without internet: everything works except the search/fetch of
  the model's tools.
- Check `backend/tools.py` (the only outbound HTTP calls) and
  `backend/main.py` (there are no pollers sending to external servers).
