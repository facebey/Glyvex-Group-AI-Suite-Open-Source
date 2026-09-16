"""
chat.py — Interfaz de chat contra el modelo activo (módulo M3).

Responsabilidades:
- Proxyear POST /api/chat/completions hacia el endpoint OpenAI-compatible del
  backend real (llama-server/ollama/lm_studio) usando httpx en streaming.
- Separar, dentro del stream, el contenido de razonamiento (<think>...</think>)
  del contenido de respuesta, emitiendo eventos distintos ("thinking_token" vs
  "token") vía SSE nativo de FastAPI (StreamingResponse).
- Calcular métricas en vivo (tokens/s, TTFT) y un resumen final ("done").
- Listar endpoints conocidos (procesos activos del launcher + default de
  config) con un ping rápido para el indicador ●verde/rojo del frontend.
- Persistir y servir el historial de conversaciones (tabla chat_conversations).

El frontend consume este stream con fetch() + ReadableStream (nunca
EventSource, que no soporta POST) — ver frontend/src/pages/Chat.jsx.

Contenido multimodal: `ChatMessage.content` acepta tanto un string como el
array de partes del formato vision de OpenAI
([{type: "image_url", ...}, {type: "text", ...}]).

Desde la Conversación E ese array admite además una parte propia de Glyvex,
{"type": "image_ref", "id": "<attachment_id>"}, que `_expand_image_refs`
convierte en un image_url con el base64 leído de data/attachments justo
antes de mandar al upstream. Así las imágenes adjuntas no viajan en base64
ni al navegador ni a la DB: el mensaje guardado solo tiene la referencia.

También expone dos endpoints de introspección que el frontend usa antes de
enviar:
- GET  /capabilities — visión, tool-calling y tamaño de contexto reales del
  endpoint activo, leídos de /props de llama-server en vez de adivinarlos
  por el nombre del modelo.
- POST /estimate     — tokens que ocuparía el próximo envío, usando
  /tokenize del upstream cuando está disponible.

Tool-calling (Conversación E, Bloque 2): cuando el request llega con
`tools_enabled`, /completions deja de ser un proxy de una sola pasada y pasa
a ser un loop — se detectan los tool_calls del modelo, se ejecuta la función,
el resultado vuelve como mensaje role=tool y se pide una generación nueva,
hasta `tools.max_rounds`. Las métricas (TTFT, tokens, t/s) se acumulan a lo
largo de todas las rondas, no se reinician en cada una.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import attachments as attachments_module
import tools as tools_module
from config import config
from stream_metrics import StreamMetrics, reasoning_from_delta

logger = logging.getLogger("glyvex.chat")
from database import (
    db_create_conversation,
    db_delete_conversation,
    db_get_conversation,
    db_list_conversations,
    db_update_conversation,
)
from launcher import manager as launcher_manager

THINK_OPEN = "<think>"
THINK_CLOSE = "</think>"

# read=120: httpx reinicia el timer por chunk, así solo corta un stream que
# se queda totalmente mudo >2 min (un modelo lento que sí emite chunks no
# se cae). Antes era read=None = sin límite de lectura.
UPSTREAM_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=30.0, pool=5.0)

TITLE_MAX_CHARS = 50

# --------------------------------------------------------------------------
# Schema (Pydantic v2)
# --------------------------------------------------------------------------

Role = Literal["user", "assistant", "system", "tool"]

ReasoningEffort = Literal["none", "low", "medium", "high"]


class ChatMessage(BaseModel):
    role: Role
    # str para texto plano; list[dict] para el formato multimodal de OpenAI.
    # None para el mensaje del asistente que solo trae tool_calls.
    content: str | list[dict[str, Any]] | None = None
    # Campos de tool-calling. Se serializan con exclude_none para no mandarle
    # claves en null a upstreams que las rechazan.
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class ChatCompletionRequest(BaseModel):
    endpoint: str = "http://127.0.0.1:8080"
    api_key: str = ""
    messages: list[ChatMessage]
    model: str = ""

    temperature: float = 0.7
    top_p: float = 0.9
    top_k: int = 40
    min_p: float = 0.0
    repeat_penalty: float = 1.1
    max_tokens: int = 4096
    seed: int = -1
    stream: bool = True

    thinking_enabled: bool = False
    budget_tokens: int = 8192
    preserve_thinking: bool = True
    reasoning_effort: ReasoningEffort = "none"

    # Acceso a web_search / fetch_url. Se decide por conversación desde la UI,
    # nunca está activo por defecto.
    tools_enabled: bool = False

    # Botón "Continuar" (Bloque 4.4): el último mensaje es el del asistente
    # con la respuesta cortada, y se espera que el modelo siga desde ahí.
    continuation: bool = False


class EndpointInfo(BaseModel):
    name: str
    url: str
    status: Literal["ok", "error"]


class Capabilities(BaseModel):
    """Lo que el endpoint activo realmente soporta, no lo que sugiere su nombre."""

    endpoint: str
    reachable: bool = False
    # De dónde salió el dato: "props" es exacto, "models" parcial,
    # "heuristic" es adivinanza por nombre de modelo.
    source: Literal["props", "models", "heuristic", "none"] = "none"
    vision: bool = False
    audio: bool = False
    tools: bool = False
    # El resto sale de chat_template_caps de /props. Sirven para no mandarle
    # al upstream parámetros que su plantilla no entiende.
    parallel_tool_calls: bool = False
    reasoning_effort: bool = False
    preserve_reasoning: bool = False
    # ¿Acepta content como array de partes, o solo string? Decide si un
    # mensaje con adjuntos se puede mandar como bloques.
    typed_content: bool = False
    system_role: bool = True
    context_size: int = 0
    model: str = ""
    build_info: str = ""


class EstimateRequest(BaseModel):
    endpoint: str = "http://127.0.0.1:8080"
    api_key: str = ""
    messages: list[dict[str, Any]] = Field(default_factory=list)
    system_prompt: str = ""
    draft: str = ""
    max_tokens: int = 4096
    # Contexto que el servidor midió al terminar la última respuesta
    # (context_tokens del evento "done"). Si viene > 0, reemplaza al historial:
    # `messages` debe traer solo lo nuevo (borrador + adjuntos) y el system
    # prompt no se vuelve a contar porque ya está adentro de esa medición.
    base_tokens: int = 0


class EstimateResponse(BaseModel):
    tokens: int = 0
    image_count: int = 0
    image_tokens: int = 0
    # "measured": todo el número salió del servidor (no hay borrador nuevo).
    source: Literal["measured", "tokenize", "heuristic"] = "heuristic"
    # Parte del total que salió de base_tokens (0 si no se usó).
    base_tokens: int = 0
    context_size: int = 0
    # Contexto que queda libre después del envío, descontando max_tokens.
    headroom: int = 0
    fits: bool = True


class ConversationCreate(BaseModel):
    title: str | None = None
    model_name: str = ""
    endpoint: str = ""
    messages: list[dict[str, Any]] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)


class ConversationUpdate(BaseModel):
    messages: list[dict[str, Any]] | None = None
    config: dict[str, Any] | None = None
    model_name: str | None = None
    title: str | None = None


class ConversationSummary(BaseModel):
    id: str
    title: str = ""
    created_at: str | None = None
    updated_at: str | None = None
    model_name: str = ""
    endpoint: str = ""
    message_count: int = 0


class ConversationDetail(ConversationSummary):
    messages: list[dict[str, Any]] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# Parser incremental de bloques <think>...</think>
# --------------------------------------------------------------------------


class ThinkingStreamParser:
    """
    Separa un stream de texto en segmentos "normales" y "thinking" según los
    tags <think>/</think>, tolerando que un tag llegue partido entre dos
    chunks del upstream (frecuente cuando el token del tag no coincide con
    el token del modelo).
    """

    def __init__(self) -> None:
        self._buffer = ""
        self.in_thinking = False

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        self._buffer += chunk
        events: list[tuple[str, str]] = []

        while True:
            tag = THINK_CLOSE if self.in_thinking else THINK_OPEN
            idx = self._buffer.find(tag)

            if idx == -1:
                # Sin tag completo todavía: emitimos todo lo "seguro" y
                # retenemos una cola lo bastante larga como para poder
                # contener el inicio partido del próximo tag.
                hold = len(tag) - 1
                if len(self._buffer) > hold:
                    safe_text = self._buffer[: len(self._buffer) - hold]
                    self._buffer = self._buffer[len(self._buffer) - hold:]
                    if safe_text:
                        events.append(
                            ("thinking_token" if self.in_thinking else "token", safe_text)
                        )
                break

            before = self._buffer[:idx]
            if before:
                events.append(("thinking_token" if self.in_thinking else "token", before))
            self._buffer = self._buffer[idx + len(tag):]
            self.in_thinking = not self.in_thinking

        return events

    def flush(self) -> list[tuple[str, str]]:
        events: list[tuple[str, str]] = []
        if self._buffer:
            events.append(("thinking_token" if self.in_thinking else "token", self._buffer))
            self._buffer = ""
        return events


def _message_text(content: Any) -> str:
    """
    Texto plano de un content, sea string o array multimodal.

    Devuelve la primera parte de texto NO vacía: un mensaje que es solo
    adjuntos lleva un bloque de texto vacío adelante, y si lo devolviéramos
    el título de la conversación quedaría en blanco.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = str(part.get("text") or "").strip()
                if text:
                    return text
    return ""


def _all_message_text(content: Any) -> str:
    """Concatena todas las partes de texto (para estimar tokens)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _count_images(content: Any) -> int:
    if not isinstance(content, list):
        return 0
    return sum(
        1
        for part in content
        if isinstance(part, dict) and part.get("type") in ("image_ref", "image_url")
    )


def _expand_image_refs(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Reemplaza las partes {"type": "image_ref", "id": ...} por el image_url
    con el base64 leído de disco.

    Si el archivo ya no está (se limpió data/attachments, o la conversación
    viene de otra máquina), la parte degrada a un bloque de texto que se lo
    explica al modelo, en vez de mandar un image_url roto que el upstream
    rechaza con 400.
    """
    expanded: list[dict[str, Any]] = []

    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            expanded.append(message)
            continue

        parts: list[dict[str, Any]] = []
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "image_ref":
                parts.append(part)
                continue

            data_url = attachments_module.resolve_image_ref(str(part.get("id") or ""))
            if data_url:
                parts.append({"type": "image_url", "image_url": {"url": data_url}})
            else:
                name = part.get("filename") or "una imagen"
                parts.append(
                    {
                        "type": "text",
                        "text": (
                            f"El usuario había adjuntado {name}, pero el archivo ya "
                            f"no está disponible en el servidor."
                        ),
                    }
                )

        expanded.append({**message, "content": parts})

    return expanded


def _apply_thinking_directive(messages: list[dict[str, Any]], enabled: bool) -> list[dict[str, Any]]:
    """
    Si thinking_enabled, antepone '/think' al primer mensaje de usuario.

    Con contenido multimodal el prefijo va sobre la parte de texto del array,
    no sobre el array entero (si no, romperíamos el formato que espera el
    upstream).
    """
    if not enabled:
        return messages
    patched = [dict(m) for m in messages]
    for m in patched:
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, str):
            m["content"] = f"/think {content}"
        elif isinstance(content, list):
            parts = [dict(p) if isinstance(p, dict) else p for p in content]
            for part in parts:
                if isinstance(part, dict) and part.get("type") == "text":
                    part["text"] = f"/think {part.get('text') or ''}"
                    break
            else:
                parts.append({"type": "text", "text": "/think"})
            m["content"] = parts
        break
    return patched


def _derive_title(messages: list[dict[str, Any]]) -> str:
    """Título automático: primeros 50 chars del primer mensaje de usuario."""
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        text = " ".join(_message_text(m.get("content")).split()).strip()
        if not text:
            continue
        return text[:TITLE_MAX_CHARS] + ("…" if len(text) > TITLE_MAX_CHARS else "")
    return "Conversación sin título"


# --------------------------------------------------------------------------
# Endpoint principal de chat (proxy streaming)
# --------------------------------------------------------------------------

router = APIRouter()


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"

# Caracteres que se miran antes de decidir si el modelo continuó o arrancó
# de cero. Suficiente para distinguirlo sin que la espera se note.
RESTART_PROBE_CHARS = 120
RESTART_MATCH_CHARS = 40


def _normalize(text: str) -> str:
    return " ".join(text.split()).lower()


def _looks_like_restart(generated: str, partial: str) -> bool:
    """
    ¿El modelo ignoró el prefill y empezó la respuesta de nuevo?

    No todos los chat templates soportan continuar desde un mensaje del
    asistente; los que no, cierran el turno y el modelo responde desde cero.
    Se compara el arranque de lo generado contra el arranque de lo que ya
    había: si coinciden, es un reinicio.
    """
    head_new = _normalize(generated)[:RESTART_MATCH_CHARS]
    head_old = _normalize(partial)[:RESTART_MATCH_CHARS]
    if len(head_new) < RESTART_MATCH_CHARS or len(head_old) < RESTART_MATCH_CHARS:
        return False
    return head_new == head_old


def _continuation_fallback(
    conversation: list[dict[str, Any]], partial: str
) -> list[dict[str, Any]]:
    """
    Plan B cuando el prefill no funciona: se saca el mensaje del asistente y
    se pide explícitamente que siga, mostrándole el final de lo que escribió.
    """
    trimmed = [m for m in conversation[:-1]]
    tail = partial[-600:]
    trimmed.append(
        {
            "role": "user",
            "content": (
                "Tu respuesta anterior se cortó por el límite de tokens. "
                "Continuá exactamente desde donde quedó, sin repetir nada de "
                "lo ya escrito y sin volver a presentar el tema. Estas son "
                f"sus últimas líneas:\n\n{tail}"
            ),
        }
    )
    return trimmed


class ToolCallAccumulator:
    """
    Rearma los tool_calls que llegan fragmentados en el stream.

    El upstream manda deltas parciales — el `id` y el `name` suelen venir en
    el primer chunk del índice, y el JSON de `arguments` repartido entre los
    siguientes, cortado en cualquier lado. Se acumula por índice y recién al
    final se parsea.
    """

    def __init__(self) -> None:
        self._calls: dict[int, dict[str, Any]] = {}

    def feed(self, deltas: list[dict[str, Any]]) -> None:
        for delta in deltas:
            if not isinstance(delta, dict):
                continue
            index = int(delta.get("index") or 0)
            call = self._calls.setdefault(
                index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
            )
            if delta.get("id"):
                call["id"] = delta["id"]
            if delta.get("type"):
                call["type"] = delta["type"]

            function = delta.get("function") or {}
            if function.get("name"):
                call["function"]["name"] = function["name"]
            if function.get("arguments"):
                call["function"]["arguments"] += function["arguments"]

    def __bool__(self) -> bool:
        return any(c["function"]["name"] for c in self._calls.values())

    def assemble(self) -> list[dict[str, Any]]:
        """Lista de tool_calls en orden de índice, con id sintético si falta."""
        out: list[dict[str, Any]] = []
        for index in sorted(self._calls):
            call = self._calls[index]
            if not call["function"]["name"]:
                continue
            if not call["id"]:
                call["id"] = f"call_{uuid.uuid4().hex[:16]}"
            out.append(call)
        return out


def _parse_arguments(raw: str) -> tuple[dict[str, Any], str | None]:
    """
    El JSON de argumentos lo genera el modelo, así que puede venir roto.
    Devuelve (argumentos, error) y deja que el error llegue al modelo como
    resultado de la tool en vez de cortar el stream.
    """
    raw = (raw or "").strip()
    if not raw:
        return {}, None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"los argumentos no son JSON válido ({exc.msg})"
    if not isinstance(parsed, dict):
        return {}, "los argumentos no son un objeto JSON"
    return parsed, None


async def _stream_chat(req: ChatCompletionRequest) -> AsyncGenerator[str, None]:
    max_rounds = int(config.get("tools.max_rounds", 5))

    conversation = _apply_thinking_directive(
        _expand_image_refs([m.model_dump(exclude_none=True) for m in req.messages]),
        req.thinking_enabled,
    )

    headers = {"Content-Type": "application/json"}
    if req.api_key:
        headers["Authorization"] = f"Bearer {req.api_key}"

    url = req.endpoint.rstrip("/") + "/v1/chat/completions"

    logger.info(
        "turno inicio: model=%s endpoint=%s msgs=%d thinking=%s tools=%s",
        req.model, req.endpoint, len(req.messages), req.thinking_enabled, req.tools_enabled,
    )

    # Métricas de todo el turno (todas las rondas de tools). La lógica vive
    # en stream_metrics.py, compartida con benchmark.py: ahí está explicado
    # por qué el razonamiento cuenta para TTFT y de dónde sale cada número.
    turn = StreamMetrics()
    finish_reason: str | None = None

    # Mensajes que el loop agregó a la conversación (assistant con tool_calls
    # + resultados). Viajan en el evento "done" para que el frontend los
    # guarde y el próximo turno conserve el contexto de lo buscado.
    extra_messages: list[dict[str, Any]] = []
    tool_activity: list[dict[str, Any]] = []

    def _metrics_event() -> str:
        return _sse({"type": "metrics", **turn.snapshot()})

    def _base_payload() -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": req.model,
            "messages": conversation,
            "temperature": req.temperature,
            "top_p": req.top_p,
            "top_k": req.top_k,
            "min_p": req.min_p,
            "repeat_penalty": req.repeat_penalty,
            "max_tokens": req.max_tokens,
            "seed": req.seed,
            "stream": True,
            # El contador de chunks del stream no es un contador de tokens:
            # un chunk puede traer varios tokens o un fragmento de uno. Con
            # esto el upstream manda el usage exacto en el último chunk, y
            # las métricas finales dejan de ser una aproximación.
            "stream_options": {"include_usage": True},
        }
        # Algunos modelos (QwQ, gpt-oss) usan reasoning_effort en vez de
        # budget_tokens; llama-server lo acepta directo en el payload.
        if req.reasoning_effort != "none":
            payload["reasoning_effort"] = req.reasoning_effort
        if req.tools_enabled:
            payload["tools"] = tools_module.TOOL_SPECS
            payload["tool_choice"] = "auto"
        return payload

    # Continuación: el último mensaje es el del asistente con la respuesta
    # cortada. Se intenta prefill (que el modelo siga ese mismo turno) y, si
    # el template no lo soporta y arranca de cero, se reintenta pidiéndoselo
    # explícitamente.
    partial_text = ""
    if req.continuation and conversation and conversation[-1].get("role") == "assistant":
        partial_text = _message_text(conversation[-1].get("content"))
    guard_active = bool(partial_text)
    fallback_used = False

    try:
        async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT) as client:
            round_index = 0
            while round_index <= max_rounds:
                # Cada ronda es una generación nueva, así que el parser de
                # <think> arranca limpio: los tags abren y cierran dentro de
                # una misma respuesta.
                parser = ThinkingStreamParser()
                accumulator = ToolCallAccumulator()
                round_text = ""
                finish_reason = None

                # Mientras el guard está activo no se emite nada: se junta lo
                # suficiente para saber si el modelo continuó o reinició.
                guard_buffer = ""
                # El razonamiento que llega mientras el guard está abierto se
                # retiene igual que el texto: si el modelo reinició, se tira.
                guard_thinking = ""
                guard_open = guard_active
                restart_detected = False
                turn.begin_round()

                async with client.stream(
                    "POST", url, json=_base_payload(), headers=headers
                ) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        detail = body[:300].decode("utf-8", "replace")
                        message = f"Upstream {response.status_code}: {detail}"
                        if req.tools_enabled and response.status_code in (400, 500):
                            message += (
                                " — si el endpoint es llama-server, el "
                                "tool-calling necesita que esté corriendo con "
                                "--jinja."
                            )
                        yield _sse({"type": "error", "message": message})
                        yield "data: [DONE]\n\n"
                        return

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

                        # usage y timings vienen en el último chunk, con
                        # choices vacío, así que se leen antes de tocar delta.
                        turn.observe_chunk(chunk)

                        choice = (chunk.get("choices") or [{}])[0]
                        delta = choice.get("delta") or {}
                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]

                        if delta.get("tool_calls"):
                            accumulator.feed(delta["tool_calls"])
                            turn.mark_token(thinking=False)

                        # llama-server (--reasoning-format por defecto), LM
                        # Studio, vLLM y Ollama mandan el razonamiento en un
                        # campo aparte, no como <think> dentro de content.
                        reasoning = reasoning_from_delta(delta)
                        if reasoning:
                            turn.mark_token(thinking=True)
                            if guard_open:
                                guard_thinking += reasoning
                            elif req.preserve_thinking:
                                yield _sse({"type": "thinking_token", "content": reasoning})

                        content = delta.get("content")
                        if not content:
                            if reasoning:
                                yield _metrics_event()
                            continue

                        # El conteo de chunks de razonamiento usa el estado
                        # del parser ANTES de este chunk (mismo criterio que
                        # antes); el TTFT de respuesta se marca abajo, con el
                        # primer segmento visible que devuelve el parser.
                        turn.mark_token(thinking=parser.in_thinking)

                        if guard_open:
                            guard_buffer += content
                            if len(guard_buffer) < RESTART_PROBE_CHARS:
                                continue
                            if _looks_like_restart(guard_buffer, partial_text):
                                restart_detected = True
                                break
                            # Continuó bien: se libera lo acumulado de una vez
                            # y a partir de acá se emite normal.
                            guard_open = False
                            content = guard_buffer
                            guard_buffer = ""
                            if guard_thinking and req.preserve_thinking:
                                yield _sse({"type": "thinking_token", "content": guard_thinking})
                            guard_thinking = ""

                        if not parser.in_thinking:
                            round_text += content

                        for kind, text in parser.feed(content):
                            if kind == "token" and text.strip():
                                turn.mark_answer()
                            if kind == "thinking_token" and not req.preserve_thinking:
                                continue
                            yield _sse({"type": kind, "content": text})

                        yield _metrics_event()

                if restart_detected:
                    turn.discard_round()
                    if fallback_used:
                        # El plan B también reinició: no hay más que probar,
                        # se lo decimos en vez de duplicar la respuesta.
                        yield _sse(
                            {
                                "type": "error",
                                "message": (
                                    "El modelo vuelve a empezar la respuesta en vez de "
                                    "continuarla. Su chat template no soporta continuar "
                                    "un turno; probá subiendo max_tokens."
                                ),
                            }
                        )
                        break
                    fallback_used = True
                    guard_active = False
                    conversation = _continuation_fallback(conversation, partial_text)
                    # No cuenta como ronda de tools: es el mismo turno.
                    continue

                if guard_open and guard_buffer:
                    # La respuesta terminó antes de llegar al umbral del guard.
                    if _looks_like_restart(guard_buffer, partial_text):
                        guard_buffer = ""
                    else:
                        if guard_thinking and req.preserve_thinking:
                            yield _sse({"type": "thinking_token", "content": guard_thinking})
                        for kind, text in parser.feed(guard_buffer):
                            if kind == "token" and text.strip():
                                turn.mark_answer()
                            if kind == "thinking_token" and not req.preserve_thinking:
                                continue
                            yield _sse({"type": kind, "content": text})
                        round_text += guard_buffer
                    guard_buffer = ""
                elif guard_open and guard_thinking and req.preserve_thinking:
                    # Solo razonamiento, sin texto que comparar: se libera.
                    yield _sse({"type": "thinking_token", "content": guard_thinking})
                guard_thinking = ""
                guard_open = False

                for kind, text in parser.flush():
                    if kind == "token" and text.strip():
                        turn.mark_answer()
                    if kind == "thinking_token" and not req.preserve_thinking:
                        continue
                    yield _sse({"type": kind, "content": text})

                turn.end_round()

                calls = accumulator.assemble() if req.tools_enabled else []

                if not calls:
                    break

                if round_index >= max_rounds:
                    # Se agotaron las rondas con el modelo todavía pidiendo
                    # tools: se lo decimos en el chat en vez de cortar mudo.
                    yield _sse(
                        {
                            "type": "token",
                            "content": (
                                f"\n\n_(Se alcanzó el límite de {max_rounds} usos de "
                                f"herramientas en un mismo turno.)_"
                            ),
                        }
                    )
                    finish_reason = "tool_rounds_exhausted"
                    break

                assistant_turn = {
                    "role": "assistant",
                    "content": round_text,
                    "tool_calls": calls,
                }
                conversation.append(assistant_turn)
                extra_messages.append(assistant_turn)

                # Se avisa de todas las llamadas antes de ejecutar ninguna:
                # con tool calls en paralelo el usuario ve las dos en marcha.
                prepared: list[dict[str, Any]] = []
                for call in calls:
                    name = call["function"]["name"]
                    arguments, arg_error = _parse_arguments(call["function"]["arguments"])
                    prepared.append({"call": call, "name": name, "arguments": arguments,
                                     "error": arg_error})
                    yield _sse(
                        {
                            "type": "tool_call",
                            "id": call["id"],
                            "name": name,
                            "arguments": arguments,
                            "round": round_index + 1,
                        }
                    )

                async def _run(item: dict[str, Any]) -> dict[str, Any]:
                    if item["error"]:
                        return {
                            "ok": False,
                            "name": item["name"],
                            "content": (
                                f"Error al ejecutar {item['name']}: {item['error']}. "
                                "Reintentá con argumentos JSON válidos."
                            ),
                            "summary": item["error"],
                            "sources": [],
                        }
                    return await tools_module.execute_tool(item["name"], item["arguments"])

                results = await asyncio.gather(
                    *(_run(item) for item in prepared), return_exceptions=True
                )

                round_index += 1

                for item, result in zip(prepared, results):
                    if isinstance(result, BaseException):
                        result = {
                            "ok": False,
                            "name": item["name"],
                            "content": f"Error al ejecutar {item['name']}: {result}",
                            "summary": str(result),
                            "sources": [],
                        }

                    tool_turn = {
                        "role": "tool",
                        "tool_call_id": item["call"]["id"],
                        "name": item["name"],
                        "content": result["content"],
                    }
                    conversation.append(tool_turn)
                    extra_messages.append(tool_turn)

                    entry = {
                        "id": item["call"]["id"],
                        "name": item["name"],
                        "arguments": item["arguments"],
                        "ok": bool(result.get("ok")),
                        "summary": result.get("summary") or "",
                        "sources": result.get("sources") or [],
                    }
                    tool_activity.append(entry)
                    yield _sse({"type": "tool_result", **entry})

        final = turn.snapshot()
        logger.info(
            "turno fin: model=%s tokens=%d thinking=%d tps=%s pp_tps=%s ttft_ms=%s "
            "ttft_respuesta_ms=%s contexto=%s fuente=%s tools=%d duracion_s=%.1f",
            req.model, final["tokens_total"], final["tokens_thinking"], final["tps"],
            final["pp_tps"], final["ttft_ms"], final["ttft_answer_ms"],
            final["context_tokens"], final["metrics_source"],
            len(tool_activity), final["duration_s"],
        )
        yield _sse(
            {
                "type": "done",
                "finish_reason": finish_reason or "stop",
                **final,
                # Compatibilidad: el frontend y los tests leían estos nombres.
                "total_tokens": final["tokens_total"],
                "tokens_source": "chunks" if final["metrics_source"] == "chunks" else "usage",
                "tool_messages": extra_messages,
                "tool_activity": tool_activity,
            }
        )
        yield "data: [DONE]\n\n"

    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as exc:
        logger.error(
            "error de conexion: endpoint=%s model=%s: %s",
            req.endpoint, req.model, exc,
        )
        yield _sse({"type": "error", "message": f"No se pudo conectar al endpoint: {exc}"})
        yield "data: [DONE]\n\n"


@router.post("/completions")
async def chat_completions(req: ChatCompletionRequest) -> StreamingResponse:
    return StreamingResponse(_stream_chat(req), media_type="text/event-stream")


# --------------------------------------------------------------------------
# Historial de conversaciones (CRUD sobre chat_conversations)
# --------------------------------------------------------------------------


@router.post("/conversations", response_model=ConversationDetail, status_code=201)
async def create_conversation(req: ConversationCreate) -> ConversationDetail:
    title = (req.title or "").strip() or _derive_title(req.messages)
    data = await db_create_conversation(
        conv_id=uuid.uuid4().hex,
        title=title,
        model_name=req.model_name,
        endpoint=req.endpoint,
        messages=req.messages,
        config=req.config,
    )
    return ConversationDetail(**data)


@router.get("/conversations", response_model=list[ConversationSummary])
async def list_conversations(
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    search: str = Query(""),
) -> list[ConversationSummary]:
    rows = await db_list_conversations(limit=limit, offset=offset, search=search)
    return [ConversationSummary(**row) for row in rows]


@router.get("/conversations/{conv_id}", response_model=ConversationDetail)
async def get_conversation(conv_id: str) -> ConversationDetail:
    data = await db_get_conversation(conv_id)
    if data is None:
        raise HTTPException(status_code=404, detail="La conversación no existe")
    return ConversationDetail(**data)


@router.put("/conversations/{conv_id}", response_model=ConversationDetail)
async def update_conversation(conv_id: str, req: ConversationUpdate) -> ConversationDetail:
    title = req.title
    if title is None and req.messages is not None:
        # Mantenemos el título si ya existe; solo lo derivamos cuando la
        # conversación se creó vacía y recién ahora tiene un primer mensaje.
        current = await db_get_conversation(conv_id)
        if current is not None and not (current.get("title") or "").strip():
            title = _derive_title(req.messages)

    data = await db_update_conversation(
        conv_id,
        messages=req.messages,
        config=req.config,
        model_name=req.model_name,
        title=title,
    )
    if data is None:
        raise HTTPException(status_code=404, detail="La conversación no existe")
    return ConversationDetail(**data)


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str) -> dict[str, Any]:
    # Se leen los adjuntos antes de borrar la fila: después ya no hay de
    # dónde sacar los ids y los archivos quedarían en disco para siempre.
    conversation = await db_get_conversation(conv_id)

    if not await db_delete_conversation(conv_id):
        raise HTTPException(status_code=404, detail="La conversación no existe")

    removed = 0
    if conversation:
        ids: set[str] = set()
        for node in conversation.get("messages") or []:
            if not isinstance(node, dict):
                continue
            for attachment in node.get("attachments") or []:
                if isinstance(attachment, dict) and attachment.get("id"):
                    ids.add(str(attachment["id"]))
        removed = attachments_module.delete_attachments(ids)

    return {"id": conv_id, "deleted": True, "attachments_removed": removed}


# --------------------------------------------------------------------------
# Descubrimiento de endpoints disponibles
# --------------------------------------------------------------------------


async def _ping(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            res = await client.get(url.rstrip("/") + "/v1/models")
            return res.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError):
        return False


VISION_NAME_HINT = r"(^|[-_\s.])(vl|vision|mmproj|llava|multimodal)([-_\s.]|$)"
TOOLS_NAME_HINT = r"(qwen|hermes|firefunction|functionary|mistral|command-r|llama-3)"


def _cap_flag(props: dict[str, Any], *keys: str, default: bool = False) -> bool:
    """
    Lee una bandera de chat_template_caps probando varios nombres.

    Las builds actuales de llama.cpp usan el prefijo supports_ (verificado
    contra una build real: supports_tools, supports_tool_calls,
    supports_parallel_tool_calls, supports_reasoning_effort,
    supports_preserve_reasoning, supports_typed_content, supports_string_content,
    supports_system_role, supports_object_arguments). Los alias sin prefijo
    quedan por compatibilidad con builds viejas.
    """
    caps = props.get("chat_template_caps")
    if not isinstance(caps, dict):
        return default
    for key in keys:
        if key in caps:
            return bool(caps[key])
    return default


def _template_supports_tools(props: dict[str, Any]) -> bool:
    """
    Soporte de tool-calling. Si /props no trae chat_template_caps (builds
    anteriores a que existiera), se busca el marcador dentro del propio
    chat_template.
    """
    caps = props.get("chat_template_caps")
    if isinstance(caps, dict):
        return _cap_flag(props, "supports_tools", "tools", "supports_tool_calls", "tool_calls")

    template = props.get("chat_template")
    if isinstance(template, str) and template:
        lowered = template.lower()
        return "tools" in lowered and "tool_call" in lowered

    return False


def _context_from_props(props: dict[str, Any]) -> int:
    settings = props.get("default_generation_settings")
    if isinstance(settings, dict):
        ctx = settings.get("n_ctx")
        if isinstance(ctx, int) and ctx > 0:
            return ctx
    ctx = props.get("n_ctx")
    return ctx if isinstance(ctx, int) and ctx > 0 else 0


# Cache corto de capacidades por endpoint. /estimate se llama cada vez que
# el usuario deja de escribir, y sin esto cada llamada dispararía dos
# requests más contra el endpoint de inferencia.
_CAPS_TTL_S = 30.0
_caps_cache: dict[str, tuple[float, Capabilities]] = {}


async def probe_capabilities(endpoint: str, model: str = "") -> Capabilities:
    """
    Qué soporta de verdad el endpoint activo.

    Orden de preferencia: /props de llama-server (exacto) → /v1/models
    (parcial: solo contexto) → nombre del modelo (adivinanza). El campo
    `source` le dice al frontend con cuánta confianza puede tratar el
    resultado.
    """
    import re

    base = endpoint.rstrip("/")

    cached = _caps_cache.get(f"{base}|{model}")
    if cached and time.monotonic() - cached[0] < _CAPS_TTL_S:
        return cached[1]

    result = Capabilities(endpoint=base, model=model)

    async with httpx.AsyncClient(timeout=4.0) as client:
        # 1. /props — ruta nativa de llama-server
        try:
            res = await client.get(f"{base}/props")
            if res.status_code == 200:
                props = res.json()
                modalities = props.get("modalities") or {}
                result.reachable = True
                result.source = "props"
                result.vision = bool(modalities.get("vision"))
                result.audio = bool(modalities.get("audio"))
                result.tools = _template_supports_tools(props)
                result.parallel_tool_calls = _cap_flag(
                    props, "supports_parallel_tool_calls", "parallel_tool_calls"
                )
                result.reasoning_effort = _cap_flag(
                    props, "supports_reasoning_effort", "reasoning_effort"
                )
                result.preserve_reasoning = _cap_flag(
                    props, "supports_preserve_reasoning", "preserve_reasoning"
                )
                result.typed_content = _cap_flag(
                    props, "supports_typed_content", "typed_content"
                )
                result.system_role = _cap_flag(
                    props, "supports_system_role", "system_role", default=True
                )
                result.context_size = _context_from_props(props)
                result.model = props.get("model_alias") or props.get("model_path") or model
                result.build_info = str(props.get("build_info") or "")
                if result.context_size > 0:
                    _caps_cache[f"{base}|{model}"] = (time.monotonic(), result)
                    return result
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError):
            pass

        # 2. /v1/models — ollama, LM Studio, o un proxy que esconde /props
        try:
            res = await client.get(f"{base}/v1/models")
            if res.status_code == 200:
                entries = (res.json() or {}).get("data") or []
                first = next(
                    (e for e in entries if e.get("id") == model), entries[0] if entries else {}
                )
                meta = first.get("meta") or {}
                ctx = int(
                    first.get("context_length") or meta.get("n_ctx") or meta.get("n_ctx_train") or 0
                )
                result.reachable = True
                if result.source != "props":
                    result.source = "models"
                if result.context_size <= 0:
                    result.context_size = ctx if ctx > 0 else 0
                result.model = result.model or first.get("id") or model
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError, ValueError):
            pass

    # 3. Heurística por nombre — último recurso, y queda marcado como tal
    if result.source in ("none", "models") and not result.vision:
        name = result.model or model
        if name and re.search(VISION_NAME_HINT, name, re.IGNORECASE):
            result.vision = True
            if result.source == "none":
                result.source = "heuristic"
    if result.source in ("none", "models") and not result.tools:
        name = result.model or model
        if name and re.search(TOOLS_NAME_HINT, name, re.IGNORECASE):
            result.tools = True

    _caps_cache[f"{base}|{model}"] = (time.monotonic(), result)
    return result


@router.get("/capabilities", response_model=Capabilities)
async def get_capabilities(
    endpoint: str = Query(..., description="URL base del endpoint OpenAI-compatible"),
    model: str = Query("", description="Modelo activo, solo para el fallback heurístico"),
) -> Capabilities:
    """
    Ruta fina sobre probe_capabilities.

    La lógica vive aparte a propósito: /estimate también la necesita, y
    llamar a una función de ruta desde código normal hace que los defaults
    de Query lleguen como objetos Query en vez de valores.
    """
    return await probe_capabilities(endpoint, model)


@router.post("/estimate", response_model=EstimateResponse)
async def estimate_context(req: EstimateRequest) -> EstimateResponse:
    """
    Cuántos tokens ocuparía el próximo envío.

    Usa POST /tokenize del upstream cuando existe (exacto para el texto,
    sin contar el overhead de la plantilla de chat) y cae a ~3.6 chars por
    token si no. Las imágenes no pasan por el tokenizer: se estiman con
    attachments.image_tokens_estimate, que es configurable porque el costo
    real varía muchísimo entre modelos de visión.

    Con `base_tokens` (contexto medido por el servidor en la última respuesta)
    solo se estima lo nuevo: es más exacto que re-tokenizar el historial,
    porque incluye la plantilla, los schemas de tools y el razonamiento que
    quedó en el KV.
    """
    per_image = int(config.get("attachments.image_tokens_estimate", 1024))
    base_tokens = max(0, req.base_tokens)

    chunks: list[str] = []
    if req.system_prompt.strip() and not base_tokens:
        chunks.append(req.system_prompt)
    image_count = 0
    for message in req.messages:
        chunks.append(_all_message_text(message.get("content")))
        image_count += _count_images(message.get("content"))
    if req.draft.strip():
        chunks.append(req.draft)

    blob = "\n".join(c for c in chunks if c)
    # ~4 tokens de overhead por mensaje entre delimitadores de rol del template.
    # Con base medida, el overhead del historial ya está contado.
    overhead = 4 * len(req.messages) if base_tokens else 4 * (len(req.messages) + 1)

    base = req.endpoint.rstrip("/")
    text_tokens = 0
    source: Literal["measured", "tokenize", "heuristic"] = "heuristic"

    if base_tokens and not blob and not req.messages:
        source = "measured"
    elif blob:
        headers = {"Content-Type": "application/json"}
        if req.api_key:
            headers["Authorization"] = f"Bearer {req.api_key}"
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                res = await client.post(
                    f"{base}/tokenize", json={"content": blob}, headers=headers
                )
                if res.status_code == 200:
                    tokens = (res.json() or {}).get("tokens")
                    if isinstance(tokens, list):
                        text_tokens = len(tokens)
                        source = "tokenize"
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError, ValueError):
            pass

        if source == "heuristic":
            text_tokens = max(1, round(len(blob) / 3.6))

    image_tokens = image_count * per_image
    total = base_tokens + text_tokens + image_tokens + overhead

    caps = await probe_capabilities(req.endpoint)
    context_size = caps.context_size
    headroom = context_size - total - req.max_tokens if context_size > 0 else 0

    return EstimateResponse(
        tokens=total,
        image_count=image_count,
        image_tokens=image_tokens,
        source=source,
        base_tokens=base_tokens,
        context_size=context_size,
        headroom=headroom,
        fits=context_size <= 0 or headroom >= 0,
    )


@router.get("/tools/status")
async def tools_status() -> dict[str, Any]:
    """
    Diagnóstico para la UI: si el proveedor de búsqueda no está listo, el
    toggle del globo lo dice en el tooltip antes de que el usuario lo active
    y se coma un error a mitad de una respuesta.
    """
    search = await tools_module.check_search_provider()
    return {
        "search": search,
        "providers": tools_module.provider_catalog(),
        "tools": [spec["function"]["name"] for spec in tools_module.TOOL_SPECS],
        "max_rounds": int(config.get("tools.max_rounds", 5)),
    }


@router.get("/endpoints", response_model=list[EndpointInfo])
async def list_endpoints() -> list[EndpointInfo]:
    candidates: dict[str, str] = {}

    for proc in launcher_manager.status_all():
        if proc.state == "running" and proc.backend in ("llama_server", "ollama"):
            url = f"http://{proc.host}:{proc.port}"
            candidates[url] = f"{proc.model_name} ({proc.backend})"

    default_port = config.get("backends.llama_server.default_port", 8080)
    default_url = f"http://127.0.0.1:{default_port}"
    candidates.setdefault(default_url, "Local llama-server")

    results = []
    for url, name in candidates.items():
        is_up = await _ping(url)
        results.append(EndpointInfo(name=name, url=url, status="ok" if is_up else "error"))
    return results


# --------------------------------------------------------------------------
# Adjuntos
# --------------------------------------------------------------------------
# Se monta acá y no en main.py para que las rutas queden bajo el prefijo
# /api/chat que ya existe: /api/chat/attachments y
# /api/chat/attachments/{id}/raw.

router.include_router(attachments_module.router, tags=["attachments"])
