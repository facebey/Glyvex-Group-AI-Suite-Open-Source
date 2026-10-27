# SEC-1 REPORT — Manual audit: network exposure and telemetry

> **Language:** [Español](REPORTE-SEC-1.es.md)

Date: 2026-10-05 (section a) · 2026-10-06 (section b) · Binary pin: `b11349`
Method: manual semantic audit (grep + code reading). CodeQL/Dependabot are
out of scope for this report (SEC-3, closed, 0 open).

## Threat model

A 100% local desktop app, single user, **no authentication** (by design).
The real threat model is NOT the one of a web app: the actual risks are
LAN exposure (`0.0.0.0` bind), command injection in the launcher
(subprocess to llama-server), path traversal, SSRF in downloads, and XSS in
the rendering of LLM markdown. What this report covers (section a) is the
network perimeter: what the app listens on, which origins it accepts, and
where it goes out to.

## Findings (section a — network exposure)

| # | Finding | Severity | Status |
|---|----------|-----------|--------|
| F-a1 | Google fonts (Exo 2 + JetBrains Mono) loaded remotely on every startup | Medium | Remediated and merged to main (2026-10-05) |
| F-a2 | No authentication on the API when exposed to LAN (`GLYVEX_HOST=0.0.0.0`) | Info (accepted) | Documented |
| F-a3 | CORS with `allow_credentials=True` and wildcards allowed in user configuration | Low | Documented |
| F-a4 | Downloads with `follow_redirects=True` | Info (accepted) | Documented |
| F-a5 | Whisper models from a third-party GitHub source (NoMercy-Entertainment) | Info (accepted) | Documented |

### F-a1 — Google Fonts at runtime (Medium)

`frontend/index.html:8-10` (preconnects + `<link rel="stylesheet">`) and
`frontend/src/index.css:2` (`@import url(...)`) load Exo 2 (300-800) and
JetBrains Mono (400,600) from `fonts.googleapis.com` / `fonts.gstatic.com`.

Impact:
- Contradicts the "100% local, no cloud" claim (AGENTS.md, `docs/PRIVACY.md`).
- Every startup sends the user's public IP to Google (request + user agent),
  even if the app "does not use cloud": it is an automatic, involuntary
  network egress.
- Soft dependency: without internet the fonts fall back to system fonts
  (the app does not break), but the network request is still attempted.

Recommendation: self-host the woff2 files in `frontend/public/fonts/` (the
pattern already exists: the splash's Poppins, with its `OFL.txt`) and remove
the remote `<link>`/`@import`. This mitigates F-a1 completely and leaves the
frontend 100% offline.

**Remediation implemented** (merged to main on 2026-10-05): the two brand-kit
fonts (Exo 2 and JetBrains Mono, both OFL 1.1) are *variable fonts* on Google
Fonts — a single woff2 per family covers all weights. The latin subset of each
was downloaded (`frontend/public/fonts/`: `exo-2-latin.woff2` 40 KB,
`jetbrains-mono-latin.woff2` 31 KB) along with its `OFL.txt`, declared via a
local `@font-face` in `frontend/src/index.css` (replacing the remote
`@import`), and the 3 `<link>` tags were removed from `frontend/index.html`.
Watch out for a build gotcha: Vite 8 / Tailwind 4's CSS minifier
(lightningcss) corrupts `unicode-range: U+0000-00FF` into `U+??` (control
characters 0x00-0x1F get serialized as `?`), invalidating the declaration;
the solution was to declare the range starting at `U+0020` (space).
Verified: `npm run build` with no reference to `googleapis`/`gstatic` in the
bundle, `npm run test` 14/14, pytest 604/604. The fonts are also versioned
in the brand kit (`assets/brand/fonts/` and the official kit outside the
repo), and its doc was updated. The `glyvex/web` website still uses remote
Google Fonts (separate project, outside this repo).

### F-a2 — No auth on LAN exposure (Info, accepted risk)

By default the app only listens on loopback (see inventory). Exposing to LAN
is an explicit opt-in (`GLYVEX_HOST=0.0.0.0`) and the documentation is
honest about it: `docs/PRIVACY.md:74-77` states that in that case "requires
authentication that you would have to implement; it is not included". There
is no false security claim. Residual risk accepted: whoever exposes the API
to LAN lets any device on the network use it (chat, TTS/STT, tools). No
action required; optional improvement: warn in the startup log when the host
is not loopback (today only CORS wildcards `*` are warned about).

### F-a3 — CORS with credentials (Low)

`backend/main.py:128-159`: `allow_credentials=True`, `allow_methods=["*"]`,
`allow_headers=["*"]`, and origins taken from `GLYVEX_CORS_ORIGINS` (default
`http://localhost:5173` for dev). In production the Tauri shell pins the
allowlist to `https://tauri.localhost,tauri://localhost`
(`src-tauri/src/lib.rs:145-148`), no wildcards. The only path to a wildcard
is manual user configuration, and the code detects it and logs it as a
warning (`main.py:156-159`). Additionally, browsers reject `*` combined with
credentials, so the real impact is nil. No change.

### F-a4 — follow_redirects in downloads (Info, accepted risk)

`backend/runtime.py:360-361`: `httpx.AsyncClient(..., follow_redirects=True)`.
A redirect could point to another host, but file integrity is guaranteed by
the mandatory incremental sha256 verification (without a sha256 the source
"does not exist", `runtime.py:137-148`). Worst case: a request is sent to an
unexpected host; the downloaded binary cannot be tampered with. The actual
redirects (GitHub/HF) are part of those CDNs' own infrastructure. No change.

### F-a5 — Third-party whisper models (Info, accepted risk)

`backend/stt_runtime.py:86-103`: the ggml-* models come from the
`NoMercy-Entertainment/nomercy-whisper-models` v2026.08.23 release (a
third-party GitHub repo, not ggml-org), as a documented deviation from D6
(HF blocked at the user's IP; `stt_runtime.py:88-93`). Existing mitigation:
per-file sha256 (and per-part, for large-v3) verified before use. The trust
chain = the sha256 pin, not the repo's author. No change; the pin is the
guarantee and it is already in the catalog.

## Verified inventory — inbound (bind)

| Point | Bind | Evidence |
|---|---|---|
| Backend (FastAPI/uvicorn) | `127.0.0.1:7981`, fallback 7981→7990→dynamic | `backend/main.py:265-298` (`_bind_with_fallback`), `main.py:338` |
| Launcher CLI | `127.0.0.1:7981` | `start.cmd:65-66`, `start.ps1:49-50` |
| Tauri shell | spawns the backend with `GLYVEX_HOST=127.0.0.1`, `GLYVEX_PORT=7981` | `src-tauri/src/lib.rs:138-139` (`SPAWN_PORT` line 23) |

Network exposure: explicit opt-in via `GLYVEX_HOST`, documented in
README/PRIVACY with the warning that no auth is included.

## Verified inventory — outbound

| Destination | When | Transport | Integrity | Opt-in |
|---|---|---|---|---|
| ~~`fonts.googleapis.com` / `fonts.gstatic.com`~~ | ~~Every startup, automatic~~ | — | — | — (removed by F-a1: self-host) |
| `github.com/ggml-org/llama.cpp` (b11349) | "Download runtime" button | HTTPS (httpx, TLS verified) | sha256 mandatory | Yes |
| `github.com/ggml-org/whisper.cpp` (b5130) | STT button | HTTPS | sha256 mandatory | Yes |
| `github.com/NoMercy-Entertainment/nomercy-whisper-models` | STT button (models) | HTTPS | sha256 per file | Yes (F-a5) |
| `github.com/thewh1teagle/kokoro-onnx` (model-files-v1.1) | TTS button | HTTPS | sha256 + size | Yes |
| `huggingface.co/rhasspy/piper-voices` (v1.0.0) | TTS button (voices) | HTTPS | sha256 + size | Yes |
| `github.com/facebey/.../releases/latest.json` (updater) | Tauri bundle update check | HTTPS | ed25519 signature (pubkey in `tauri.conf.json`) | Yes |
| `api.search.brave.com` / `api.tavily.com` | Search tool, only with the user's API key | HTTPS | — | Yes (key in `data/config.json`, not versioned) |
| SearXNG (`127.0.0.1:8888`) | Search tool, user's own self-hosted instance | Local HTTP | — | Yes |
| Chat/benchmark endpoints | Only to the endpoint configured by the user (default `127.0.0.1:8080`) | HTTP | — | Yes |
| `*.glyvexgroup.com`, `github.com/facebey/...` (About links) | Explicit user click | HTTPS | — | Yes |

## Absence of telemetry — verified

- No analytics/telemetry libraries in backend or frontend: there is no
  sentry, posthog, mixpanel, segment, plausible, umami, hotjar, or
  `navigator.sendBeacon` anywhere in the app code.
- All WebSockets (`Monitor`, `Benchmark`, `Launcher`, `useLlmStream`) and SSE
  streams go to first-party origins via `wsUrlFor(...)` / same-origin fetch.
  None points to an external host.
- `@opentelemetry/api` appears in `package-lock.json` **only as a
  peerDependency of vitest** (dev-only, it is neither installed nor bundled;
  it is an interfaces-only package with no exporter and no network). It is
  not telemetry.
- Conclusion: the only automatic, involuntary network egress the app had was
  Google Fonts (F-a1), already remediated and merged to main (self-host).
  The app now has no automatic network egress: everything else is explicit
  button-triggered downloads, user-configured endpoints, or click-initiated
  navigation.

## Findings (section b — backend: command injection, path traversal, SQL)

Date: 2026-10-06 · Baseline: pytest 610 green before the change.

| # | Finding | Severity | Status |
|---|----------|-----------|--------|
| F-b1 | zip-slip containment in `_extract_zip` via `str.startswith` (sibling-directory bypass) | Low | Remediated on this branch |

### F-b1 — zip-slip: `startswith` vs `is_relative_to` (Low)

`backend/runtime.py:342-344` (before the fix): the guard validated
`str(dest).startswith(str(target.resolve()))`. An entry resolving into a
*sibling* directory whose name starts with the destination's (`../out-evil/x`
with target `.../out`) passes the `startswith` (`out-evil` starts with `out`)
and gets written outside the target. Remediated with
`dest.is_relative_to(target.resolve())` + regression test
(`tests/test_runtime.py::test_extract_zip_rejects_sibling_dir_bypass`).
Real exploitability: low — all zips going through here (llama.cpp runtime,
whisper models) come from fixed manifest URLs with mandatory sha256
verification; the guard is a second line of defense, not the first. It
covers the 3 call sites (`runtime.py` ×2, `stt_runtime.py` ×1).

### Audit — command injection (no findings)

- All subprocesses use `asyncio.create_subprocess_exec` / an argv list,
  **never** `shell=True` or `Popen` with a string: `launcher.py:634`
  (`--help`/`--version` probe), `launcher.py:1331` (launch), `stt.py:182`
  (whisper-cli probe), `stt_runtime.py:530`, `tts.py:161` (PowerShell SAPI).
- The launch command (`build_llama_server_command`, `launcher.py:770`) is an
  argv list with each value as its own entry, prefixed by its flag; the only
  concatenation is `--lora-scaled PATH:SCALE` (a single entry, no shell).
  There is no OS command injection.
- `tts.py`: the PowerShell script is written to a temp dir with fixed content
  (the `_VOICES_SCRIPT`/`_SPEAK_SCRIPT` constants) and the args go as a list.
- Informational note (no change): user config values (`lora_path`,
  `mmproj_path`, `mtp_draft_model`, `host`, `cache_type_k`, `load_mode`,
  `reasoning_effort`, `rope_scaling_type`) are passed as argv entries; a
  value starting with `-` could be reinterpreted as a flag by llama-server
  (argument injection in the binary itself). Impact nil in practice: the
  config belongs to the user of the local machine themselves.

### Audit — path traversal (no new findings)

| Point | Containment | Evidence |
|---|---|---|
| Attachments (`/api/chat/attachments/{id}/raw`) | 32-char hex id: `isalnum()` + exact length → 400 | `attachments.py:625-631` |
| Attachment writes | id generated by the app (`uuid4().hex`) | `attachments.py:559-561` |
| SPA fallback | always a fixed `index.html`; the request path never touches the filesystem | `main.py:265-271` |
| `/assets` | Starlette's `StaticFiles` (sanitizes the path) | `main.py:263` |
| Launcher `log_file` | `relative_to(DATA_DIR)` → 400 if it escapes | `launcher.py:541-574` (C3) |
| Runtime/STT/TTS downloads | fixed destinations in `runtime_dir()` and dedicated dirs; manifest URL with mandatory sha256 | `runtime.py:350-384` |
| Piper voices by name | `name` must exist in the fixed `PIPER_VOICES` registry → 404 | `piper_runtime_api.py:42-47` |
| GGUF header reading | paths from `models.json` (user-configured) — reads files on the user's own local machine | `models.py:511` (informational) |

### Audit — SQL (no findings)

- All data access goes through the SQLAlchemy ORM with parameters
  (`select()`, `.ilike()`, `.order_by(column)`): `database.py:399-412`,
  `benchmark.py:630/1097/1192`.
- The only SQL f-strings (`metrics_store.py:245/287/322-324`) interpolate
  table names from a fixed internal dictionary (`TABLE[src]`) and `?`
  placeholders; values always go as bound parameters.

## Findings (section c — secrets and SSRF)

Date: 2026-10-06 · No code changes (audit only).

| # | Finding | Severity | Status |
|---|----------|-----------|--------|
| F-c1 | API keys in clear text on the local (loopback) API | Low (accepted) | Documented |
| F-c2 | `--api-key` on the child llama-server's command line | Low (informational) | Documented |

### F-c1 — API keys in clear text on the local API (Low, accepted risk)

`GET /api/config` (`main.py:191-193`) returns the full `config.data`, with
`tools.brave_api_key` / `tools.tavily_api_key` in clear text; `GET
/api/launcher/status` (and `/launch`, `/stop`, `/restart`) return
`ProcessInfo.launch_config` with `api_key` in clear text
(`launcher.py:484,1356`). The frontend needs them to populate/edit the
configuration fields (`Config.jsx`, `Launcher.jsx:1273-1279`). Same trust
boundary as F-a2: by default everything listens on loopback and LAN is an
opt-in without auth (accepted). What IS protected: the `command` exposed by
the API and written to logs goes through `mask_command`
(`launcher.py:754-767`, `--api-key` → `********`), and config logs use
`_redact_secrets` (`main.py:70-86`). Error messages never include the key
(generic `ProviderError` in `tools.py`). No change.

### F-c2 — `--api-key` in the child's argv (Low, informational)

`launcher.py:978-979`: the key is passed as a command-line argument to
llama-server, visible to any local process (tasklist/WMI/Process Explorer)
while the server runs. llama-server offers no environment-variable
alternative; on a single-user machine the key is the user's own.
Informational; no change.

### Verified clean — secrets

- `data/config.json` and `models.json` in `.gitignore`;
  `data/config.example.json` is versioned with no values (all keys/tokens/
  passwords are `""`).
- Prometheus token: timing-safe comparison with `hmac.compare_digest`
  (`metrics_export.py:395-402`), endpoint disabled by default
  (`exports.prometheus_enabled=false`) and 404 when not enabled.
- Tool keys with env > config.json precedence (`tools.py:151-154`):
  `BRAVE_API_KEY`/`TAVILY_API_KEY` avoid leaving the key on disk.
- `GET /api/llm-metrics/processes` exposes only id/name/state/host/port
  (`llm_metrics.py:808-812`), no `launch_config`.
- Updater: the ed25519 entry in `tauri.conf.json` is the PUBLIC verification
  key (there is no secret to protect).

### Verified clean — SSRF

| Surface | Risk | Evidence |
|---|---|---|
| `fetch_url` tool (LLM-suggested URL) | Full SSRF guard: resolves DNS locally (does not trust the literal) and blocks `is_private \| is_loopback \| is_link_local \| is_reserved \| is_multicast` — covers 169.254.169.254 (cloud metadata) — applied to the original URL AND on every redirect | `tools.py:436-463,534-570`; explicit opt-out `tools.allow_private_hosts` (default `false`) |
| Brave / Tavily search | URLs fixed in code (`https://api.search.brave.com`, `https://api.tavily.com`); the key goes in a header/body and never in the URL | `tools.py:284,323-331` |
| Chat/benchmark endpoints | The user configures the endpoint (their own infrastructure; default `127.0.0.1:8080`) — by design, not SSRF | `chat.py:107,1266-1270` |
| Runtime/STT/TTS downloads | URLs fixed in the manifest + mandatory sha256 | `runtime.py:350-384` (F-a4) |
| SearXNG | URL configured by the user (default `127.0.0.1:8888`, their own self-hosted instance) | `tools.py:143` |

Note (no change): between the guard's `getaddrinfo` and httpx's own
resolution there is a theoretical DNS rebinding window; exploiting it requires
control over the DNS of a domain the victim does not administer, out of scope
for a single-user local app.

## Findings (section d — XSS in the frontend)

No findings to remediate. The LLM-controlled content rendering surface was
verified clean layer by layer.

### Verified clean — markdown rendering

| Surface | Risk | Evidence |
|---|---|---|
| `dangerouslySetInnerHTML` / `eval` / `document.write` / `window.open` | Absent from all of `frontend/src` | direct search (0 matches) |
| Raw HTML in markdown | `react-markdown` **without** `rehype-raw` → literal HTML in the LLM response is escaped and rendered as text | `MarkdownMessage.jsx:153` (only `components` are overlaid, no raw-HTML plugins) |
| Dangerous URLs in `href`/`src` | `urlTransform` **not overridden** → react-markdown 10.1.0's `defaultUrlTransform` rules: any protocol outside the allowlist (`javascript:`, `file:`, `data:`…) is emptied to `""` BEFORE reaching the custom components | `react-markdown/lib/index.js:320,421-444`; `MarkdownMessage.jsx` does not define `urlTransform` |
| External links (custom `a` component) | Browser mode: `target="_blank" rel="noopener noreferrer"` (no reverse tabnabbing). Tauri mode: `preventDefault()` + `openExternal(href)` | `ExternalLink.jsx:1-27` (used only by `MarkdownMessage`) |
| `openExternal` → OS handler | The `shell:allow-open` permission is granted (`src-tauri/capabilities/default.json`) BUT with no `plugins.shell.open` in `tauri.conf.json`, the plugin applies its default allowlist: only `mailto:`, `tel:`, `https?://` reach the OS handler. `file://`, `javascript:` and relative paths (e.g. `/C:/Windows/System32/...`) are **rejected by the plugin** before ShellExecute — no local-binary execution vector | `tauri-plugin-shell-2.4.0/src/lib.rs:178-196` (`ShellAllowlistOpen::Unset` → regex `^((mailto:\w+)\|(tel:\w+)\|(https?://\w+)).+`) |
| Benchmark HTML report | In the frontend it is only **downloaded** to disk (`downloadBlob`), never rendered in the browser. The report served by the backend escapes every dynamic value with `_esc` | `Benchmark.jsx:446-451`; `benchmark.py:455,460-529` |

### Verified clean — local storage

- `localStorage`/`sessionStorage` only store: language, theme, collapsible
  panel state, and the active benchmark's `run_id` (internal UUID,
  re-validated against the backend on load). No LLM or user content goes
  through them → no stored-XSS vector.

Observation (no change, optional defense-in-depth): making explicit
`"plugins": { "shell": { "open": "^https?://..." } }` in `tauri.conf.json`
would make the allowlist the plugin currently applies by default visible, and
a schema check in `openExternal` (`lib/tauri.js:45`) would also protect a
future second caller. Not necessary with the two current layers
(`defaultUrlTransform` + plugin scope).

## Findings (section e — functional findings per module)

Verified with file:line. The detail (findings and proposed fixes) is kept in
an internal annex, outside the public repo.

## Status of epic SEC-1

- a) network/telemetry: ✅ — a.1 (bind) loopback by default at every point;
  a.2 (CORS) explicit allowlist, no wildcards in production;
  a.3 (outbound + telemetry) 1 finding (F-a1, merged to main), the rest
  documented/accepted; a.4 (report) this document.
- b) commands/paths/SQL: ✅ (above) — 1 finding (F-b1, remediated on a
  branch), the rest verified clean.
- c) secrets and SSRF: ✅ — 2 documented findings (F-c1 accepted, F-c2
  informational), the rest verified clean. No code changes.
- d) XSS in frontend: ✅ — 0 findings to remediate; surface verified clean
  layer by layer (urlTransform + shell plugin scope + no rehype-raw +
  localStorage without LLM content). 1 optional defense-in-depth observation
  documented.
- e) functional findings per module: ✅ — verified with file:line; the detail
  (findings and proposed fixes) lives in an internal annex, outside the
  public repo.
- f) close: ✅ — complete document (sections a–e).
