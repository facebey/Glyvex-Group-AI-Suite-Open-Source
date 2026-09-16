"""
stream_metrics.py — Métricas de una generación en streaming (chat y benchmark).

Por qué existe: chat.py y benchmark.py medían t/s y TTFT solo a partir de
`delta.content`. llama-server (con el --reasoning-format por defecto) manda el
razonamiento aparte, en `delta.reasoning_content`, así que:

- TTFT medía "hasta la primera palabra de la respuesta" e incluía todo el
  razonamiento (9 s en vez de ~200 ms).
- t/s dividía `usage.completion_tokens` (que SÍ incluye el razonamiento) por
  el tiempo desde la primera palabra visible: 164 tokens / 9 ms = 18.392 t/s.

Reglas de este módulo:

1. El primer token es el primer delta de cualquier tipo: razonamiento,
   contenido o tool_calls. El primero visible de la respuesta se registra
   aparte (`ttft_answer_ms`).
2. Numerador y denominador salen siempre de la misma fuente. Por prioridad:
   - "timings": el objeto que llama-server manda en el stream
     (predicted_n / predicted_ms, prompt_n / prompt_ms). Exacto.
   - "usage": completion_tokens del upstream sobre el tiempo de decode
     medido en la ronda.
   - "chunks": cantidad de chunks sobre el tiempo de decode. Aproximado.
3. Los turnos con tools tienen varias rondas. Se suma token y tiempo de
   decode de cada ronda, así la espera de una búsqueda web entre rondas no
   baja los t/s.

Sin dependencias: se testea sin levantar FastAPI ni un servidor.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Literal

MetricsSource = Literal["timings", "usage", "chunks"]

_SOURCE_RANK = {"chunks": 0, "usage": 1, "timings": 2}


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def reasoning_from_delta(delta: dict[str, Any]) -> str:
    """
    Texto de razonamiento de un delta, si lo trae.

    llama-server y LM Studio usan `reasoning_content`; vLLM y el endpoint
    OpenAI-compatible de Ollama usan `reasoning`.
    """
    for key in ("reasoning_content", "reasoning"):
        value = delta.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


class StreamMetrics:
    """Acumulador de métricas para un turno de una o varias rondas."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.start = clock()
        self.first_token_at: float | None = None
        self.first_answer_at: float | None = None

        # Acumulado de rondas cerradas.
        self._tokens = 0
        self._thinking = 0
        self._decode_tokens = 0
        self._decode_s = 0.0
        self._prompt_tokens = 0
        self._prompt_s = 0.0
        self._source: MetricsSource | None = None
        self._context_tokens: int | None = None

        self._reset_round()

    # -- ciclo de vida de una ronda ------------------------------------------

    def _reset_round(self) -> None:
        self._round_start: float | None = None
        self._round_first: float | None = None
        self._round_last: float | None = None
        self._round_chunks = 0
        self._round_thinking_chunks = 0
        self._round_usage: dict[str, Any] | None = None
        self._round_timings: dict[str, Any] | None = None
        self._round_set_first = False
        self._round_set_answer = False

    def begin_round(self) -> None:
        self._reset_round()
        self._round_start = self._clock()

    def observe_chunk(self, chunk: dict[str, Any]) -> None:
        """Lee usage y timings. Llamar con cada chunk JSON parseado."""
        usage = chunk.get("usage")
        if isinstance(usage, dict):
            self._round_usage = usage
        timings = chunk.get("timings")
        if isinstance(timings, dict):
            # Los valores son acumulados del request: el último que llega
            # es el bueno (con timings_per_token llegan en cada chunk).
            self._round_timings = timings

    def mark_token(self, *, thinking: bool) -> None:
        """Un delta con contenido generado (razonamiento, texto o tool_calls)."""
        now = self._clock()
        if self._round_start is None:
            self._round_start = now
        if self._round_first is None:
            self._round_first = now
        self._round_last = now
        self._round_chunks += 1
        if thinking:
            self._round_thinking_chunks += 1
        if self.first_token_at is None:
            self.first_token_at = now
            self._round_set_first = True

    def mark_answer(self) -> None:
        """Primer texto visible de la respuesta (fuera del razonamiento)."""
        if self.first_answer_at is None:
            self.first_answer_at = self._clock()
            self._round_set_answer = True

    def discard_round(self) -> None:
        """
        Ronda descartada (el guard de "continuar" detectó que el modelo
        reinició la respuesta): no suma tokens y, si fue la que marcó el primer
        token, TTFT se vuelve a medir con la ronda siguiente.
        """
        if self._round_set_first:
            self.first_token_at = None
        if self._round_set_answer:
            self.first_answer_at = None
        self._reset_round()

    def end_round(self) -> None:
        timings = self._round_timings or {}
        usage = self._round_usage or {}

        predicted_n = _num(timings.get("predicted_n"))
        predicted_ms = _num(timings.get("predicted_ms"))
        completion = _num(usage.get("completion_tokens"))

        # -- tokens y tiempo de decode ---------------------------------------
        source: MetricsSource
        if predicted_n is not None and predicted_ms is not None and predicted_ms > 0:
            tokens = int(predicted_n)
            decode_tokens = tokens
            decode_s = predicted_ms / 1000.0
            source = "timings"
        else:
            if completion is not None and completion > 0:
                tokens = int(completion)
                source = "usage"
            else:
                tokens = self._round_chunks
                source = "chunks"
            span = (
                self._round_last - self._round_first
                if self._round_first is not None and self._round_last is not None
                else 0.0
            )
            # El primer token cierra el TTFT; el intervalo de decode cubre
            # los N-1 restantes. Con una sola muestra no hay velocidad.
            if span > 0 and tokens > 1:
                decode_tokens = tokens - 1
                decode_s = span
            else:
                decode_tokens = 0
                decode_s = 0.0

        if tokens or self._round_chunks:
            self._tokens += tokens
            self._decode_tokens += decode_tokens
            self._decode_s += decode_s
            # El usage no separa razonamiento de respuesta: se escala la
            # proporción medida en chunks.
            if self._round_chunks:
                self._thinking += round(self._round_thinking_chunks * tokens / self._round_chunks)
            if self._source is None or _SOURCE_RANK[source] < _SOURCE_RANK[self._source]:
                # La fuente del turno es la peor de sus rondas: si una ronda
                # fue aproximada, el total también lo es.
                self._source = source

        # -- procesamiento de prompt -----------------------------------------
        prompt_n = _num(timings.get("prompt_n"))
        prompt_ms = _num(timings.get("prompt_ms"))
        if prompt_n is not None and prompt_ms is not None and prompt_ms > 0:
            self._prompt_tokens += int(prompt_n)
            self._prompt_s += prompt_ms / 1000.0

        # -- contexto ocupado al terminar la ronda -------------------------
        # La última ronda es la que tiene la conversación completa (incluye
        # resultados de tools). cache_n son tokens reutilizados del KV.
        cache_n = _num(timings.get("cache_n"))
        prompt_total = _num(usage.get("prompt_tokens"))
        if prompt_n is not None and predicted_n is not None:
            self._context_tokens = int(prompt_n + (cache_n or 0) + predicted_n)
        elif prompt_total is not None and completion is not None:
            self._context_tokens = int(prompt_total + completion)

        self._reset_round()

    # -- lectura ---------------------------------------------------------------

    def _live_round(self, now: float) -> tuple[int, int, float]:
        """Tokens, tokens de decode y segundos de la ronda en curso (chunks)."""
        chunks = self._round_chunks
        if chunks > 1 and self._round_first is not None:
            return chunks, chunks - 1, max(0.0, now - self._round_first)
        return chunks, 0, 0.0

    def snapshot(self) -> dict[str, Any]:
        now = self._clock()
        live_tokens, live_decode, live_s = self._live_round(now)

        decode_tokens = self._decode_tokens + live_decode
        decode_s = self._decode_s + live_s
        tps = round(decode_tokens / decode_s, 1) if decode_s > 0 else None
        pp_tps = round(self._prompt_tokens / self._prompt_s, 1) if self._prompt_s > 0 else None

        live_thinking = 0
        if self._round_chunks:
            live_thinking = self._round_thinking_chunks

        def _ms(t: float | None) -> float | None:
            return round((t - self.start) * 1000, 1) if t is not None else None

        return {
            "tps": tps,
            "pp_tps": pp_tps,
            "ttft_ms": _ms(self.first_token_at),
            "ttft_answer_ms": _ms(self.first_answer_at),
            "tokens_total": self._tokens + live_tokens,
            "tokens_thinking": self._thinking + live_thinking,
            "context_tokens": self._context_tokens,
            "metrics_source": self._source or "chunks",
            "duration_s": round(now - self.start, 2),
        }
