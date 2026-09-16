"""
database.py — Capa de persistencia SQLite de Glyvex-AI-Suite (módulo M7).

Además de los runs de benchmark, esta capa es ahora la fuente de verdad de
los templates de hardware (tabla `hw_templates`). El JSON histórico
`data/templates/hw_templates.json` se sigue leyendo al arrancar, pero solo
como semilla: los templates que todavía no existen en la DB se importan una
vez (idempotente vía `session.merge`) y a partir de ahí toda lectura y
escritura pasa por SQLite.

Desde la Conversación D suma también el historial de chat
(tabla `chat_conversations`): cada conversación del módulo M3 se guarda
completa como JSON, con metadatos ligeros (título, modelo, endpoint,
cantidad de mensajes) para poder listarla sin traer el array entero.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiofiles
from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "glyvex.db"
RUNS_DIR = DATA_DIR / "benchmarks"
TEMPLATES_JSON = DATA_DIR / "templates" / "hw_templates.json"

DB_URL = f"sqlite+aiosqlite:///{DB_PATH.as_posix()}"


class Base(DeclarativeBase):
    pass


class BenchmarkRunRow(Base):
    __tablename__ = "benchmark_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str | None] = mapped_column(Text)
    endpoint: Mapped[str | None] = mapped_column(Text)
    sets: Mapped[str | None] = mapped_column(Text)
    config: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)


class BenchmarkResultRow(Base):
    __tablename__ = "benchmark_results"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("benchmark_runs.id"), index=True)
    prompt_id: Mapped[str | None] = mapped_column(Text)
    set_id: Mapped[str | None] = mapped_column(Text)
    prompt_title: Mapped[str | None] = mapped_column(Text)
    prompt_text: Mapped[str | None] = mapped_column(Text)
    response: Mapped[str | None] = mapped_column(Text)
    thinking: Mapped[str | None] = mapped_column(Text)
    tps: Mapped[float | None] = mapped_column(Float)
    ttft_ms: Mapped[float | None] = mapped_column(Float)
    tokens_generated: Mapped[int | None] = mapped_column(Integer)
    tokens_thinking: Mapped[int | None] = mapped_column(Integer)
    duration_s: Mapped[float | None] = mapped_column(Float)
    keywords_found: Mapped[str | None] = mapped_column(Text)
    keywords_missing: Mapped[str | None] = mapped_column(Text)
    score_keywords: Mapped[float | None] = mapped_column(Float)
    score_judge: Mapped[float | None] = mapped_column(Float)
    judge_reasoning: Mapped[str | None] = mapped_column(Text)
    judge_model: Mapped[str | None] = mapped_column(Text)
    judged_at: Mapped[str | None] = mapped_column(Text)
    score_manual: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)


class BenchmarkSummaryRow(Base):
    __tablename__ = "benchmark_summaries"
    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    total_prompts: Mapped[int | None] = mapped_column(Integer)
    completed: Mapped[int | None] = mapped_column(Integer)
    errors: Mapped[int | None] = mapped_column(Integer)
    avg_tps: Mapped[float | None] = mapped_column(Float)
    avg_ttft_ms: Mapped[float | None] = mapped_column(Float)
    avg_tokens: Mapped[float | None] = mapped_column(Float)
    total_duration_s: Mapped[float | None] = mapped_column(Float)
    keyword_hit_rate: Mapped[float | None] = mapped_column(Float)
    avg_judge_score: Mapped[float | None] = mapped_column(Float)


class ImportedRunRow(Base):
    __tablename__ = "imported_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)


class HWTemplateRow(Base):
    __tablename__ = "hw_templates"
    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    params: Mapped[str] = mapped_column(Text)  # JSON serializado


class ChatConversationRow(Base):
    """Historial del módulo M3. `messages` guarda el array completo en JSON."""

    __tablename__ = "chat_conversations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str | None] = mapped_column(Text)          # auto del primer mensaje
    created_at: Mapped[str | None] = mapped_column(Text)     # ISO8601
    updated_at: Mapped[str | None] = mapped_column(Text, index=True)  # ISO8601
    model_name: Mapped[str | None] = mapped_column(Text)
    endpoint: Mapped[str | None] = mapped_column(Text)
    messages: Mapped[str | None] = mapped_column(Text)       # JSON array completo
    config: Mapped[str | None] = mapped_column(Text)         # JSON: temperatura, etc.
    message_count: Mapped[int | None] = mapped_column(Integer)


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _session_factory
    if _engine is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _engine = create_async_engine(DB_URL, echo=False)
        _session_factory = async_sessionmaker(
            _engine, class_=AsyncSession, expire_on_commit=False
        )
    return _engine


def get_session() -> AsyncSession:
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    return _session_factory()


async def init_db() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.exec_driver_sql("PRAGMA synchronous=NORMAL")
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)
    logger.info("DB lista en %s", DB_PATH)
    await _sync_builtin_templates()
    await _migrate_existing_runs()


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads_dict(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _loads_list(raw: str | None) -> list[Any]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def utc_now_iso() -> str:
    """Timestamp ISO8601 en UTC, con segundos de resolución."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_result_id(run_id: str, index: int) -> str:
    return f"{run_id}-{index:04d}"


# --------------------------------------------------------------------------
# Templates de hardware (tabla hw_templates)
# --------------------------------------------------------------------------


def _template_to_dict(row: HWTemplateRow) -> dict[str, Any]:
    return {
        "name": row.name,
        "builtin": bool(row.builtin),
        "params": _loads_dict(row.params),
    }


async def _sync_builtin_templates() -> None:
    """
    Semilla de templates desde data/templates/hw_templates.json.

    Solo inserta los que todavía NO existen en la tabla `hw_templates`, así
    que los templates ya guardados por el usuario nunca se pisan. Se importan
    también las entradas con builtin=False del JSON histórico (migración de
    una sola vez): si no lo hiciéramos, los templates custom que el usuario
    tenía guardados en el JSON se perderían al migrar a SQLite. El flag
    `builtin` se respeta tal cual viene en el archivo.
    """
    if not TEMPLATES_JSON.exists():
        return
    try:
        async with aiofiles.open(TEMPLATES_JSON, "r", encoding="utf-8") as f:
            raw = await f.read()
        items = json.loads(raw) if raw.strip() else []
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("No se pudo leer %s: %s", TEMPLATES_JSON, exc)
        return
    if not isinstance(items, list):
        return

    synced = 0
    async with get_session() as session:
        existing = set(
            (await session.execute(select(HWTemplateRow.name))).scalars().all()
        )
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name or name in existing:
                continue
            params = item.get("params")
            await session.merge(HWTemplateRow(
                name=name,
                builtin=bool(item.get("builtin", False)),
                params=_dumps(params if isinstance(params, dict) else {}),
            ))
            existing.add(name)
            synced += 1
        if synced:
            await session.commit()
    if synced:
        logger.info("Templates de hardware sincronizados a la DB: %d", synced)


async def db_list_templates() -> list[dict[str, Any]]:
    """Templates ordenados: primero los predefinidos, después alfabético."""
    async with get_session() as session:
        rows = (
            await session.execute(
                select(HWTemplateRow).order_by(
                    HWTemplateRow.builtin.desc(), HWTemplateRow.name.asc()
                )
            )
        ).scalars().all()
    return [_template_to_dict(row) for row in rows]


async def db_get_template(name: str) -> dict[str, Any] | None:
    async with get_session() as session:
        row = await session.get(HWTemplateRow, name)
        return _template_to_dict(row) if row is not None else None


async def db_upsert_template(name: str, builtin: bool, params: dict[str, Any]) -> dict[str, Any]:
    """INSERT OR REPLACE del template. Retorna el template guardado."""
    payload = params if isinstance(params, dict) else {}
    async with get_session() as session:
        await session.merge(HWTemplateRow(
            name=name, builtin=bool(builtin), params=_dumps(payload)
        ))
        await session.commit()
    return {"name": name, "builtin": bool(builtin), "params": dict(payload)}


async def db_delete_template(name: str) -> bool:
    """
    Borra un template custom. Retorna False si no existe o si es builtin
    (los predefinidos no se pueden eliminar).
    """
    async with get_session() as session:
        row = await session.get(HWTemplateRow, name)
        if row is None or row.builtin:
            return False
        await session.delete(row)
        await session.commit()
    return True


# --------------------------------------------------------------------------
# Historial de chat (tabla chat_conversations)
# --------------------------------------------------------------------------


def _conversation_to_dict(row: ChatConversationRow, *, with_messages: bool) -> dict[str, Any]:
    """
    Serializa una fila de conversación.

    `with_messages=False` devuelve la versión ligera que usa el listado del
    panel de historial: sin el array de mensajes, que puede pesar megas
    cuando la conversación incluye imágenes en base64.
    """
    data: dict[str, Any] = {
        "id": row.id,
        "title": row.title or "",
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "model_name": row.model_name or "",
        "endpoint": row.endpoint or "",
        "message_count": int(row.message_count or 0),
    }
    if with_messages:
        data["messages"] = _loads_list(row.messages)
        data["config"] = _loads_dict(row.config)
    return data


async def db_create_conversation(
    conv_id: str,
    title: str,
    model_name: str,
    endpoint: str,
    messages: list[Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Crea la conversación y la devuelve completa."""
    now = utc_now_iso()
    row = ChatConversationRow(
        id=conv_id,
        title=title,
        created_at=now,
        updated_at=now,
        model_name=model_name,
        endpoint=endpoint,
        messages=_dumps(_as_list(messages)),
        config=_dumps(config if isinstance(config, dict) else {}),
        message_count=len(_as_list(messages)),
    )
    async with get_session() as session:
        await session.merge(row)
        await session.commit()
    return _conversation_to_dict(row, with_messages=True)


async def db_list_conversations(
    limit: int = 20, offset: int = 0, search: str = ""
) -> list[dict[str, Any]]:
    """Listado ligero ordenado por updated_at DESC, con búsqueda opcional."""
    stmt = select(ChatConversationRow).order_by(
        ChatConversationRow.updated_at.desc(), ChatConversationRow.created_at.desc()
    )
    term = (search or "").strip()
    if term:
        like = f"%{term}%"
        # ilike sobre SQLite es case-insensitive para ASCII vía LIKE.
        stmt = stmt.where(
            or_(
                ChatConversationRow.title.ilike(like),
                ChatConversationRow.messages.ilike(like),
            )
        )
    stmt = stmt.limit(max(1, min(limit, 200))).offset(max(0, offset))

    async with get_session() as session:
        rows = (await session.execute(stmt)).scalars().all()
    return [_conversation_to_dict(row, with_messages=False) for row in rows]


async def db_get_conversation(conv_id: str) -> dict[str, Any] | None:
    async with get_session() as session:
        row = await session.get(ChatConversationRow, conv_id)
        return _conversation_to_dict(row, with_messages=True) if row is not None else None


async def db_update_conversation(
    conv_id: str,
    messages: list[Any] | None = None,
    config: dict[str, Any] | None = None,
    model_name: str | None = None,
    title: str | None = None,
) -> dict[str, Any] | None:
    """
    Actualiza los campos provistos (los que llegan en None no se tocan) y
    refresca updated_at + message_count. Retorna None si no existe.
    """
    async with get_session() as session:
        row = await session.get(ChatConversationRow, conv_id)
        if row is None:
            return None
        if messages is not None:
            items = _as_list(messages)
            row.messages = _dumps(items)
            row.message_count = len(items)
        if config is not None:
            row.config = _dumps(config if isinstance(config, dict) else {})
        if model_name is not None:
            row.model_name = model_name
        if title is not None:
            row.title = title
        row.updated_at = utc_now_iso()
        await session.commit()
        return _conversation_to_dict(row, with_messages=True)


async def db_delete_conversation(conv_id: str) -> bool:
    async with get_session() as session:
        row = await session.get(ChatConversationRow, conv_id)
        if row is None:
            return False
        await session.delete(row)
        await session.commit()
    return True


# --------------------------------------------------------------------------
# Migración de runs históricos en JSON
# --------------------------------------------------------------------------


async def _migrate_existing_runs() -> None:
    if not RUNS_DIR.exists():
        return
    paths = sorted(RUNS_DIR.glob("*.json"))
    if not paths:
        return
    imported = 0
    async with get_session() as session:
        already = set(
            (await session.execute(select(ImportedRunRow.id))).scalars().all()
        )
        for path in paths:
            if path.stem in already:
                continue
            try:
                async with aiofiles.open(path, "r", encoding="utf-8") as f:
                    payload = json.loads(await f.read())
                if not isinstance(payload, dict):
                    raise ValueError("el JSON no es un objeto")
                run_id = str(payload.get("run_id") or path.stem)
                cfg = payload.get("config")
                cfg = cfg if isinstance(cfg, dict) else {}
                await session.merge(BenchmarkRunRow(
                    id=run_id,
                    started_at=payload.get("started_at"),
                    finished_at=payload.get("finished_at"),
                    model_name=cfg.get("model_name"),
                    endpoint=cfg.get("endpoint"),
                    sets=_dumps(_as_list(cfg.get("sets"))),
                    config=_dumps(cfg),
                    status=payload.get("status"),
                ))
                for index, item in enumerate(_as_list(payload.get("results"))):
                    if not isinstance(item, dict):
                        continue
                    metrics = item.get("metrics")
                    metrics = metrics if isinstance(metrics, dict) else {}
                    result_id = str(item.get("result_id") or make_result_id(run_id, index))
                    await session.merge(BenchmarkResultRow(
                        id=result_id, run_id=run_id,
                        prompt_id=item.get("prompt_id"), set_id=item.get("set_id"),
                        prompt_title=item.get("prompt_title"), prompt_text=item.get("prompt_text"),
                        response=item.get("response"), thinking=item.get("thinking"),
                        tps=_as_float(metrics.get("tps")), ttft_ms=_as_float(metrics.get("ttft_ms")),
                        tokens_generated=_as_int(metrics.get("tokens_generated")),
                        tokens_thinking=_as_int(metrics.get("tokens_thinking")),
                        duration_s=_as_float(metrics.get("duration_s")),
                        keywords_found=_dumps(_as_list(item.get("keywords_found"))),
                        keywords_missing=_dumps(_as_list(item.get("keywords_missing"))),
                        score_keywords=_as_float(item.get("score_auto")),
                        score_judge=_as_float(item.get("score_judge")),
                        judge_reasoning=item.get("judge_reasoning"),
                        judge_model=item.get("judge_model"),
                        judged_at=item.get("judged_at"),
                        score_manual=_as_float(item.get("score_manual")),
                        error=item.get("error"),
                    ))
                summary = payload.get("summary")
                if isinstance(summary, dict):
                    await session.merge(BenchmarkSummaryRow(
                        run_id=run_id,
                        total_prompts=_as_int(summary.get("total_prompts")),
                        completed=_as_int(summary.get("completed")),
                        errors=_as_int(summary.get("errors")),
                        avg_tps=_as_float(summary.get("avg_tps")),
                        avg_ttft_ms=_as_float(summary.get("avg_ttft_ms")),
                        avg_tokens=_as_float(summary.get("avg_tokens")),
                        total_duration_s=_as_float(summary.get("total_duration_s")),
                        keyword_hit_rate=_as_float(summary.get("keyword_hit_rate")),
                        avg_judge_score=_as_float(summary.get("avg_judge_score")),
                    ))
                await session.merge(ImportedRunRow(id=path.stem))
                await session.commit()
                imported += 1
            except Exception as exc:
                await session.rollback()
                logger.debug("Run histórico ignorado (%s): %s", path.name, exc)
                continue
    if imported:
        logger.info("Runs históricos importados a la DB: %d", imported)


async def db_all_attachment_ids() -> set[str]:
    """
    Ids de adjuntos referenciados por alguna conversación guardada.

    Lo usa la limpieza de huérfanos al arrancar: cualquier archivo en
    data/attachments que no esté acá es basura de un borrador que nunca se
    envió o de una conversación ya borrada.

    Se leen solo los mensajes, no las filas completas, y se parsea con json
    en vez de cargar el árbol: acá solo interesan los ids.
    """
    ids: set[str] = set()

    async with get_session() as session:
        result = await session.execute(select(ChatConversationRow.messages))
        for (raw,) in result.all():
            if not raw:
                continue
            try:
                nodes = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(nodes, list):
                continue
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                for attachment in node.get("attachments") or []:
                    if isinstance(attachment, dict) and attachment.get("id"):
                        ids.add(str(attachment["id"]))

    return ids
