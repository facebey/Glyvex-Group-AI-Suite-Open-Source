# Troubleshooting — problemas frecuentes

Casos reales con el diagnóstico detrás (verificado contra el código del
launcher y contra el source de llama.cpp).

## Warning "enable_thinking via --chat-template-kwargs is deprecated" en el log

**Síntoma.** El log de `llama-server` muestra:
`Setting 'enable_thinking' via --chat-template-kwargs is deprecated. Use --reasoning on / --reasoning off instead.`

**Qué pasa.** El Launcher pasa `thinking_enabled` por
`--chat-template-kwargs '{"enable_thinking": ...}'` (solo cuando el chat
template del modelo lo lee, detectado del header GGUF, y con `--jinja`
encendido). En builds recientes de llama.cpp ese camino quedó deprecado a
favor de flags nativos (`--reasoning on/off`), pero **sigue funcionando**:
es un warning, no un error.

**Qué hacer.** Nada urgente: el thinking sigue operando. La app ya usa los
flags nativos donde existen (`--reasoning-effort`, `--reasoning-budget`,
`--no-reasoning-preserve`); migrar `thinking_enabled` a `--reasoning on/off`
es una mejora pendiente del launcher.

**Variante — "el thinking no hace nada":**
- El modelo no soporta `enable_thinking` (el toggle no se habilita si el
  scanner del header no lo detecta).
- `--jinja` está apagado: en ese caso el backend no envía los kwargs y lo
  avisa en su propio log (`thinking_enabled/budget_tokens requieren --jinja`).
- El template no lee ese kwarg en particular: llama.cpp lo ignora
  silenciosamente (es best-effort por diseño).

## `--cache-idle-slots` se desactiva solo (requiere `--cache-ram`)

**Síntoma.** Con `--cache-ram` en 0, el log de llama-server muestra:
`--cache-idle-slots requires --cache-ram, disabling` — y el guardado de slots
ociosos no funciona.

**Qué pasa.** `cache_idle_slots` viene **encendido por defecto** en
llama-server, pero depende del prompt cache en RAM: al arrancar una tarea
nueva, los slots ociosos se guardan en el prompt cache (y se limpian si hay
`--kv-unified`; sin él, el KV se queda en VRAM y solo se publica la copia en
RAM cache). Sin `--cache-ram`, no hay RAM cache a dónde guardarlos, así que
el server se desactiva el flag con warning.

**Qué hacer.** Dejá `--cache-ram > 0` (el default de la app es 8192 MiB). Si
en un template o config pusiste `cache_ram_mib = 0`, sabé que perdiste la
reutilización de prompts: cada switch de conversación reprocesa el prompt
completo (TTFT más alto).

## MTP / speculative decoding consume más VRAM de la esperada

**Síntoma.** Con MTP activado el modelo que antes cabía deja de caber, o la
VRAM se dispara.

**Qué pasa.** MTP suma dos cosas sobre el modelo principal:
1. Los **pesos del draft** (sidecar `mtp-*.gguf`, o los tensores embebidos
   `blk.N.nextn.*`).
2. Su **propio KV cache** — y llama-server lo pone en **f16 por defecto**,
   aunque el modelo principal esté en q4_0.

**Qué hacer.**
- La app ya usa **q8_0** por defecto para el KV del draft
  (`--spec-draft-type-k/-v`): mitad de VRAM que f16 con aceptación
  prácticamente idéntica. Verificá que no lo hayas subido a f16 en el
  template.
- Bajá `n_draft` (`--spec-draft-n-max`, default 5) si usás poco la
  especulación.
- Mirá el estimado de VRAM antes de lanzar (endpoint de estimación de la
  app) o el Monitor post-lanzamiento.
- Si usás una build anterior a b11007: actualizar el binario trae el fix de
  recaptura de CUDA graph con MTP (~4–5% extra en decode), sin cambiar
  flags.

## La conversación excede el contexto (context overflow)

**Síntoma.** El chat deja de responder o el server rechaza el request cuando
la conversación crece: el prompt (system + historial + adjuntos + tool
calls) pasa el `n_ctx` del server.

**Qué pasa.** Cada mensaje suma al prompt completo que se reenvía. Un adjunto
grande o muchas herramientas pueden comer decenas de k tokens de golpe.

**Qué hacer.**
- **Iniciá una conversación nueva** para la tarea nueva: es la salida más
  barata.
- Subí `n_ctx` si la VRAM lo permite (ver `--fit-target`: llama.cpp reserva
  un margen y auto-reduce el ctx para que el modelo siga cabiendo).
- Revisá adjuntos: un PDF de 100 páginas no entra en ningún contexto
  razonable; resumilo o mandá solo la sección.
- En arquitecturas híbridas (SSM), los checkpoints de contexto también
  cuestan VRAM (~150 MiB cada uno): si subís ctx, considerá bajar
  `ctx_checkpoints` o subir `checkpoint_min_step`.

## El launcher descarta flags en builds distintas

**Síntoma.** Warnings en el log tipo "el binario no conoce este flag; se
descarta" al lanzar.

**Qué pasa.** El launcher hace un **probe de capacidades**: lee `--help` una
vez por build de `llama-server` y descarta los flags que esa build no
conoce, en vez de romper el lanzamiento. Es el mecanismo que permite correr
la app contra builds viejas o nuevas sin cambios.

**Qué hacer.** No es un error: la feature que dependía de ese flag queda en
el default de la build. Si la necesitás (checkpoints de contexto,
`--fit-target`, reasoning native, etc.), actualizá el binario de
llama-server. Los flags deprecados tipo group attention se descartan así en
builds que los removieron.

## Crash o fallback silencioso con Flash Attention y KV cache

**Síntoma.** Crash al arrancar, mensaje `FA_QUANTS` en el log, o el server
cae a f16 sin avisar.

**Qué pasa.** Con `--flash-attn on`, llama.cpp solo acepta **pares
simétricos** de KV cache: `q4_0-q4_0`, `q8_0-q8_0`, `f16-f16`,
`bf16-bf16`. Cualquier otra combinación es inválida con FA.

**Qué hacer.** La app valida esto antes de lanzar (rechaza con un 422 que
dice qué par recibiste y cómo corregirlo). Si agregaste flags custom, igualá
los tipos de K y V o desactivá `--flash-attn`.

## Slots paralelos: cada uno reserva su contexto completo

**Síntoma.** Con `n_parallel > 1` la VRAM se va por las nubes aunque no haya
varios usuarios.

**Qué pasa.** Sin `--kv-unified`, **cada slot reserva su propio `n_ctx`
completo**: N slots = N × la VRAM de KV de un contexto.

**Qué hacer.** Activá `--kv-unified` (unifica el KV entre slots) y usá
`--kv-unified-per-slot` para acotar el contexto por slot si no necesitás el
global completo en cada uno.

## El modelo no cabe en la VRAM

**Qué hacer, en orden de costo:**
1. `--fit-target <MiB>`: le pedís a llama.cpp que reserve ese margen y
   auto-reduce lo que haga falta (ctx incluido). Es el control diseñado para
   cargar modelos arbitrarios sin conocer su tamaño.
2. `--fit on` (default de la app desde b11009): ajusta solo los argumentos
   que **no** fijaste.
3. KV cache en q4_0 (default de la app) en vez de f16.
4. Bajá `n_ctx` / `ctx_checkpoints` / subí `checkpoint_min_step`.
5. Cambiá de cuantización: un Q4_K_XL que no cabe, un IQ3/IQ2 sí (con la
   pérdida de calidad de cada uno).
