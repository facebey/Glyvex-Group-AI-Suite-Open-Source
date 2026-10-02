# Web search

> **Language:** [Español](README.es.md)

The `web_search` tool in the chat supports four providers. The one to use is
chosen in `tools.search_provider` in `data/config.json`.

| Provider | Needs | When it makes sense |
|---|---|---|
| `ddgs` (default) | nothing | Works as soon as you install the app |
| `searxng` | your own instance | No rate limits, no exposing your identity |
| `brave` | API key | Consistent volume and quality |
| `tavily` | API key | Long snippets, built for agents |

There is no automatic fallback between providers. If you chose SearXNG for
privacy, silently falling back to DuckDuckGo would be the opposite of what
you asked — when the provider fails, the error says what happened and how
to change it.

The diagnostic is at `GET /api/chat/tools/status`, which performs a real,
disposable search. That is what the UI uses to decide whether the globe
toggle is available and what to explain in the tooltip when it is not.

## DuckDuckGo (default)

Nothing to configure. Comes with `pip install -r requirements.txt`.

It scrapes, so with intensive use it may receive temporary rate limits.
If it happens to you often, switch to SearXNG or to a provider with an API
key.

To restrict results to a region, `tools.region`: `wt-wt` is global,
`ar-es` Argentina, `es-es` Spain.

## Brave or Tavily

Get a key from the provider and put it in an environment variable:

```
setx BRAVE_API_KEY "your-key"     # Windows
export BRAVE_API_KEY="your-key"   # Linux
```

You can also leave it in `tools.brave_api_key` / `tools.tavily_api_key`,
but that writes it to `config.json`. The environment variable takes
precedence.

After that, set `tools.search_provider` to `brave` or `tavily`.

## SearXNG (optional)

Requires Docker, so it is an external dependency of the app: **it does not
come included in the Glyvex package**. It is the best option if you already
have an instance running or if you care that searches do not go out with
your identity.

1. Generate a secret and put it in `settings.yml`:

   ```
   openssl rand -hex 32
   ```

2. Start the container:

   ```
   cd docs/searxng
   docker compose up -d
   ```

3. Verify that the JSON format is enabled:

   ```
   curl "http://127.0.0.1:8888/search?q=test&format=json"
   ```

   If it returns JSON, it is ready. If it returns **403 Forbidden** or HTML,
   `json` is missing from `search.formats` in `settings.yml` — restart the
   container after adding it.

4. In `data/config.json`, set `tools.search_provider` to `searxng`.

On Windows, Docker Desktop requires WSL2. If you do not have it and do not
want to install it, stay with the default provider.

## Rest of the configuration

| Key | Default | What it does |
|---|---|---|
| `search_provider` | `ddgs` | Which of the four is used |
| `region` | `wt-wt` | DuckDuckGo region |
| `searxng_url` | `http://127.0.0.1:8888` | Where SearXNG listens |
| `max_results` | `5` | Results per search |
| `fetch_max_chars` | `8000` | Text cap per page read |
| `max_rounds` | `5` | Model↔tool round trips per turn |
| `allow_private_hosts` | `false` | Allows `fetch_url` to read internal network addresses |

`allow_private_hosts` is off on purpose. The `fetch_url` URL is chosen by
the model, not the user, and a manipulated search result could try to
steer it to a service on your network. Enable it only if you want the model
to be able to read internal documentation.

## Model requirements

Tool-calling needs the model's chat template to support it. With
llama-server it must be started with `--jinja`; without that the endpoint
rejects requests that carry `tools`. The app detects support by reading
`chat_template_caps` from `/props` and disables the toggle when it is not
there.
