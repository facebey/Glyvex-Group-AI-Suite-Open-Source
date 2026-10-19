"""
session_tokens.py — Acumulador de tokens del chat por sesión (MON-2).

El panel "Costo de sesión" del Monitor muestra cuánto consumió la sesión
local y cuánto habría costado en la nube (precios de config.cloud_pricing).

- Solo cuenta el chat: el benchmark es prueba deliberada, no uso.
- Se persiste en <DATA_DIR>/session_tokens.json (una escritura por turno
  cerrado) para que el total sobreviva reinicios de la app. El usuario puede
  reiniciarlo desde el Monitor.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiofiles
from fastapi import APIRouter
from paths import DATA_DIR

from config import get_cloud_pricing

SESSION_TOKENS_PATH = DATA_DIR / "session_tokens.json"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fresh_state() -> dict[str, Any]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cached_tokens": 0,
        "turns": 0,
        "started_at": None,
        "updated_at": None,
    }


class SessionTokens:
    """Total de la sesión en memoria, persistido en JSON."""

    def __init__(self, path: Path = SESSION_TOKENS_PATH) -> None:
        self.path = path
        self._state = _fresh_state()

    @property
    def state(self) -> dict[str, Any]:
        return self._state

    def reset_state(self) -> None:
        self._state = _fresh_state()

    def snapshot(self) -> dict[str, Any]:
        return dict(self._state)

    async def load(self) -> None:
        """Carga el total acumulado de disco (arranque de la app)."""
        try:
            async with aiofiles.open(self.path, "r", encoding="utf-8") as f:
                raw = await f.read()
            loaded = json.loads(raw) if raw.strip() else {}
        except (OSError, json.JSONDecodeError):
            # Sin archivo o archivo roto: se arranca desde cero sin advertir.
            return
        if not isinstance(loaded, dict):
            return
        for key in self._state:
            if key in loaded:
                self._state[key] = loaded[key]

    async def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(self.path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(self._state, indent=2))

    async def record_turn(
        self,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cached_tokens: int = 0,
    ) -> None:
        """Suma el uso de un turno cerrado (StreamMetrics ya sumó sus rondas)."""
        self._state["prompt_tokens"] += int(prompt_tokens or 0)
        self._state["completion_tokens"] += int(completion_tokens or 0)
        self._state["cached_tokens"] += int(cached_tokens or 0)
        self._state["turns"] += 1
        now = _utcnow()
        self._state["updated_at"] = now
        if self._state["started_at"] is None:
            self._state["started_at"] = now
        await self._save()

    async def reset(self) -> dict[str, Any]:
        self._state = _fresh_state()
        await self._save()
        return self.snapshot()


# Instancia única compartida por toda la app (misma convención que los
# managers de metrics/llm_metrics: se registra en main.py).
session_tokens = SessionTokens()


# --------------------------------------------------------------------------
# Router: /api/metrics/session-tokens (se registra en main.py con prefix
# "/metrics", misma convención que metrics y metrics_export)
# --------------------------------------------------------------------------

router = APIRouter()


@router.get("/session-tokens")
async def get_session_tokens() -> dict[str, Any]:
    """Total de la sesión + precios de nube: el frontend calcula el costo
    con lo que trae acá mismo, así un cambio en Config se refleja sin
    otra petición."""
    return {
        "tokens": session_tokens.snapshot(),
        "pricing": get_cloud_pricing().model_dump(),
    }


@router.post("/session-tokens/reset")
async def reset_session_tokens() -> dict[str, Any]:
    return await session_tokens.reset()
