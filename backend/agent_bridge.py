"""
agent_bridge.py — AG-1: integración OpenCode (fase 1).

Mientras un server llega a `running`, publica su bloque de provider en el
config de OpenCode (~/.config/opencode/); lo retira al detener el server, al
caer el proceso o al cerrar la app. OpenCode recarga el config en caliente
(documentado en V2: "Configuration files normally reload automatically") → no
hace falta reiniciar OpenCode.

Diseño y fuentes: Files/Docs/design/ag-1-opencode-bridge.md (2026-10-22,
verificado contra opencode.ai/v2/docs y contra el config real del usuario, que
está en formato V1 y OpenCode 2 lo lee igual). Puntos duros:
- Espejar el formato del archivo: estilo V1 ("provider"/npm/options/modalities)
  o V2 ("providers"/package/settings/capabilities). El bloque se escribe en el
  estilo que el archivo ya usa; un archivo nuevo se crea en V2 (canónico).
- Solo se tocan nuestras claves. En V1 el id de provider lleva el puerto
  ("glyvex-<puerto>"): el esquema V1 admite UNA baseURL por provider — por eso
  el config manual del usuario también tiene un provider por endpoint. La clave
  exacta "glyvex" en V1 queda intacta (podría ser un bloque manual del
  usuario). En V2 sí hay un único "glyvex" con settings por modelo.
- capabilities/modalities y limit SIEMPRE explícitos: para modelos custom no
  catalogados OpenCode asume tools + visión + 200k/32k por default; sin
  valores exactos publicaría visión en un modelo sin mmproj.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter

from config import config

logger = logging.getLogger("glyvex.agent_bridge")

router = APIRouter()

PROVIDER_ID = "glyvex"
PROVIDER_NAME = "Glyvex AI Suite"
# Paquete OpenAI-compatible de cada generación (verificados en la guía
# Providers de OpenCode V2 y en el config manual V1 del usuario).
NPM_V1 = "@ai-sdk/openai-compatible"
PACKAGE_V2 = "@opencode/ai/providers/openai-compatible"
# Fallbacks de limit cuando el server no reporta su n_ctx real: se mantiene el
# de la config de launch; si tampoco existe, un piso conservador.
FALLBACK_CONTEXT = 8192
OUTPUT_CAP = 8192
OUTPUT_FLOOR = 1024

# Registry en memoria de lo que ESTA instancia publica (process_id -> registro).
# Con él, cada cambio re-renderiza el bloque completo: publish/unpublish son
# idempotentes por construcción y multi-servidor es solo un registro más.
# El lock se crea por event loop: un asyncio.Lock queda atado al loop donde se
# usa por primera vez (en producción es uno solo; pytest-asyncio trae uno nuevo
# por test). _reset_for_tests los limpia.
_locks: list[tuple[object, asyncio.Lock]] = []
_registry: dict[str, dict[str, Any]] = {}
_backed_up = False
_last_error: str | None = None


def _current_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    for holder_loop, lock in _locks:
        if holder_loop is loop:
            return lock
    lock = asyncio.Lock()
    _locks.append((loop, lock))
    return lock


def enabled() -> bool:
    return bool(config.get("agent_bridge.enabled", False))


def opencode_config_path() -> Path:
    """Ruta destino: env OPENCODE_CONFIG > opencode.json > opencode.jsonc.
    Si ninguno existe, destino de creación: opencode.json (archivo nuevo, V2)."""
    env = os.environ.get("OPENCODE_CONFIG", "").strip()
    if env:
        return Path(env).expanduser()
    base = Path.home() / ".config" / "opencode"
    for name in ("opencode.json", "opencode.jsonc"):
        candidate = base / name
        if candidate.exists():
            return candidate
    return base / "opencode.json"


def _strip_jsonc(text: str) -> str:
    """Quitá comentarios // y /* */ y las comas finales de objetos/arreglos.
    Máquina de estados que respeta strings: el interior de un string nunca se
    toca. Solo se usa como fallback de lectura: la reescritura sale en JSON
    estricto (con backup .bak previo), igual que hace el propio migrador de
    OpenCode al pasar un .jsonc a strict JSON."""
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    escape = False
    while i < n:
        char = text[i]
        if in_string:
            out.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            i += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            i += 1
            continue
        if char == "/" and i + 1 < n:
            follow = text[i + 1]
            if follow == "/":
                end = text.find("\n", i + 2)
                i = n if end == -1 else end
                continue
            if follow == "*":
                end = text.find("*/", i + 2)
                if end == -1:
                    break  # comentario abierto: el parseo posterior va a fallar
                i = end + 2
                continue
        if char == ",":
            probe = i + 1
            while probe < n and text[probe] in " \t\r\n":
                probe += 1
            if probe < n and text[probe] in "}]":
                i += 1  # coma final: se descarta
                continue
        out.append(char)
        i += 1
    return "".join(out)


def _read_config(path: Path) -> tuple[dict[str, Any] | None, str | None, bool]:
    """(data, error, had_jsonc). Un archivo corrupto se reporta y NO se toca jamas.
    had_jsonc=True si hubo que quitar comentarios/comas finales para parsearlo: la
    reescritura sale en JSON estricto (con backup .bak previo), como hace el propio
    migrador de OpenCode al pasar un .jsonc a strict JSON."""
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}, None, False
    except OSError as exc:
        return None, f"no se pudo leer {path}: {exc}", False
    if not raw.strip():
        return {}, None, False
    had_jsonc = False
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError:
        try:
            data = json.loads(_strip_jsonc(raw))
            had_jsonc = True
        except json.JSONDecodeError as exc:
            return None, f"config de OpenCode no parseable: {exc}", False
    if not isinstance(data, dict):
        return None, "el config de OpenCode no es un objeto JSON", False
    return data, None, had_jsonc


def _style_of(data: dict[str, Any]) -> str:
    """Espeja el formato del archivo: V1 usa la clave 'provider', V2 'providers'
    (verificado en opencode.ai/v2/docs/providers). Archivo nuevo o vacio: V2, que
    es el formato canonico de la guia."""
    if "providers" in data:
        return "v2"
    if "provider" in data:
        return "v1"
    return "v2"


def _model_key(name: str, used: set[str]) -> str:
    """Clave del modelo = nombre del GGUF sin extension: la convencion que el
    usuario ya usa a mano en su config (el stem que models.py expone como
    model_name). Adentro de un provider se sanitizan caracteres invalidos y las
    colisiones se desambiguan con sufijo numerico."""
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "model"
    key = base
    n = 2
    while key in used:
        key = f"{base}-{n}"
        n += 1
    return key


def _base_url(rec: dict[str, Any]) -> str:
    return f"http://{rec['host']}:{rec['port']}/v1"


def _provider_id(rec: dict[str, Any], style: str) -> str:
    """V1: un provider por endpoint ('glyvex-<puerto>'): el esquema V1 admite una
    sola baseURL en provider.options — el config manual del usuario hace lo mismo
    (un provider por endpoint). La clave exacta 'glyvex' en V1 es del usuario si
    existe: el bridge no la escribe ni la borra. V2: un unico 'glyvex' con
    settings por modelo."""
    return PROVIDER_ID if style == "v2" else f"{PROVIDER_ID}-{rec['port']}"


async def _server_context(info) -> int | None:
    """n_ctx real del server via /props, consultado en caliente al publicar (el
    server esta healthy en ese momento). /props es exclusivo de llama.cpp; si el
    backend no lo expone (Ollama) o la consulta falla, fallback a la n_ctx de la
    config de launch. Nunca un valor inventado si el launch trajo uno valido."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"http://{info.host}:{info.port}/props")
        if resp.status_code == 200:
            props = resp.json()
            if isinstance(props, dict):
                gen = props.get("default_generation_settings")
                candidates = [props.get("n_ctx")]
                if isinstance(gen, dict):
                    candidates.append(gen.get("n_ctx"))
                for value in candidates:
                    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                        return value
    except (httpx.HTTPError, ValueError):
        pass
    cfg_ctx = info.launch_config.get("n_ctx")
    return cfg_ctx if isinstance(cfg_ctx, int) and cfg_ctx > 0 else None


def _entry_from(rec: dict[str, Any], style: str) -> dict[str, Any]:
    cfg = rec["launch_config"]
    api_key = cfg.get("api_key") or "local"  # placeholder: el bloque debe ser autocontenido
    ctx = rec["ctx"]
    # output = mitad del contexto, piso 1k, tope 8k: el rango que usan los bloques
    # manuales del usuario (131k→8k, 98k→4k) y deja aire para el resto del contexto.
    out = max(OUTPUT_FLOOR, min(OUTPUT_CAP, ctx // 2))
    vision = bool(cfg.get("mmproj_path"))  # vision solo si el launch uso mmproj
    inputs = ["text", "image"] if vision else ["text"]
    entry: dict[str, Any] = {"name": rec["model_name"]}
    if style == "v2":
        entry["modelID"] = rec["model_name"]
        entry["settings"] = {"baseURL": _base_url(rec), "apiKey": api_key}
        entry["capabilities"] = {"tools": True, "input": inputs, "output": ["text"]}
    else:
        entry["modalities"] = {"input": inputs, "output": ["text"]}
    entry["limit"] = {"context": ctx, "output": out}
    return entry


def _render_blocks(style: str) -> dict[str, dict[str, Any]]:
    """Re-renderiza todos los bloques propios desde el registry en memoria: publish/
    unpublish son idempotentes por construccion y multi-servidor es un registro mas."""
    blocks: dict[str, dict[str, Any]] = {}
    used: dict[str, set[str]] = {}
    for rec in _registry.values():
        pk = _provider_id(rec, style)
        entry = _entry_from(rec, style)
        if style == "v2":
            block = blocks.setdefault(pk, {
                "name": PROVIDER_NAME,
                "package": PACKAGE_V2,
                "models": {},
            })
            # settings del provider desde el primer endpoint: la instancia se
            # construye con esas opciones y cada modelo las reemplaza con sus
            # settings propias (documentado en OpenCode V2: se aplica el valor del
            # provider y despues el del modelo).
            block.setdefault("settings", dict(entry["settings"]))
        else:
            block = blocks.setdefault(pk, {
                "npm": NPM_V1,
                "name": PROVIDER_NAME,
                "options": {
                    "baseURL": _base_url(rec),
                    "apiKey": rec["launch_config"].get("api_key") or "local",
                },
                "models": {},
            })
        key = _model_key(rec["model_name"], used.setdefault(pk, set()))
        used[pk].add(key)
        block["models"][key] = entry
    return blocks


def _prune_our_keys(data: dict[str, Any]) -> None:
    """Borra todas las claves que escribe este bridge, incluso las del estilo
    contrario: si el usuario convirtio el archivo entre una publicacion y una
    retirada, la clave huerfana propia se limpia igual. Jamas toca la clave
    exacta 'glyvex' en V1 (podria ser un bloque manual del usuario) ni ninguna
    otra clave."""
    v1 = data.get("provider")
    if isinstance(v1, dict):
        for key in [k for k in v1 if re.fullmatch(r"glyvex-\d+", k)]:
            del v1[key]
    v2 = data.get("providers")
    if isinstance(v2, dict):
        v2.pop(PROVIDER_ID, None)


def _ensure_backup(path: Path) -> None:
    """Un .bak por sesion de app, antes de la primera escritura (diseno AG-1):
    con timestamp UTC, dentro de backup/ si esa carpeta ya existe (convencion del
    usuario) y si no, junto al archivo."""
    global _backed_up
    if _backed_up:
        return
    _backed_up = True
    if not path.exists():
        return
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = path.parent / "backup"
    if not backup_dir.is_dir():
        backup_dir = path.parent
    shutil.copy2(path, backup_dir / f"{path.name}.bak-ag1-{ts}")


def _apply_sync(
    path: Path,
    data: dict[str, Any],
    style: str,
    created_new: bool,
    had_jsonc: bool,
) -> bool:
    """Merge y escritura del config. Es sincronica (corre en un thread, patron D7):
    solo toca nuestras claves; la escritura es atomica (tmp + os.replace). Devuelve
    False si no se pudo escribir."""
    global _touched
    _prune_our_keys(data)
    blocks = _render_blocks(style)
    container_key = "providers" if style == "v2" else "provider"
    if blocks or container_key in data:
        container = data.setdefault(container_key, {})
        if not isinstance(container, dict):
            # El archivo tiene algo que no es objeto en esa clave: no lo tocamos.
            logger.warning("agent bridge: %s no es un objeto en %s", container_key, path)
            return False
        container.update(blocks)
    if created_new:
        data.setdefault("$schema", "https://opencode.ai/config.json")
    _ensure_backup(path)
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_name(path.name + ".ag1.tmp")
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("agent bridge: no se pudo escribir %s: %s", path, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    _touched = True
    if had_jsonc:
        logger.warning(
            "agent bridge: el config de OpenCode tenia comentarios JSONC; se reesbrio "
            "como JSON estricto (el original queda preservado en el backup .bak)"
        )
    return True


async def publish(info) -> bool:
    """Publica (o actualiza) el bloque de un server que llego a 'running'.
    Nunca lanza: los problemas de lectura/escritura se registran en _last_error
    para la UI y devuelven False — un fallo del bridge jamas puede romper un
    launch. Con el toggle OFF no toca el archivo."""
    global _last_error
    if not enabled():
        return False
    path = opencode_config_path()
    async with _current_lock():
        data, err, had_jsonc = _read_config(path)
        if err:
            _last_error = err
            logger.warning("agent bridge: %s", err)
            return False
        ctx = await _server_context(info) or FALLBACK_CONTEXT
        _registry[info.process_id] = {
            "model_name": info.model_name,
            "host": info.host,
            "port": info.port,
            "launch_config": dict(info.launch_config),
            "ctx": ctx,
        }
        created_new = not path.exists() or not data
        ok = await asyncio.to_thread(
            _apply_sync, path, data, _style_of(data), created_new, had_jsonc
        )
        if not ok:
            _last_error = f"no se pudo escribir {path}"
        elif _touched:
            _last_error = None
        return ok


async def unpublish(process_id: str) -> bool:
    """Retira la entrada de un server (stop, caida o shutdown). Si queda un
    provider sin modelos, la clave del provider desaparece junto con el resto de
    nuestro prefijo. A proposito NO chequea el toggle: un server que para con el
    toggle apagado tambien debe limpiar lo que publicaba. No-op si no publicaba."""
    global _last_error
    async with _current_lock():
        if process_id not in _registry:
            return False
        _registry.pop(process_id)
        path = opencode_config_path()
        data, err, had_jsonc = _read_config(path)
        if err:
            _last_error = err
            logger.warning("agent bridge: %s", err)
            return False
        if not data:
            # Archivo vacio o desaparecido: no hay nada que limpiar.
            return True
        return await asyncio.to_thread(
            _apply_sync, path, data, _style_of(data), False, had_jsonc
        )


async def cleanup_all() -> None:
    """Shutdown de la app / toggle OFF: saca todo lo que ESTA instancia publico.
    Si la instancia nunca toco el archivo, no lo toca tampoco ahora: un provider
    'glyvex' armado a mano por el usuario no se ve afectado."""
    async with _current_lock():
        if not _registry and not _touched:
            return
        _registry.clear()
        path = opencode_config_path()
        data, err, had_jsonc = _read_config(path)
        if err or not data:
            return
        await asyncio.to_thread(_apply_sync, path, data, _style_of(data), False, had_jsonc)


def bridge_status() -> dict[str, Any]:
    """Diagnostico para la UI y validacion E2E: estado del toggle, ruta destino,
    claves nuestras presentes en el archivo y registros publicados en memoria."""
    path = opencode_config_path()
    data, err, _ = _read_config(path)
    our_keys: list[str] = []
    style: str | None = None
    if isinstance(data, dict):
        v1 = data.get("provider")
        if isinstance(v1, dict):
            our_keys += [k for k in v1 if re.fullmatch(r"glyvex-\d+", k)]
        v2 = data.get("providers")
        if isinstance(v2, dict) and PROVIDER_ID in v2:
            our_keys.append(PROVIDER_ID)
        if data:
            style = _style_of(data)
    published = [
        {
            "process_id": pid,
            "model_name": rec["model_name"],
            "host": rec["host"],
            "port": rec["port"],
            "vision": bool(rec["launch_config"].get("mmproj_path")),
        }
        for pid, rec in _registry.items()
    ]
    return {
        "enabled": enabled(),
        "config_path": str(path),
        "file_exists": path.exists(),
        "style": style,
        "bridge_keys": sorted(our_keys),
        "published": published,
        "last_error": _last_error if err is None else err,
    }


@router.get("/status")
async def bridge_status_endpoint() -> dict[str, Any]:
    return await asyncio.to_thread(bridge_status)


@router.post("/launch")
async def launch_opencode() -> dict[str, Any]:
    """Abre la TUI de OpenCode desde la app (atajo del Launcher: modelo
    publicado → abrir OpenCode y charlar). Lanza el CLI solo si está en el
    PATH; en Windows lo hace en una consola propia para que la ventana no
    quede atada al proceso del backend."""
    binary = shutil.which("opencode")
    if not binary:
        return {"launched": False, "error": "opencode_cli_not_found"}
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    try:
        proc = await asyncio.to_thread(lambda: subprocess.Popen([binary], **kwargs))
    except OSError as exc:
        logger.warning("no se pudo lanzar OpenCode: %s", exc)
        return {"launched": False, "error": "launch_failed"}
    return {"launched": True, "pid": proc.pid}


def _reset_for_tests() -> None:
    """Singleton con estado en memoria: los tests lo reinician para no arrastrar
    registry/backup/errores de un test al siguiente (mismo criterio que
    _isolated_state en conftest)."""
    global _backed_up, _touched, _last_error
    _registry.clear()
    _locks.clear()
    _backed_up = False
    _touched = False
    _last_error = None
