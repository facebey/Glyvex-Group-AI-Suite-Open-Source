# Launch flags reference

Every `llama-server` flag the Launcher (M2) can emit, in plain language: what it
does, its impact, the app default, and when you should change it. The source of
truth is `build_llama_server_command()` in [`backend/launcher.py`](../backend/launcher.py);
this doc is the readable companion to it (the field-by-field one is
[`launcher-params.md`](launcher-params.md), ES).

The same help text shown under each control in the Launcher UI comes from the
binary's own `--help` (the `/api/launcher/backend-info` probe), so what you read
there always matches your installed build.

## How the app handles flags

- **Toggle OFF = flag omitted.** Most fields are optional: when the toggle is OFF
  (value `None` / the "off" default) the builder does **not** emit the flag, so
  `llama-server` falls back to the **build default**. This is what you want for
  fine benchmarks — don't let the app impose an opinion.
- **Capability probe.** On the first launch per build the Launcher reads the
  binary's `--help` and **drops any flag that build doesn't know** (logged as a
  warning). A valid config therefore launches against both old and new builds:
  the unknown flag is discarded instead of crashing the launch.
- **Automatic mode.** `auto_mode = True` sends a strict, minimal command — model +
  port (+ host if it differs from `127.0.0.1`) — and lets the build decide
  everything else. The app's own infra is kept: `--verbosity` (logs) and
  `--metrics` (Monitor).

Defaults below are the **app defaults** (what the Launcher sets). Where the build
default differs, it's noted in the Impact or Default column.

## Core (always sent)

| Flag | What it does | Impact | Default | When to change |
|------|--------------|--------|---------|----------------|
| `--model` | GGUF file to load. | Mandatory. | From inventory (M1). | Always — pick the model. |
| `--port` | TCP port the server listens on. | None. | `8080`. | Only if something else uses it. Never hardcode — the default will migrate off 8080; always read it from config. |
| `--host` | Bind address. | None when `127.0.0.1`. | `127.0.0.1` (omitted otherwise). | Only to expose the server on the LAN. |
| `--verbosity` | Server log level. | None. | `1` (app-fixed). | Don't change. |
| `--metrics` | Expose `/metrics` for the Monitor. | None. | on (app-fixed). | Don't change. |

## Context

| Flag | What it does | Impact | Default | When to change |
|------|--------------|--------|---------|----------------|
| `--ctx-size` | Maximum tokens of context (prompt + generation) the server keeps. | Larger = more VRAM (KV cache) and more checkpoint/SSM state. Small = faster, less history. | `65536` (toggle; OFF = build default = model's native ctx). | Raise for long documents / big chats; lower to save VRAM on small models. |

## Batching

| Flag | What it does | Impact | Default | When to change |
|------|--------------|--------|---------|----------------|
| `--batch-size` | Max tokens processed per prompt-processing step. | Larger = faster prompt processing (pp) on wide GPUs. | `2048` (toggle; OFF = build default). | Keep 2048 on GPU; 512 is noticeably slower at pp. |
| `--ubatch-size` | Compute-buffer size per step. | Larger helps on CPU; larger than needed just inflates memory. | `512` (toggle; OFF = build default). | Leave at 512 unless profiling a CPU build. |

## GPU offload

| Flag | What it does | Impact | Default | When to change |
|------|--------------|--------|---------|----------------|
| `--n-gpu-layers` | How many model layers to offload to the GPU. `-1` = all. | More layers on GPU = much faster, but more VRAM. `0` = CPU only. | `-1` (all). `gpu_mode="cpu_only"` forces `0`. | Lower it until the model fits in VRAM; `-1` when it fits. |
| `--flash-attn` | Flash Attention (reduces KV memory + speeds decode). | on = less KV VRAM, faster. | `on` (toggle). | Keep on. With FA the KV types below must be symmetric pairs. |
| `--load-mode` | How the model file is mapped into memory (`auto`/`none`/`mmap`/`mlock`/`mmap+mlock`/`dio`). | `mlock`/`mmap+mlock` pin the file in RAM (no re-reads); `mmap` lazy-loads. | `auto` (toggle; OFF = build default). | `mlock` if you want the whole model resident; leave `auto` otherwise. |
| `--numa` | NUMA policy. Requires a value: `distribute`/`isolate`/`numactl`. | Matters only on multi-socket NUMA hosts. | off (toggle; app sends `distribute`). | Only on NUMA machines. |
| `--no-kv-offload` | Keep the KV cache on CPU, not GPU. | Frees GPU VRAM at the cost of speed. | off (toggle). | Only if you're VRAM-starved and can afford slower decode. |

## KV cache

| Flag | What it does | Impact | Default | When to change |
|------|--------------|--------|---------|----------------|
| `--cache-type-k` | Quantization of the K cache. | Lower precision = less VRAM. With FA, K and V must form a symmetric pair. | `q4_0` (toggle; OFF = build default `f16`). | Keep `q4_0`/`q8_0` to save VRAM. Allowed pairs with FA: `q4_0-q4_0`, `q8_0-q8_0`, `f16-f16`, `bf16-bf16`. |
| `--cache-type-v` | Quantization of the V cache. | Same as above. | `q4_0` (toggle; OFF = build default `f16`). | Pair it with `--cache-type-k`. |
| `--cache-reuse` | Reuse cached prefix KV across requests. | Higher = more reuse, slightly more bookkeeping. `0` = off. | `0` (toggle). | Bump (0–256) for repeated long system prompts. |
| `--kv-unified` | Share one KV pool across parallel slots. | Without it, each slot reserves its full ctx (× N VRAM). | off (toggle). | On when `--parallel > 1` to avoid N× ctx VRAM. |
| `--kv-unified-per-slot` | Per-slot context cap when `--kv-unified` is on. `0` = unlimited (uses global `--ctx-size`). | Caps per-slot VRAM. | `0` (toggle). | Set with `--kv-unified` to bound per-slot memory. |
| `--defrag-thold` | KV defragmentation threshold. | Marked **DEPRECATED** in recent builds; still emitted for now. | `-1` (off; toggle). | Leave off; the probe drops it if the build removed it. |

## Memory & auto-fit

| Flag | What it does | Impact | Default | When to change |
|------|--------------|--------|---------|----------------|
| `--fit` | Auto-adjust the arguments you didn't fix so the model fits the device (ctx included). | on = fewer "doesn't fit" crashes on arbitrary models. | `on` (toggle; build default on for b11009). | Keep on. |
| `--fit-target` | VRAM headroom (MiB) to reserve per device. `0` = off. | `>0` reserves that margin and auto-reduces what it must. | `0` (toggle). | Set `>0` if you run other things on the GPU. |
| `--cache-ram` | Idle prompt-cache cap in **system RAM** (not VRAM). | Frees fast switches between conversations; `0` = off. | `8192` (toggle). | Raise if you have lots of RAM (e.g. 64 GB) and switch chats a lot. |

## Context checkpoints (hybrid / SSM models)

| Flag | What it does | Impact | Default | When to change |
|------|--------------|--------|---------|----------------|
| `--ctx-checkpoints` | Max context checkpoints per slot. | Each one copies recurrent state: **~150 MiB VRAM** on SSM/hybrid models. At 128k, build defaults (32/8192) can add ~2.4 GiB. | `8` (app; build default `32`; toggle). | The app default (8) already caps this; lower further if VRAM is tight. |
| `--checkpoint-min-step` | Minimum spacing (tokens) between checkpoints. | Larger = fewer checkpoints, slower re-prompt of old messages. | `16384` (app; build default `8192`; toggle). | ~16k ≈ 12–16 s worst-case re-process at ~1300 t/s — usually imperceptible. |

## RoPE scaling

| Flag | What it does | Impact | Default | When to change |
|------|--------------|--------|---------|----------------|
| `--rope-scaling` | Position-encoding scaling type (`none`/`linear`/`yarn`). | Needed to use a model beyond its trained length. | `none` (grouped toggle; OFF = no flag). | Only if you extend the context window. |
| `--rope-freq-base` | RoPE base frequency. `0` = auto. | Tunes long-context behavior with yarn. | `0.0` (= auto; toggle). | Only together with `--rope-scaling yarn`. |
| `--yarn-ext-factor` | YaRN extension factor. `-1` = auto. | Controls the extrapolation range. | `-1.0` (= auto; toggle). | Only together with `--rope-scaling yarn`. |

## Group attention

| Flag | What it does | Impact | Default | When to change |
|------|--------------|--------|---------|----------------|
| `--grp-attn-n` | Group-attention window (n). `1` = disabled. | Deprecate/remove varies by build. | `1` (toggle). | Leave disabled. |
| `--grp-attn-w` | Group-attention width. | Only when `--grp-attn-n > 1`. | `512` (toggle). | Leave disabled. |

> Both are marked deprecated/removed in several builds: the probe drops them with
> a warning if your binary doesn't know them.

## Reasoning / thinking

| Flag | What it does | Impact | Default | When to change |
|------|--------------|--------|---------|----------------|
| `--jinja` / `--no-jinja` | Use Jinja chat templates (required for `--reasoning-effort`). | on = full template features. | `--jinja` (toggle). | Keep on for reasoning models. |
| `--reasoning` | Native thinking on/off (`on`/`off`). | Enables the model's reasoning path. | `off` (toggle). Only emitted when the model reads `enable_thinking` and `--jinja` is on. | The other lever is the `/think` prefix in chat. |
| `--reasoning-budget` | Token budget for the thinking pass. `-1` = unlimited (not sent). | Caps how long the model thinks. | `reasoning_budget=-1` (native) / `budget_tokens=8192` (preset). Explicit `reasoning_budget` wins over `budget_tokens`. | Cap it to bound latency/cost on chatty reasoners. |
| `--reasoning-effort` | Reasoning effort level (`none`/`low`/`medium`/`high`/`xhigh`). | Higher = more (and slower) thinking. | `none` (toggle). | Raise for hard problems on effort-aware models. |
| `--no-reasoning-preserve` | Stop re-emitting the reasoning trace every turn. | b11003+ preserves (re-sends) reasoning by default, wasting tokens. | off (toggle; app sends it to disable). | Keep on to save tokens. |

## Speculative decoding (MTP / NextN)

| Flag | What it does | Impact | Default | When to change |
|------|--------------|--------|---------|----------------|
| `--spec-type` | Speculation mode. App sends `draft-mtp`. | Enables MTP/NextN draft speculation (~4–5% decode gain with `draft-mtp`). | off (toggle). | On for models with a MTP head. |
| `--spec-draft-model` | Sidecar draft model path (`mtp-*.gguf`), if not embedded. | Required when the head isn't in the main file. | — (toggle; auto-detected or manual). | Only when the model has an external MTP sidecar. |
| `--spec-draft-n-max` | Draft tokens per step. | More = more accepted tokens, up to the head's capacity. | `5` (toggle). | Leave at 5. |
| `--spec-draft-type-k` | Draft KV K quant. | `q8_0` ≈ half the VRAM of the build default `f16`, near-identical acceptance. | `q8_0` (toggle). | Keep `q8_0`. Subject to the FA symmetric-pair rule too. |
| `--spec-draft-type-v` | Draft KV V quant. | Same as K. | `q8_0` (toggle). | Pair with K. |

## Vision / LoRA

| Flag | What it does | Impact | Default | When to change |
|------|--------------|--------|---------|----------------|
| `--mmproj` | Vision encoder (mmproj), sidecar or embedded. | Enables image input. | — (path field). | Only for vision models that need an external mmproj. |
| `--lora` | LoRA adapter path. | Adds the adapter at load. | — (path field). | Only when using a LoRA. |
| `--lora-scale` | LoRA strength (0.0–2.0). | Scales the adapter's effect. | `1.0`. | Tune for the adapter you load. |

## Sampling (server defaults for all clients)

When passed as launch flags these become the **defaults for every client** that
doesn't override them.

| Flag | What it does | Range | Default (app presets) |
|------|--------------|-------|-----------------------|
| `--temp` | Sampling temperature. | 0–2 | `0.7` (instruct) · `1.0` (thinking) |
| `--top-p` | Nucleus sampling cutoff. | 0–1 | `0.80` (instruct) · `0.95` (thinking) |
| `--top-k` | Keep top-k tokens. `0` = disabled. | ≥ 0 | `20` (both presets) |
| `--min-p` | Drop tokens below p = min_p × top token. | 0–1 | `0.0` (both presets) |
| `--presence-penalty` | Penalize already-present tokens. | 0–2 | `0.3` (instruct) · `0.0` (thinking) |
| `--repeat-penalty` | Penalize repeated tokens. | 1–2 | `1.1` (instruct) · `1.0` (thinking) |

> High penalties (repeat/presence ≥ 1.5) **degrade Qwen-family output**,
> especially code. Keep them low.

## Server / process

| Flag | What it does | Impact | Default | When to change |
|------|--------------|--------|---------|----------------|
| `--parallel` | Number of parallel slots. | Each slot reserves KV (use `--kv-unified` to share). | `1` (field, 1–8). | Raise for concurrent users; pair with `--kv-unified`. |
| `--threads` | CPU threads. `-1`/`0` = auto (flag omitted). | Tune for your core count. | `-1` (auto; flag omitted). | Only if auto under/overshoots your cores. |
| `--api-key` | Bearer key required to call the server. | `SECRET_FLAG` — never logged. | `""` (off). | Set if you expose the port beyond localhost. |
| `--sleep-idle-seconds` | Free VRAM after N s idle. `0` = off. | Lets other tasks use the GPU. | `0` (off; toggle). | Set to reclaim VRAM between sessions. |
| `--no-warmup` | Skip the empty warmup run at startup. | On by default the app does a warmup; this disables it. | warmup **on** (app sends `--no-warmup` only when OFF). | Leave on (warmup makes the first reply faster). |
| `--lazy-mode` | On-demand loading of large tensors. `auto` = only >4 GiB. | Trades a little startup latency for less upfront I/O. | `auto` (toggle). | Leave `auto`. |

## Validations (422 before launch)

The app rejects contradictory configs up front:

- `gpu_only` requires `n_gpu_layers ≠ 0`; `cpu_only` forces `0`.
- With `flash_attn=on`, K/V (and draft K/V) must be a symmetric whitelisted pair.
- `lora_scale` 0–2 · `n_parallel` 1–8 · `cache_reuse` 0–256.
- `checkpoint_min_step` ≥ 512 · `cache_ram_mib` ≥ 0 · `fit_target_mib` ≥ 0.
- Sampling within the ranges above.

Old saved configs with removed KV types or flags (e.g. `q4_1`, `use_mlock`) are
migrated silently with a warning so a stale template never breaks a launch.
