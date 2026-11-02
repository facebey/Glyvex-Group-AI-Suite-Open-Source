# Modelos verificados

Modelos que han sido **probados en hardware real** con la Suite (corridas de
estabilidad, memory breakdown y benchmarks M4) y para los que existe una
configuración de lanzamiento recomendada. Este documento es el registro: cada
entrada dice exactamente qué se verificó, en qué hardware y con qué build del
runtime.

Por qué existe: una familia de modelos no se "prueba" en su conjunto — lo que
se prueba es un **modelo + cuantización + configuración + hardware**. Si estás
por descargar una cuantización y querés saber si está probada con la Suite,
chequeá primero acá.

## Niveles

| Nivel | Significa |
|---|---|
| **Verificado** | Corrió sesiones de estabilidad (chat, tools, contexto largo) sin caídas, con el desglose de memoria registrado. |
| **Optimizado** | Verificado **y** ajustado: configuración de lanzamiento con resultados medidos (benchmarks M4) y recomendaciones documentadas. |
| **Candidato** | Instalado en el entorno de referencia pero sin registro de verificación. No tiene ningún claim. |

Las entradas son append-only: una entrada nunca se borra, solo se actualiza con
nueva información. Cada entrada declara el build del runtime con el que se
verificó; tras un bump de versión del runtime, las entradas afectadas se
revalidan antes de seguir figurando como vigentes.

## Verificados y optimizados

| Modelo | Cuantización (probada) | Hardware | Runtime | Notas |
|---|---|---|---|---|
| Gemma 4 26B A4B (MoE) | Unsloth Dynamic `UD-Q4_K_XL` + assistant MTP adjunto + visión `mmproj-F16` | NVIDIA RTX 3090 24 GB | llama.cpp b11827 | Chat, visión y tools a 131.072 ctx; FlashAttention activo; draft MTP (`--spec-type draft-mtp`); offload total de capas |
| Qwen3-Coder-30B-A3B (MoE) | Unsloth Dynamic `UD-Q4_K_XL` | NVIDIA RTX 3090 24 GB | llama.cpp b11827 | Modelo orientado a programación; medido frente a un denso de 27B con el protocolo M4 de la Suite; KV cache `q8_0` |
| Qwen3-1.7B | Unsloth Dynamic `UD-Q4_K_XL` | RTX 3090 24 GB y CPU pura | llama.cpp b11827 | Modelo utilitario liviano: chat rápido en CPU y pruebas de humo de todo el pipeline |

### Gemma 4 26B A4B — `UD-Q4_K_XL` (verificado, optimizado)

- **Archivos probados**: `gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf` (≈15,8 GiB,
  5.39 BPW), assistant MTP adjunto `mtp-gemma-4-26B-A4B-it.gguf` (≈440 MB) y
  codificador de visión `mmproj-F16.gguf` (≈1,1 GiB). El escáner de la Suite
  (M1) detecta el par MTP/mmproj y lo agrupa con el modelo base automáticamente.
- **Configuración probada**: `-fa on` con KV cache `q8_0`; offload a GPU hasta
  donde permite el cálculo de fit (M6) — llegó a 31/31 capas offloaded;
  `-c 131072`; decodificación especulativa con el assistant oficial adjunto
  (`--spec-type draft-mtp`).
- **Comportamiento medido**: ≈21,6 GB de VRAM total con draft + visión activos
  a 131.072 ctx; generación estable con tools y entrada de imágenes. Como
  Gemma 4 es híbrido (atención de ventana deslizante mezclada con atención
  completa), el estimador de fit reserva memoria por tipo de capa — se calcula
  con la metadata GGUF del propio modelo (SWA-aware desde el schema v7).
- **Evidencia**: corridas de benchmark registradas en la app (M4 → Reports) y
  desgloses de memoria de esas corridas en el hardware indicado.

### Qwen3-Coder-30B-A3B — `UD-Q4_K_XL` (verificado, optimizado)

- **Archivos probados**: `Qwen3-Coder-30B-A3B-Instruct-UD-Q4_K_XL.gguf` (≈16,5 GiB).
- **Configuración probada**: `-fa on` con KV cache `q8_0`; offload a GPU hasta
  entrar en 24 GB; sampling estándar para tareas de programación. Sesiones de
  programación con contexto largo verificadas.
- **Comportamiento medido**: MoE con 3B de parámetros activos por token: el
  modelo completo no entra en 24 GB con contexto alto y KV `q8_0` — el
  estimador de fit de la Suite (M6) resuelve el reparto automáticamente.
  Quedó seleccionado como el modelo de programación de referencia tras un
  benchmark cara a cara contra un denso de 27B puntuado tarea por tarea.
- **Evidencia**: corridas de benchmark M4 registradas en la app (Reports)
  comparando calidad por tarea de programación y tokens/s en ambos candidatos.

### Qwen3-1.7B — `UD-Q4_K_XL` (verificado)

- **Archivos probados**: `Qwen3-1.7B-UD-Q4_K_XL.gguf` (≈1,1 GiB).
- **Configuración probada**: corre solo en CPU en una desktop moderna (no
  necesita GPU) y en GPU para respuestas rápidas. Usado para pruebas de humo
  de todo el pipeline (chat, métricas de streaming, tools) y como modelo
  utilitario de latencia mínima.
- **Evidencia**: corridas de estabilidad en ambas configuraciones (CPU y GPU).

### Verificados previamente (no instalados hoy)

- **Qwen3.8-27B** (denso) con cabeza NextN/MTP embebida dentro del propio
  `.gguf` — probado y ajustado extensamente en RTX 3090 (drafting MTP,
  comportamiento de VRAM); de ahí nació la detección automática de MTP embebido
  de la Suite. Hoy no está en disco; si se reinstala, vuelve al registro con
  entrada propia.

## Candidatos (instalados, sin verificar aún)

Estas cuantizaciones están en el entorno de referencia pero no tienen registro
de verificación. No sostienen ningún claim hasta completar el proceso de abajo.

- Gemma 4 31B — `Q4_0` QAT + `mmproj BF16`
- Gemma 4 E4B — `Q4_K_M` + `mmproj BF16`
- Mistral Small 3.2 24B — `UD-Q4_K_XL` + `mmproj BF16`
- Qwen2.5-Coder-7B — `q8_0`
- Agents-A1 4B — `Q8_0` + `mmproj`
- EmbeddingGemma 300M — `Q8_0` (embeddings)

## Cómo agregar un modelo verificado nuevo

1. Instalá el modelo y dejá que el Inventario (M1) lo indexe.
2. Corré sesiones de estabilidad: chat con tools, contexto largo, visión si
   trae `mmproj`; sin caídas a lo de varias sesiones.
3. Registrá el desglose de memoria (VRAM usada, capas offloaded, tamaño de KV)
   para el contexto que recomendás.
4. Corré benchmarks M4 (el módulo Benchmark) para ajustar y documentar la
   configuración.
5. Agregá la entrada acá con la plantilla de abajo, en **ambas versiones
   idiomáticas** de este documento, en el mismo commit.

Plantilla:

```markdown
### <nombre del modelo> — <cuantización> (verificado | optimizado)

- **Archivos probados**: <nombres exactos de los .gguf y tamaños aproximados>
- **Configuración probada**: <flags que la Suite emite para este modelo>
- **Comportamiento medido**: <VRAM, capas offloaded, ctx utilizable, tokens/s aprox.>
- **Evidencia**: <corridas M4 / desgloses de memoria en <hardware>>
- **Verificado con**: llama.cpp <build> · <fecha>
```

> Nota: las entradas declaran el build del runtime con el que se registró lo
> observado. El registro describe lo que el proyecto midió; no promete números
> idénticos en otro hardware — promete que la configuración está probada y
> funciona en el hardware indicado.
