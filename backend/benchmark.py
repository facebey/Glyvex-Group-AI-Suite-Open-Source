"""
benchmark.py — Suite de benchmark automático contra un endpoint LLM (módulo M4).

Responsabilidades:
- Cargar los sets de prompts desde backend/prompts/*.json (+ permitir sets
  custom persistidos ahí mismo).
- Correr un benchmark como asyncio.Task cancelable (NUNCA threading): para
  cada prompt llama al endpoint OpenAI-compatible en modo streaming (para
  medir TTFT real), separa razonamiento (<think>) reusando el parser de
  chat.py, verifica keywords case-insensitive, y empuja progreso a un
  asyncio.Queue por cada prompt completado.
- DOBLE PERSISTENCIA: cada run se guarda en data/benchmarks/{run_id}.json
  (aiofiles) + reporte HTML autónomo + filas en SQLite (database.py).
  La DB es la fuente de verdad para historial, resultados y scoring; el JSON
  se mantiene como backup portable. Ningún fallo de DB puede tumbar un run:
  todas las operaciones de persistencia van envueltas en try/except + log.
- LLM-as-judge manual: POST /run/{run_id}/judge evalúa con un segundo modelo
  los resultados sin score_judge y emite progreso por SSE.
- Exponer historial, detalle, cancelación y progreso en vivo por WebSocket.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import aiofiles
import httpx
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, select, update

from chat import ThinkingStreamParser
from stream_metrics import StreamMetrics, reasoning_from_delta
from paths import BASE_DIR, DATA_DIR
from database import (
    BenchmarkResultRow,
    BenchmarkRunRow,
    BenchmarkSummaryRow,
    ImportedRunRow,
    get_session,
    make_result_id,
)

logger = logging.getLogger(__name__)

# PROMPTS_DIR es código (los 6 sets versionados viven en el repo), no estado:
# sigue colgando de BASE_DIR y es COMPARTIDO entre instancias a propósito.
PROMPTS_DIR = BASE_DIR / "backend" / "prompts"
RUNS_DIR = DATA_DIR / "benchmarks"

PROGRESS_BUFFER_MAXLEN = 500

JUDGE_TIMEOUT_S = 60.0
JUDGE_MAX_TOKENS = 1024

JUDGE_SYSTEM_PROMPT = (
    "Sos un evaluador experto de respuestas de modelos de lenguaje. "
    "Tu tarea es evaluar la calidad de una respuesta dado el prompt original. "
    "Respondé SOLO con un objeto JSON válido, sin texto adicional, sin backticks, "
    "sin explicaciones fuera del JSON."
)

# --------------------------------------------------------------------------
# Schema (Pydantic v2)
# --------------------------------------------------------------------------

Difficulty = Literal["easy", "medium", "hard"]
RunStatus = Literal["running", "completed", "cancelled", "error"]


class PromptItem(BaseModel):
    id: str
    title: str
    prompt: str
    expected_keywords: list[str] = Field(default_factory=list)
    category: str
    difficulty: Difficulty = "medium"
    expected_answer: str | None = None


class PromptSet(BaseModel):
    id: str
    name: str
    description: str
    icon: str = "📋"
    version: str = "1.0"
    prompts: list[PromptItem]


class PromptSetSummary(BaseModel):
    id: str
    name: str
    description: str
    icon: str
    version: str
    prompt_count: int


class BenchmarkConfig(BaseModel):
    endpoint: str = "http://127.0.0.1:8080"
    api_key: str = ""
    model_name: str = ""
    sets: list[str]
    thinking_enabled: bool = False
    temperature: float = 0.0
    max_tokens: int = 2048
    repetitions: int = 1
    timeout_s: int = 120


class BenchmarkMetrics(BaseModel):
    tps: float = 0.0
    ttft_ms: float = 0.0
    tokens_generated: int = 0
    tokens_thinking: int = 0
    duration_s: float = 0.0


class BenchmarkResult(BaseModel):
    # result_id identifica la fila en benchmark_results. Se genera
    # determinísticamente ({run_id}-{índice}) al correr el benchmark; el uuid
    # es solo fallback al releer JSON viejos que no lo tenían.
    result_id: str = Field(default_factory=lambda: str(uuid4()))
    prompt_id: str
    set_id: str
    prompt_title: str
    prompt_text: str
    response: str = ""
    thinking: str | None = None
    metrics: BenchmarkMetrics | None = None
    score_auto: float | None = None
    score_manual: float | None = None
    keywords_found: list[str] = Field(default_factory=list)
    keywords_missing: list[str] = Field(default_factory=list)
    error: str | None = None


class RunSummary(BaseModel):
    total_prompts: int
    completed: int
    errors: int
    avg_tps: float
    avg_ttft_ms: float
    avg_tokens: float
    total_duration_s: float
    keyword_hit_rate: float


class BenchmarkRun(BaseModel):
    run_id: str
    started_at: str
    finished_at: str | None = None
    status: RunStatus = "running"
    config: BenchmarkConfig
    results: list[BenchmarkResult] = Field(default_factory=list)
    summary: RunSummary | None = None
    error: str | None = None


class JudgeRequest(BaseModel):
    endpoint: str
    model: str
    api_key: str = ""


class ManualScoreUpdate(BaseModel):
    score_manual: float | None = None

    @model_validator(mode="after")
    def _check_range(self) -> ManualScoreUpdate:
        if self.score_manual is not None and not 0.0 <= self.score_manual <= 10.0:
            raise ValueError("score_manual debe estar entre 0 y 10 (o null para limpiar)")
        return self


# --------------------------------------------------------------------------
# Helpers de (de)serialización para la DB
# --------------------------------------------------------------------------


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads_list(raw: str | None) -> list[Any]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _result_values(result: BenchmarkResult) -> dict[str, Any]:
    """Campos de benchmark_results derivados de un BenchmarkResult (sin judge)."""
    m = result.metrics
    return {
        "prompt_id": result.prompt_id,
        "set_id": result.set_id,
        "prompt_title": result.prompt_title,
        "prompt_text": result.prompt_text,
        "response": result.response,
        "thinking": result.thinking,
        "tps": m.tps if m else None,
        "ttft_ms": m.ttft_ms if m else None,
        "tokens_generated": m.tokens_generated if m else None,
        "tokens_thinking": m.tokens_thinking if m else None,
        "duration_s": m.duration_s if m else None,
        "keywords_found": _dumps(result.keywords_found),
        "keywords_missing": _dumps(result.keywords_missing),
        "score_keywords": result.score_auto,
        "score_manual": result.score_manual,
        "error": result.error,
    }


def _result_row_to_dict(row: BenchmarkResultRow) -> dict[str, Any]:
    metrics = None
    if row.tps is not None or row.tokens_generated is not None:
        metrics = {
            "tps": row.tps or 0.0,
            "ttft_ms": row.ttft_ms or 0.0,
            "tokens_generated": row.tokens_generated or 0,
            "tokens_thinking": row.tokens_thinking or 0,
            "duration_s": row.duration_s or 0.0,
        }
    return {
        "result_id": row.id,
        "run_id": row.run_id,
        "prompt_id": row.prompt_id,
        "set_id": row.set_id,
        "prompt_title": row.prompt_title,
        "prompt_text": row.prompt_text,
        "response": row.response,
        "thinking": row.thinking,
        "metrics": metrics,
        "keywords_found": _loads_list(row.keywords_found),
        "keywords_missing": _loads_list(row.keywords_missing),
        "score_keywords": row.score_keywords,
        "score_judge": row.score_judge,
        "judge_reasoning": row.judge_reasoning,
        "judge_model": row.judge_model,
        "judged_at": row.judged_at,
        "score_manual": row.score_manual,
        "error": row.error,
    }


def _summary_row_to_dict(row: BenchmarkSummaryRow) -> dict[str, Any]:
    return {
        "total_prompts": row.total_prompts or 0,
        "completed": row.completed or 0,
        "errors": row.errors or 0,
        "avg_tps": row.avg_tps or 0.0,
        "avg_ttft_ms": row.avg_ttft_ms or 0.0,
        "avg_tokens": row.avg_tokens or 0.0,
        "total_duration_s": row.total_duration_s or 0.0,
        "keyword_hit_rate": row.keyword_hit_rate or 0.0,
        "avg_judge_score": row.avg_judge_score,
    }


# --------------------------------------------------------------------------
# Store de sets de prompts (backend/prompts/*.json)
# --------------------------------------------------------------------------


class PromptSetStore:
    def __init__(self, directory: Path = PROMPTS_DIR) -> None:
        self.directory = directory
        self._sets: dict[str, PromptSet] = {}
        self._loaded = False

    async def ensure_loaded(self) -> None:
        if self._loaded:
            return
        await self.reload()

    async def reload(self) -> None:
        self._sets = {}
        if not self.directory.exists():
            self._loaded = True
            return
        for path in sorted(self.directory.glob("*.json")):
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                raw = await f.read()
            try:
                data = json.loads(raw)
                prompt_set = PromptSet(**data)
                self._sets[prompt_set.id] = prompt_set
            except Exception:
                continue
        self._loaded = True

    def list_summaries(self) -> list[PromptSetSummary]:
        return [
            PromptSetSummary(
                id=s.id, name=s.name, description=s.description,
                icon=s.icon, version=s.version, prompt_count=len(s.prompts),
            )
            for s in self._sets.values()
        ]

    def get(self, set_id: str) -> PromptSet | None:
        return self._sets.get(set_id)

    async def create(self, prompt_set: PromptSet) -> PromptSet:
        if prompt_set.id in self._sets:
            raise HTTPException(status_code=409, detail=f"Ya existe un set con id '{prompt_set.id}'")
        path = self.directory / f"{prompt_set.id}.json"
        self.directory.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(prompt_set.model_dump(), indent=2, ensure_ascii=False))
        self._sets[prompt_set.id] = prompt_set
        return prompt_set


prompt_sets = PromptSetStore()

# --------------------------------------------------------------------------
# Llamada al modelo para un prompt individual (streaming, mide TTFT)
# --------------------------------------------------------------------------


async def _call_model(
    endpoint: str,
    api_key: str,
    model_name: str,
    prompt_text: str,
    thinking_enabled: bool,
    temperature: float,
    max_tokens: int,
    timeout_s: int,
) -> tuple[str, str | None, BenchmarkMetrics, str | None]:
    content = f"/think {prompt_text}" if thinking_enabled else prompt_text
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": content}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
        # Mismo criterio que chat.py: el usage y los timings exactos llegan en
        # el último chunk y reemplazan a la cuenta de chunks.
        "stream_options": {"include_usage": True},
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    url = endpoint.rstrip("/") + "/v1/chat/completions"
    parser = ThinkingStreamParser()

    response_text = ""
    thinking_text = ""
    # Ver stream_metrics.py: con llama-server el razonamiento llega en
    # reasoning_content y antes quedaba fuera de TTFT, de t/s y del resultado.
    stream = StreamMetrics()
    stream.begin_round()

    timeout = httpx.Timeout(connect=5.0, read=float(timeout_s), write=30.0, pool=5.0)

    try:
        async with asyncio.timeout(timeout_s + 10):
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", url, json=payload, headers=headers) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        return "", None, BenchmarkMetrics(), f"HTTP {response.status_code}: {body[:200].decode('utf-8', 'replace')}"

                    async for raw_line in response.aiter_lines():
                        if not raw_line or not raw_line.startswith("data:"):
                            continue
                        data_str = raw_line[len("data:"):].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        stream.observe_chunk(chunk)
                        choice = (chunk.get("choices") or [{}])[0]
                        delta = choice.get("delta") or {}

                        reasoning = reasoning_from_delta(delta)
                        if reasoning:
                            stream.mark_token(thinking=True)
                            thinking_text += reasoning

                        piece = delta.get("content")
                        if not piece:
                            continue

                        stream.mark_token(thinking=parser.in_thinking)
                        for kind, text in parser.feed(piece):
                            if kind == "thinking_token":
                                thinking_text += text
                            else:
                                if text.strip():
                                    stream.mark_answer()
                                response_text += text

        for kind, text in parser.flush():
            if kind == "thinking_token":
                thinking_text += text
            else:
                response_text += text

    except asyncio.TimeoutError:
        return response_text, thinking_text or None, BenchmarkMetrics(), f"Timeout tras {timeout_s}s"
    except (httpx.ConnectError, httpx.HTTPError) as exc:
        return response_text, thinking_text or None, BenchmarkMetrics(), f"Error de conexión: {exc}"

    stream.end_round()
    snap = stream.snapshot()
    metrics = BenchmarkMetrics(
        tps=snap["tps"] or 0.0,
        ttft_ms=snap["ttft_ms"] or 0.0,
        tokens_generated=snap["tokens_total"],
        tokens_thinking=snap["tokens_thinking"],
        duration_s=snap["duration_s"],
    )
    return response_text, thinking_text or None, metrics, None


def _check_keywords(response_text: str, expected: list[str]) -> tuple[list[str], list[str]]:
    lowered = response_text.lower()
    found = [kw for kw in expected if kw.lower() in lowered]
    missing = [kw for kw in expected if kw not in found]
    return found, missing


# --------------------------------------------------------------------------
# Reporte HTML autónomo
# --------------------------------------------------------------------------


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if text else ""
    )


def build_html_report(run: BenchmarkRun) -> str:
    summary = run.summary
    max_tps = max((r.metrics.tps for r in run.results if r.metrics), default=1.0) or 1.0

    rows = []
    for r in run.results:
        tps = r.metrics.tps if r.metrics else 0.0
        bar_pct = round((tps / max_tps) * 100, 1) if max_tps else 0
        status = "✗" if r.error else "✓"
        status_color = "#ef4444" if r.error else "#22c55e"
        rows.append(f"""
        <tr>
          <td>{_esc(r.set_id)}</td>
          <td>{_esc(r.prompt_title)}</td>
          <td style="color:{status_color}; font-weight:600;">{status}</td>
          <td>{tps:.1f}</td>
          <td>{r.metrics.ttft_ms if r.metrics else '—'}</td>
          <td>{r.metrics.tokens_generated if r.metrics else '—'}</td>
          <td>{len(r.keywords_found)}/{len(r.keywords_found) + len(r.keywords_missing)}</td>
          <td><div style="background:#3b82f6; height:10px; width:{bar_pct}%; border-radius:3px;"></div></td>
        </tr>""")

    summary_html = ""
    if summary:
        summary_html = f"""
        <div class="summary">
          <div><span>Total</span><strong>{summary.total_prompts}</strong></div>
          <div><span>Completados</span><strong>{summary.completed}</strong></div>
          <div><span>Errores</span><strong>{summary.errors}</strong></div>
          <div><span>t/s promedio</span><strong>{summary.avg_tps:.1f}</strong></div>
          <div><span>TTFT promedio</span><strong>{summary.avg_ttft_ms:.0f} ms</strong></div>
          <div><span>Keyword hit rate</span><strong>{summary.keyword_hit_rate * 100:.0f}%</strong></div>
          <div><span>Duración total</span><strong>{summary.total_duration_s:.1f}s</strong></div>
        </div>"""

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Glyvex Benchmark — {_esc(run.run_id)}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; background:#0f0f0f; color:#fff; margin:0; padding:2rem; }}
  h1 {{ font-size: 1.25rem; margin-bottom: 0.25rem; }}
  .meta {{ color:#9ca3af; font-size: 0.875rem; margin-bottom: 1.5rem; }}
  .summary {{ display:flex; flex-wrap:wrap; gap:1rem; margin-bottom: 1.5rem; }}
  .summary div {{ background:#1a1a1a; border:1px solid rgba(255,255,255,0.1); border-radius:8px; padding:0.75rem 1rem; min-width:120px; }}
  .summary span {{ display:block; color:#9ca3af; font-size:0.75rem; text-transform:uppercase; }}
  .summary strong {{ font-size:1.1rem; }}
  table {{ width:100%; border-collapse: collapse; font-size:0.875rem; }}
  th, td {{ text-align:left; padding:0.5rem 0.75rem; border-bottom:1px solid rgba(255,255,255,0.08); }}
  th {{ color:#9ca3af; text-transform:uppercase; font-size:0.7rem; }}
</style>
</head>
<body>
  <h1>Glyvex-AI-Suite — Reporte de Benchmark</h1>
  <p class="meta">
    Modelo: {_esc(run.config.model_name)} · Endpoint: {_esc(run.config.endpoint)} ·
    Iniciado: {_esc(run.started_at)} · Run ID: {_esc(run.run_id)}
  </p>
  {summary_html}
  <table>
    <thead>
      <tr><th>Set</th><th>Prompt</th><th>OK</th><th>t/s</th><th>TTFT (ms)</th><th>Tokens</th><th>Keywords</th><th></th></tr>
    </thead>
    <tbody>
      {"".join(rows)}
    </tbody>
  </table>
</body>
</html>"""


# --------------------------------------------------------------------------
# LLM-as-judge (evaluación manual, post-run)
# --------------------------------------------------------------------------


def _build_judge_user_prompt(prompt_text: str, response_text: str) -> str:
    return (
        f"PROMPT ORIGINAL:\n{prompt_text}\n\n"
        f"RESPUESTA DEL MODELO:\n{response_text}\n\n"
        "Evaluá la calidad de la respuesta con un score del 1 al 10 y una justificación.\n"
        "Criterios: precisión técnica, completitud, claridad y utilidad práctica.\n"
        "Respondé ÚNICAMENTE con este JSON (sin nada más):\n"
        '{"score": <número entero 1-10>, "reasoning": "<justificación 1-2 oraciones>"}'
    )


def _parse_judge_payload(raw: str) -> tuple[float, str] | None:
    """Extrae {"score", "reasoning"} tolerando <think>, backticks y prosa."""
    if not raw:
        return None

    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None

    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "score" not in data:
        return None

    try:
        score = float(data["score"])
    except (TypeError, ValueError):
        return None

    score = max(1.0, min(10.0, round(score, 2)))
    reasoning = str(data.get("reasoning") or "").strip()
    return score, reasoning


async def _judge_once(
    client: httpx.AsyncClient,
    req: JudgeRequest,
    prompt_text: str,
    response_text: str,
    temperature: float,
    reinforce: bool,
) -> tuple[float, str] | None:
    """Una llamada al judge. Devuelve None si la respuesta no es JSON parseable."""
    user_prompt = _build_judge_user_prompt(prompt_text, response_text)
    if reinforce:
        user_prompt += (
            "\n\nIMPORTANTE: tu respuesta anterior no fue JSON válido. "
            "Devolvé solamente el objeto JSON, sin backticks ni texto alrededor."
        )

    payload = {
        "model": req.model,
        "messages": [
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": JUDGE_MAX_TOKENS,
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    if req.api_key:
        headers["Authorization"] = f"Bearer {req.api_key}"

    url = req.endpoint.rstrip("/") + "/v1/chat/completions"
    response = await client.post(url, json=payload, headers=headers)
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")

    data = response.json()
    message = ((data.get("choices") or [{}])[0]).get("message") or {}
    content = message.get("content") or ""
    return _parse_judge_payload(content)


async def _load_pending_judge_targets(run_id: str) -> list[dict[str, str]]:
    """Resultados del run sin score_judge, con respuesta útil para evaluar."""
    async with get_session() as session:
        rows = (
            await session.execute(
                select(BenchmarkResultRow)
                .where(
                    BenchmarkResultRow.run_id == run_id,
                    BenchmarkResultRow.score_judge.is_(None),
                )
                .order_by(BenchmarkResultRow.id)
            )
        ).scalars().all()

    return [
        {
            "id": row.id,
            "title": row.prompt_title or row.prompt_id or row.id,
            "prompt": row.prompt_text or "",
            "response": row.response or "",
        }
        for row in rows
        # Los results con error o respuesta vacía no se mandan al judge:
        # quemarían el timeout completo para devolver siempre un score basura.
        if not row.error and (row.response or "").strip()
    ]


async def _store_judge_result(
    result_id: str, score: float, reasoning: str, judge_model: str
) -> None:
    async with get_session() as session:
        await session.execute(
            update(BenchmarkResultRow)
            .where(BenchmarkResultRow.id == result_id)
            .values(
                score_judge=score,
                judge_reasoning=reasoning,
                judge_model=judge_model,
                judged_at=_now_iso(),
            )
        )
        await session.commit()


async def _refresh_avg_judge_score(run_id: str) -> float | None:
    """Recalcula benchmark_summaries.avg_judge_score sobre los scores presentes."""
    async with get_session() as session:
        scores = (
            await session.execute(
                select(BenchmarkResultRow.score_judge).where(
                    BenchmarkResultRow.run_id == run_id,
                    BenchmarkResultRow.score_judge.is_not(None),
                )
            )
        ).scalars().all()

        avg = round(sum(scores) / len(scores), 2) if scores else None

        summary = await session.get(BenchmarkSummaryRow, run_id)
        if summary is None:
            session.add(BenchmarkSummaryRow(run_id=run_id, avg_judge_score=avg))
        else:
            summary.avg_judge_score = avg
        await session.commit()

    return avg


# --------------------------------------------------------------------------
# Manager de runs
# --------------------------------------------------------------------------


class BenchmarkManager:
    def __init__(self) -> None:
        self._runs: dict[str, BenchmarkRun] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._progress_buffer: dict[str, list[dict[str, Any]]] = {}
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        # Total de prompts por run (set cuando se arma la cola): el handler
        # de cancelación lo necesita para el evento de progreso, y puede
        # llegar a correr antes de que la cola exista.
        self._queue_totals: dict[str, int] = {}

    def _publish(self, run_id: str, event: dict[str, Any]) -> None:
        buf = self._progress_buffer.setdefault(run_id, [])
        buf.append(event)
        if len(buf) > PROGRESS_BUFFER_MAXLEN:
            del buf[: len(buf) - PROGRESS_BUFFER_MAXLEN]
        for queue in self._subscribers.get(run_id, []):
            queue.put_nowait(event)

    def subscribe(self, run_id: str) -> tuple[asyncio.Queue, list[dict[str, Any]]]:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(run_id, []).append(queue)
        return queue, list(self._progress_buffer.get(run_id, []))

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        subs = self._subscribers.get(run_id)
        if subs and queue in subs:
            subs.remove(queue)

    def get_run(self, run_id: str) -> BenchmarkRun | None:
        return self._runs.get(run_id)

    # ---------------- Persistencia en DB (nunca rompe el run) -------------

    @staticmethod
    async def _db_create_run(run: BenchmarkRun) -> None:
        try:
            async with get_session() as session:
                await session.merge(
                    BenchmarkRunRow(
                        id=run.run_id,
                        started_at=run.started_at,
                        finished_at=run.finished_at,
                        model_name=run.config.model_name,
                        endpoint=run.config.endpoint,
                        sets=_dumps(run.config.sets),
                        config=_dumps(run.config.model_dump()),
                        status=run.status,
                    )
                )
                await session.commit()
        except Exception as exc:
            logger.warning("DB: no se pudo crear el run %s: %s", run.run_id, exc)

    @staticmethod
    async def _db_insert_result(run_id: str, result: BenchmarkResult) -> None:
        try:
            async with get_session() as session:
                await session.merge(
                    BenchmarkResultRow(
                        id=result.result_id, run_id=run_id, **_result_values(result)
                    )
                )
                await session.commit()
        except Exception as exc:
            logger.warning(
                "DB: no se pudo guardar el result %s: %s", result.result_id, exc
            )

    @staticmethod
    async def _db_update_status(
        run_id: str, status: str, finished_at: str | None = None
    ) -> None:
        try:
            values: dict[str, Any] = {"status": status}
            if finished_at is not None:
                values["finished_at"] = finished_at
            async with get_session() as session:
                await session.execute(
                    update(BenchmarkRunRow)
                    .where(BenchmarkRunRow.id == run_id)
                    .values(**values)
                )
                await session.commit()
        except Exception as exc:
            logger.warning("DB: no se pudo actualizar el run %s: %s", run_id, exc)

    @staticmethod
    async def _db_persist_run(run: BenchmarkRun) -> None:
        """UPDATE del run + summary + campos finales de cada result."""
        try:
            async with get_session() as session:
                await session.merge(
                    BenchmarkRunRow(
                        id=run.run_id,
                        started_at=run.started_at,
                        finished_at=run.finished_at,
                        model_name=run.config.model_name,
                        endpoint=run.config.endpoint,
                        sets=_dumps(run.config.sets),
                        config=_dumps(run.config.model_dump()),
                        status=run.status,
                    )
                )

                for result in run.results:
                    values = _result_values(result)
                    # UPDATE parcial: NO toca score_judge / judge_* / judged_at
                    outcome = await session.execute(
                        update(BenchmarkResultRow)
                        .where(BenchmarkResultRow.id == result.result_id)
                        .values(**values)
                    )
                    if outcome.rowcount == 0:
                        session.add(
                            BenchmarkResultRow(
                                id=result.result_id, run_id=run.run_id, **values
                            )
                        )

                if run.summary is not None:
                    data = run.summary.model_dump()
                    summary = await session.get(BenchmarkSummaryRow, run.run_id)
                    if summary is None:
                        session.add(
                            BenchmarkSummaryRow(run_id=run.run_id, **data)
                        )
                    else:
                        for key, value in data.items():
                            setattr(summary, key, value)

                await session.commit()
        except Exception as exc:
            logger.warning("DB: no se pudo persistir el run %s: %s", run.run_id, exc)

    # ---------------- Ciclo de vida del run -------------------------------

    async def start(self, cfg: BenchmarkConfig) -> BenchmarkRun:
        await prompt_sets.ensure_loaded()

        missing_sets = [s for s in cfg.sets if prompt_sets.get(s) is None]
        if missing_sets:
            raise HTTPException(status_code=404, detail=f"Sets no encontrados: {missing_sets}")

        run_id = str(uuid4())
        run = BenchmarkRun(
            run_id=run_id,
            started_at=datetime.now(timezone.utc).isoformat(),
            status="running",
            config=cfg,
        )
        self._runs[run_id] = run
        self._progress_buffer[run_id] = []

        await self._db_create_run(run)

        task = asyncio.create_task(self._run(run_id))
        # Safety net: si el task se cancela ANTES de su primer step, Python
        # (>= 3.12) nunca le entrega el CancelledError a la coroutine — el
        # handler de _run jamás corre — y el run quedaría "running" para
        # siempre. El done callback cubre ese caso.
        task.add_done_callback(lambda t: self._on_task_done(run_id, t))
        self._tasks[run_id] = task
        return run

    async def _run(self, run_id: str) -> None:
        # El `try` es la PRIMERA línea de la coroutine a propósito: si el
        # task se cancela antes de su primer step, Python lanza el
        # CancelledError justo ahí (antes de cualquier otra sentencia), y
        # sin esto el run quedaría "running" para siempre — el handler no
        # lo vería.
        try:
            await self._run_body(run_id)
        except asyncio.CancelledError:
            run = self._runs.get(run_id)
            if run is not None and run.status == "running":
                run.status = "cancelled"
                total = self._queue_totals.get(run_id, 0)
                self._publish(run_id, {
                    "type": "cancelled",
                    "completed": len(run.results),
                    "total": total,
                })
                run.finished_at = datetime.now(timezone.utc).isoformat()
                await self._db_update_status(run_id, "cancelled", run.finished_at)
                await self._persist(run)
            raise

    async def _run_body(self, run_id: str) -> None:
        run = self._runs[run_id]
        cfg = run.config

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                health = await client.get(cfg.endpoint.rstrip("/") + "/health")
                if health.status_code >= 400:
                    raise RuntimeError(f"HTTP {health.status_code}")
        except Exception as exc:
            run.status = "error"
            run.error = f"Endpoint no disponible: {exc}"
            run.finished_at = datetime.now(timezone.utc).isoformat()
            await self._db_update_status(run_id, "error", run.finished_at)
            self._publish(run_id, {"type": "error", "message": run.error})
            return

        prompt_items: list[PromptItem] = []
        set_lookup: dict[str, str] = {}
        for set_id in cfg.sets:
            prompt_set = prompt_sets.get(set_id)
            for item in prompt_set.prompts:
                set_lookup[item.id] = set_id
                prompt_items.append(item)

        full_queue = prompt_items * max(cfg.repetitions, 1)
        total = len(full_queue)
        self._queue_totals[run_id] = total
        self._publish(run_id, {"type": "start", "total": total})

        for index, item in enumerate(full_queue):
            set_id = set_lookup[item.id]
            response_text, thinking_text, metrics, error = await _call_model(
                endpoint=cfg.endpoint,
                api_key=cfg.api_key,
                model_name=cfg.model_name,
                prompt_text=item.prompt,
                thinking_enabled=cfg.thinking_enabled,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
                timeout_s=cfg.timeout_s,
            )

            found, missing = _check_keywords(response_text, item.expected_keywords)
            score_auto = (
                round(len(found) / len(item.expected_keywords), 2)
                if item.expected_keywords
                else None
            )

            result = BenchmarkResult(
                result_id=make_result_id(run_id, index),
                prompt_id=item.id,
                set_id=set_id,
                prompt_title=item.title,
                prompt_text=item.prompt,
                response=response_text,
                thinking=thinking_text,
                metrics=metrics if not error else None,
                score_auto=score_auto,
                keywords_found=found,
                keywords_missing=missing,
                error=error,
            )
            run.results.append(result)
            await self._db_insert_result(run_id, result)

            self._publish(run_id, {
                "type": "result",
                "completed": index + 1,
                "total": total,
                "prompt_id": item.id,
                "set_id": set_id,
                "prompt_title": item.title,
                "ok": error is None,
                "tps": metrics.tps if metrics else 0.0,
                "error": error,
            })

        run.status = "completed"
        run.finished_at = datetime.now(timezone.utc).isoformat()
        run.summary = self._summarize(run)
        await self._persist(run)
        self._publish(run_id, {"type": "complete", "summary": run.summary.model_dump()})

    @staticmethod
    def _summarize(run: BenchmarkRun) -> RunSummary:
        results = run.results
        ok_results = [r for r in results if r.error is None and r.metrics]
        errors = len(results) - len(ok_results)
        n = len(ok_results) or 1

        total_keywords = sum(len(r.keywords_found) + len(r.keywords_missing) for r in results)
        found_keywords = sum(len(r.keywords_found) for r in results)

        started = datetime.fromisoformat(run.started_at)
        finished = datetime.fromisoformat(run.finished_at) if run.finished_at else started
        total_duration_s = round((finished - started).total_seconds(), 1)

        return RunSummary(
            total_prompts=len(results),
            completed=len(ok_results),
            errors=errors,
            avg_tps=round(sum(r.metrics.tps for r in ok_results) / n, 1) if ok_results else 0.0,
            avg_ttft_ms=round(sum(r.metrics.ttft_ms for r in ok_results) / n, 1) if ok_results else 0.0,
            avg_tokens=round(sum(r.metrics.tokens_generated for r in ok_results) / n, 1) if ok_results else 0.0,
            total_duration_s=total_duration_s,
            keyword_hit_rate=round(found_keywords / total_keywords, 2) if total_keywords else 0.0,
        )

    async def _persist(self, run: BenchmarkRun) -> None:
        # --- 1. JSON + HTML (comportamiento original, intacto) ------------
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        json_path = RUNS_DIR / f"{run.run_id}.json"
        async with aiofiles.open(json_path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(run.model_dump(), indent=2, ensure_ascii=False))

        html_path = RUNS_DIR / f"{run.run_id}.html"
        async with aiofiles.open(html_path, "w", encoding="utf-8") as f:
            await f.write(build_html_report(run))

        # --- 2. SQLite (doble persistencia) -------------------------------
        await self._db_persist_run(run)

        # El run ya vive en la DB: marcarlo como importado evita que
        # _migrate_existing_runs lo vuelva a leer desde el JSON al reiniciar.
        try:
            async with get_session() as session:
                await session.merge(ImportedRunRow(id=run.run_id))
                await session.commit()
        except Exception as exc:
            logger.debug("DB: no se pudo marcar %s como importado: %s", run.run_id, exc)

    def cancel(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        # Un run ya terminado (completed/error) puede seguir vivo en su cola
        # final de persistencia en DB: cancelarlo ahí interrumpe los merges
        # y el DELETE termina sin borrar nada. Solo se cancela si el run
        # sigue corriendo prompts.
        run = self._runs.get(run_id)
        if run is None or run.status != "running":
            return False
        task.cancel()
        return True

    async def wait_finished(self, run_id: str) -> None:
        """Espera a que el task del run termine, incluida la persistencia
        final que corre DESPUÉS de marcar el status (si el task ya terminó
        es un no-op)."""
        task = self._tasks.get(run_id)
        if task is None:
            return
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _on_task_done(self, run_id: str, task: asyncio.Task) -> None:
        """
        Corre cuando el task termina. Si terminó cancelado pero el run sigue
        "running", la coroutine nunca procesó la cancelación (se canceló
        antes de su primer step: Python >= 3.12 no entrega el CancelledError
        en ese caso) y hay que completar aquí el finalizado. Si el run ya
        tiene otro status, el handler de _run ya hizo su trabajo.
        """
        run = self._runs.get(run_id)
        if run is None or run.status != "running" or not task.cancelled():
            return
        try:
            asyncio.get_running_loop().create_task(
                self._finalize_cancelled(run_id, run)
            )
        except RuntimeError:
            pass  # sin loop activo: el run queda en memoria como cancelled

    async def _finalize_cancelled(self, run_id: str, run: BenchmarkRun) -> None:
        run.status = "cancelled"
        total = self._queue_totals.get(run_id, 0)
        self._publish(run_id, {
            "type": "cancelled",
            "completed": len(run.results),
            "total": total,
        })
        run.finished_at = datetime.now(timezone.utc).isoformat()
        await self._db_update_status(run_id, "cancelled", run.finished_at)
        await self._persist(run)

    @staticmethod
    async def load_from_disk(run_id: str) -> BenchmarkRun | None:
        path = RUNS_DIR / f"{run_id}.json"
        if not path.exists():
            return None
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            raw = await f.read()
        return BenchmarkRun(**json.loads(raw))

    @staticmethod
    async def list_history_db() -> list[dict[str, Any]]:
        """Historial desde la DB (runs + summary, más reciente primero)."""
        async with get_session() as session:
            rows = (
                await session.execute(
                    select(BenchmarkRunRow, BenchmarkSummaryRow)
                    .outerjoin(
                        BenchmarkSummaryRow,
                        BenchmarkSummaryRow.run_id == BenchmarkRunRow.id,
                    )
                    .order_by(BenchmarkRunRow.started_at.desc())
                )
            ).all()

        entries: list[dict[str, Any]] = []
        for run_row, summary_row in rows:
            entries.append({
                "run_id": run_row.id,
                "started_at": run_row.started_at,
                "finished_at": run_row.finished_at,
                "status": run_row.status,
                "model_name": run_row.model_name,
                "sets": _loads_list(run_row.sets),
                "summary": _summary_row_to_dict(summary_row) if summary_row else None,
            })
        return entries

    @staticmethod
    async def list_history() -> list[dict[str, Any]]:
        """Fallback: historial leído de los JSON en data/benchmarks/."""
        if not RUNS_DIR.exists():
            return []
        entries = []
        for path in sorted(RUNS_DIR.glob("*.json"), reverse=True):
            try:
                async with aiofiles.open(path, "r", encoding="utf-8") as f:
                    raw = await f.read()
                data = json.loads(raw)
                entries.append({
                    "run_id": data.get("run_id"),
                    "started_at": data.get("started_at"),
                    "finished_at": data.get("finished_at"),
                    "status": data.get("status"),
                    "model_name": data.get("config", {}).get("model_name"),
                    "sets": data.get("config", {}).get("sets", []),
                    "summary": data.get("summary"),
                })
            except Exception:
                continue
        return entries


manager = BenchmarkManager()

# --------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------

router = APIRouter()


@router.get("/sets", response_model=list[PromptSetSummary])
async def list_sets() -> list[PromptSetSummary]:
    await prompt_sets.ensure_loaded()
    return prompt_sets.list_summaries()


@router.get("/sets/{set_id}", response_model=PromptSet)
async def get_set(set_id: str) -> PromptSet:
    await prompt_sets.ensure_loaded()
    prompt_set = prompt_sets.get(set_id)
    if prompt_set is None:
        raise HTTPException(status_code=404, detail="Set no encontrado")
    return prompt_set


@router.post("/sets", response_model=PromptSet)
async def create_set(prompt_set: PromptSet) -> PromptSet:
    await prompt_sets.ensure_loaded()
    return await prompt_sets.create(prompt_set)


@router.post("/run", response_model=BenchmarkRun)
async def start_run(cfg: BenchmarkConfig) -> BenchmarkRun:
    return await manager.start(cfg)


@router.get("/run/{run_id}", response_model=BenchmarkRun)
async def get_run(run_id: str) -> BenchmarkRun:
    run = manager.get_run(run_id)
    if run is None:
        run = await BenchmarkManager.load_from_disk(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run no encontrado")
    return run


@router.get("/run/{run_id}/results")
async def get_run_results(run_id: str) -> list[dict[str, Any]]:
    """Resultados del run desde la DB, incluyendo score_judge y judge_reasoning."""
    async with get_session() as session:
        rows = (
            await session.execute(
                select(BenchmarkResultRow)
                .where(BenchmarkResultRow.run_id == run_id)
                .order_by(BenchmarkResultRow.id)
            )
        ).scalars().all()

        if not rows:
            exists = await session.get(BenchmarkRunRow, run_id)

    if rows:
        return [_result_row_to_dict(row) for row in rows]

    if exists is None and manager.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run no encontrado")
    return []


@router.patch("/results/{result_id}/score")
async def set_manual_score(
    result_id: str, payload: ManualScoreUpdate
) -> dict[str, Any]:
    async with get_session() as session:
        outcome = await session.execute(
            update(BenchmarkResultRow)
            .where(BenchmarkResultRow.id == result_id)
            .values(score_manual=payload.score_manual)
        )
        if outcome.rowcount == 0:
            await session.rollback()
            raise HTTPException(status_code=404, detail="Result no encontrado")
        await session.commit()

    return {"result_id": result_id, "score_manual": payload.score_manual}


@router.post("/run/{run_id}/judge")
async def judge_run(run_id: str, req: JudgeRequest) -> StreamingResponse:
    """
    Evalúa con un LLM-judge todos los results del run sin score_judge y emite
    progreso por SSE:
      data: {"evaluated": N, "total": M, "current_prompt": "...", "current_score": 8}
      data: {"status": "complete", "avg_score": 7.3, "evaluated": M, "total": M}
    """

    def sse(payload: dict[str, Any]) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    async def event_stream() -> AsyncIterator[str]:
        try:
            pending = await _load_pending_judge_targets(run_id)
        except Exception as exc:
            logger.warning("Judge: error leyendo la DB para %s: %s", run_id, exc)
            yield sse({"status": "error", "message": f"Error leyendo la DB: {exc}"})
            return

        total = len(pending)
        if total == 0:
            avg = await _refresh_avg_judge_score(run_id)
            yield sse({
                "status": "complete",
                "avg_score": avg,
                "evaluated": 0,
                "total": 0,
                "message": "No hay resultados pendientes de evaluar",
            })
            return

        yield sse({"status": "start", "total": total, "judge_model": req.model})

        headers = {}
        if req.api_key:
            headers["Authorization"] = f"Bearer {req.api_key}"

        timeout = httpx.Timeout(
            connect=10.0, read=JUDGE_TIMEOUT_S, write=30.0, pool=10.0
        )

        async with httpx.AsyncClient(timeout=timeout) as client:
            # Chequeo de disponibilidad: solo abortamos si el endpoint no
            # responde a nivel transporte (un 4xx en /v1/models es aceptable).
            try:
                await client.get(
                    req.endpoint.rstrip("/") + "/v1/models",
                    headers=headers,
                    timeout=10.0,
                )
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                yield sse({
                    "status": "error",
                    "message": f"Endpoint no disponible: {exc}",
                })
                return
            except httpx.HTTPError:
                pass

            evaluated = 0
            for item in pending:
                parsed: tuple[float, str] | None = None
                last_error: str | None = None

                for attempt in (0, 1):
                    try:
                        parsed = await _judge_once(
                            client,
                            req,
                            item["prompt"],
                            item["response"],
                            temperature=0.0,
                            reinforce=attempt == 1,
                        )
                    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                        yield sse({
                            "status": "error",
                            "message": f"Endpoint no disponible: {exc}",
                            "evaluated": evaluated,
                            "total": total,
                        })
                        return
                    except (httpx.TimeoutException, httpx.HTTPError, RuntimeError, ValueError) as exc:
                        last_error = str(exc)
                        parsed = None
                    if parsed is not None:
                        break

                if parsed is None:
                    logger.warning(
                        "Judge: sin JSON válido para '%s' (%s) — score_judge=null",
                        item["title"],
                        last_error or "respuesta no parseable",
                    )
                    yield sse({
                        "evaluated": evaluated,
                        "total": total,
                        "current_prompt": item["title"],
                        "current_score": None,
                        "error": last_error or "JSON inválido",
                    })
                    continue

                score, reasoning = parsed
                try:
                    await _store_judge_result(item["id"], score, reasoning, req.model)
                except Exception as exc:
                    logger.warning(
                        "Judge: no se pudo guardar el score de %s: %s", item["id"], exc
                    )

                evaluated += 1
                yield sse({
                    "evaluated": evaluated,
                    "total": total,
                    "current_prompt": item["title"],
                    "current_score": score,
                })

        avg = await _refresh_avg_judge_score(run_id)
        yield sse({
            "status": "complete",
            "avg_score": avg,
            "evaluated": evaluated,
            "total": total,
        })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/run/{run_id}")
async def delete_or_cancel_run(run_id: str) -> dict[str, bool]:
    if manager.cancel(run_id):
        return {"cancelled": True}

    # Si el run ya terminó, su task puede estar en la cola final de
    # persistencia: esperarla evita que los merges reinserten las filas
    # que estamos por borrar.
    await manager.wait_finished(run_id)

    json_path = RUNS_DIR / f"{run_id}.json"
    html_path = RUNS_DIR / f"{run_id}.html"

    async with get_session() as session:
        run_row = await session.get(BenchmarkRunRow, run_id)
        if run_row is None and not json_path.exists():
            raise HTTPException(status_code=404, detail="Run no encontrado")

        # El historial ahora se lee de la DB: hay que limpiar ahí también.
        await session.execute(
            delete(BenchmarkResultRow).where(BenchmarkResultRow.run_id == run_id)
        )
        await session.execute(
            delete(BenchmarkSummaryRow).where(BenchmarkSummaryRow.run_id == run_id)
        )
        await session.execute(
            delete(BenchmarkRunRow).where(BenchmarkRunRow.id == run_id)
        )
        await session.execute(
            delete(ImportedRunRow).where(ImportedRunRow.id == run_id)
        )
        await session.commit()

    json_path.unlink(missing_ok=True)
    html_path.unlink(missing_ok=True)
    return {"deleted": True}


@router.get("/history")
async def get_history() -> list[dict[str, Any]]:
    try:
        entries = await BenchmarkManager.list_history_db()
    except Exception as exc:
        logger.warning("Historial: fallo leyendo la DB, uso los JSON: %s", exc)
        entries = []
    if entries:
        return entries
    return await BenchmarkManager.list_history()


@router.get("/history/{run_id}", response_model=BenchmarkRun)
async def get_history_run(run_id: str) -> BenchmarkRun:
    run = manager.get_run(run_id) or await BenchmarkManager.load_from_disk(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run no encontrado")
    return run


@router.get("/history/{run_id}/report", response_class=HTMLResponse)
async def get_history_report(run_id: str) -> HTMLResponse:
    html_path = RUNS_DIR / f"{run_id}.html"
    if html_path.exists():
        async with aiofiles.open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(await f.read())

    run = manager.get_run(run_id) or await BenchmarkManager.load_from_disk(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run no encontrado")
    return HTMLResponse(build_html_report(run))


@router.websocket("/run/{run_id}/progress")
async def run_progress_ws(websocket: WebSocket, run_id: str) -> None:
    await websocket.accept()

    if manager.get_run(run_id) is None:
        await websocket.send_json({"error": "Run no encontrado"})
        await websocket.close()
        return

    queue, buffered = manager.subscribe(run_id)
    try:
        for event in buffered:
            await websocket.send_json(event)
        while True:
            event = await queue.get()
            await websocket.send_json(event)
            if event.get("type") in ("complete", "cancelled", "error"):
                break
    except WebSocketDisconnect:
        pass
    finally:
        manager.unsubscribe(run_id, queue)
