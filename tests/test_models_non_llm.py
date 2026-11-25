r"""Tests de INVENT-NOLLM — modelos que NO son LLM en el inventario (models.py).

Cobertura:
- el scanner ahora encuentra checkpoints .safetensors SUELTOS (flux/sd de
  ComfyUI, sin config.json) y descarta los shards de un repo HF multi-archivo;
- clasificación de `model_kind` medida (config.json de difusers / architectures
  de transformers / nombres de tensor del header .safetensors), con "unknown"
  cuando no hay señal: sin señal no se afirma nada;
- un modelo de imagen no recibe backends LLM y no entra en la composición de
  un launch (build_groups);
- INVENT-GGUF-KIND: los .gguf se clasifican desde la metadata del header que el
  scan ya leyó (sin IO extra): tokenizer → llm; arch ltxv/wan/flux/clip sin
  tokenizer → image; GGUF v3 con n_kv=0 (qwen-image, minimax fl2va) sin señal →
  "unknown", tampoco se afirma por nombre de archivo. Los drafts heredan "llm"
  (tienen arch, les falta el tokenizer del base). Un mmproj-*.gguf queda como
  auxiliar del grupo, no como modelo lanzable;
- INVENT-GGUF-TENSORNAMES: los .gguf sin metadata (n_kv=0, los ComfyUI-GGUF)
  se afirman "image" cuando la tabla tensor-info del propio header trae nombres
  de difusión — el fixture replica los 4 qwen-image-2.1 reales medidos en
  G:\LLM\gguf (265/265 tensores model.diffusion_model.*). Un archivo que ya
  declaró tokenizer o arch LLM queda "llm" pase lo que pase con los nombres;
  sin firma de tensor sigue "unknown";
- los .gguf de LLM y las carpetas HF siguen reportando "llm" con sus backends
  (regresión).
- INVENT-AUDIO-KIND (F-5): la ruta config.json afirma "audio" cuando la tarea
  medida (tts/asr/speech/audio/music/codec/heart*) aparece en _class_name,
  architectures o model_type, ANTES de los patrones de arquitectura LLM — los
  fixtures replican los config.json reales medidos en I:\Proyectos\comfyui\
  ComfyUI\models (qwen-tts y heart-music salían "llm" con unsloth/vllm:
  "forconditionalgeneration" matcheaba la arch de Qwen3-TTS y el fallback de
  model_type afirmaba a HeartMuLa/HeartCodec por declarar cualquier model_type).
"""

import json
import struct
from pathlib import Path

import models as models_module

from helpers import scan_and_wait, write_gguf

# ---------------------------------------------------------------------------
# Helpers: un .safetensors de mentira, pero con header válido
# ---------------------------------------------------------------------------


def write_safetensors(path, tensors: dict, metadata: dict | None = None):
    """Escribe un .safetensors mínimo: u64 LE con la longitud del header JSON
    + el JSON + unos bytes de relleno. El scanner solo lee el header."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = dict(tensors)
    if metadata is not None:
        header["_metadata_"] = metadata
    raw = json.dumps(header).encode("utf-8")
    with path.open("wb") as f:
        f.write(struct.pack("<Q", len(raw)))
        f.write(raw)
        f.write(b"\x00" * 16)
    return path


FLUX_TENSORS = {
    "double_blocks.0.img_attn.qkv.weight": {"dtype": "F16", "shape": [3072, 3072]},
    "single_blocks.0.linear.linear.weight": {"dtype": "F16", "shape": [3072, 3072]},
}
SD_TENSORS = {
    "model.diffusion_model.input_blocks.0.0.weight": {"dtype": "F32", "shape": [320, 3, 3, 3]},
    "alphas_cumprod": {"dtype": "F32", "shape": [1000]},
}
LLM_TENSORS = {
    "model.layers.0.self_attn.q_proj.weight": {"dtype": "F32", "shape": [4096, 4096]},
}


# ---------------------------------------------------------------------------
# Scanner: .safetensors sueltos sí; shards de repo HF no
# ---------------------------------------------------------------------------


def test_walk_dir_yields_loose_safetensors(tmp_path):
    root = tmp_path / "comfyui"
    (root / "models").mkdir(parents=True)
    write_safetensors(root / "models" / "flux1-dev.safetensors", FLUX_TENSORS)
    (root / "models" / "sd_xl_base.safetensors").touch()
    (root / "models" / "model.gguf").touch()

    found = {(p.name, kind) for p, kind in models_module._walk_dir(root, 3)}
    assert ("flux1-dev.safetensors", "safetensors_file") in found
    assert ("sd_xl_base.safetensors", "safetensors_file") in found
    assert ("model.gguf", "gguf") in found


def test_walk_dir_skips_hf_shards_without_config(tmp_path):
    # Un repo HF partido en shards, copiado sin config.json: sueltos no son un
    # modelo completo, no entran al inventario.
    root = tmp_path / "shards"
    root.mkdir()
    for i in (1, 2):
        write_safetensors(root / f"model-0000{i}-of-00002.safetensors", LLM_TENSORS)

    found = [p.name for p, kind in models_module._walk_dir(root, 3)]
    assert found == []


# ---------------------------------------------------------------------------
# Clasificación medida
# ---------------------------------------------------------------------------


def test_read_safetensors_header_rejects_non_safetensors(tmp_path):
    junk = tmp_path / "basura.safetensors"
    junk.write_bytes(b"esto no es safetensors")
    assert models_module._read_safetensors_header(junk) is None


def test_classify_hf_dir_from_config_diffusers(tmp_path):
    d = tmp_path / "flux"
    d.mkdir()
    (d / "config.json").write_text(
        json.dumps({"_class_name": "FluxPipeline"}), encoding="utf-8"
    )
    write_safetensors(d / "diffusion_pytorch_model.safetensors", LLM_TENSORS)
    # El config dice difusers: manda el config aunque los tensores parezcan de otra cosa.
    files = list(d.glob("*.safetensors"))
    assert models_module._classify_safetensors_kind(d, files) == "image"


def test_classify_hf_dir_from_config_transformers_llm(tmp_path):
    d = tmp_path / "llama"
    d.mkdir()
    (d / "config.json").write_text(
        json.dumps({"_name_or_path": "x", "architectures": ["LlamaForCausalLM"]}),
        encoding="utf-8",
    )
    write_safetensors(d / "model.safetensors", LLM_TENSORS)
    files = list(d.glob("*.safetensors"))
    assert models_module._classify_safetensors_kind(d, files) == "llm"


def test_classify_by_tensor_names_without_config(tmp_path):
    d = tmp_path / "sin-config"
    d.mkdir()
    write_safetensors(d / "sd.safetensors", SD_TENSORS)
    files = list(d.glob("*.safetensors"))
    assert models_module._classify_safetensors_kind(d, files) == "image"


def test_classify_by_safetensors_metadata(tmp_path):
    d = tmp_path / "sin-config-meta"
    d.mkdir()
    write_safetensors(
        d / "unknown-but-flux.safetensors",
        {"foo.weight": {"dtype": "F16", "shape": [8]}},
        metadata={"flux_weight_dtype": "fp8"},
    )
    files = list(d.glob("*.safetensors"))
    assert models_module._classify_safetensors_kind(d, files) == "image"


def test_classify_without_signal_is_unknown(tmp_path):
    d = tmp_path / "sin-senal"
    d.mkdir()
    (d / "config.json").write_text(json.dumps({"some": "thing"}), encoding="utf-8")
    write_safetensors(d / "pesos.safetensors", {"foo.weight": {"dtype": "F32", "shape": [2]}})
    files = list(d.glob("*.safetensors"))
    assert models_module._classify_safetensors_kind(d, files) == "unknown"


# ---------------------------------------------------------------------------
# Inventario por API
# ---------------------------------------------------------------------------


async def test_scan_indexes_loose_checkpoint_as_image(client, tmp_path):
    models_dir = tmp_path / "comfy"
    write_safetensors(models_dir / "flux1-dev.safetensors", FLUX_TENSORS)

    events = await scan_and_wait(client, model_dirs=[str(models_dir)])
    assert any('"complete"' in e for e in events)

    listed = (await client.get("/api/models")).json()
    assert len(listed) == 1
    entry = listed[0]
    assert entry["filename"] == "flux1-dev.safetensors"
    assert entry["format"] == "safetensors"
    assert entry["model_kind"] == "image"
    # Sin clase LLM medida no se adjudican unsloth/vllm.
    assert entry["compatible_backends"] == []


async def test_scan_without_signal_reports_unknown(client, tmp_path):
    models_dir = tmp_path / "raros"
    write_safetensors(models_dir / "pesos.safetensors", {"foo.weight": {"dtype": "F32", "shape": [2]}})

    await scan_and_wait(client, model_dirs=[str(models_dir)])
    listed = (await client.get("/api/models")).json()
    assert len(listed) == 1
    assert listed[0]["model_kind"] == "unknown"
    assert listed[0]["compatible_backends"] == []


async def test_scan_keeps_llm_kind_and_backends(client, tmp_path):
    # Regresión: los .gguf y las carpetas HF de LLM no cambian de comportamiento.
    models_dir = tmp_path / "mixto"
    models_dir.mkdir()
    (models_dir / "Qwen3-8B-Q4_K_M.gguf").touch()
    hf = models_dir / "llama-llm"
    hf.mkdir()
    (hf / "config.json").write_text(
        json.dumps({"architectures": ["LlamaForCausalLM"]}), encoding="utf-8"
    )
    write_safetensors(hf / "model.safetensors", LLM_TENSORS)

    await scan_and_wait(client, model_dirs=[str(models_dir)])
    listed = {m["filename"]: m for m in (await client.get("/api/models")).json()}
    assert listed["Qwen3-8B-Q4_K_M.gguf"]["model_kind"] == "llm"
    assert listed["Qwen3-8B-Q4_K_M.gguf"]["compatible_backends"]
    assert listed["llama-llm"]["model_kind"] == "llm"
    assert listed["llama-llm"]["compatible_backends"] == ["unsloth", "vllm"]


# ---------------------------------------------------------------------------
# build_groups: un modelo de imagen no forma parte de un launch
# ---------------------------------------------------------------------------


async def test_image_models_do_not_form_groups(client, tmp_path):
    models_dir = tmp_path / "carpeta-mixta"
    write_safetensors(models_dir / "flux1-dev.safetensors", FLUX_TENSORS)
    (models_dir / "Qwen3-8B-Q4_K_M.gguf").touch()

    await scan_and_wait(client, model_dirs=[str(models_dir)])
    groups = (await client.get("/api/models/groups")).json()
    assert len(groups) == 1
    names = [m["filename"] for m in groups[0]["base_models"]]
    assert names == ["Qwen3-8B-Q4_K_M.gguf"]


async def test_folder_with_only_image_models_has_no_group(client, tmp_path):
    models_dir = tmp_path / "solo-imagen"
    write_safetensors(models_dir / "flux1-dev.safetensors", FLUX_TENSORS)

    await scan_and_wait(client, model_dirs=[str(models_dir)])
    assert (await client.get("/api/models")).json()  # sí está en la lista
    assert (await client.get("/api/models/groups")).json() == []


# ---------------------------------------------------------------------------
# INVENT-GGUF-KIND: clasificación de .gguf desde la metadata ya leída
# ---------------------------------------------------------------------------


def write_gguf_no_kv(path, tensor_names: list[str] | None = None):
    """GGUF v3 con n_kv=0: lo que traen los qwen-image-2.1 y minimax_h3_fl2va
    reales de ComfyUI. Header válido y parseable, pero no hay KV que leer.
    Con `tensor_names` escribe además la tabla tensor-info (tensores dummy 1-D
    F32): los archivos reales traen ahí sus 265 nombres de tensores aunque no
    declaren metadata. Sin nombres ni firma: la entrada debe quedar "unknown",
    no afirmada por el nombre de archivo."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    names = tensor_names or []
    blob = bytearray(b"GGUF" + struct.pack("<IQQ", 3, len(names), 0))
    for name in names:
        raw = name.encode("utf-8")
        blob += struct.pack("<Q", len(raw)) + raw
        blob += struct.pack("<I", 1)          # n_dims
        blob += struct.pack("<Q", 1)          # dims[0]
        blob += struct.pack("<I", 8)          # dtype F32
        blob += struct.pack("<Q", 0)          # offset
    path.write_bytes(bytes(blob))
    return path


def gguf_meta(arch=None, tokenizer_model=None, vocab_size=None,
             chat_template=None, added_tokens_count=None):
    return {
        "architecture": arch,
        "tokenizer_model": tokenizer_model,
        "vocab_size": vocab_size,
        "chat_template": chat_template,
        "added_tokens_count": added_tokens_count,
    }


def test_classify_gguf_tokenizer_is_llm():
    assert models_module._classify_gguf_kind(
        gguf_meta(arch="gemma3", tokenizer_model="gemma2", vocab_size=262144)
    ) == "llm"
    # El chat template solo también alcanza (mismo archivo, otra metadata).
    assert models_module._classify_gguf_kind(
        gguf_meta(arch="qwen3vl", chat_template="x")
    ) == "llm"


def test_classify_gguf_diffusion_arch_without_tokenizer_is_image():
    for arch in ("ltxv", "wan", "flux", "clip"):
        assert models_module._classify_gguf_kind(gguf_meta(arch=arch)) == "image", arch


def test_classify_gguf_non_llm_arch_wins_over_vocab():
    # Los CLIP completos que escribe convert_clip.py declaran clip.tokens /
    # clip.vocab_size y siguen sin ser un modelo de chat lanzable: la arch
    # medida manda. Ninguna arch de lenguaje usa estos nombres, así que este
    # orden no puede degradar un LLM (verado: los 17 .gguf reales de
    # C:\LLM\gguf y los gemma3/qwen3vl de ComfyUI siguen saliendo "llm").
    assert models_module._classify_gguf_kind(
        gguf_meta(arch="clip", vocab_size=49408, tokenizer_model="clip")
    ) == "image"


def test_classify_gguf_draft_without_tokenizer_is_llm():
    # Drafts MTP/DFlash: tienen arch y les falta el tokenizer (hereda del base);
    # siguen siendo un LLM y mantienen sus backends.
    assert models_module._classify_gguf_kind(gguf_meta(arch="qwen2")) == "llm"


def test_classify_gguf_without_signal_is_unknown():
    assert models_module._classify_gguf_kind(gguf_meta()) == "unknown"


def test_classify_gguf_unreadable_metadata_stays_llm():
    # Comportamiento existente para .gguf cuyo header no se pudo leer: el formato
    # en sí es de llama.cpp y no hay señal medida que lo contradiga.
    assert models_module._classify_gguf_kind(None) == "llm"


# ---------------------------------------------------------------------------
# INVENT-GGUF-TENSORNAMES: n_kv=0 + nombres de tensor de difusión en el header
# ---------------------------------------------------------------------------

# Nombres reales medidos 2026-11-24 en G:\LLM\gguf\Qwen-Image-2.1 (los 4
# archivos): la firma exacta que ya se usa para los checkpoints .safetensors.
QWEN_IMAGE_TENSOR_NAMES = [
    "model.diffusion_model.img_in.weight",
    "model.diffusion_model.modulation.1.weight",
    "model.diffusion_model.norm_out.linear.weight",
]


def test_classify_gguf_no_kv_with_diffusion_tensor_names_is_image():
    # qwen-image-2.1 / minimax fl2va: sin metadata pero con tensor-info de
    # difusión — señal medida, no suposición por nombre de archivo.
    assert models_module._classify_gguf_kind(
        gguf_meta(), QWEN_IMAGE_TENSOR_NAMES
    ) == "image"


def test_classify_gguf_no_kv_without_diffusion_names_stays_unknown():
    # Sin firma de difusión en los nombres no se afirma nada.
    assert models_module._classify_gguf_kind(
        gguf_meta(), ["blk.0.ffn.weight", "output.weight"]
    ) == "unknown"
    assert models_module._classify_gguf_kind(gguf_meta(), []) == "unknown"


def test_classify_gguf_tokenizer_wins_over_tensor_names():
    # La cascada es a propósito: un archivo que declaró tokenizer o arch LLM
    # queda "llm" aunque sus nombres extraños firmen difusión. La firma solo
    # alcanza donde antes no había ninguna señal.
    assert models_module._classify_gguf_kind(
        gguf_meta(arch="gemma3", tokenizer_model="gemma2", vocab_size=262144),
        QWEN_IMAGE_TENSOR_NAMES,
    ) == "llm"
    assert models_module._classify_gguf_kind(
        gguf_meta(arch="qwen2"), QWEN_IMAGE_TENSOR_NAMES
    ) == "llm"


async def test_scan_classifies_diffusion_gguf_as_image(client, tmp_path):
    # Caso real medido en ComfyUI: ltx-2.3-22b-dev-Q4_K_M (arch ltxv, sin
    # tokenizer ni block_count de lenguaje).
    models_dir = tmp_path / "comfy-gguf"
    models_dir.mkdir(parents=True)
    write_gguf(models_dir / "ltx-2.3-22b-dev-Q4_K_M.gguf", arch="ltxv",
               kv={"block_count": 30, "embedding_length": 4096})
    await scan_and_wait(client, model_dirs=[str(models_dir)])
    listed = (await client.get("/api/models")).json()
    assert len(listed) == 1
    entry = listed[0]
    assert entry["model_kind"] == "image"
    assert entry["compatible_backends"] == []
    # No forma parte de la composición de un launch.
    assert (await client.get("/api/models/groups")).json() == []


async def test_scan_classifies_gguf_without_kv_as_unknown(client, tmp_path):
    # qwen-image-2.1-Q4_K_M / -Q8_0 y minimax_h3_fl2va: GGUF v3 con n_kv=0.
    models_dir = tmp_path / "comfy-gguf-vacio"
    write_gguf_no_kv(models_dir / "qwen-image-2.1-Q4_K_M.gguf")
    await scan_and_wait(client, model_dirs=[str(models_dir)])
    listed = (await client.get("/api/models")).json()
    assert len(listed) == 1
    assert listed[0]["model_kind"] == "unknown"
    assert listed[0]["compatible_backends"] == []
    assert (await client.get("/api/models/groups")).json() == []


async def test_scan_classifies_no_kv_gguf_with_diffusion_tensors_as_image(client, tmp_path):
    # Caso real medido 2026-11-24 en los 4 qwen-image-2.1 de G:\LLM\gguf:
    # GGUF v3 con n_kv=0 y 265/265 tensores model.diffusion_model.* en su
    # tabla tensor-info. El fixture replica esa estructura con nombres reales.
    models_dir = tmp_path / "comfy-gguf-difusion"
    write_gguf_no_kv(
        models_dir / "qwen-image-2.1-Q4_K_M.gguf", QWEN_IMAGE_TENSOR_NAMES
    )
    await scan_and_wait(client, model_dirs=[str(models_dir)])
    listed = (await client.get("/api/models")).json()
    assert len(listed) == 1
    assert listed[0]["model_kind"] == "image"
    # Sin backends ni entrada en la composición de un launch: ComfyUI lo carga,
    # llama.cpp no.
    assert listed[0]["compatible_backends"] == []
    assert (await client.get("/api/models/groups")).json() == []


async def test_standalone_mmproj_is_image_but_stays_group_auxiliary(client, tmp_path):
    # Caso real medido: mmproj-BF16 (arch clip, sin tokenizer). Su ficha queda
    # "image" sin backends — no se ofrece como modelo lanzable — pero sigue
    # siendo candidato de visión del grupo y pareja declarada del base.
    models_dir = tmp_path / "vision-pair"
    models_dir.mkdir(parents=True)
    write_gguf(models_dir / "qwen3vl-8b-Q4_K_M.gguf", arch="qwen3vl",
               chat_template="{% for message in messages %}{{ message['content'] }}{% endfor %}")
    write_gguf(models_dir / "mmproj-BF16.gguf", arch="clip")

    await scan_and_wait(client, model_dirs=[str(models_dir)])
    listed = {m["filename"]: m for m in (await client.get("/api/models")).json()}
    assert listed["qwen3vl-8b-Q4_K_M.gguf"]["model_kind"] == "llm"
    assert listed["qwen3vl-8b-Q4_K_M.gguf"]["compatible_backends"]
    assert listed["qwen3vl-8b-Q4_K_M.gguf"]["has_mmproj"] is True
    assert listed["mmproj-BF16.gguf"]["model_kind"] == "image"
    assert listed["mmproj-BF16.gguf"]["compatible_backends"] == []

    groups = (await client.get("/api/models/groups")).json()
    assert len(groups) == 1
    assert [m["filename"] for m in groups[0]["base_models"]] == ["qwen3vl-8b-Q4_K_M.gguf"]
    assert [m["filename"] for m in groups[0]["mmproj_models"]] == ["mmproj-BF16.gguf"]


# ---------------------------------------------------------------------------
# INVENT-AUDIO-KIND (F-5): la tarea medida manda sobre el patrón de arquitectura
# ---------------------------------------------------------------------------

# Copias exactas de los config.json medidos 2026-11-23 en
# I:\Proyectos\comfyui\ComfyUI\models (qwen-tts y heart-music). Ningún archivo
# declara _class_name: la señal está en architectures + model_type.
AUDIO_CONFIGS = {
    "Qwen3-TTS-12Hz-1.7B-Base": {
        "architectures": ["Qwen3TTSForConditionalGeneration"],
        "model_type": "qwen3_tts",
    },
    "Qwen3-TTS-Tokenizer-12Hz": {
        "architectures": ["Qwen3TTSTokenizerV2Model"],
        "model_type": "qwen3_tts_tokenizer_12hz",
    },
    "HeartMuLa-oss-3B": {
        "architectures": ["HeartMuLa"],
        "model_type": "heartmula",
    },
    "HeartCodec-oss": {
        "architectures": ["HeartCodec"],
        "model_type": "heartcodec",
    },
}


def test_config_json_audio_beats_llm_arch_pattern(tmp_path):
    # El falso positivo original (LIVE-TEST b): "forconditionalgeneration" es
    # subcadena de Qwen3TTSForConditionalGeneration y la ruta afirmaba "llm" con
    # unsloth/vllm. La tarea medida (tts) manda, misma precedencia que F-1.
    d = tmp_path / "Qwen3-TTS-12Hz-1.7B-Base"
    d.mkdir()
    (d / "config.json").write_text(
        json.dumps(AUDIO_CONFIGS["Qwen3-TTS-12Hz-1.7B-Base"]), encoding="utf-8"
    )
    assert models_module._config_json_model_kind(d) == "audio"


def test_config_json_audio_from_measured_comfyui_configs(tmp_path):
    for name, cfg in AUDIO_CONFIGS.items():
        d = tmp_path / name
        d.mkdir()
        (d / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        assert models_module._config_json_model_kind(d) == "audio", name


def test_config_audio_by_class_name_asr(tmp_path):
    # La lista amplia también toma tareas declaradas vía _class_name (p. ej.
    # AutoModelForAsr), no solo architectures/model_type.
    d = tmp_path / "asr-generico"
    d.mkdir()
    (d / "config.json").write_text(
        json.dumps({"_class_name": "AutoModelForAsr"}), encoding="utf-8"
    )
    assert models_module._config_json_model_kind(d) == "audio"


def test_config_json_llm_without_audio_signal_stays_llm(tmp_path):
    # Regresión: un ForConditionalGeneration sin ninguna señal de audio sigue
    # siendo llm. El token "audio" solo roba modelos multimodales CON entrada
    # de audio (Qwen2-Audio), aceptado a propósito: llama.cpp no los lanza como
    # chat de todos modos.
    d = tmp_path / "gemma3-vision"
    d.mkdir()
    (d / "config.json").write_text(
        json.dumps({"architectures": ["Gemma3ForConditionalGeneration"],
                    "model_type": "gemma3_text"}),
        encoding="utf-8",
    )
    assert models_module._config_json_model_kind(d) == "llm"
    d2 = tmp_path / "qwen2-audio"
    d2.mkdir()
    (d2 / "config.json").write_text(
        json.dumps({"architectures": ["Qwen2AudioForConditionalGeneration"],
                    "model_type": "qwen2_audio"}),
        encoding="utf-8",
    )
    assert models_module._config_json_model_kind(d2) == "audio"


async def test_scan_classifies_audio_dirs_without_backends_or_groups(client, tmp_path):
    # Réplica de la estructura real de ComfyUI: carpetas HF con config.json +
    # pesos. Los pesos parecen de LLM a propósito: el config con tarea medida
    # manda sobre los nombres de tensor.
    models_dir = tmp_path / "comfy-audio"
    for name, cfg in AUDIO_CONFIGS.items():
        d = models_dir / name
        d.mkdir(parents=True)
        (d / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        write_safetensors(d / "model.safetensors", LLM_TENSORS)

    await scan_and_wait(client, model_dirs=[str(models_dir)])
    listed = {m["filename"]: m for m in (await client.get("/api/models")).json()}
    assert len(listed) == len(AUDIO_CONFIGS)
    for name in AUDIO_CONFIGS:
        assert listed[name]["model_kind"] == "audio", name
        assert listed[name]["compatible_backends"] == [], name
    # Ninguno forma parte de la composición de un launch.
    assert (await client.get("/api/models/groups")).json() == []
