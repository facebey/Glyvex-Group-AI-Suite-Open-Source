# Verified models

Models that have been **tested on real hardware** with the Suite (stability
runs, memory breakdown and M4 benchmarks) and for which a recommended launch
configuration exists. This document is the registry: each entry states exactly
what was verified, on which hardware and with which runtime build.

Why this exists: a model family is not "tested" as a whole — what is tested is
a **model + quantization + configuration + hardware**. If you are about to
download a quantization and want to know whether it is proven with the Suite,
check here first.

## Levels

| Level | Meaning |
|---|---|
| **Verified** | Ran stability sessions (chat, tools, long context) without crashes, with the memory breakdown recorded. |
| **Optimized** | Verified **and** tuned: launch configuration with measured results (M4 benchmarks) and documented recommendations. |
| **Candidate** | Installed in the reference environment but without a verification record yet. It carries no claim. |

Entries are append-only: an entry is never deleted, only updated with new
information. Every entry states the runtime build it was verified against;
after a runtime version bump, entries affected by the bump are revalidated
before being listed as current.

## Verified & optimized

| Model | Quantization (tested) | Hardware | Runtime | Notes |
|---|---|---|---|---|
| Gemma 4 26B A4B (MoE) | Unsloth Dynamic `UD-Q4_K_XL` + attached MTP assistant + `mmproj-F16` vision | NVIDIA RTX 3090 24 GB | llama.cpp b11827 | Chat, vision and tools at 131,072 ctx; FlashAttention on; draft MTP (`--spec-type draft-mtp`); full layer offload |
| Qwen3-Coder-30B-A3B (MoE) | Unsloth Dynamic `UD-Q4_K_XL` | NVIDIA RTX 3090 24 GB | llama.cpp b11827 | Coding-focused model; benchmarked against a dense 27B model with the Suite's M4 protocol; KV cache `q8_0` |
| Qwen3-1.7B | Unsloth Dynamic `UD-Q4_K_XL` | RTX 3090 24 GB and pure CPU | llama.cpp b11827 | Light utility model: fast chat on CPU and smoke tests of the full pipeline |

### Gemma 4 26B A4B — `UD-Q4_K_XL` (verified, optimized)

- **Files tested**: `gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf` (≈15.8 GiB, 5.39 BPW),
  attached MTP assistant `mtp-gemma-4-26B-A4B-it.gguf` (≈440 MB) and vision
  encoder `mmproj-F16.gguf` (≈1.1 GiB). The Suite's scanner (M1) detects the
  MTP/mmproj pair and groups it with the base model automatically.
- **Configuration proven**: `-fa on` with KV cache `q8_0`; GPU offload to the
  limit the memory fit (M6) allows — reached 31/31 layers offloaded;
  `-c 131072`; speculative decoding with the official attached assistant
  (`--spec-type draft-mtp`).
- **Measured behavior**: ≈21.6 GB VRAM total with draft + vision active at
  131,072 ctx; stable generation with tools and image input. Because Gemma 4
  is hybrid (sliding-window attention mixed with full attention), the fit
  estimate reserves memory per layer type — this is computed from the model's
  own GGUF metadata (SWA-aware since schema v7).
- **Evidence**: benchmark runs recorded in-app (M4 → Reports) and memory
  breakdowns from those runs on the hardware stated above.

### Qwen3-Coder-30B-A3B — `UD-Q4_K_XL` (verified, optimized)

- **Files tested**: `Qwen3-Coder-30B-A3B-Instruct-UD-Q4_K_XL.gguf` (≈16.5 GiB).
- **Configuration proven**: `-fa on` with KV cache `q8_0`; GPU offload to fit
  on 24 GB; standard sampling for coding tasks. Long-context coding sessions
  verified.
- **Measured behavior**: MoE with 3B active params per token: full model does
  not fit in 24 GB at high context with `q8_0` KV — the Suite's fit estimate
  (M6) handles the split automatically. Selected as the reference coding model
  after a head-to-head benchmark against a dense 27B model scored task by task.
- **Evidence**: M4 benchmark runs recorded in-app (Reports) comparing quality
  per coding task and tokens/s on both candidates.

### Qwen3-1.7B — `UD-Q4_K_XL` (verified)

- **Files tested**: `Qwen3-1.7B-UD-Q4_K_XL.gguf` (≈1.1 GiB).
- **Configuration proven**: runs on CPU only on a modern desktop (no GPU
  needed) and on GPU for fast responses. Used for smoke tests of the full
  pipeline (chat, streaming metrics, tools) and as a low-latency utility model.
- **Evidence**: stability runs on both CPU and GPU configurations.

### Previously verified (not currently installed)

- **Qwen3.8-27B** (dense) with embedded NextN/MTP head inside the `.gguf` —
  extensively tested and tuned on RTX 3090 (MTP drafting, VRAM behavior); the
  embedded-MTP detection feature grew out of it. No longer on disk; if it is
  reinstalled it re-enters this registry under its own entry.

## Candidates (installed, not verified yet)

These quantizations are present in the reference environment but have no
verification record. They make no claim until they pass the process below.

- Gemma 4 31B — `Q4_0` QAT + `mmproj BF16`
- Gemma 4 E4B — `Q4_K_M` + `mmproj BF16`
- Mistral Small 3.2 24B — `UD-Q4_K_XL` + `mmproj BF16`
- Qwen2.5-Coder-7B — `q8_0`
- Agents-A1 4B — `Q8_0` + `mmproj`
- EmbeddingGemma 300M — `Q8_0` (embeddings)

## How to add a new verified model

1. Install the model and let the Inventory (M1) index it.
2. Run stability sessions: chat with tools, long context, vision if it ships
   `mmproj`; no crashes over multiple sessions.
3. Record the memory breakdown (VRAM used, layers offloaded, KV size) for the
   context size you recommend.
4. Run M4 benchmarks (the Benchmark module) to tune and document the
   configuration.
5. Add the entry here using the template below, in **both language versions**
   of this document, in the same commit.

Template:

```markdown
### <model name> — <quantization> (verified | optimized)

- **Files tested**: <exact .gguf names and approximate sizes>
- **Configuration proven**: <flags the Suite emits for this model>
- **Measured behavior**: <VRAM, layers offloaded, usable ctx, approx. tokens/s>
- **Evidence**: <M4 benchmark runs / memory breakdowns on <hardware>>
- **Verified against**: llama.cpp <build> · <date>
```

> Note: entries state the runtime build used for the record. The registry
> describes what the project has measured; it does not promise identical
> numbers on other hardware — it promises the configuration is proven to work
> on the hardware stated.
