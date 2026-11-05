"""
mcp_server.py — AG-2: la Suite como servidor MCP para OpenCode (fase 1).

Servidor Streamable HTTP servido por el propio backend FastAPI (montado en
/mcp — ver main.py), sin proceso stdio aparte. Herramientas: suite_status,
list_models, estimate_vram (U1) y launch_model/stop_model con safety rails
(U2). Diseño completo: Files/Docs/design/ag-2-mcp-server.md.

Convenciones:
- Las tools son wrappers DELGADOS sobre la misma lógica interna de los
  endpoints REST (inventory, launcher.manager, collector de GPU): sin HTTP
  loopback, comparten validaciones y errores con /api.
- Errores legibles por el modelo: ToolError. En el hilo JSON-RPC el SDK la
  convierte en un CallToolResult con isError:true y el mensaje como texto.
- Gate: con config mcp_server.enabled off, mcp_asgi_app responde 404 antes de
  tocar el servidor (main.py además no arranca el session_manager). Ojo:
  session_manager.run() es una sola vez por instancia — en tests hay un único
  test que abre el hilo completo (test_mcp_server.py); el resto usa
  mcp.call_tool() in-process, que no toca el session_manager.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

import launcher
import metrics
import models
from config import config

mcp = MCPServer(
    "Glyvex-AI-Suite",
    instructions=(
        "Gestiona modelos LLM locales con Glyvex-AI-Suite: list_models muestra "
        "el inventario, estimate_vram estima el costo de un lanzamiento, "
        "suite_status informa qué está corriendo, la VRAM disponible y las "
        "plantillas de hardware disponibles, launch_model lanza un modelo con "
        "una plantilla obligatoria (parámetros finos solo dentro de los límites "
        "de la plantilla) y stop_model lo detiene. Tras un launch el modelo "
        "carga en segundo plano: consultar suite_status hasta ver el proceso "
        "en estado 'running'."
    ),
)

# Campos de ModelEntry útiles para elegir un modelo desde un agente. A propósito
# no viajan los internos de gestión (mtime, metadata cacheada, última vez visto):
# list_models(include_metadata=true) trae la metadata GGUF cuando hace falta.
_MODEL_FIELDS = (
    "id",
    "name",
    "filename",
    "path",
    "format",
    "size_gb",
    "quantization",
    "family",
    "parameters",
    "compatible_backends",
    "has_mmproj",
    "mmproj_path",
    "mtp_embedded",
    "tags",
)


async def _list_models(include_metadata: bool) -> dict[str, Any]:
    await models.inventory.ensure_loaded()
    items = []
    for entry in models.inventory.list():
        item: dict[str, Any] = {f: getattr(entry, f) for f in _MODEL_FIELDS}
        if include_metadata:
            # Mismo criterio que GET /models/{id}/metadata: si el scan no llegó
            # a extraer la metadata (entry.metadata None), se lee del archivo.
            item["metadata"] = entry.metadata
            if item["metadata"] is None and entry.format == "gguf":
                item["metadata"] = await models.get_entry_metadata(entry)
        items.append(item)
    return {"count": len(items), "models": items}


async def _estimate_vram(
    model_id: str,
    n_ctx: int,
    cache_type_k: str,
    cache_type_v: str,
    n_parallel: int,
    flash_attn: bool,
) -> dict[str, Any]:
    try:
        # Llamada directa al handler del endpoint REST (comparte validaciones;
        # el único raise es el 404 de modelo inexistente).
        result = await models.get_model_vram_estimate(
            model_id=model_id,
            n_ctx=n_ctx,
            cache_type_k=cache_type_k,
            cache_type_v=cache_type_v,
            n_parallel=n_parallel,
            flash_attn=flash_attn,
        )
    except HTTPException as exc:
        raise ToolError(f"modelo {model_id}: {exc.detail}") from exc
    if result.get("available"):
        # Honestidad obligatoria (mismo criterio que la LoadCard): el agente debe
        # saber qué NO modela esta estimación antes de decidir un lanzamiento.
        result["notes"] = [
            "No se modela el offload a CPU de expertos MoE: en modelos MoE "
            "dispersos el uso real de GPU puede ser menor a esta estimación.",
            "El draft MTP está excluido del cálculo: con MTP activo el uso real "
            "es ~1-2 GB mayor (ver mtp_available).",
        ]
    return result


async def _suite_status() -> dict[str, Any]:
    active = [p.model_dump() for p in launcher.manager.status_all() if p.state != "stopped"]
    gpu, gpu_error = metrics.manager.collector.collect_gpu()
    # U2: los nombres de plantillas viajan aquí para que el agente pueda usar
    # launch_model sin tener que adivinarlos (la plantilla es obligatoria).
    await launcher.templates.ensure_loaded()
    templates_list = [t.name for t in await launcher.templates.list()]
    running = [p for p in active if p["state"] == "running"]
    return {
        "active_processes": active,
        "gpu": [g.model_dump() for g in gpu] if gpu else None,
        "gpu_error": gpu_error,
        "launch_templates": templates_list,
        "summary": {
            "active_count": len(active),
            "running_models": [p["model_name"] for p in running],
        },
    }


@mcp.tool()
async def suite_status() -> dict[str, Any]:
    """Estado de la Suite: procesos activos que ella lanzó (modelo, estado,
    host/puerto), snapshot GPU con la VRAM por tarjeta y nombres de las
    plantillas de hardware para launch_model. Consultar antes de lanzar o
    detener un modelo."""
    return await _suite_status()


@mcp.tool()
async def list_models(include_metadata: bool = False) -> dict[str, Any]:
    """Inventario de modelos locales gestionados por la Suite: id, archivo,
    tamaño, cuantización, familia y backends compatibles. El id devuelto es el
    que usan estimate_vram y las tools de lanzamiento. include_metadata=true
    agrega la metadata del header GGUF (capas, cabezales, MoE) por modelo."""
    return await _list_models(include_metadata)


@mcp.tool()
async def estimate_vram(
    model_id: str,
    n_ctx: int = 65536,
    cache_type_k: str = "f16",
    cache_type_v: str = "f16",
    n_parallel: int = 1,
    flash_attn: bool = False,
) -> dict[str, Any]:
    """VRAM estimada (pesos + auxiliares mmproj/draft + KV + cómputo) para
    lanzar model_id con esos parámetros, clasificada contra la VRAM de la GPU
    configurada (state: comodo/justo/no_cabe). Incluye una leyenda de totales
    por contexto común para elegir n_ctx y las limitaciones conocidas del
    modelo de estimación. NO lanza nada: es solo una estimación."""
    return await _estimate_vram(
        model_id=model_id,
        n_ctx=n_ctx,
        cache_type_k=cache_type_k,
        cache_type_v=cache_type_v,
        n_parallel=n_parallel,
        flash_attn=flash_attn,
    )


# ---------------------------------------------------------------------------
# U2 — escritura con safety rails (diseño §3.3, GO de Fabian 2026-11-04).
# El punto de MCP sobre el uso por UI es que el propio backend rechace
# combinaciones peligrosas con mensajes legibles: launch_model NO expone
# LaunchConfig entero — parte de una plantilla obligatoria y solo deja mover
# 4 parámetros dentro de límites propios (LaunchConfig no acota n_ctx; el
# límite lo pone aquí, relativo a la plantilla).
# ---------------------------------------------------------------------------

_LAUNCH_OVERRIDE_KEYS = {"n_ctx", "n_gpu_layers", "mtp", "mmproj"}

# Lo que launch_model devuelve del config aplicado: suficiente para que el
# agente sepa qué quedó realmente activo sin exponer LaunchConfig completo.
_LAUNCH_REPORT_KEYS = (
    "n_ctx",
    "n_gpu_layers",
    "n_parallel",
    "cache_type_k",
    "cache_type_v",
    "flash_attn",
    "gpu_mode",
    "mtp_embedded",
    "mtp_draft_model",
    "mmproj_path",
)

# Techo de n_ctx cuando la plantilla no declara contexto: el default de
# LaunchConfig (65536); el override permite subir hasta 2× ese valor.
_NCTX_BASE_FALLBACK = 65536


def _validation_messages(exc: ValidationError) -> str:
    return "; ".join(
        f"{p or 'config'}: {err['msg']}"
        for err in exc.errors()
        for p in [". ".join(str(x) for x in err["loc"])]
    )


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise ToolError(message)


async def _build_launch_config(
    model_id: str, template: str, overrides: dict[str, Any] | None
) -> tuple[launcher.LaunchConfig, models.ModelEntry]:
    """Rails 1–4: el modelo debe estar en el inventario, la plantilla es
    obligatoria, los overrides pasan por una whitelist acotada y la config
    resultante se revalida con los validators cruzados de LaunchConfig."""
    await models.inventory.ensure_loaded()
    entry = models.inventory.get(model_id)
    if entry is None:
        known = ", ".join(sorted(e.id for e in models.inventory.list()))
        raise ToolError(
            f"El modelo {model_id!r} no está en el inventario. "
            f"Ids disponibles: {known or 'ninguno'} (usa list_models)."
        )

    await launcher.templates.ensure_loaded()
    tpl = await launcher.templates.get(template)
    if tpl is None:
        names = ", ".join(t.name for t in await launcher.templates.list())
        raise ToolError(
            f"La plantilla {template!r} no existe. Plantillas disponibles: {names} "
            "(también visibles en suite_status)."
        )

    # Config base = params de la plantilla + model_id. Claves que solo usa el
    # frontend (p. ej. 'toggles') son inofensivas: pydantic ignora campos no
    # declarados, y los before-validators de LaunchConfig migran claves legacy.
    try:
        cfg = launcher.LaunchConfig(**{**tpl.params, "model_id": model_id})
    except ValidationError as exc:
        raise ToolError(
            f"La plantilla {template!r} produce una configuración inválida: "
            f"{_validation_messages(exc)}"
        ) from exc

    ov = overrides or {}
    unknown = sorted(set(ov) - _LAUNCH_OVERRIDE_KEYS)
    _require(
        not unknown,
        "Overrides no permitidos: " + ", ".join(unknown)
        + ". Solo se aceptan: " + ", ".join(sorted(_LAUNCH_OVERRIDE_KEYS)) + ".",
    )

    merged = cfg.model_dump()

    if "n_ctx" in ov:
        value = ov["n_ctx"]
        _require(
            isinstance(value, int) and not isinstance(value, bool),
            "n_ctx debe ser un entero de tokens.",
        )
        ceiling = 2 * (cfg.n_ctx or _NCTX_BASE_FALLBACK)
        _require(
            512 <= value <= ceiling,
            f"n_ctx={value} está fuera del límite de la plantilla {template!r}: "
            f"se permite entre 512 y {ceiling} (2× su contexto).",
        )
        merged["n_ctx"] = value

    if "n_gpu_layers" in ov:
        value = ov["n_gpu_layers"]
        base = cfg.n_gpu_layers  # -1 = todas las capas en GPU
        _require(
            isinstance(value, int) and not isinstance(value, bool),
            "n_gpu_layers debe ser un entero (-1 = todas las capas en GPU).",
        )
        # Nunca más capas en GPU que la plantilla: con -1 de base se permite
        # -1 o cualquier valor >=0 (menos offload); con base acotada solo 0..base.
        permitted = (value == -1 and base == -1) or (
            value >= 0 and (base == -1 or value <= base)
        )
        limite = f"0 a {base}" if base >= 0 else "cualquier valor >= 0 o -1"
        _require(
            permitted,
            f"n_gpu_layers={value} no permitido para la plantilla {template!r}: "
            f"valores válidos {limite}. La regla: nunca más capas en GPU que "
            "la plantilla.",
        )
        merged["n_gpu_layers"] = value

    if "mtp" in ov:
        value = ov["mtp"]
        _require(isinstance(value, bool), "mtp debe ser true o false.")
        companion = models.mtp_companion_file(entry)
        if value:
            _require(
                entry.mtp_embedded or companion is not None,
                f"El modelo {entry.name} no tiene MTP: ni tensores nextn embebidos "
                "ni un archivo draft .gguf en su carpeta.",
            )
            if entry.mtp_embedded:
                merged["mtp_embedded"] = True
            else:
                merged["mtp_draft_model"] = companion
        else:
            merged["mtp_embedded"] = False
            merged["mtp_draft_model"] = None

    if "mmproj" in ov:
        value = ov["mmproj"]
        _require(isinstance(value, bool), "mmproj debe ser true o false.")
        if value:
            _require(
                entry.has_mmproj and entry.mmproj_path is not None,
                f"El modelo {entry.name} no tiene un archivo mmproj de visión en "
                "su carpeta: no puede lanzarse con mmproj.",
            )
            merged["mmproj_path"] = entry.mmproj_path
        else:
            merged["mmproj_path"] = None

    # Rail 4: la config fusionada pasa por los validators cruzados del backend
    # (p. ej. gpu_mode <-> n_gpu_layers), los mismos que rechazan por /api.
    try:
        cfg = launcher.LaunchConfig(**merged)
    except ValidationError as exc:
        raise ToolError(
            f"La configuración final es inválida: {_validation_messages(exc)}"
        ) from exc
    return cfg, entry


async def _preflight_launch_vram(cfg: launcher.LaunchConfig, model_id: str) -> dict[str, Any] | None:
    """Rail 5: no lanzar algo que claramente no cabe. Devuelve None si no se
    puede juzgar (gpu_mode cpu_only, sin estimación disponible, o sin ningún
    dato de GPU); si hay snapshot vivo de GPU es la autoridad (mide lo libre
    REAL, otros modelos ocupan VRAM); si no, queda el fallback de la
    clasificación contra hardware.vram_gb. Con hardware.vram_gb sin configurar
    el estado queda 'unknown' y el launch no se toca."""
    if cfg.gpu_mode == "cpu_only":
        return None
    try:
        est = await models.get_model_vram_estimate(
            model_id=model_id,
            n_ctx=cfg.n_ctx or _NCTX_BASE_FALLBACK,
            cache_type_k=cfg.cache_type_k or "f16",
            cache_type_v=cfg.cache_type_v or "f16",
            n_parallel=cfg.n_parallel or 1,
            flash_attn=bool(cfg.flash_attn),
        )
    except HTTPException as exc:
        raise ToolError(f"modelo {model_id}: {exc.detail}") from exc
    if not est.get("available"):
        return None
    out: dict[str, Any] = {"total_gb": est["total_gb"], "state": est["state"], "blocked": False}
    gpus, _ = metrics.manager.collector.collect_gpu()
    if gpus:
        free_mb = sum(g.vram_free_mb for g in gpus)
        needed_mb = est["total_gb"] * 1024
        out.update(
            {"basis": "live_gpu_snapshot", "vram_free_mb": round(free_mb, 1), "needed_mb": round(needed_mb, 1)}
        )
        out["blocked"] = needed_mb > free_mb
    elif est["state"] == "no_cabe":
        out["basis"] = "config_vram_gb"
        out["blocked"] = True
    return out


@mcp.tool()
async def launch_model(
    model_id: str,
    template: str,
    overrides: dict[str, Any] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Lanza model_id en la Suite usando una plantilla de hardware OBLIGATORIA
    (los nombres están en suite_status). Los overrides admiten solo 4 claves
    acotadas: n_ctx (entre 512 y 2× el contexto de la plantilla), n_gpu_layers
    (nunca más GPU que la plantilla), mtp y mmproj (true/false, solo si el
    modelo los tiene). Un prefight de VRAM rechaza el lanzamiento si la
    estimación no cabe en la GPU real o configurada; force=true lo permite
    igual y reporta el prefight. NO espera a que termine la carga: consulta
    suite_status hasta ver el process_id en estado 'running'."""
    cfg, entry = await _build_launch_config(model_id, template, overrides)
    preflight = await _preflight_launch_vram(cfg, model_id)
    if preflight and preflight["blocked"] and not force:
        if preflight["basis"] == "live_gpu_snapshot":
            raise ToolError(
                f"Prefight de VRAM: el lanzamiento necesita ~{preflight['needed_mb']:.0f} MB "
                f"y las GPUs tienen ~{preflight['vram_free_mb']:.0f} MB libres. Baja n_ctx, "
                "usa una plantilla más conservadora o pasa force=true para lanzar igual."
            )
        raise ToolError(
            f"Prefight de VRAM: la estimación de {preflight['total_gb']} GB no cabe en la "
            "GPU configurada (state no_cabe). Baja n_ctx, usa otra plantilla o pasa "
            "force=true para lanzar igual."
        )

    try:
        info = await launcher.manager.start(cfg)
    except HTTPException as exc:
        raise ToolError(f"No se pudo lanzar {entry.name}: {exc.detail}") from exc

    applied = cfg.model_dump()
    result: dict[str, Any] = {
        "process_id": info.process_id,
        "state": info.state,
        "model_name": info.model_name,
        "backend": info.backend,
        "host": info.host,
        "port": info.port,
        "template": template,
        "applied_config": {k: applied[k] for k in _LAUNCH_REPORT_KEYS},
        "vram_preflight": preflight,
    }
    if info.state != "running":
        # La carga de un modelo tarda minutos: bloquear aquí cortaría el hilo
        # del agente; la polling de suite_status es el contrato acordado.
        result["next_step"] = (
            "El modelo carga en segundo plano: consulta suite_status hasta ver este "
            "process_id en estado 'running' antes de usarlo."
        )
    return result


@mcp.tool()
async def stop_model(process_id: str) -> dict[str, Any]:
    """Detiene un proceso lanzado por la Suite (el id lo devuelven launch_model
    o suite_status). Un id desconocido devuelve error con la lista de ids
    activos. Para un proceso lm_studio que la Suite solo consume, 'detener' lo
    retira de la lista sin tocar el server externo."""
    try:
        info = await launcher.manager.stop(process_id)
    except HTTPException as exc:
        active = [p.process_id for p in launcher.manager.status_all() if p.state != "stopped"]
        raise ToolError(
            f"Proceso {process_id!r}: {exc.detail}. Ids activos: "
            f"{', '.join(active) if active else 'ninguno'} (mira suite_status)."
        ) from exc
    return {
        "process_id": info.process_id,
        "state": info.state,
        "model_name": info.model_name,
        "backend": info.backend,
    }


# Montaje: el path "/" hace que el mount prefix (/mcp) sea la ruta pública
# completa. stateless + json_response: un POST autónomo por mensaje, sin stream
# SSE persistente (patrón documentado para montar bajo otro ASGI).
mcp_app = mcp.streamable_http_app(
    streamable_http_path="/",
    stateless_http=True,
    json_response=True,
)


async def mcp_asgi_app(scope: Any, receive: Any, send: Any) -> None:
    """Gate del montaje: con mcp_server.enabled off la sub-app no atiende
    (404 legible) y no se toca el session_manager, que puede ni estar corriendo."""
    if not config.get("mcp_server.enabled"):
        response = JSONResponse(
            {"detail": "Servidor MCP deshabilitado (config: mcp_server.enabled = false)"},
            status_code=404,
        )
        await response(scope, receive, send)
        return
    await mcp_app(scope, receive, send)
