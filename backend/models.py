"""
models.py — Gestión e inventario de modelos LLM locales (módulo M1).

Responsabilidades:
- Escanear los `model_dirs` configurados en config.json (recursivo, max depth 3).
- Detectar archivos .gguf, .ggml y carpetas HuggingFace (config.json + *.safetensors).
- Extraer metadata (familia, parámetros, cuantización) del nombre de archivo GGUF.
- Leer metadata real del header GGUF (context_length, arquitectura, chat template,
  soporte de thinking) vía la librería `gguf`, sin cargar el modelo en memoria.
- Cachear el inventario en data/models.json (persistencia async con aiofiles).
- Exponer el inventario y el disparo de scan (vía SSE) como router de FastAPI.

El streaming de progreso usa `StreamingResponse` nativo de FastAPI con un
generador async (NO se usa sse-starlette ni ninguna librería externa de SSE).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import struct
import time
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import aiofiles
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from config import config
from paths import DATA_DIR
from vram_estimate import estimate_vram_usage, estimate_vram_legend

logger = logging.getLogger("glyvex.models")

MODELS_CACHE_PATH = DATA_DIR / "models.json"
SCHEMA_VERSION_PATH = DATA_DIR / "models_schema_version.json"

MAX_SCAN_DEPTH = 3

# Versión del esquema de ModelEntry en lo que respecta a campos DERIVADOS DEL
# HEADER del .gguf (mtp_embedded, has_vision_embedded, …).
#
# Por qué existe: el scan es incremental (si el mtime del archivo no cambió, se
# reusa la entrada cacheada sin releer el header). Cuando agregamos un campo
# nuevo que se calcula leyendo el header, las entradas viejas del cache
# quedarían con el valor default para siempre, porque los archivos en disco no
# cambiaron. Subir este número invalida el cache una sola vez y fuerza a
# recalcular todo en el próximo scan, sin que el usuario tenga que borrar
# data/models.json a mano.
#
# SUBIR ESTE NÚMERO cada vez que se agregue/cambie un campo de ModelEntry que
# dependa de leer el header GGUF (no hace falta para campos derivados solo del
# nombre de archivo, que se recalculan igual de rápido).
MODELS_CACHE_SCHEMA_VERSION = 6

# --------------------------------------------------------------------------
# Schema (Pydantic v2)
# --------------------------------------------------------------------------

ModelFormat = Literal["gguf", "safetensors", "ggml"]


class ModelEntry(BaseModel):
    id: str
    name: str
    filename: str
    path: str
    format: ModelFormat
    size_gb: float
    quantization: str | None = None
    family: str | None = None
    parameters: str | None = None
    compatible_backends: list[str] = Field(default_factory=list)
    has_mmproj: bool = False
    mmproj_path: str | None = None
    # Cabeza MTP / NextN autocontenida dentro del propio .gguf (ej. varios
    # Qwen3.8): no necesita un archivo de draft aparte para especular.
    mtp_embedded: bool = False
    mtp_embedded_layers: int | None = None
    # Encoder de visión horneado dentro del propio .gguf. Poco común: la
    # convención dominante sigue siendo un mmproj-*.gguf separado, que ya se
    # cubre con has_mmproj/mmproj_path.
    has_vision_embedded: bool = False
    last_seen: str
    tags: list[str] = Field(default_factory=list)
    # Metadata interna usada para el scan incremental (no se expone en docs
    # como campo funcional del modelo, pero viaja en el JSON cacheado).
    mtime: float = 0.0
    # Metadata completa del header GGUF (n_layer, n_embd, MoE, thinking, etc.),
    # extraída en el scan cuando el reader ya estaba abierto para MTP/visión.
    # Persistida en models.json para que /metadata, /vram-estimate y el gate
    # de thinking del launcher respondan SIN abrir el archivo — ni en caliente
    # ni tras un restart (cold start cero). Si es None (entrada de esquema
    # viejo), los endpoints caen a read_gguf_metadata_cached.
    metadata: dict[str, Any] | None = None


class ModelPatch(BaseModel):
    """Body de PATCH /api/models/{id}. Solo tags y campos custom editables."""

    model_config = {"extra": "allow"}

    tags: list[str] | None = None


class ModelGroup(BaseModel):
    """
    Agrupación de modelos por carpeta padre (mejora M5): una cuantización
    base más, opcionalmente, sus draft models MTP y módulos de visión
    mmproj asociados, todos detectados dentro del mismo directorio.
    """

    group_id: str
    name: str
    folder: str
    base_models: list[ModelEntry] = Field(default_factory=list)
    mtp_models: list[ModelEntry] = Field(default_factory=list)
    mmproj_models: list[ModelEntry] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Extracción de metadata desde el nombre de archivo GGUF/GGML
# --------------------------------------------------------------------------

# Cuantización: Q4_K_M, IQ2_XS, UD-Q4_K_XL, etc. — se busca al final del nombre.
# Acepta "-", "_" o "." como separador previo (los tres se usan en GGUFs reales).
_QUANT_RE = re.compile(
    r"(?:^|[-_.])((?:UD-)?(?:IQ|Q)\d[A-Za-z0-9_]*)$",
    re.IGNORECASE,
)

# Parámetros: 27B, 8B, 30B-A3B (MoE: total-activos), 1.5B, etc.
_PARAMS_RE = re.compile(
    r"\d+(?:\.\d+)?[AB](?:-A\d+(?:\.\d+)?B)?",
    re.IGNORECASE,
)

# Prefijo alfabético + dígitos pegados (ej. "Qwen3", "Llama") al comienzo del nombre.
_FAMILY_HEAD_RE = re.compile(r"^[A-Za-z]+\d*")

# Sufijo de versión con punto tipo "-3.1" que NO sea en sí mismo un conteo de
# parámetros (o sea, no termina en B/A) — se anexa a la familia si aparece
# pegado justo después del prefijo (ej. "Llama" + "-3.1" -> "Llama-3.1").
_FAMILY_VERSION_SUFFIX_RE = re.compile(r"^-\d+(?:\.\d+)+(?![A-Za-z])")


def _extract_metadata(stem: str) -> tuple[str | None, str | None, str | None]:
    """
    Dado el nombre de archivo sin extensión, devuelve (family, parameters, quantization).

    Ejemplo: "Qwen3.8-27B-UD-Q4_K_XL" -> ("Qwen3", "27B", "UD-Q4_K_XL")
    """
    remaining = stem

    quant_match = _QUANT_RE.search(remaining)
    quantization = quant_match.group(1) if quant_match else None
    if quant_match:
        remaining = remaining[: quant_match.start()].rstrip("-_")

    params_match = _PARAMS_RE.search(remaining)
    parameters = params_match.group(0) if params_match else None

    if params_match:
        family_source = remaining[: params_match.start()].rstrip("-_.")
    else:
        family_source = remaining.rstrip("-_.")

    if not family_source:
        return (family_source or None, parameters, quantization)

    head_match = _FAMILY_HEAD_RE.match(family_source)
    if not head_match:
        return (family_source, parameters, quantization)

    family = head_match.group(0)
    leftover = family_source[head_match.end():]
    version_match = _FAMILY_VERSION_SUFFIX_RE.match(leftover)
    if version_match and version_match.end() == len(leftover):
        family += version_match.group(0)

    return (family or None, parameters, quantization)


def _compatible_backends(fmt: ModelFormat) -> list[str]:
    return {
        "gguf": ["llama_server", "ollama", "lm_studio"],
        "safetensors": ["unsloth", "vllm"],
        "ggml": ["llama_server"],
    }[fmt]


def _make_id(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:8]


def _find_mmproj(directory: Path) -> Path | None:
    for candidate in directory.glob("*mmproj*"):
        if candidate.is_file():
            return candidate
    return None


# --------------------------------------------------------------------------
# Metadata real del header GGUF (librería `gguf`)
# --------------------------------------------------------------------------

_METADATA_KEYS = (
    "context_length",
    "architecture",
    "model_name",
    "n_layer",
    "n_head",
    "n_embd",
    "vocab_size",
    "chat_template",
    "bos_token",
    "eos_token",
    "thinking_support",
    "enable_thinking_kwarg",
    "instruct_mode",
    "suggested_preset",
    "n_head_kv",
    "head_dim",
    "is_moe",
    "ffn_expert_count",
    "ffn_expert_shared_count",
    "tensor_count",
    "kv_data",
)


def _metadata_error(message: str) -> dict[str, Any]:
    """Dict de metadata con la misma forma que el caso exitoso, pero vacío.

    Devolver siempre las mismas claves evita que el frontend tenga que
    distinguir entre 'no vino el campo' y 'hubo error'.
    """
    payload: dict[str, Any] = {key: None for key in _METADATA_KEYS}
    payload["thinking_support"] = False
    payload["enable_thinking_kwarg"] = False
    payload["instruct_mode"] = False
    payload["is_moe"] = False
    payload["kv_data"] = {}
    payload["error"] = message
    return payload


def _field_str(field: Any) -> str | None:
    """Valor string de un ReaderField, tolerando las distintas versiones de `gguf`."""
    if field is None:
        return None
    # gguf >= 0.10 expone .contents(), que ya resuelve el tipo del campo.
    try:
        contents = field.contents()
        if isinstance(contents, str):
            return contents
        if isinstance(contents, (bytes, bytearray)):
            return bytes(contents).decode("utf-8", errors="replace")
    except Exception:
        pass
    # Fallback manual: el string vive en parts[data[-1]] como array de bytes.
    try:
        data = getattr(field, "data", None)
        part = field.parts[data[-1]] if data else field.parts[-1]
        return bytes(part).decode("utf-8", errors="replace")
    except Exception:
        return None


def _field_int(field: Any) -> int | None:
    """Valor entero de un ReaderField, tolerando las distintas versiones de `gguf`."""
    if field is None:
        return None
    try:
        contents = field.contents()
        if isinstance(contents, bool):
            return int(contents)
        if isinstance(contents, (int, float)):
            return int(contents)
    except Exception:
        pass
    try:
        data = getattr(field, "data", None)
        part = field.parts[data[-1]] if data else field.parts[-1]
        return int(part[0])
    except Exception:
        return None


def _field_len(field: Any) -> int | None:
    """
    Cantidad de elementos de un campo array (ej. tokenizer.ggml.tokens).

    Se cuenta por índices, sin decodificar el contenido: un vocabulario de
    150k tokens tarda milisegundos así y varios segundos con .contents().
    """
    if field is None:
        return None
    try:
        data = getattr(field, "data", None)
        if data is not None:
            return len(data)
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------
# Parser rápido del header GGUF (sin la librería gguf)
# --------------------------------------------------------------------------
#
# `GGUFReader` (gguf 0.19) parsea en el constructor, en Python puro, TODO el
# tokenizer: cada string de tokenizer.ggml.merges/tokens (cientos de miles).
# En un vocabulario gemma-4 toma MINUTOS por archivo, y durante el scan eso
# bloqueaba el event loop (todo el backend se congelaba). Para lo que necesita
# esta app bastan los KVs escalares, un string (chat_template) y los nombres
# de tensores: se lee el header como un bloque de bytes y se recorre con un
# cursor con bounds checks, materializando solo lo que se usa y saltando los
# arrays grandes → milisegundos incluso con 250k+ tokens. Si el parser rápido
# falla (versión/tipo desconocido, archivo corrupto) se cae a la librería.
#
# Formato binario GGUF v3 (validado contra gguf_writer/gguf_reader y archivos
# reales): string = [u64 byte_len][bytes] (NO null-terminated); KV = [key]
# [tipo u32][valor] — sin count para escalares/strings; STRING = 8; ARRAY = 9
# ([sub_tipo u32][count u64][items]); tensor = [nombre][n_dims u32][dims
# u64×n][dtype u32][offset u64][size u64]. No existe tipo BINARY en v3.
#
# Se lee como BLOQUE y se recorre con cursor (sin seeks sobre el file): en
# CPython 3.14 un seek relativo negativo (f.seek(-x, 1)) tras reads por
# chunks grandes desincroniza el buffer interno de BufferedReader — tell()
# queda correcto pero la siguiente read devuelve bytes de otra posición.

_GGUF_MAGIC = b"GGUF"
_GGUF_KNOWN_VERSIONS = (2, 3)
# GGUFValueType: id de escalar → formato struct. 8 = STRING, 9 = ARRAY.
_GGUF_SCALAR_FORMATS = {
    0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i",
    6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d",
}
_GGUF_TYPE_STRING = 8
_GGUF_TYPE_ARRAY = 9
# Tope del header que se lee a memoria. Los headers reales son de 2-30 MB
# (vocabulario + merges); si el header lo supera se devuelve None y decide
# la librería.
_GGUF_FAST_HEADER_MAX = 64 * (1 << 20)
# Primer tramo: 8 MB cubre la mayoría de headers; los más largos (vocab
# enorme) se releen hasta _GGUF_FAST_HEADER_MAX en una sola pasada.
_GGUF_FAST_HEADER_FIRST = 8 * (1 << 20)
# Arrays de escalar que se materializan como lista (p. ej. head_count_kv
# por capa); los más grandes solo se saltan.
_GGUF_MAX_SCALAR_ARRAY = 4096
# Unicos KVs STRING que se materializan; los demás (p. ej. tokenizer.hf_json,
# de varios MB) se saltan sin decodificar.
_FAST_STRING_KEYS = frozenset(
    {"general.architecture", "general.name", "tokenizer.chat_template"}
)

# Sentinel: el bloque termina a mitad del header (hacen falta más bytes).
_GGUF_TRUNCATED = object()


class _GgufTruncated(ValueError):
    """El bloque termina antes de completar el header: falta leer más."""


def _parse_gguf_header_blob(blob: bytes) -> Any:
    """
    Parsea un bloque que contiene el header GGUF.

    Devuelve el dict de header, None si el bloque es corrupto o de tipo
    desconocido, o _GGUF_TRUNCATED si el bloque termina a mitad del header.
    """
    n = len(blob)
    if n < 24 or blob[:4] != _GGUF_MAGIC:
        return None
    (version,) = struct.unpack_from("<I", blob, 4)
    if version not in _GGUF_KNOWN_VERSIONS:
        return None
    tensor_count, kv_count = struct.unpack_from("<QQ", blob, 8)
    cur = 24

    def _u(offset: int, fmt: str):
        # Campo de ancho fijo con guard: si sobrepasa el fin del bloque es
        # TRUNCADO (faltan bytes y conviene re-leer más), no un fallo de
        # parseo. Sin esto, un header > 8 MB cortado a mitad de campo lanzaba
        # struct.error y se interpretaba como None (corrupto) en vez de
        # _GGUF_TRUNCATED.
        size = struct.calcsize(fmt)
        if offset + size > n:
            raise _GgufTruncated("fin del bloque GGUF")
        return struct.unpack_from(fmt, blob, offset)

    def read_str() -> str:
        nonlocal cur
        (slen,) = _u(cur, "<Q")
        if slen > (1 << 30) or cur + 8 + slen > n:
            raise _GgufTruncated("string GGUF fuera del bloque")
        s = blob[cur + 8 : cur + 8 + slen].decode("utf-8", "replace")
        cur += 8 + slen
        return s

    def skip_str() -> None:
        nonlocal cur
        (slen,) = _u(cur, "<Q")
        if slen > (1 << 30) or cur + 8 + slen > n:
            raise _GgufTruncated("string GGUF fuera del bloque")
        cur += 8 + slen

    try:
        kvs: dict[str, Any] = {}
        kv_names: list[str] = []
        for _ in range(kv_count):
            key = read_str()
            kv_names.append(key)
            (kv_type,) = _u(cur, "<I")
            cur += 4
            if kv_type in _GGUF_SCALAR_FORMATS:
                fmt = _GGUF_SCALAR_FORMATS[kv_type]
                width = struct.calcsize(fmt)
                if cur + width > n:
                    raise _GgufTruncated(f"escalar GGUF {key}")
                kvs[key] = struct.unpack_from(fmt, blob, cur)[0]
                cur += width
            elif kv_type == _GGUF_TYPE_STRING:
                if key in _FAST_STRING_KEYS:
                    kvs[key] = read_str()
                else:
                    skip_str()
            elif kv_type == _GGUF_TYPE_ARRAY:
                (sub_type,) = _u(cur, "<I")
                (count,) = _u(cur + 4, "<Q")
                cur += 12
                if sub_type == _GGUF_TYPE_STRING:
                    # Array de strings (tokens/merges/scores/token_type): se
                    # recorre byte a byte sin materializar — el caso de 250k+
                    # strings que antes tomaba minutos en la librería.
                    if key == "tokenizer.ggml.tokens":
                        kvs[key] = count
                    for _i in range(count):
                        (slen,) = _u(cur, "<Q")
                        if slen > (1 << 30) or cur + 8 + slen > n:
                            raise _GgufTruncated("array de strings GGUF")
                        cur += 8 + slen
                elif sub_type in _GGUF_SCALAR_FORMATS:
                    fmt = _GGUF_SCALAR_FORMATS[sub_type]
                    total = count * struct.calcsize(fmt)
                    if cur + total > n:
                        raise _GgufTruncated("array de escalares GGUF")
                    if count <= _GGUF_MAX_SCALAR_ARRAY:
                        # fmt trae el prefijo "<" de little-endian: solo se
                        # coloca una vez en el formato final.
                        kvs[key] = list(struct.unpack_from(f"<{count}{fmt[1:]}", blob, cur))
                    cur += total
                else:
                    return None  # sub-tipo desconocido: que decida la librería
            else:
                return None  # tipo desconocido: que decida la librería

        tensor_names: list[str] = []
        for _ in range(tensor_count):
            tensor_names.append(read_str())
            (n_dims,) = _u(cur, "<I")
            # dims (u64 × n_dims) + dtype (u32) + offset (u64) — sin size
            cur += 4 + 8 * n_dims + 4 + 8
            if cur > n:
                raise _GgufTruncated("tabla de tensores GGUF")
    except _GgufTruncated:
        return _GGUF_TRUNCATED
    except (ValueError, struct.error):
        return None

    return {
        "kvs": kvs,
        "kv_names": kv_names,
        "tensor_names": tensor_names,
        "tensor_count": tensor_count,
    }


def read_gguf_header_fast(path: Path) -> dict[str, Any] | None:
    """
    Lee el header GGUF (KVs + nombres de tensores) sin la librería gguf.

    Lee el header como un solo bloque (hasta _GGUF_FAST_HEADER_MAX) y lo
    recorre con un cursor con bounds checks — sin seeks: determinista e
    inmune al desync de buffer de BufferedReader de CPython 3.14.

    Devuelve {"kvs": {...}, "kv_names": [...], "tensor_names": [...],
    "tensor_count": int} donde:
      - `kvs` materializa TODOS los KVs escalares (arrays de escalar
        pequeños como lista), los strings de _FAST_STRING_KEYS, y
        `tokenizer.ggml.tokens` como su COUNT (tamaño de vocabulario) sin
        leer los tokens;
      - `kv_names` trae todas las claves (para detectar clip.*/vision.*);
      - `tensor_names` trae solo el nombre de cada tensor (dims/offsets se
        saltan).

    Milisegundos incluso en archivos de 16+ GB con vocabulario enorme.
    Devuelve None ante cualquier fallo de parseo (quien llama cae a
    GGUFReader).
    """
    try:
        cap = min(path.stat().st_size, _GGUF_FAST_HEADER_MAX)
        with open(path, "rb") as f:
            blob = f.read(min(cap, _GGUF_FAST_HEADER_FIRST))
            header = _parse_gguf_header_blob(blob)
            if header is _GGUF_TRUNCATED and cap > len(blob):
                # Header más largo que el primer tramo (vocab enorme): se
                # relee hasta el tope en una sola pasada.
                f.seek(0)
                blob = f.read(cap)
                header = _parse_gguf_header_blob(blob)
    except OSError:
        return None
    return None if header is _GGUF_TRUNCATED else header


def _as_int(value: Any) -> int | None:
    """Escalar del header rápido → int (tolera float, bool y array de escalar)."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, (list, tuple)) and value:
        # Array de escalar (p. ej. attention.head_count_kv por capa): se toma
        # el primer elemento (todos iguales en la práctica).
        return _as_int(value[0])
    return None


def _metadata_from_kv(header: dict[str, Any]) -> dict[str, Any]:
    """
    Arma el dict de metadata desde el header rápido (valores planos, ver
    read_gguf_header_fast). Espeja la lógica de _metadata_from_reader sin
    depender de objetos ReaderField.
    """
    kvs = header["kvs"]

    arch = kvs.get("general.architecture") or ""
    chat_template = kvs.get("tokenizer.chat_template") or ""

    lowered_template = chat_template.lower()
    thinking_support = "think" in lowered_template
    enable_thinking_kwarg = "enable_thinking" in chat_template

    ctx = _as_int(
        kvs.get(f"{arch}.context_length")
        or kvs.get(f"{arch}.max_context_length")
        or kvs.get("llama.context_length")
        or kvs.get("llama.max_context_length")
    )

    vocab_size = _as_int(
        kvs.get("tokenizer.ggml.tokens")
        or kvs.get(f"{arch}.vocab_size")
        or kvs.get("llama.vocab_size")
    )

    ffn_expert_count = _as_int(kvs.get(f"{arch}.ffn_expert_count"))

    return {
        "context_length": ctx,
        "architecture": arch or None,
        "model_name": kvs.get("general.name"),
        "n_layer": _as_int(kvs.get(f"{arch}.block_count")),
        "n_head": _as_int(kvs.get(f"{arch}.attention.head_count")),
        "n_embd": _as_int(kvs.get(f"{arch}.embedding_length")),
        "n_head_kv": _as_int(kvs.get(f"{arch}.attention.head_count_kv")),
        "head_dim": _as_int(
            kvs.get(f"{arch}.attention.head_size")
            or kvs.get(f"{arch}.attention.key_length")
            or kvs.get(f"{arch}.attention.value_length")
        ),
        # Híbrido SSM/atención (Qwen3-Next/Qwen3.5): solo 1 de cada
        # full_attention_interval capas tiene KV que crece con el contexto;
        # ssm.* alimenta el estado recurrente fijo del estimador de VRAM.
        "full_attention_interval": _as_int(kvs.get(f"{arch}.full_attention_interval")),
        "ssm_inner_size": _as_int(kvs.get(f"{arch}.ssm.inner_size")),
        "ssm_state_size": _as_int(kvs.get(f"{arch}.ssm.state_size")),
        "ssm_conv_kernel": _as_int(kvs.get(f"{arch}.ssm.conv_kernel")),
        "is_moe": ffn_expert_count is not None and ffn_expert_count > 0,
        "ffn_expert_count": ffn_expert_count,
        "ffn_expert_shared_count": _as_int(kvs.get(f"{arch}.ffn_expert_shared_count")),
        "vocab_size": vocab_size,
        "chat_template": chat_template or None,
        "bos_token": _as_int(kvs.get("tokenizer.ggml.bos_token_id")),
        "eos_token": _as_int(kvs.get("tokenizer.ggml.eos_token_id")),
        "thinking_support": thinking_support,
        "enable_thinking_kwarg": enable_thinking_kwarg,
        "instruct_mode": bool(chat_template),
        "suggested_preset": "thinking" if thinking_support else "instruct",
        "tensor_count": header.get("tensor_count"),
        "kv_data": {
            # Todos los KVs escalares: se aprovecha que el scan es poco
            # frecuente — derivaciones futuras no necesitan releer el header.
            k: v for k, v in kvs.items() if not isinstance(v, str)
        },
        "error": None,
    }


def _metadata_from_reader(reader: Any) -> dict[str, Any]:
    """
    Arma el dict de metadata desde un GGUFReader YA ABIERTO.

    Separada a propósito: el paso caro es el constructor de `GGUFReader`
    (parsea en Python puro todo el tokenizer: con vocabularios grandes toma
    decenas de segundos), no esta extracción. Quien ya tiene el reader abierto
    (el scan, para _detect_embedded_modules) reusa la extracción sin reabrir
    el archivo y aprovecha de paso para calentar la cache de metadata.
    """
    fields = {f.name: f for f in reader.fields.values()}

    arch = _field_str(fields.get("general.architecture")) or ""
    chat_template = _field_str(fields.get("tokenizer.chat_template")) or ""

    # Detección en dos capas sobre el template (la heurística por arch
    # daba falsos positivos: qwen3moe/qwen3vlmoe cuantizados traen template
    # sin ningún think):
    # - thinking_support: el template menciona thinking de alguna forma →
    #   alimenta suggested_preset (modelo thinking aunque no use kwarg conocido).
    # - enable_thinking_kwarg: el template lee literalmente el kwarg que la
    #   app envía por --chat-template-kwargs → puerta del builder y griseado UI.
    lowered_template = chat_template.lower()
    thinking_support = "think" in lowered_template
    enable_thinking_kwarg = "enable_thinking" in chat_template

    # El prefijo de las claves de arquitectura es el valor de
    # general.architecture ("qwen3.context_length", "llama.context_length"…).
    # Se cae a "llama." como último recurso porque algunos convertidores
    # viejos escriben todo bajo ese namespace.
    ctx = (
        _field_int(fields.get(f"{arch}.context_length"))
        or _field_int(fields.get(f"{arch}.max_context_length"))
        or _field_int(fields.get("llama.context_length"))
        or _field_int(fields.get("llama.max_context_length"))
    )

    vocab_size = (
        _field_len(fields.get("tokenizer.ggml.tokens"))
        or _field_int(fields.get(f"{arch}.vocab_size"))
        or _field_int(fields.get("llama.vocab_size"))
    )

    # Campos que alimenta el estimador de VRAM (ver vram_estimate.py).
    # Los MoE reales escriben ffn_expert_count; si está ausente el modelo
    # se trata como denso (head_count_kv ausente cae a MHA en el estimador).
    n_head = _field_int(fields.get(f"{arch}.attention.head_count"))
    n_embd = _field_int(fields.get(f"{arch}.embedding_length"))
    ffn_expert_count = _field_int(fields.get(f"{arch}.ffn_expert_count"))

    return {
        "context_length": ctx,
        "architecture": arch or None,
        "model_name": _field_str(fields.get("general.name")),
        "n_layer": _field_int(fields.get(f"{arch}.block_count")),
        "n_head": n_head,
        "n_embd": n_embd,
        "n_head_kv": _field_int(fields.get(f"{arch}.attention.head_count_kv")),
        "head_dim": (
            _field_int(fields.get(f"{arch}.attention.head_size"))
            or _field_int(fields.get(f"{arch}.attention.key_length"))
            or _field_int(fields.get(f"{arch}.attention.value_length"))
        ),
        "full_attention_interval": _field_int(fields.get(f"{arch}.full_attention_interval")),
        "ssm_inner_size": _field_int(fields.get(f"{arch}.ssm.inner_size")),
        "ssm_state_size": _field_int(fields.get(f"{arch}.ssm.state_size")),
        "ssm_conv_kernel": _field_int(fields.get(f"{arch}.ssm.conv_kernel")),
        "is_moe": ffn_expert_count is not None and ffn_expert_count > 0,
        "ffn_expert_count": ffn_expert_count,
        "ffn_expert_shared_count": _field_int(fields.get(f"{arch}.ffn_expert_shared_count")),
        "vocab_size": vocab_size,
        "chat_template": chat_template or None,
        "bos_token": _field_int(fields.get("tokenizer.ggml.bos_token_id")),
        "eos_token": _field_int(fields.get("tokenizer.ggml.eos_token_id")),
        "thinking_support": thinking_support,
        "enable_thinking_kwarg": enable_thinking_kwarg,
        "instruct_mode": bool(chat_template),
        "suggested_preset": "thinking" if thinking_support else "instruct",
        "tensor_count": _field_int(fields.get("GGUF.tensor_count")),
        "kv_data": _reader_kv_data(fields),
        "error": None,
    }


def _reader_kv_data(fields: dict[str, Any]) -> dict[str, Any]:
    """
    KVs escalares del camino reader (misma forma que kv_data del parser
    rápido): todo escalar y array de escalar pequeño. Se excluyen los
    campos sintéticos de la librería (GGUF.*), los strings y los arrays
    de strings (tokens/merges: enormes).
    """
    from gguf import GGUFValueType

    scalar_types = {
        GGUFValueType.UINT8, GGUFValueType.INT8, GGUFValueType.UINT16,
        GGUFValueType.INT16, GGUFValueType.UINT32, GGUFValueType.INT32,
        GGUFValueType.FLOAT32, GGUFValueType.BOOL,
        GGUFValueType.UINT64, GGUFValueType.INT64, GGUFValueType.FLOAT64,
    }
    data: dict[str, Any] = {}
    for name, field in fields.items():
        if name.startswith("GGUF."):
            continue  # sintético de la librería, no es un KV real
        types = getattr(field, "types", None) or []
        if not types or types[-1] not in scalar_types:
            continue  # string o array no-escalar: no materializar
        try:
            contents = field.contents()
        except Exception:
            continue
        if isinstance(contents, (list, tuple)):
            if len(contents) <= _GGUF_MAX_SCALAR_ARRAY:
                data[name] = list(contents)
        elif isinstance(contents, (bool, int, float)):
            data[name] = contents
    return data


def read_gguf_metadata(path: Path) -> dict[str, Any]:
    """
    Lee el header de un .gguf y devuelve su metadata.

    Síncrona a propósito: hace I/O bloqueante (header del archivo), así que
    desde código async hay que llamarla vía `read_gguf_metadata_async`.
    Usa el parser rápido (read_gguf_header_fast: milisegundos, salta los
    arrays grandes del tokenizer) y cae a GGUFReader solo si el parser
    rápido falla.
    Nunca levanta: cualquier problema vuelve como {"error": "..."} para que el
    frontend lo muestre sin romper el flujo de lanzamiento.
    """
    try:
        header = read_gguf_header_fast(path)
        if header is not None:
            try:
                return _metadata_from_kv(header)
            except Exception:
                pass  # header legible pero extracción falló: probar librería
    except Exception:
        pass  # el parser rápido no debería levantar; no bloquear el fallback

    try:
        from gguf import GGUFReader
    except ImportError:
        return _metadata_error("gguf library not installed — run: pip install gguf")

    try:
        reader = GGUFReader(str(path), "r")
        return _metadata_from_reader(reader)
    except Exception as exc:
        return _metadata_error(str(exc))


async def read_gguf_metadata_async(path: Path) -> dict[str, Any]:
    """Wrapper que corre la lectura en un thread del executor por defecto."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, read_gguf_metadata, path)


# --------------------------------------------------------------------------
# Cache de metadata (mtime+size como clave)
# --------------------------------------------------------------------------
#
# La metadata no cambia mientras el archivo no cambie, así que se lee UNA vez
# por proceso y por archivo y se persiste en la entrada (ver
# ModelEntry.metadata). La cache en memoria sigue valiendo la pena aunque el
# parser rápido (read_gguf_header_fast) baje el costo a milisegundos: abrir un
# handle sobre un archivo de 15-17 GB en Windows (mapeo + antivirus
# interceptando cada apertura) es más caro que un dict.
#   - el scan calienta la cache de rebote (el parse del header para detectar
#     MTP/visión ya trae la metadata completa; ver _detect_embedded_modules),
#   y _warm_metadata_cache cubre las entradas que el scan incremental saltó.
# /metadata, /vram-estimate y el gate de thinking del launcher leen de cache.

_METADATA_CACHE: dict[str, tuple[float, float, dict[str, Any]]] = {}
_metadata_locks: dict[str, asyncio.Lock] = {}
_warmup_task: asyncio.Task | None = None


def _metadata_lock(key: str) -> asyncio.Lock:
    lock = _metadata_locks.get(key)
    if lock is None:
        lock = _metadata_locks[key] = asyncio.Lock()
    return lock


def _metadata_cache_put(path: Path, meta: dict[str, Any]) -> None:
    try:
        st = path.stat()
    except OSError:
        return
    _METADATA_CACHE[str(path)] = (st.st_mtime, st.st_size, meta)


def _metadata_cache_get(path: Path) -> dict[str, Any] | None:
    hit = _METADATA_CACHE.get(str(path))
    return hit[2] if hit else None


async def read_gguf_metadata_cached(path: Path) -> dict[str, Any]:
    """
    `read_gguf_metadata` con cache por (mtime, size): la primera lectura de un
    archivo grande tarda lo que tarde GGUFReader, pero cada lectura siguiente
    (y las concurrentes, que se serializan con un lock por path) vuelve de
    memoria sin tocar el archivo.
    """
    loop = asyncio.get_running_loop()
    key = str(path)
    stat = await loop.run_in_executor(None, path.stat)
    hit = _METADATA_CACHE.get(key)
    if hit is not None and hit[0] == stat.st_mtime and hit[1] == stat.st_size:
        return hit[2]
    async with _metadata_lock(key):
        stat = await loop.run_in_executor(None, path.stat)
        hit = _METADATA_CACHE.get(key)
        if hit is not None and hit[0] == stat.st_mtime and hit[1] == stat.st_size:
            return hit[2]
        meta = await read_gguf_metadata_async(path)
        _METADATA_CACHE[key] = (stat.st_mtime, stat.st_size, meta)
        return meta


async def get_entry_metadata(entry: "ModelEntry") -> dict[str, Any]:
    """
    Metadata para una entrada .gguf del inventario.

    Usa la metadata PERSISTIDA en la entrada si el archivo no cambió desde el
    último scan (mismo criterio que el scan incremental): zero I/O, responde
    instantáneo incluso en cold start. Si la entrada no la tiene (esquema
    viejo) o el archivo cambió, relee con la cache en memoria como respaldo.
    Nunca levanta: vuelve como mínimo {"error": "..."}.
    """
    if entry.metadata is not None:
        try:
            if Path(entry.path).stat().st_mtime == entry.mtime:
                return entry.metadata
        except OSError:
            pass
    return await read_gguf_metadata_cached(Path(entry.path))


async def _warm_metadata_cache() -> None:
    """Pre-lee la metadata de todos los .gguf del inventario (una vez)."""
    for entry in inventory.list():
        if entry.format != "gguf":
            continue
        try:
            await read_gguf_metadata_cached(Path(entry.path))
        except Exception:
            logger.exception("warmup de metadata falló para %s", entry.path)


def _start_metadata_warmup() -> None:
    global _warmup_task
    if _warmup_task is not None and not _warmup_task.done():
        return
    try:
        _warmup_task = asyncio.get_running_loop().create_task(_warm_metadata_cache())
    except RuntimeError:
        pass  # sin event loop (tests síncronicos): nada que calentar


# --------------------------------------------------------------------------
# Detección de módulos embebidos (MTP / visión) en el propio .gguf
# --------------------------------------------------------------------------
#
# Ambas funciones leen SOLO el header (metadata + lista de nombres de tensores),
# nunca los datos de los tensores, así que son instantáneas incluso en archivos
# de 16+ GB. Nunca levantan: ante cualquier problema devuelven "no detectado",
# que deja el control manual como está hoy.


def _detect_embedded_modules(path: Path) -> dict[str, Any]:
    """
    ¿El propio .gguf trae MTP/NextN y/o el encoder de visión horneados adentro?

    Una SOLA lectura del header para las dos preguntas — antes eran dos
    funciones separadas, cada una con su propio `GGUFReader(path)`, y cada
    apertura mapea el archivo completo (mmap). Con modelos de 15-17GB y varios
    en la misma carpeta, abrir cada archivo dos veces extra durante el scan
    se nota en Windows (mapeo de archivos grandes + antivirus interceptando
    cada apertura de un handle nuevo). Con read_gguf_header_fast esa lectura
    son milisegundos (no se mapea el archivo, solo se recorre el header) y de
    rebote deja la metadata completa en la cache de metadata para que
    /metadata, /vram-estimate y el gate de thinking no vuelvan a tocar el
    archivo. Si el parser rápido falla (None), cae a GGUFReader (lento pero
    completo).

    Señales, cada una exige metadata KV + tensores reales (no solo la KV
    sola) para no dar falsos positivos con archivos mal formados:
      - MTP: `{arch}.nextn_predict_layers` > 0  +  tensores `*.nextn.*`
      - Visión: KV `clip.*`/`vision.*`  +  tensores `clip.*`/`v.*`

    Devuelve {"mtp_embedded": bool, "mtp_layers": int | None, "vision_embedded": bool}.

    Cualquier excepción se loguea (logger.exception -> models.log) antes de
    caer al fallback seguro: tragarla en silencio hizo que un bug real (doble
    apertura de mmap sobre archivos grandes en Windows) pasara invisible
    durante horas — vale más un log ruidoso que un "no detectado"
    indistinguible de "no tiene esto".
    """
    fallback = {"mtp_embedded": False, "mtp_layers": None, "vision_embedded": False}

    header = read_gguf_header_fast(path)
    if header is not None:
        kvs = header["kvs"]
        arch = kvs.get("general.architecture") or ""

        # Rebote: el header ya está leído y la metadata completa sale de los
        # mismos kvs — se guarda en la cache para que los endpoints no vuelvan
        # a abrir archivos de 15-17 GB.
        try:
            _metadata_cache_put(path, _metadata_from_kv(header))
        except Exception:
            pass  # si no se pudo derivar, los endpoints la re-leerán

        tensor_names = header["tensor_names"]
        layers = _as_int(kvs.get(f"{arch}.nextn_predict_layers")) or 0
        has_nextn_tensors = any(".nextn." in name for name in tensor_names)
        mtp_embedded = layers > 0 and has_nextn_tensors

        kv_names = header["kv_names"]
        has_clip_kv = any(name.startswith("clip.") or name.startswith("vision.") for name in kv_names)
        has_clip_tensors = any(name.startswith("clip.") or name.startswith("v.") for name in tensor_names)
        vision_embedded = has_clip_kv and has_clip_tensors

        return {
            "mtp_embedded": mtp_embedded,
            "mtp_layers": layers if mtp_embedded else None,
            "vision_embedded": vision_embedded,
        }

    return _detect_embedded_modules_via_reader(path, fallback)


def _detect_embedded_modules_via_reader(
    path: Path, fallback: dict[str, Any]
) -> dict[str, Any]:
    """
    Fallback lento: abre GGUFReader (parsea todo el tokenizer en Python puro:
    minutos en vocabularios grandes). Solo se llega aquí si el parser rápido
    devolvió None.
    """
    try:
        from gguf import GGUFReader
    except ImportError:
        return fallback

    try:
        reader = GGUFReader(str(path), "r")
        fields = {f.name: f for f in reader.fields.values()}
        tensor_names = [tensor.name for tensor in reader.tensors]  # un solo recorrido

        # Rebote: abrir el reader es LA parte cara (decenas de segundos en
        # tokenizers grandes) y la metadata completa sale de los mismos
        # fields, así que se guarda en la cache de metadata para que
        # /metadata, /vram-estimate y el gate de thinking del launcher no
        # vuelvan a abrir archivos de 15-17 GB.
        try:
            _metadata_cache_put(path, _metadata_from_reader(reader))
        except Exception:
            pass  # si no se pudo derivar, los endpoints la re-leerán

        arch = _field_str(fields.get("general.architecture")) or ""
        layers = _field_int(fields.get(f"{arch}.nextn_predict_layers")) or 0
        has_nextn_tensors = any(".nextn." in name for name in tensor_names)
        mtp_embedded = layers > 0 and has_nextn_tensors

        has_clip_kv = any(name.startswith("clip.") or name.startswith("vision.") for name in fields)
        has_clip_tensors = any(name.startswith("clip.") or name.startswith("v.") for name in tensor_names)
        vision_embedded = has_clip_kv and has_clip_tensors

        return {
            "mtp_embedded": mtp_embedded,
            "mtp_layers": layers if mtp_embedded else None,
            "vision_embedded": vision_embedded,
        }
    except Exception:
        logger.exception("excepcion detectando modulos embebidos en %s", path)
        return fallback


# --------------------------------------------------------------------------
# Scanner
# --------------------------------------------------------------------------


def _walk_dir(root: Path, max_depth: int):
    """
    Recorre `root` recursivamente hasta `max_depth` niveles, yieldeando
    (path, kind) donde kind es "gguf", "ggml" o "safetensors_dir".
    """
    if not root.exists() or not root.is_dir():
        return

    def _walk(current: Path, depth: int):
        try:
            entries = list(current.iterdir())
        except (PermissionError, OSError):
            return

        # Directorio HuggingFace: config.json + al menos un *.safetensors
        has_config = any(e.name == "config.json" for e in entries)
        has_safetensors = any(e.suffix == ".safetensors" for e in entries)
        if has_config and has_safetensors:
            yield (current, "safetensors_dir")
            # No seguimos bajando dentro de un directorio de modelo HF ya detectado.
            return

        if depth >= max_depth:
            return

        for entry in entries:
            if entry.is_file():
                if entry.suffix.lower() == ".gguf":
                    yield (entry, "gguf")
                elif entry.suffix.lower() == ".ggml":
                    yield (entry, "ggml")
            elif entry.is_dir():
                yield from _walk(entry, depth + 1)

    yield from _walk(root, 0)


def _build_gguf_entry(path: Path, existing: dict[str, Any] | None) -> ModelEntry:
    stem = path.stem
    family, parameters, quantization = _extract_metadata(stem)
    mmproj = _find_mmproj(path.parent)
    detected = _detect_embedded_modules(path)
    stat = path.stat()

    tags = existing.get("tags", []) if existing else []

    # _detect_embedded_modules ya leyó el header (rápido) y guardó la
    # metadata completa en la cache: la reutilizamos aquí, sin reabrir.
    metadata = _metadata_cache_get(path)

    return ModelEntry(
        id=_make_id(path),
        name=stem,
        filename=path.name,
        path=str(path.resolve()),
        format="gguf",
        size_gb=round(stat.st_size / (1024**3), 2),
        quantization=quantization,
        family=family,
        parameters=parameters,
        compatible_backends=_compatible_backends("gguf"),
        has_mmproj=mmproj is not None,
        mmproj_path=str(mmproj.resolve()) if mmproj else None,
        mtp_embedded=detected["mtp_embedded"],
        mtp_embedded_layers=detected["mtp_layers"],
        has_vision_embedded=detected["vision_embedded"],
        last_seen=datetime.now(timezone.utc).isoformat(),
        tags=tags,
        mtime=stat.st_mtime,
        metadata=metadata,
    )


def _build_ggml_entry(path: Path, existing: dict[str, Any] | None) -> ModelEntry:
    stem = path.stem
    family, parameters, quantization = _extract_metadata(stem)
    stat = path.stat()
    tags = existing.get("tags", []) if existing else []

    return ModelEntry(
        id=_make_id(path),
        name=stem,
        filename=path.name,
        path=str(path.resolve()),
        format="ggml",
        size_gb=round(stat.st_size / (1024**3), 2),
        quantization=quantization,
        family=family,
        parameters=parameters,
        compatible_backends=_compatible_backends("ggml"),
        has_mmproj=False,
        mmproj_path=None,
        last_seen=datetime.now(timezone.utc).isoformat(),
        tags=tags,
        mtime=stat.st_mtime,
    )


def _build_safetensors_entry(directory: Path, existing: dict[str, Any] | None) -> ModelEntry:
    name = directory.name
    family, parameters, quantization = _extract_metadata(name)
    total_bytes = sum(
        f.stat().st_size for f in directory.glob("*.safetensors") if f.is_file()
    )
    stat = directory.stat()
    tags = existing.get("tags", []) if existing else []

    return ModelEntry(
        id=_make_id(directory),
        name=name,
        filename=directory.name,
        path=str(directory.resolve()),
        format="safetensors",
        size_gb=round(total_bytes / (1024**3), 2),
        quantization=quantization,
        family=family,
        parameters=parameters,
        compatible_backends=_compatible_backends("safetensors"),
        has_mmproj=False,
        mmproj_path=None,
        last_seen=datetime.now(timezone.utc).isoformat(),
        tags=tags,
        mtime=stat.st_mtime,
    )


# --------------------------------------------------------------------------
# Inventario (cache persistido en data/models.json)
# --------------------------------------------------------------------------


async def _read_cache_schema_version(path: Path = SCHEMA_VERSION_PATH) -> int:
    """Versión de esquema con la que se escribió el cache actual (0 = desconocida)."""
    if not path.exists():
        return 0
    try:
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            raw = await f.read()
        return int(json.loads(raw).get("version", 0))
    except Exception:
        return 0


async def _write_cache_schema_version(
    version: int, path: Path = SCHEMA_VERSION_PATH
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(json.dumps({"version": version}))


class ModelInventory:
    def __init__(self, path: Path = MODELS_CACHE_PATH) -> None:
        self.path = path
        self._by_id: dict[str, ModelEntry] = {}
        self._loaded = False

    async def load(self) -> None:
        stored_version = await _read_cache_schema_version()
        if stored_version != MODELS_CACHE_SCHEMA_VERSION:
            # El cache se escribió con un esquema anterior: sus entradas no
            # tienen los campos derivados del header (mtp_embedded, kv_data,
            # etc.) y el scan incremental por mtime nunca los recalcularía,
            # porque los archivos en disco no cambiaron. Se descarta el cache
            # entero: el próximo scan relee headers (rápido) y reconstruye
            # todo.
            self._by_id = {}
            self._loaded = True
            await _write_cache_schema_version(MODELS_CACHE_SCHEMA_VERSION)
            return

        if not self.path.exists():
            self._by_id = {}
            self._loaded = True
            return

        async with aiofiles.open(self.path, "r", encoding="utf-8") as f:
            raw = await f.read()

        try:
            items = json.loads(raw) if raw.strip() else []
        except json.JSONDecodeError:
            items = []

        self._by_id = {}
        for item in items:
            try:
                entry = ModelEntry(**item)
                self._by_id[entry.id] = entry
            except Exception:
                # Entrada corrupta o de un esquema viejo: se descarta silenciosamente.
                continue
        self._loaded = True

    async def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [entry.model_dump() for entry in self._by_id.values()]
        async with aiofiles.open(self.path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(payload, indent=2, ensure_ascii=False))

    async def ensure_loaded(self) -> None:
        if not self._loaded:
            await self.load()

    def list(self) -> list[ModelEntry]:
        return sorted(self._by_id.values(), key=lambda m: m.name.lower())

    def get(self, model_id: str) -> ModelEntry | None:
        return self._by_id.get(model_id)

    def delete(self, model_id: str) -> bool:
        return self._by_id.pop(model_id, None) is not None

    def patch(self, model_id: str, patch: ModelPatch) -> ModelEntry | None:
        entry = self._by_id.get(model_id)
        if entry is None:
            return None
        data = entry.model_dump()
        updates = patch.model_dump(exclude_unset=True, exclude_none=True)
        data.update(updates)
        updated = ModelEntry(**data)
        self._by_id[model_id] = updated
        return updated

    async def scan(self, model_dirs: list[str]) -> AsyncGenerator[dict[str, Any], None]:
        """
        Generador async que escanea `model_dirs` y va emitiendo eventos de
        progreso. El último evento tiene status="complete" con el resumen.

        Scan incremental: si un path ya está en cache y su mtime no cambió,
        se reusa la entrada existente (no se re-parsea metadata) — solo se
        refresca `last_seen`.
        """
        start = time.monotonic()
        seen_ids: set[str] = set()
        found_total = 0
        new_count = 0
        loop = asyncio.get_running_loop()

        existing_by_path = {entry.path: entry for entry in self._by_id.values()}

        for raw_dir in model_dirs:
            root = Path(raw_dir)
            found_in_dir = 0

            for target, kind in _walk_dir(root, MAX_SCAN_DEPTH):
                resolved = str(target.resolve())
                existing = existing_by_path.get(resolved)
                stat = target.stat()

                # Scan incremental: mismo path y mismo mtime -> no reprocesar.
                if existing is not None and existing.mtime == stat.st_mtime:
                    existing.last_seen = datetime.now(timezone.utc).isoformat()
                    seen_ids.add(existing.id)
                    found_in_dir += 1
                    found_total += 1
                    continue

                existing_dict = existing.model_dump() if existing else None
                # Todo el build corre en el executor para NUNCA bloquear el
                # event loop: el header GGUF se lee con el parser rápido
                # (milisegundos) pero queda el I/O del stat/mmproj y el
                # fallback lento de GGUFReader — con la versión síncrona
                # anterior, todo el backend (health, metadata, launch) se
                # congelaba mientras el scan procesaba los archivos.
                if kind == "gguf":
                    entry = await loop.run_in_executor(None, _build_gguf_entry, target, existing_dict)
                elif kind == "ggml":
                    entry = await loop.run_in_executor(None, _build_ggml_entry, target, existing_dict)
                else:
                    entry = await loop.run_in_executor(None, _build_safetensors_entry, target, existing_dict)

                if existing is None:
                    new_count += 1

                self._by_id[entry.id] = entry
                seen_ids.add(entry.id)
                found_in_dir += 1
                found_total += 1

                # Cede el control al event loop para que el stream SSE llegue
                # al cliente de forma incremental en directorios grandes.
                await asyncio.sleep(0)

            yield {
                "status": "scanning",
                "dir": raw_dir,
                "found": found_in_dir,
                "total_so_far": found_total,
            }

        # Cualquier entrada que no fue vista en este scan y cuyo archivo/carpeta
        # ya no existe físicamente se elimina del inventario.
        removed = 0
        for model_id in list(self._by_id.keys()):
            entry = self._by_id[model_id]
            if model_id not in seen_ids and not Path(entry.path).exists():
                del self._by_id[model_id]
                removed += 1

        await self.save()

        logger.info(
            "scan completo: total=%d nuevos=%d removidos=%d duracion_s=%.2f dirs=%s",
            len(self._by_id), new_count, removed, time.monotonic() - start, model_dirs,
        )

        # Las entradas que el scan incremental saltó (mtime sin cambios) no
        # pasaron por _detect_embedded_modules, así que la cache de metadata
        # las cubre en background: cuando el usuario elige ese modelo, la
        # metadata ya está en memoria.
        _start_metadata_warmup()

        yield {
            "status": "complete",
            "total": len(self._by_id),
            "new": new_count,
            "removed": removed,
            "duration_s": round(time.monotonic() - start, 2),
        }


inventory = ModelInventory()


def _group_folder_id(folder: Path) -> str:
    return hashlib.sha256(str(folder.resolve()).encode("utf-8")).hexdigest()[:8]


def build_groups(entries: list[ModelEntry]) -> list[ModelGroup]:
    """
    Agrupa el inventario plano por carpeta padre (mejora M5). Dentro de cada
    carpeta, clasifica por presencia de un token en el nombre de archivo
    (no exige que sea prefijo estricto, porque en la práctica muchos GGUF
    llevan "mtp"/"mmproj" en medio del nombre, ej. "Qwen2.5-VL-7B-mmproj-f16.gguf"):
      - contiene "mtp"    -> draft model MTP
      - contiene "mmproj" -> módulo de visión
      - resto             -> cuantización base
    """
    by_folder: dict[str, list[ModelEntry]] = {}
    for entry in entries:
        # Para GGUF/GGML, path es el archivo -> la carpeta padre es su directorio.
        # Para safetensors, path YA es el directorio del modelo -> agrupamos por
        # su propio padre, ya que ese directorio identifica una única variante.
        entry_path = Path(entry.path)
        # Para GGUF/GGML, path es el archivo -> la carpeta padre es su directorio.
        # Para safetensors, path ya es el directorio del modelo -> también usamos
        # su padre, de forma que variantes hermanas queden en el mismo grupo.
        folder = entry_path.parent
        by_folder.setdefault(str(folder), []).append(entry)

    groups: list[ModelGroup] = []
    for folder_str, items in by_folder.items():
        folder = Path(folder_str)
        base_models, mtp_models, mmproj_models = [], [], []
        for item in items:
            lowered = item.filename.lower()
            if "mtp" in lowered:
                mtp_models.append(item)
            elif "mmproj" in lowered:
                mmproj_models.append(item)
            else:
                base_models.append(item)

        groups.append(
            ModelGroup(
                group_id=_group_folder_id(folder),
                name=folder.name,
                folder=str(folder),
                base_models=base_models,
                mtp_models=mtp_models,
                mmproj_models=mmproj_models,
            )
        )

    groups.sort(key=lambda g: g.name.lower())
    return groups

# --------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------

router = APIRouter()


@router.get("", response_model=list[ModelEntry])
async def list_models() -> list[ModelEntry]:
    await inventory.ensure_loaded()
    return inventory.list()


@router.get("/groups", response_model=list[ModelGroup])
async def list_model_groups() -> list[ModelGroup]:
    # IMPORTANTE: esta ruta debe registrarse antes que GET /{model_id}, o
    # FastAPI matchearía "groups" como si fuera un model_id.
    await inventory.ensure_loaded()
    return build_groups(inventory.list())


@router.get("/{model_id}", response_model=ModelEntry)
async def get_model(model_id: str) -> ModelEntry:
    await inventory.ensure_loaded()
    entry = inventory.get(model_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Modelo no encontrado en el inventario")
    return entry


@router.get("/{model_id}/exists")
async def model_exists(model_id: str) -> dict[str, bool]:
    await inventory.ensure_loaded()
    entry = inventory.get(model_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Modelo no encontrado en el inventario")
    return {"exists": Path(entry.path).exists()}


@router.get("/{model_id}/metadata")
async def get_model_metadata(model_id: str) -> dict[str, Any]:
    """
    Metadata leída del header del .gguf.

    Solo el modelo inexistente da 404: cualquier problema de lectura vuelve
    con status 200 y "error" != None, para que el Launcher pueda seguir
    configurando el lanzamiento aunque no haya podido leer el archivo.
    """
    await inventory.ensure_loaded()
    entry = inventory.get(model_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Modelo no encontrado en el inventario")

    if entry.format != "gguf":
        return _metadata_error(
            f"Solo se puede leer metadata de archivos .gguf (este modelo es {entry.format})"
        )

    path = Path(entry.path)
    if not path.is_file():
        return _metadata_error("El archivo del modelo ya no existe en disco")

    return await get_entry_metadata(entry)


@router.get("/{model_id}/vram-estimate")
async def get_model_vram_estimate(
    model_id: str,
    n_ctx: int = 65536,
    cache_type_k: str = "f16",
    cache_type_v: str = "f16",
    n_parallel: int = 1,
    flash_attn: bool = False,
) -> dict[str, Any]:
    """
    VRAM estimada (pesos + KV + cómputo) para este modelo con los parámetros
    de lanzamiento dados, comparada con la VRAM declarada en
    hardware.vram_gb. Como el endpoint de metadata, siempre responde 200:
    modelo no .gguf o archivo ilegible devuelven {"available": false, ...}
    para que el Launcher oculte la tarjeta sin romper nada. Con éxito incluye
    además "legend": el total de VRAM para cada contexto común (N_CTX_PRESETS),
    para que la UI muestre cómo crece el consumo al subir el contexto.
    """
    await inventory.ensure_loaded()
    entry = inventory.get(model_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Modelo no encontrado en el inventario")

    if entry.format != "gguf":
        return {
            "available": False,
            "error": f"Solo se puede estimar VRAM de modelos .gguf (este modelo es {entry.format})",
        }

    path = Path(entry.path)
    if not path.is_file():
        return {"available": False, "error": "El archivo del modelo ya no existe en disco"}

    meta = await get_entry_metadata(entry)
    if meta.get("error"):
        return {"available": False, "error": meta["error"]}

    gpu_vram_gb = float(config.get("hardware.vram_gb") or 0)
    result = estimate_vram_usage(
        meta,
        model_size_gb=entry.size_gb,
        n_ctx=n_ctx,
        cache_type_k=cache_type_k,
        cache_type_v=cache_type_v,
        n_parallel=n_parallel,
        flash_attn=flash_attn,
        gpu_vram_gb=gpu_vram_gb,
    )
    if result.get("available"):
        result["legend"] = estimate_vram_legend(
            meta,
            model_size_gb=entry.size_gb,
            cache_type_k=cache_type_k,
            cache_type_v=cache_type_v,
            n_parallel=n_parallel,
            flash_attn=flash_attn,
            gpu_vram_gb=gpu_vram_gb,
        )
    return result


@router.delete("/{model_id}")
async def delete_model(model_id: str) -> dict[str, bool]:
    await inventory.ensure_loaded()
    removed = inventory.delete(model_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Modelo no encontrado en el inventario")
    await inventory.save()
    return {"deleted": True}


@router.patch("/{model_id}", response_model=ModelEntry)
async def patch_model(model_id: str, patch: ModelPatch) -> ModelEntry:
    await inventory.ensure_loaded()
    updated = inventory.patch(model_id, patch)
    if updated is None:
        raise HTTPException(status_code=404, detail="Modelo no encontrado en el inventario")
    await inventory.save()
    return updated


@router.post("/scan")
async def scan_models() -> StreamingResponse:
    await inventory.ensure_loaded()
    model_dirs = config.get("model_dirs", [])

    async def event_stream() -> AsyncGenerator[str, None]:
        async for event in inventory.scan(model_dirs):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
