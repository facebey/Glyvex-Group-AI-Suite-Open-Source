"""
chat_presets.py — presets de muestreo built-in del Chat (CHAT-PRESETS, U1).

Los valores vienen de las cards oficiales de Hugging Face verificadas el
2026-11-14 (Qwen/Qwen3-Coder-30B-A3B-Instruct, Qwen/Qwen3.8-27B y
google/gemma-4-26B-A4B-it). La semántica de las penalidades está comprobada
contra llama.cpp: `repeat_penalty` replica la regla de `repetition_penalty`
de HF (divide logits positivos, multiplica los negativos;
src/llama-sampler.cpp, window `penalty_last_n` = contexto completo por
default del server) y `presence_penalty` es aditiva por presencia
(server-task.cpp), igual que la variante OpenAI/HF. Por eso los valores de
las cards se trasladan directos a los nombres de llama-server.

Los ids son estables: la UI los traduce vía labelKey y las variantes de una
misma familia se distinguen por `mode` (None = aplica a cualquier modo).
"""

from __future__ import annotations

import re

BUILTIN_PRESETS: list[dict] = [
    {
        "id": "qwen3-coder",
        "family": "qwen3-coder",
        "mode": None,
        "labelKey": "chatPresets.qwen3Coder",
        "params": {
            "temperature": 0.7,
            "top_p": 0.8,
            "top_k": 20,
            "repeat_penalty": 1.05,
        },
    },
    {
        "id": "qwen3.8-thinking",
        "family": "qwen3.8",
        "mode": "thinking",
        "labelKey": "chatPresets.qwen38Thinking",
        "params": {
            "temperature": 1.0,
            "top_p": 0.95,
            "top_k": 20,
            "min_p": 0.0,
        },
    },
    {
        "id": "qwen3.8-instruct",
        "family": "qwen3.8",
        "mode": "instruct",
        "labelKey": "chatPresets.qwen38Instruct",
        # presence 1.5 es el valor de la card, ajustable 0-2 según la guía.
        "params": {
            "temperature": 0.7,
            "top_p": 0.8,
            "top_k": 20,
            "presence_penalty": 1.5,
        },
    },
    {
        "id": "gemma4",
        "family": "gemma4",
        "mode": None,
        "labelKey": "chatPresets.gemma4",
        # Google estandariza estos valores para Gemma 4 en todos los usos.
        "params": {
            "temperature": 1.0,
            "top_p": 0.95,
            "top_k": 64,
        },
    },
]

# El Chat conoce el nombre del modelo del endpoint, no el path del GGUF, así
# que la familia se resuelve sobre ese nombre. Orden = prioridad: el patrón
# más específico primero (coder antes que la familia Qwen general, y "3.8"
# con el punto literal para no tragarse un Qwen3-8B antiguo).
_FAMILY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("qwen3-coder", re.compile(r"\bqwen[.\s_-]?3[.\s_-]?coder", re.I)),
    ("qwen3.8", re.compile(r"\bqwen[.\s_-]?3\.8\b", re.I)),
    ("gemma4", re.compile(r"\bgemma[.\s_-]?4\b", re.I)),
]


# --------------------------------------------------------------------------
# Validación de presets de usuario (API POST /api/chat/presets)
# --------------------------------------------------------------------------

# Rangos que llama-server acepta (tools/server/README.md). Fuera de ellos el
# preset no tiene sentido y la API lo rechaza con 422.
PARAM_LIMITS: dict[str, tuple[float, float]] = {
    "temperature": (0.0, 2.0),
    "top_p": (0.0, 1.0),
    "top_k": (1, 400),
    "min_p": (0.0, 1.0),
    "repeat_penalty": (0.0, 3.0),
    "presence_penalty": (-2.0, 2.0),
}

FAMILY_KEYS = {family for family, _ in _FAMILY_PATTERNS}


def detect_family(model_name: str | None) -> str | None:
    """Clave de preset para un nombre de modelo del endpoint, o None."""
    name = (model_name or "").strip()
    if not name:
        return None
    for family, pattern in _FAMILY_PATTERNS:
        if pattern.search(name):
            return family
    return None


def validate_params(params: dict) -> tuple[dict, list[str]]:
    """Valida los params de un preset de usuario. Retorna (params sanos, errores).

    top_k es el único entero; los demás aceptan int o float. Un preset sin
    ningún parámetro no tiene sentido: se reporta como error.
    """
    clean: dict[str, float | int] = {}
    errors: list[str] = []
    for key, value in params.items():
        limits = PARAM_LIMITS.get(key)
        if limits is None:
            errors.append(f"parámetro desconocido: {key}")
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{key} debe ser un número")
            continue
        if key == "top_k":
            if not float(value).is_integer():
                errors.append("top_k debe ser un entero")
                continue
            clean[key] = int(value)
        else:
            clean[key] = float(value)
        lo, hi = limits
        if not lo <= clean[key] <= hi:
            errors.append(f"{key} fuera de rango ({lo} a {hi})")
            del clean[key]
    if not params:
        errors.append("el preset debe incluir al menos un parámetro")
    return clean, errors


def resolve_builtin_presets(model_name: str, mode: str | None = None) -> list[dict]:
    """Presets built-in aplicables a un modelo, marcando el recomendado.

    `mode` ("thinking"/"instruct") decide entre variantes de una misma
    familia. Sin `mode`, solo se recomienda un preset único de la familia
    (los de una sola variante); una familia con variantes se devuelve entera
    para que la UI las liste sin adivinar.
    """
    family = detect_family(model_name)
    if family is None:
        return []
    presets = [dict(p, builtin=True) for p in BUILTIN_PRESETS if p["family"] == family]
    recommended_id = None
    if mode in ("thinking", "instruct"):
        for p in presets:
            if p["mode"] == mode:
                recommended_id = p["id"]
                break
    if recommended_id is None and len(presets) == 1:
        recommended_id = presets[0]["id"]
    for p in presets:
        p["recommended"] = p["id"] == recommended_id
    return presets
