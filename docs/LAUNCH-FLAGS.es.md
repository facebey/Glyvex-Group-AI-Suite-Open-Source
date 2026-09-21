# Referencia de flags de lanzamiento

Todos los flags de `llama-server` que el Launcher (M2) puede emitir, en
lenguaje claro: qué hace cada uno, su impacto, el default de la app y cuándo
cambiarlo. La fuente de verdad es `build_llama_server_command()` en
[`backend/launcher.py`](../backend/launcher.py); esta doc es su versión en
texto plano (la referencia campo por campo, en español, es
[`launcher-params.md`](launcher-params.md)).

El mismo texto de ayuda que se muestra bajo cada control del Launcher sale del
`--help` del propio binario (el probe de `/api/launcher/backend-info`), así que
lo que leés ahí siempre coincide con la build que tenés instalada.

## Cómo maneja la app los flags

- **Toggle OFF = flag omitido.** La mayoría de los campos son opcionales: cuando el
  toggle está OFF (valor `None` / el default "desactivado") el builder **no emite el
  flag** y `llama-server` usa el **default de la build**. Es justo lo que querés en
  benchmarks finos: que la app no imponga su opinión.
- **Probe de capacidades.** Al primer lanzamiento por build, el Launcher lee el
  `--help` del binario y **descarta los flags que esa build no conoce** (con warning
  en el log). Por eso una config válida lanza igual contra builds viejas o nuevas: el
  flag inexistente se tira en vez de romper el launch.
- **Modo automático.** `auto_mode = True` envía un comando estricto y mínimo — modelo
  + puerto (+ host si difiere de `127.0.0.1`) — y deja que la build decida todo lo
  demás. Se conserva la infra de la app: `--verbosity` (logs) y `--metrics`
  (Monitor).

Los defaults de abajo son los **defaults de la app** (lo que el Launcher pone).
Donde el default de la build difiere, se nota en la columna de Impacto o Default.

## Núcleo (siempre se envía)

| Flag | Qué hace | Impacto | Default | Cuándo cambiarlo |
|------|----------|---------|---------|------------------|
| `--model` | GGUF a cargar. | Obligatorio. | Del inventario (M1). | Siempre: elegís el modelo. |
| `--port` | Puerto TCP donde escucha el server. | Ninguno. | `8080`. | Solo si otra cosa lo usa. Nunca hardcodear: el default migrará de 8080; siempre salir de config. |
| `--host` | Dirección de bind. | Ninguno con `127.0.0.1`. | `127.0.0.1` (se omite si es ese). | Solo para exponer el server en la LAN. |
| `--verbosity` | Nivel de log del server. | Ninguno. | `1` (fijo de la app). | No cambiar. |
| `--metrics` | Expone `/metrics` para el Monitor. | Ninguno. | on (fijo de la app). | No cambiar. |

## Contexto

| Flag | Qué hace | Impacto | Default | Cuándo cambiarlo |
|------|----------|---------|---------|------------------|
| `--ctx-size` | Máximo de tokens de contexto (prompt + generación) que el server mantiene. | Más grande = más VRAM (KV cache) y más estado de checkpoints/SSM. Más chico = más rápido, menos historial. | `65536` (toggle; OFF = default de la build = ctx nativo del modelo). | Subilo para docs largas / chats grandes; bajalo para ahorrar VRAM en modelos chicos. |

## Batching

| Flag | Qué hace | Impacto | Default | Cuándo cambiarlo |
|------|----------|---------|---------|------------------|
| `--batch-size` | Máx. de tokens procesados por paso de prompt processing. | Más grande = pp más rápido en GPUs anchas. | `2048` (toggle; OFF = default de la build). | Dejá 2048 en GPU; 512 rinde bastante menos en pp. |
| `--ubatch-size` | Tamaño del compute buffer por paso. | Más grande ayuda en CPU; más de lo necesario solo infla memoria. | `512` (toggle; OFF = default de la build). | Dejá en 512 salvo que estés perfilando una build CPU. |

## Offload GPU

| Flag | Qué hace | Impacto | Default | Cuándo cambiarlo |
|------|----------|---------|---------|------------------|
| `--n-gpu-layers` | Cuántas capas del modelo se mandan a la GPU. `-1` = todas. | Más capas en GPU = mucho más rápido, pero más VRAM. `0` = solo CPU. | `-1` (todas). `gpu_mode="cpu_only"` lo fuerza a `0`. | Bajalo hasta que el modelo quepa en VRAM; `-1` si quepa. |
| `--flash-attn` | Flash Attention (reduce memoria de KV + acelera el decode). | on = menos VRAM de KV, más rápido. | `on` (toggle). | Dejalo on. Con FA los tipos de KV de abajo tienen que ser pares simétricos. |
| `--load-mode` | Cómo se mapea el archivo del modelo a memoria (`auto`/`none`/`mmap`/`mlock`/`mmap+mlock`/`dio`). | `mlock`/`mmap+mlock` fijan el archivo en RAM (sin re-leer); `mmap` carga perezoso. | `auto` (toggle; OFF = default de la build). | `mlock` si querés el modelo entero residente; dejá `auto` en lo demás. |
| `--numa` | Política NUMA. Exige valor: `distribute`/`isolate`/`numactl`. | Solo importa en hosts NUMA de varios sockets. | off (toggle; la app envía `distribute`). | Solo en máquinas NUMA. |
| `--no-kv-offload` | Mantiene el KV cache en CPU, no en GPU. | Libera VRAM de GPU a cambio de velocidad. | off (toggle). | Solo si estás justo de VRAM y podés aguantar un decode más lento. |

## KV cache

| Flag | Qué hace | Impacto | Default | Cuándo cambiarlo |
|------|----------|---------|---------|------------------|
| `--cache-type-k` | Cuantización del cache K. | Menor precisión = menos VRAM. Con FA, K y V deben formar un par simétrico. | `q4_0` (toggle; OFF = default de la build `f16`). | Dejá `q4_0`/`q8_0` para ahorrar VRAM. Pares válidos con FA: `q4_0-q4_0`, `q8_0-q8_0`, `f16-f16`, `bf16-bf16`. |
| `--cache-type-v` | Cuantización del cache V. | Igual que arriba. | `q4_0` (toggle; OFF = default de la build `f16`). | Emparejalo con `--cache-type-k`. |
| `--cache-reuse` | Reusa el KV de prefijos cacheados entre requests. | Más alto = más reuso, un poquito más de bookkeeping. `0` = off. | `0` (toggle). | Subilo (0–256) si repetís system prompts largos. |
| `--kv-unified` | Comparte un solo pool de KV entre slots paralelos. | Sin esto, cada slot reserva su ctx completo (× N de VRAM). | off (toggle). | On cuando `--parallel > 1` para evitar N× ctx de VRAM. |
| `--kv-unified-per-slot` | Tope de contexto por slot con `--kv-unified` activo. `0` = sin límite (usa el `--ctx-size` global). | Limita VRAM por slot. | `0` (toggle). | Ponelo con `--kv-unified` para acotar la memoria por slot. |
| `--defrag-thold` | Umbral de defragmentación del KV. | Marcado **DEPRECATED** en builds recientes; se sigue enviando por ahora. | `-1` (off; toggle). | Dejalo off; el probe lo tira si la build lo quitó. |

## Memoria y auto-fit

| Flag | Qué hace | Impacto | Default | Cuándo cambiarlo |
|------|----------|---------|---------|------------------|
| `--fit` | Auto-ajusta los argumentos que no fijaste para que el modelo quepa en el dispositivo (ctx incluido). | on = menos crashes de "no quepa" en modelos arbitrarios. | `on` (toggle; default de la build on en b11009). | Dejalo on. |
| `--fit-target` | Margen de VRAM (MiB) a reservar por dispositivo. `0` = off. | `>0` reserva ese margen y reduce lo que haga falta. | `0` (toggle). | Ponelo `>0` si corré otras cosas en la GPU. |
| `--cache-ram` | Tope del caché de prompts ociosos en **RAM del sistema** (no VRAM). | Acelera el switch entre conversaciones; `0` lo desactiva. | `8192` (toggle). | Subilo si tenés mucha RAM (p. ej. 64 GB) y cambiás de chat seguido. |

## Checkpoints de contexto (modelos híbridos / SSM)

| Flag | Qué hace | Impacto | Default | Cuándo cambiarlo |
|------|----------|---------|---------|------------------|
| `--ctx-checkpoints` | Máx. checkpoints de contexto por slot. | Cada uno copia el estado recurrente: **~150 MiB de VRAM** en modelos SSM/híbridos. En 128k los defaults de la build (32/8192) pueden sumar ~2.4 GiB. | `8` (app; default de la build `32`; toggle). | El default de la app (8) ya lo acota; bajalo más si la VRAM aprieta. |
| `--checkpoint-min-step` | Espaciado mínimo (tokens) entre checkpoints. | Más grande = menos checkpoints, más lento el re-prompt de mensajes viejos. | `16384` (app; default de la build `8192`; toggle). | ~16k ≈ 12–16 s de re-procesado en el peor caso a ~1300 t/s — casi nunca perceptible. |

## Escalado RoPE

| Flag | Qué hace | Impacto | Default | Cuándo cambiarlo |
|------|----------|---------|---------|------------------|
| `--rope-scaling` | Tipo de escalado de encoding posicional (`none`/`linear`/`yarn`). | Necesario para usar un modelo más allá de su largo entrenado. | `none` (toggle agrupado; OFF = sin flag). | Solo si extendés la ventana de contexto. |
| `--rope-freq-base` | Frecuencia base de RoPE. `0` = auto. | Ajusta el comportamiento a contexto largo con yarn. | `0.0` (= auto; toggle). | Solo junto a `--rope-scaling yarn`. |
| `--yarn-ext-factor` | Factor de extensión de YaRN. `-1` = auto. | Controla el rango de extrapolación. | `-1.0` (= auto; toggle). | Solo junto a `--rope-scaling yarn`. |

## Group attention

| Flag | Qué hace | Impacto | Default | Cuándo cambiarlo |
|------|----------|---------|---------|------------------|
| `--grp-attn-n` | Ventana de group attention (n). `1` = desactivado. | Deprecado/quitado según la build. | `1` (toggle). | Dejá desactivado. |
| `--grp-attn-w` | Ancho de group attention. | Solo cuando `--grp-attn-n > 1`. | `512` (toggle). | Dejá desactivado. |

> Ambos están marcados deprecados/quitados en varias builds: el probe los tira con
> warning si tu binario no los conoce.

## Razonamiento / thinking

| Flag | Qué hace | Impacto | Default | Cuándo cambiarlo |
|------|----------|---------|---------|------------------|
| `--jinja` / `--no-jinja` | Usa plantillas de chat Jinja (requerido para `--reasoning-effort`). | on = funciones completas de la plantilla. | `--jinja` (toggle). | Dejalo on en modelos de razonamiento. |
| `--reasoning` | Thinking nativo on/off (`on`/`off`). | Activa el camino de razonamiento del modelo. | `off` (toggle). Solo se emite si el modelo lee `enable_thinking` y `--jinja` está on. | El otro lever es el prefijo `/think` del chat. |
| `--reasoning-budget` | Presupuesto de tokens del paso de thinking. `-1` = sin límite (no se envía). | Acota cuánto piensa el modelo. | `reasoning_budget=-1` (nativo) / `budget_tokens=8192` (preset). El `reasoning_budget` explícito tiene prioridad sobre `budget_tokens`. | Acotalo para limitar latencia/costo en reasoners parlanchines. |
| `--reasoning-effort` | Nivel de esfuerzo de razonamiento (`none`/`low`/`medium`/`high`/`xhigh`). | Más alto = más (y más lento) thinking. | `none` (toggle). | Subilo para problemas difíciles en modelos que respetan effort. |
| `--no-reasoning-preserve` | Detiene la re-emisión de la traza de razonamiento cada turno. | b11003+ preserva (re-envía) el razonamiento por defecto, gastando tokens. | off (toggle; la app lo envía para desactivarlo). | Dejalo on para ahorrar tokens. |

## Decodificación especulativa (MTP / NextN)

| Flag | Qué hace | Impacto | Default | Cuándo cambiarlo |
|------|----------|---------|---------|------------------|
| `--spec-type` | Modo de especulación. La app envía `draft-mtp`. | Activa la especulación de borrador MTP/NextN (~4–5% de ganancia en decode con `draft-mtp`). | off (toggle). | On para modelos con cabeza MTP. |
| `--spec-draft-model` | Path del modelo draft sidecar (`mtp-*.gguf`), si no va embebido. | Requerido cuando la cabeza no está en el archivo principal. | — (toggle; auto-detectado o manual). | Solo si el modelo tiene un sidecar MTP externo. |
| `--spec-draft-n-max` | Tokens draft por paso. | Más = más tokens aceptados, hasta la capacidad de la cabeza. | `5` (toggle). | Dejá en 5. |
| `--spec-draft-type-k` | Cuant del KV K del draft. | `q8_0` ≈ mitad de la VRAM del default de la build `f16`, con aceptación prácticamente idéntica. | `q8_0` (toggle). | Dejá `q8_0`. También cae bajo la regla de pares simétricos con FA. |
| `--spec-draft-type-v` | Cuant del KV V del draft. | Igual que K. | `q8_0` (toggle). | Emparejá con K. |

## Visión / LoRA

| Flag | Qué hace | Impacto | Default | Cuándo cambiarlo |
|------|----------|---------|---------|------------------|
| `--mmproj` | Encoder de visión (mmproj), sidecar o embebido. | Habilita entrada de imágenes. | — (campo de path). | Solo en modelos de visión que necesitan un mmproj externo. |
| `--lora` | Path del adapter LoRA. | Añade el adapter al cargar. | — (campo de path). | Solo cuando usás un LoRA. |
| `--lora-scale` | Fuerza del LoRA (0.0–2.0). | Escala el efecto del adapter. | `1.0`. | Ajustala al adapter que cargues. |

## Sampling (defaults del servidor para todos los clientes)

Pasados como flags de arranque, `llama-server` los usa como **defaults para todos
los clientes** que no especifiquen los suyos.

| Flag | Qué hace | Rango | Default (presets de la app) |
|------|----------|-------|------------------------------|
| `--temp` | Temperatura de muestreo. | 0–2 | `0.7` (instruct) · `1.0` (thinking) |
| `--top-p` | Corte de muestreo por núcleo. | 0–1 | `0.80` (instruct) · `0.95` (thinking) |
| `--top-k` | Mantiene el top-k de tokens. `0` = deshabilitado. | ≥ 0 | `20` (ambos presets) |
| `--min-p` | Descarta tokens por debajo de p = min_p × token top. | 0–1 | `0.0` (ambos presets) |
| `--presence-penalty` | Penaliza tokens ya presentes. | 0–2 | `0.3` (instruct) · `0.0` (thinking) |
| `--repeat-penalty` | Penaliza tokens repetidos. | 1–2 | `1.1` (instruct) · `1.0` (thinking) |

> Penalties altos (repeat/presence ≥ 1.5) **degradan la salida de la familia Qwen**,
> especialmente código. Mantenelos bajos.

## Server / proceso

| Flag | Qué hace | Impacto | Default | Cuándo cambiarlo |
|------|----------|---------|---------|------------------|
| `--parallel` | Cantidad de slots paralelos. | Cada slot reserva KV (usá `--kv-unified` para compartir). | `1` (campo, 1–8). | Subilo para usuarios concurrentes; emparejá con `--kv-unified`. |
| `--threads` | Hilos de CPU. `-1`/`0` = auto (se omite el flag). | Ajustalo a tu cantidad de núcleos. | `-1` (auto; se omite el flag). | Solo si auto se pasa o se queda corto con tus núcleos. |
| `--api-key` | Key bearer requerida para llamar al server. | `SECRET_FLAG` — nunca se loguea. | `""` (off). | Ponela si exponés el puerto más allá de localhost. |
| `--sleep-idle-seconds` | Libera VRAM tras N s de inactividad. `0` = off. | Deja que otras tareas usen la GPU. | `0` (off; toggle). | Ponelo para recuperar VRAM entre sesiones. |
| `--no-warmup` | Salta la corrida vacía de warmup al arrancar. | Por defecto la app hace warmup; esto lo desactiva. | warmup **on** (la app envía `--no-warmup` solo cuando está OFF). | Dejalo on (el warmup hace que la primera respuesta sea más rápida). |
| `--lazy-mode` | Carga bajo demanda de tensores grandes. `auto` = solo >4 GiB. | Cambia un poquito de latencia de arranque por menos I/O inicial. | `auto` (toggle). | Dejá `auto`. |

## Validaciones (422 antes de lanzar)

La app rechaza configs contradictorias antes de lanzar:

- `gpu_only` exige `n_gpu_layers ≠ 0`; `cpu_only` fuerza `0`.
- Con `flash_attn=on`, K/V (y draft K/V) deben ser un par simétrico de la whitelist.
- `lora_scale` 0–2 · `n_parallel` 1–8 · `cache_reuse` 0–256.
- `checkpoint_min_step` ≥ 512 · `cache_ram_mib` ≥ 0 · `fit_target_mib` ≥ 0.
- Sampling dentro de los rangos de arriba.

Configs guardados viejos con tipos de KV o flags quitados (p. ej. `q4_1`,
`use_mlock`) se migran en silencio con warning: un template viejo nunca rompe el
lanzamiento.
