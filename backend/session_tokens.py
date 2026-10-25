"""
session_tokens.py — Acumulador de tokens del chat por sesión (MON-2).

El panel "Costo de sesión" del Monitor muestra cuánto consumió la sesión
local y cuánto habría costado en la nube (precios de config.cloud_pricing).

- Solo cuenta el chat: el benchmark es prueba deliberada, no uso.
- Se persiste en <DATA_DIR>/session_tokens.json (una escritura por turno
  cerrado) para que el total sobreviva reinicios de la app. El usuario puede
  reiniciar la SESIÓN desde el Monitor; el total "lifetime" (desglose
  chat-vs-externo del panel MON-5) nunca se borra con reset().
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


def _fresh_portion() -> dict[str, Any]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cached_tokens": 0,
        "turns": 0,
        "started_at": None,
        "updated_at": None,
    }


def _fresh_state() -> dict[str, Any]:
    state = _fresh_portion()
    # Lifetime total (MON-5): reset() NUNCA lo toca. Es lo que permite
    # separar chat de la app vs consumo externo en el total "todas las
    # instancias"; la sesión reiniciable es la parte de arriba.
    state["lifetime"] = _fresh_portion()
    return state


class SessionTokens:
    """Total de la sesión + lifetime en memoria, persistido en JSON."""

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
        # Normaliza el lifetime. Un archivo anterior a este campo solo tiene
        # la sesión: esa sesión pasa a ser el lifetime conocido (el chat
        # anterior a un reset ya no es recuperable, y la UI lo aclara).
        life = _fresh_portion()
        src = loaded.get("lifetime")
        if not isinstance(src, dict):
            src = loaded
        for key in life:
            if key in src:
                life[key] = src[key]
        self._state["lifetime"] = life

    async def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(self.path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(self._state, indent=2))

    def _apply_turn(
        self,
        portion: dict[str, Any],
        prompt_tokens: int,
        completion_tokens: int,
        cached_tokens: int,
        now: str,
    ) -> None:
        portion["prompt_tokens"] += int(prompt_tokens or 0)
        portion["completion_tokens"] += int(completion_tokens or 0)
        portion["cached_tokens"] += int(cached_tokens or 0)
        portion["turns"] += 1
        portion["updated_at"] = now
        if portion["started_at"] is None:
            portion["started_at"] = now

    async def record_turn(
        self,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cached_tokens: int = 0,
    ) -> None:
        """Suma el uso de un turno cerrado (StreamMetrics ya sumó sus rondas).

        Registra la sesión Y el lifetime: el reset borra la sesión pero el
        lifetime es lo que mantiene trazable el chat de por vida (MON-5).
        """
        now = _utcnow()
        self._apply_turn(self._state, prompt_tokens, completion_tokens, cached_tokens, now)
        self._apply_turn(
            self._state["lifetime"], prompt_tokens, completion_tokens, cached_tokens, now
        )
        await self._save()

    async def reset(self) -> dict[str, Any]:
        """Zera la sesión; el total lifetime no se pierde nunca."""
        lifetime = self._state.get("lifetime") or _fresh_portion()
        self._state = _fresh_state()
        self._state["lifetime"] = lifetime
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
