"""
helpers.py — utilidades compartidas entre módulos de test.

No es uno de los archivos explícitamente pedidos por el prompt, pero hace
falta para no repetir el mismo boilerplate (drenar streams SSE, polling con
timeout) en cada test_*.py. Vive en tests/ como módulo plano (sin
__init__.py en tests/), así que se importa con `from helpers import ...`
igual que se importan los módulos de backend/ vía sys.path.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path


async def drain_sse(response) -> list[str]:
    """Consume completamente un stream SSE (httpx streaming response) y
    devuelve la lista de líneas que empiezan con 'data:' (incluye el
    '[DONE]' final si el endpoint lo manda)."""
    events: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            events.append(line)
    return events


async def scan_and_wait(client) -> list[str]:
    """Dispara POST /api/models/scan y espera a que el stream SSE termine."""
    async with client.stream("POST", "/api/models/scan") as res:
        return await drain_sse(res)


async def wait_until(
    predicate: Callable[[], bool | Awaitable[bool]],
    timeout: float = 5.0,
    interval: float = 0.05,
) -> bool:
    """
    Poll `predicate` (sync o async) hasta que devuelva True o se cumpla el
    timeout. Usado para esperar transiciones de estado (starting -> running,
    running -> stopped, etc.) sin depender de sleeps fijos.
    """
    elapsed = 0.0
    while elapsed < timeout:
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return True
        await asyncio.sleep(interval)
        elapsed += interval
    return False


def write_gguf(path, arch: str = "llama", chat_template: str | None = None,
               kv: dict[str, int] | None = None):
    """Escribe un GGUF mínimo pero real (header legible por read_gguf_metadata).

    Los fixtures de sample_model_dir usan .touch() vacío (sirven para el
    scanner pero no para metadata: mmap de archivo vacío falla). Para tests
    de metadata hace falta un header de verdad con el chat_template a probar.

    `kv` agrega campos extra bajo el namespace de la arch con claves relativas
    (ej. {"attention.head_count_kv": 8} -> "<arch>.attention.head_count_kv").
    Números enteros (GGUFValueType.UINT32): suficiente para block_count,
    head_count(_kv), embedding_length, attention.head_size, ffn_expert_count…
    """
    from gguf import GGUFValueType, GGUFWriter

    path = Path(path)
    if path.exists():
        path.unlink()
    writer = GGUFWriter(str(path), arch)
    writer.open_output_file()
    if chat_template is not None:
        writer.add_chat_template(chat_template)
    for key, value in (kv or {}).items():
        writer.add_key_value(f"{arch}.{key}", value, GGUFValueType.UINT32)
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.close()
    return path
