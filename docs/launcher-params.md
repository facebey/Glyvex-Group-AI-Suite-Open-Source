# Parámetros de lanzamiento — referencia

Los parámetros que el Launcher (M2) maneja para `llama-server`, con su flag
de llama.cpp y el default de la app (schema `LaunchConfig` en
`backend/launcher.py`, la fuente de verdad).

Dos conceptos que se repiten:

- **Toggle / `None`** — varios campos son opcionales: cuando el toggle está
  OFF (valor `None` o el default "desactivado") el builder **no emite el
  flag** y llama-server usa el default de la build. Útil para benchmarks
  finos donde no querés que la app opine.
- **Probe de capacidades** — al primer lanzamiento por build, el Launcher
  lee `--help` del binario y **descarta los flags que esa build no conoce**
  (con warning en el log). Por eso una config válida puede lanzar igual
  contra builds viejas o nuevas, tirando el flag inexistente en vez de
  romperse.

## Núcleo

| Campo | Flag | Default app | Notas |
|-------|------|-------------|-------|
| `model_id` | — | — | GGUF del inventario (M1). |
| `backend` | — | `llama_server` | `llama_server` \| `ollama` \| `lm_studio`. |
| `n_ctx` | `-c` | 65536 | Toggle: `None` = default de la build. |
| `n_batch` | `-b` | 2048 | Medido: pp2048 ~1400 t/s en GPUs anchas; 512 rinde bastante menos en prompt processing. |
| `n_ubatch` | `--ubatch-size` | 512 | Toggle. Se deja en 512 para no inflar el compute buffer. |
| `n_gpu_layers` | `-ngl` | -1 (todas) | `gpu_mode="cpu_only"` lo fuerza a 0. |
| `gpu_mode` | — | `gpu_only` | `gpu_only` \| `cpu_only` \| `hybrid`. `gpu_only` + `n_gpu_layers=0` es inválido (usar `-1` o hybrid). |
| `cache_type_k` / `cache_type_v` | `--cache-type-k/-v` | `q4_0` | Con Flash Attention **solo pares simétricos**: `q4_0-q4_0`, `q8_0-q8_0`, `f16-f16`, `bf16-bf16`. Cualquier otra combinación termina en crash o fallback silencioso a f16. |
| `flash_attn` | `--flash-attn` | `on` | Acepta `on`/`off`/`auto` (no 1/0). |
| `load_mode` | `--load-mode` | `auto` | `auto` \| `none` \| `mmap` \| `mlock` \| `mmap+mlock` \| `dio`. Reemplaza a los viejos `--mlock`/`--no-mmap`. |
| `mmproj_path` | `--mmproj` | — | Encoder de visión (sidecar o embebido). |
| `lora_path` / `lora_scale` | `--lora` | — / 1.0 | `lora_scale` entre 0.0 y 2.0. |

## Speculative decoding (MTP / NextN)

| Campo | Flag | Default app | Notas |
|-------|------|-------------|-------|
| `mtp_draft_model` | `--spec-draft-model` | — | Sidecar `mtp-*.gguf` detectado o path manual. |
| `mtp_embedded` | — | `False` | El modelo trae los tensores `blk.N.nextn.*` (lo detecta el scanner de M1). |
| `n_draft` | `--spec-draft-n-max` | 5 | Tokens draft por paso. |
| `cache_type_k/v_draft` | `--spec-draft-type-k/-v` | `q8_0` | KV del draft. Default q8_0: mitad de VRAM que f16 (el default de llama-server) con aceptación prácticamente idéntica. Con FA también cae bajo la whitelist de pares simétricos. |

## Razonamiento / thinking

| Campo | Cómo viaja | Default app | Notas |
|-------|-----------|-------------|-------|
| `thinking_enabled` | `--reasoning` `on\|off` | `False` | Flag nativo: el kwarg `enable_thinking` en `--chat-template-kwargs` quedó deprecado en llama.cpp (warning en el log del server). Solo se emite si el modelo lee `enable_thinking` y con `--jinja` activado. El otro lever es el prefijo `/think` del chat. |
| `budget_tokens` | `--reasoning-budget` | 8192 | Solo con thinking enabled y si `reasoning_budget` es -1 (ese campo, al ser explícito, tiene prioridad). `-1` = sin límite (no se envía). |
| `jinja` | `--jinja` | `True` | |
| `reasoning_effort` | — | `none` | `none` \| `low` \| `medium` \| `high` \| `xhigh`. |
| `no_reasoning_preserve` | — | `False` | b11003 activa el preservado por defecto y gasta tokens re-emitiendo el razonamiento cada turno; esto lo apaga. |
| `reasoning_budget` | `--reasoning-budget` | -1 | Token budget nativo del server (b11009). `-1` = sin límite (no se emite), `0` = fin inmediato del thinking. Tiene prioridad sobre `budget_tokens`. |

## Sampling (defaults del servidor)

Cuando se pasan como flags de arranque, llama-server los usa como defaults
para **todos** los clientes que no especifiquen los suyos.

Preset `thinking`: temp 1.0 · top_p 0.95 · top_k 20 · min_p 0.0 ·
presence 0.0 · repeat 1.0

Preset `instruct` (default): temp 0.7 · top_p 0.80 · top_k 20 · min_p 0.0 ·
presence 0.3 · repeat 1.1

> Ojo con penalties altos: repeat_penalty 1.5 / presence 1.5 **degeneran la
> salida en la familia Qwen** (especialmente código).

Rangos válidos: temp 0–2 · top_p 0–1 · top_k ≥ 0 (0 = deshabilitado) ·
min_p 0–1.

## RoPE scaling (toggle agrupado)

OFF = los tres campos `None` → ni un solo flag.

| Campo | Default | Notas |
|-------|---------|-------|
| `rope_freq_base` | 0.0 (= auto) | |
| `rope_scaling_type` | `none` | `none` \| `linear` \| `yarn` |
| `yarn_ext_factor` | -1.0 (= auto) | |

## Checkpoints de contexto (VRAM en arquitecturas híbridas/SSM)

Cada checkpoint copia el estado recurrente: **~150 MiB de VRAM** en
arquitecturas híbridas (SSM). Con ctx largos son el componente que domina el
consumo post-carga. Los defaults de la build (32 / 8192) pueden llegar a
~2.4 GiB extra en un ctx de 128k; los de la app los acotan:

| Campo | Flag | Default app | Default build |
|-------|------|-------------|---------------|
| `ctx_checkpoints` | `-ctxcp` | 8 | 32 |
| `checkpoint_min_step` | `-cms` | 16384 | 8192 |

Con `-cms 16384` el reproceso máximo al regenerar un mensaje viejo es ~16k
tokens (~12–16 s a pp ~1300 t/s), casi nunca perceptible.

## Prompt cache (RAM del sistema, no VRAM)

| Campo | Flag | Default app | Notas |
|-------|------|-------------|-------|
| `cache_ram_mib` | `--cache-ram` | 8192 | Límite del caché de prompts ociosos en RAM. 0 lo desactiva. Con 64 GB de RAM sobra para subirlo y acelerar el switch entre conversaciones. |

## Ajuste automático de VRAM

| Campo | Flag | Default app | Notas |
|-------|------|-------------|-------|
| `fit_target_mib` | `--fit-target` | 0 (off) | >0: llama.cpp reserva ese margen de VRAM y auto-reduce lo que haga falta (ctx incluido). Ideal para cargar modelos arbitrarios sin conocer su tamaño. |
| `fit` | `--fit` | `on` | Ajusta los argumentos que el usuario **no** fijó para caber en la memoria del dispositivo (default de b11009: on). |

## Optimizaciones avanzadas

| Campo | Flag | Default app | Notas |
|-------|------|-------------|-------|
| `numa` | `--numa` | off | El flag EXIGE valor: `distribute` \| `isolate` \| `numactl`. |
| `no_kv_offload` | `--no-kv-offload` | off | |
| `cache_reuse` | `--cache-reuse` | 0 | 0–256. |
| `defrag_thold` | `--defrag-thold` | -1 (off) | |
| `kv_unified` | `--kv-unified` | off | Sin esto y con `n_parallel > 1`, **cada slot reserva su propio ctx completo** (× N de VRAM de KV). |
| `kv_unified_per_slot` | `--kv-unified-per-slot` | 0 | 0 = sin límite (cada slot usa el `n_ctx` global). Solo con `kv_unified`. |
| `sleep_idle_seconds` | `--sleep-idle-seconds` | 0 (off) | El server se "duerme" (libera VRAM) tras N s de inactividad. |
| `warmup` | `--warmup` / `--no-warmup` | on | Corrida vacía al arrancar. |
| `lazy_mode` | `--lazy-mode` | `auto` | Lectura bajo demanda de tensores grandes. `auto` = solo >4 GiB. |
| `grp_attn_n` / `grp_attn_w` | (group attention) | 1 / 512 | Flag deprecado/removido en varias builds: el probe lo descarta con warning si el binario no lo conoce. |
| `n_parallel` | `--parallel` | 1 | 1–8. |
| `n_threads` | `-t` | -1 (auto) | -1 o 0 = no se pasa el flag. |

## Modo automático

`auto_mode = True` → comando estricto: solo modelo + puerto (+ host si difiere
de 127.0.0.1). Todo lo demás usa el default de la build. Se conserva la
infra de la app (`--verbosity` para logs, `--metrics` para el Monitor).

## General

| Campo | Default | Notas |
|-------|---------|-------|
| `host` | 127.0.0.1 | |
| `port` | 8080 | El puerto default del server migrará de 8080 a 9931 en el futuro: nunca hardcodear, siempre salir de `cfg.port` / config de backends. |
| `api_key` | `""` | |
| `log_file` | `data/logs/llama-server.log` | |
| `template_name` | — | Template de hardware (predefinidos o custom, persistidos en SQLite). |

## Validaciones cruzadas (resumen)

La app rechaza configs contradictorias antes de lanzar (422):

- `gpu_only` exige `n_gpu_layers ≠ 0` · `cpu_only` fuerza 0
- Con `flash_attn=on`, KV K/V (y del draft) en pares simétricos de la whitelist
- `lora_scale` 0–2 · `n_parallel` 1–8 · `cache_reuse` 0–256
- `checkpoint_min_step` ≥ 512 · `cache_ram_mib` ≥ 0 · `fit_target_mib` ≥ 0
- Sampling dentro de rangos (ver arriba)

Configuraciones guardadas con tipos de KV o flags removidos (p. ej. `q4_1`,
`use_mlock`) se migran en silencio con warning: un template viejo no rompe el
lanzamiento.
