# Troubleshooting — common problems

> **Language:** [Español](TROUBLESHOOTING.es.md)

Real cases with the diagnosis behind them (verified against the launcher code
and the llama.cpp source).

## Warning "enable_thinking via --chat-template-kwargs is deprecated" in the log

**Symptom.** The `llama-server` log shows:
`Setting 'enable_thinking' via --chat-template-kwargs is deprecated. Use --reasoning on / --reasoning off instead.`

**What happens.** The Launcher passes `thinking_enabled` via
`--chat-template-kwargs '{"enable_thinking": ...}'` (only when the model's chat
template reads it, detected from the GGUF header, and with `--jinja`
on). In recent llama.cpp builds that path has been deprecated in favor of the
native flags (`--reasoning on/off`), but **it still works**:
it is a warning, not an error.

**What to do.** Nothing urgent: thinking keeps working. The app already uses the
native flags where they exist (`--reasoning-effort`, `--reasoning-budget`,
`--no-reasoning-preserve`); migrating `thinking_enabled` to `--reasoning on/off`
is a pending improvement on the launcher.

**Variant — "thinking does nothing":**
- The model does not support `enable_thinking` (the toggle is not enabled if
  the header scanner does not detect it).
- `--jinja` is off: in that case the backend does not send the kwargs and
  reports it in its own log (`thinking_enabled/budget_tokens requieren --jinja`).
- The template does not read that particular kwarg: llama.cpp ignores it
  silently (best-effort by design).

## `--cache-idle-slots` disables itself (requires `--cache-ram`)

**Symptom.** With `--cache-ram` at 0, the llama-server log shows:
`--cache-idle-slots requires --cache-ram, disabling` — and idle slot
persistence does not work.

**What happens.** `cache_idle_slots` is **on by default** in
llama-server, but it depends on the in-RAM prompt cache: when a new task
starts, idle slots are saved into the prompt cache (and are cleaned if there
is `--kv-unified`; without it, the KV stays in VRAM and only the RAM cache
copy is published). Without `--cache-ram` there is no RAM cache to save them
to, so the server disables the flag with a warning.

**What to do.** Keep `--cache-ram > 0` (the app default is 8192 MiB). If
you set `cache_ram_mib = 0` in a template or config, know that you lost prompt
re-use: every conversation switch reprocesses the full prompt
(higher TTFT).

## MTP / speculative decoding consumes more VRAM than expected

**Symptom.** With MTP enabled a model that used to fit no longer fits, or
VRAM spikes.

**What happens.** MTP adds two things on top of the main model:
1. The **draft weights** (sidecar `mtp-*.gguf`, or the embedded tensors
   `blk.N.nextn.*`).
2. Its **own KV cache** — and llama-server puts it in **f16 by default**,
   even if the main model is q4_0.

**What to do.**
- The app already uses **q8_0** by default for the draft KV
  (`--spec-draft-type-k/-v`): half the VRAM of f16 with practically
  identical acceptance. Check you have not raised it to f16 in the
  template.
- Lower `n_draft` (`--spec-draft-n-max`, default 5) if you use
  speculation little.
- Check the VRAM estimate before launching (the app's estimate
  endpoint) or the Monitor after launch.
- If you are on a build earlier than b11007: updating the binary brings the
  CUDA graph recapture fix with MTP (~4–5% extra in decode), without
  changing flags.

## The conversation exceeds the context (context overflow)

**Symptom.** The UI warns **before sending**: the composer shows "This send
exceeds the model context by about N tokens" (and the metrics bar shows the
context usage and the margin after the response). If you send it anyway,
llama-server responds 400 and the chat shows
`Upstream 400: ... exceeds n_ctx ...`.

**What happens.** Every message is added to the full prompt that gets resent
(system + history + attachments + tool calls). The llama.cpp check is not
`prompt > n_ctx` but **`prompt + max_tokens > n_ctx`**: the chat's
`max_tokens` default is 4096, so a "nearly full" conversation is already
rejected. A large attachment or many tools can eat tens of k
tokens at once.

**What to do.**
- **Start a new conversation** for the new task: it is the cheapest
  way out.
- **Lower `max_tokens`** in the chat settings (default 4096): it frees the
  margin the server reserves for the response.
- Raise `n_ctx` if VRAM allows (see `--fit-target`: llama.cpp reserves
  a margin and auto-reduces the ctx so the model keeps fitting).
- Review attachments: a 100-page PDF does not fit in any reasonable
  context; summarize it or send only the section.
- In hybrid architectures (SSM), context checkpoints also
  cost VRAM (~150 MiB each): if you raise ctx, consider lowering
  `ctx_checkpoints` or raising `checkpoint_min_step`.

## The launcher drops flags on different builds

**Symptom.** Warnings in the log like "the binary does not know this flag;
dropping it" when launching.

**What happens.** The launcher does a **capability probe**: it reads `--help`
once per `llama-server` build and drops the flags that build does not
know, instead of breaking the launch. It is the mechanism that allows running
the app against old or new builds without changes.

**What to do.** It is not an error: the feature that depended on that flag
falls back to the build's default. If you need it (context checkpoints,
`--fit-target`, native reasoning, etc.), update the
llama-server binary. Deprecated flags like group attention are dropped this
way on builds that removed them.

## Crash or silent fallback with Flash Attention and the KV cache

**Symptom.** Crash at startup, `FA_QUANTS` message in the log, or the server
falls back to f16 without warning.

**What happens.** With `--flash-attn on`, llama.cpp only accepts **symmetric
pairs** of KV cache: `q4_0-q4_0`, `q8_0-q8_0`, `f16-f16`,
`bf16-bf16`. Any other combination is invalid with FA.

**What to do.** The app validates this before launching (rejects with a 422
that says which pair you got and how to fix it). If you added custom flags,
make the K and V types equal or disable `--flash-attn`.

## Parallel slots: each one reserves its full context

**Symptom.** With `n_parallel > 1` VRAM goes through the roof even though there
are not several users.

**What happens.** Without `--kv-unified`, **each slot reserves its own full
`n_ctx`**: N slots = N × the KV VRAM of one context.

**What to do.** Enable `--kv-unified` (unifies the KV across slots) and use
`--kv-unified-per-slot` to cap the context per slot if you do not need the
full global one in each.

## The model does not fit in VRAM

**What to do, in order of cost:**
1. `--fit-target <MiB>`: you ask llama.cpp to reserve that margin and
   auto-reduce whatever is needed (ctx included). It is the control designed
   to load arbitrary models without knowing their size.
2. `--fit on` (the app default since b11009): it only adjusts the
   arguments you did **not** set.
3. KV cache in q4_0 (the app default) instead of f16.
4. Lower `n_ctx` / `ctx_checkpoints` / raise `checkpoint_min_step`.
5. Change quantization: a Q4_K_XL that does not fit, an IQ3/IQ2 will (with
   the quality loss of each).
