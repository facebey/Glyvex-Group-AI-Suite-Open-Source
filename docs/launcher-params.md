# Launch parameters — reference

> **Language:** [Español](launcher-params.es.md)

The parameters the Launcher (M2) manages for `llama-server`, with their
llama.cpp flag and the app default (schema `LaunchConfig` in
`backend/launcher.py`, the source of truth).

Two concepts that recur:

- **Toggle / `None`** — several fields are optional: when the toggle is
  OFF (value `None` or the default "disabled") the builder **does not emit
  the flag** and llama-server uses the build default. Useful for fine
  benchmarks where you do not want the app to have an opinion.
- **Capability probe** — on the first launch per build, the Launcher reads
  the binary's `--help` and **drops the flags that build does not know**
  (with a warning in the log). That is why a valid config can still launch
  against old or new builds, throwing away the nonexistent flag instead of
  breaking.

## Core

| Field | Flag | App default | Notes |
|-------|------|-------------|-------|
| `model_id` | — | — | GGUF from the inventory (M1). |
| `backend` | — | `llama_server` | `llama_server` \| `ollama` \| `lm_studio`. |
| `n_ctx` | `-c` | 65536 | Toggle: `None` = build default. |
| `n_batch` | `-b` | 2048 | Measured: pp2048 ~1400 t/s on wide GPUs; 512 performs much worse in prompt processing. |
| `n_ubatch` | `--ubatch-size` | 512 | Toggle. Kept at 512 to not inflate the compute buffer. |
| `n_gpu_layers` | `-ngl` | -1 (all) | `gpu_mode="cpu_only"` forces it to 0. |
| `n_cpu_moe` | `--n-cpu-moe` | 0 (none) | Only emitted when > 0. Moves MoE expert layers to system RAM for models that don't fit in VRAM. |
| `gpu_mode` | — | `gpu_only` | `gpu_only` \| `cpu_only` \| `hybrid`. `gpu_only` + `n_gpu_layers=0` is invalid (use `-1` or hybrid). |
| `cache_type_k` / `cache_type_v` | `--cache-type-k/-v` | `q4_0` | With Flash Attention **symmetric pairs only**: `q4_0-q4_0`, `q8_0-q8_0`, `f16-f16`, `bf16-bf16`. Any other combination ends in a crash or a silent fallback to f16. |
| `flash_attn` | `--flash-attn` | `on` | Accepts `on`/`off`/`auto` (not 1/0). |
| `load_mode` | `--load-mode` | `auto` | `auto` \| `none` \| `mmap` \| `mlock` \| `mmap+mlock` \| `dio`. Replaces the old `--mlock`/`--no-mmap`. |
| `mmproj_path` | `--mmproj` | — | Vision encoder (sidecar or embedded). |
| `lora_path` / `lora_scale` | `--lora` / `--lora-scaled` | — / 1.0 | `lora_scale` between 0.0 and 2.0. From b11146 (v0.5.0) `--lora-scale` does not exist: with the probe's feature-detect it emits `--lora-scaled PATH:SCALE`; on earlier builds (or without probe) the classic pair `--lora PATH --lora-scale N`. |

## Speculative decoding (MTP / NextN)

| Field | Flag | App default | Notes |
|-------|------|-------------|-------|
| `mtp_draft_model` | `--spec-draft-model` | — | Detected `mtp-*.gguf` sidecar or manual path. |
| `mtp_embedded` | — | `False` | The model carries the `blk.N.nextn.*` tensors (detected by the M1 scanner). |
| `n_draft` | `--spec-draft-n-max` | 5 | Draft tokens per step. |
| `n_draft_min` | `--spec-draft-n-min` | `None` (not emitted) | Floor for draft tokens per step. `None` = build default. |
| `p_draft_min` | `--spec-draft-p-min` | `None` (not emitted) | Acceptance probability floor. `None` = build's `0.00` (no floor); `0.75` stops dubious speculation early. |
| `cache_type_k/v_draft` | `--spec-draft-type-k/-v` | `q8_0` | Draft KV. Default q8_0: half the VRAM of f16 (llama-server's default) with practically identical acceptance. With FA it also falls under the symmetric-pair whitelist. |

## Reasoning / thinking

| Field | How it travels | App default | Notes |
|-------|-----------|-------------|-------|
| `thinking_enabled` | `--reasoning` `on\|off` | `False` | Native flag: the `enable_thinking` kwarg in `--chat-template-kwargs` became deprecated in llama.cpp (warning in the server log). Only emitted if the model reads `enable_thinking` and with `--jinja` enabled. The other lever is the chat's `/think` prefix. |
| `budget_tokens` | `--reasoning-budget` | 8192 | Only with thinking enabled and if `reasoning_budget` is -1 (that field, being explicit, has priority). `-1` = no limit (not sent). |
| `jinja` | `--jinja` | `True` | |
| `reasoning_effort` | — | `none` | `none` \| `low` \| `medium` \| `high` \| `xhigh`. |
| `no_reasoning_preserve` | — | `False` | b11003 enables preservation by default and spends tokens re-emitting the reasoning every turn; this turns it off. |
| `reasoning_budget` | `--reasoning-budget` | -1 | Native token budget of the server (b11009). `-1` = no limit (not emitted), `0` = immediate end of thinking. Has priority over `budget_tokens`. |

## Sampling (server defaults)

When passed as launch flags, llama-server uses them as defaults for
**all** clients that do not specify their own.

Preset `thinking`: temp 1.0 · top_p 0.95 · top_k 20 · min_p 0.0 ·
presence 0.0 · repeat 1.0

Preset `instruct` (default): temp 0.7 · top_p 0.80 · top_k 20 · min_p 0.0 ·
presence 0.3 · repeat 1.1

> Careful with high penalties: repeat_penalty 1.5 / presence 1.5 **degenerate
> the output in the Qwen family** (especially code).

Valid ranges: temp 0–2 · top_p 0–1 · top_k ≥ 0 (0 = disabled) ·
min_p 0–1.

## RoPE scaling (grouped toggle)

OFF = the three fields `None` → not a single flag.

| Field | Default | Notes |
|-------|---------|-------|
| `rope_freq_base` | 0.0 (= auto) | |
| `rope_scaling_type` | `none` | `none` \| `linear` \| `yarn` |
| `yarn_ext_factor` | -1.0 (= auto) | |

## Context checkpoints (VRAM in hybrid/SSM architectures)

Each checkpoint copies the recurrent state: **~150 MiB of VRAM** in
hybrid architectures (SSM). With long ctxs it is the component that dominates
post-load consumption. The build defaults (32 / 8192) can reach
~2.4 GiB extra in a 128k ctx; the app's cap them:

| Field | Flag | App default | Build default |
|-------|------|-------------|---------------|
| `ctx_checkpoints` | `-ctxcp` | 8 | 32 |
| `checkpoint_min_step` | `-cms` | 16384 | 8192 |

With `-cms 16384` the maximum reprocessing when regenerating an old message
is ~16k tokens (~12–16 s at pp ~1300 t/s), almost never noticeable.

## Prompt cache (system RAM, not VRAM)

| Field | Flag | App default | Notes |
|-------|------|-------------|-------|
| `cache_ram_mib` | `--cache-ram` | 8192 | Limit of the idle prompt cache in RAM. 0 disables it. With 64 GB of RAM there is room to raise it and speed up switching between conversations. |

## Automatic VRAM fitting

| Field | Flag | App default | Notes |
|-------|------|-------------|-------|
| `fit_target_mib` | `--fit-target` | 0 (off) | >0: llama.cpp reserves that VRAM margin and auto-reduces whatever is needed (including ctx). Ideal for loading arbitrary models without knowing their size. |
| `fit` | `--fit` | `on` | Adjusts the arguments the user did **not** set to fit the device memory (b11009 default: on). |

## Advanced optimizations

| Field | Flag | App default | Notes |
|-------|------|-------------|-------|
| `numa` | `--numa` | off | The flag REQUIRES a value: `distribute` \| `isolate` \| `numactl`. |
| `no_kv_offload` | `--no-kv-offload` | off | |
| `cache_reuse` | `--cache-reuse` | 0 | 0–256. |
| `defrag_thold` | `--defrag-thold` | -1 (off) | |
| `kv_unified` | `--kv-unified` | off | Without this and with `n_parallel > 1`, **each slot reserves its own full ctx** (KV VRAM × N). |
| `kv_unified_per_slot` | `--kv-unified-per-slot` | 0 | 0 = no limit (each slot uses the global `n_ctx`). Only with `kv_unified`. |
| `sleep_idle_seconds` | `--sleep-idle-seconds` | 0 (off) | The server "sleeps" (frees VRAM) after N s of inactivity. |
| `warmup` | `--warmup` / `--no-warmup` | on | Empty run at startup. |
| `lazy_mode` | `--lazy-mode` | `auto` | On-demand reading of large tensors. `auto` = only >4 GiB. |
| `grp_attn_n` / `grp_attn_w` | (group attention) | 1 / 512 | Flag deprecated/removed in several builds: the probe drops it with a warning if the binary does not know it. |
| `n_parallel` | `--parallel` | 1 | 1–8. |
| `n_threads` | `-t` | -1 (auto) | -1 or 0 = the flag is not passed. |

## Automatic mode

`auto_mode = True` → strict command: only model + port (+ host if different
from 127.0.0.1). Everything else uses the build default. The app's
infrastructure is kept (`--verbosity` for logs, `--metrics` for the Monitor).

## General

| Field | Default | Notes |
|-------|---------|-------|
| `host` | 127.0.0.1 | |
| `port` | 8080 | The server's default port will migrate from 8080 to 9931 in the future: never hardcode, always come out of `cfg.port` / backends config. |
| `api_key` | `""` | |
| `log_file` | `data/logs/llama-server.log` | |
| `template_name` | — | Hardware template (predefined or custom, persisted in SQLite). |

## Cross-validations (summary)

The app rejects contradictory configs before launching (422):

- `gpu_only` requires `n_gpu_layers ≠ 0` · `cpu_only` forces 0
- With `flash_attn=on`, KV K/V (and draft) in whitelisted symmetric pairs
- `lora_scale` 0–2 · `n_parallel` 1–8 · `cache_reuse` 0–256
- `checkpoint_min_step` ≥ 512 · `cache_ram_mib` ≥ 0 · `fit_target_mib` ≥ 0
- Sampling within ranges (see above)

Saved configurations with KV types or removed flags (e.g. `q4_1`,
`use_mlock`) are migrated silently with a warning: an old template does not
break the launch.
