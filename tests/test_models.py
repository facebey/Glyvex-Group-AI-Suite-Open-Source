"""Tests de models.py — scanner, extracción de metadata, mmproj, inventario incremental."""

import asyncio
import os
from pathlib import Path

import models as models_module

from helpers import scan_and_wait, write_gguf


async def test_scan_empty_dirs(client):
    res = await client.get("/api/models")
    assert res.status_code == 200
    assert res.json() == []


async def test_scan_finds_gguf(client, sample_model_dir):
    await client.post("/api/config", json={"model_dirs": [str(sample_model_dir)]})
    events = await scan_and_wait(client)
    assert any('"complete"' in e for e in events)

    listed = (await client.get("/api/models")).json()
    assert len(listed) == 3  # los 3 .gguf de sample_model_dir


async def test_parse_qwen3(client, sample_model_dir):
    await client.post("/api/config", json={"model_dirs": [str(sample_model_dir)]})
    await scan_and_wait(client)

    listed = (await client.get("/api/models")).json()
    qwen = next(m for m in listed if m["filename"] == "Qwen3.8-27B-UD-Q4_K_XL.gguf")
    assert qwen["family"] == "Qwen3"
    assert qwen["parameters"] == "27B"
    assert qwen["quantization"] == "UD-Q4_K_XL"


async def test_parse_llama(client, sample_model_dir):
    await client.post("/api/config", json={"model_dirs": [str(sample_model_dir)]})
    await scan_and_wait(client)

    listed = (await client.get("/api/models")).json()
    llama = next(m for m in listed if m["filename"] == "Llama-3.1-8B-Q4_K_M.gguf")
    assert llama["family"] == "Llama-3.1"
    assert llama["parameters"] == "8B"
    assert llama["quantization"] == "Q4_K_M"


async def test_parse_mistral(client, tmp_path):
    model_dir = tmp_path / "mistral_models"
    model_dir.mkdir()
    (model_dir / "Mistral-7B-v0.3.Q5_K_S.gguf").touch()

    await client.post("/api/config", json={"model_dirs": [str(model_dir)]})
    await scan_and_wait(client)

    listed = (await client.get("/api/models")).json()
    mistral = next(m for m in listed if m["filename"].startswith("Mistral"))
    assert mistral["family"] == "Mistral"
    assert mistral["parameters"] == "7B"


async def test_detects_mmproj(client, sample_model_dir):
    await client.post("/api/config", json={"model_dirs": [str(sample_model_dir)]})
    await scan_and_wait(client)

    listed = (await client.get("/api/models")).json()
    base = next(m for m in listed if m["filename"] == "Qwen3.8-27B-UD-Q4_K_XL.gguf")
    assert base["has_mmproj"] is True
    assert base["mmproj_path"] is not None
    assert "mmproj" in Path(base["mmproj_path"]).name.lower()


async def test_removes_deleted_file(client, sample_model_dir):
    await client.post("/api/config", json={"model_dirs": [str(sample_model_dir)]})
    await scan_and_wait(client)
    before = (await client.get("/api/models")).json()
    assert len(before) == 3

    (sample_model_dir / "Llama-3.1-8B-Q4_K_M.gguf").unlink()
    await scan_and_wait(client)

    after = (await client.get("/api/models")).json()
    assert len(after) == 2
    assert all(m["filename"] != "Llama-3.1-8B-Q4_K_M.gguf" for m in after)


async def test_incremental_no_duplicates(client, sample_model_dir):
    await client.post("/api/config", json={"model_dirs": [str(sample_model_dir)]})
    await scan_and_wait(client)
    first = (await client.get("/api/models")).json()

    await scan_and_wait(client)
    second = (await client.get("/api/models")).json()

    assert len(first) == len(second) == 3
    assert sorted(m["id"] for m in first) == sorted(m["id"] for m in second)


async def test_model_exists_true(client, sample_model_dir):
    await client.post("/api/config", json={"model_dirs": [str(sample_model_dir)]})
    await scan_and_wait(client)
    model_id = (await client.get("/api/models")).json()[0]["id"]

    res = await client.get(f"/api/models/{model_id}/exists")
    assert res.status_code == 200
    assert res.json()["exists"] is True


async def test_model_exists_false(client, sample_model_dir):
    await client.post("/api/config", json={"model_dirs": [str(sample_model_dir)]})
    await scan_and_wait(client)
    listed = (await client.get("/api/models")).json()
    target = next(m for m in listed if m["filename"] == "Llama-3.1-8B-Q4_K_M.gguf")

    # Se borra el archivo SIN volver a escanear: /exists chequea el disco en
    # vivo, así que debe reflejar la ausencia aunque el inventario cacheado
    # todavía no se haya actualizado.
    Path(target["path"]).unlink()

    res = await client.get(f"/api/models/{target['id']}/exists")
    assert res.json()["exists"] is False


async def test_compatible_backends_gguf(client, sample_model_dir):
    await client.post("/api/config", json={"model_dirs": [str(sample_model_dir)]})
    await scan_and_wait(client)

    listed = (await client.get("/api/models")).json()
    gguf_model = next(m for m in listed if m["format"] == "gguf")
    assert set(gguf_model["compatible_backends"]) == {"llama_server", "ollama", "lm_studio"}


# --------------------------------------------------------------------------
# Detección de thinking por template (dos capas):
# thinking_support ("think" en cualquier variante) vs enable_thinking_kwarg
# (el kwarg exacto que la app envía por --chat-template-kwargs).
# --------------------------------------------------------------------------


async def _metadata_for(client, tmp_path, filename: str, arch: str, template: str | None,
                        kv: dict[str, int] | None = None) -> dict:
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    write_gguf(model_dir / filename, arch, template, kv)
    await client.post("/api/config", json={"model_dirs": [str(model_dir)]})
    await scan_and_wait(client)
    model_id = (await client.get("/api/models")).json()[0]["id"]
    res = await client.get(f"/api/models/{model_id}/metadata")
    assert res.status_code == 200
    return res.json()


async def test_metadata_enable_thinking_kwarg_detected(client, tmp_path):
    data = await _metadata_for(
        client, tmp_path,
        "Thinker-8B-Q4_K_M.gguf", "qwen3",
        "{% if enable_thinking %}<|think|>{% endif %}",
    )
    assert data["thinking_support"] is True
    assert data["enable_thinking_kwarg"] is True
    assert data["suggested_preset"] == "thinking"


async def test_metadata_qwen3moe_without_think_not_flagged(client, tmp_path):
    # Falso positivo de la heurística vieja (arch "qwen3"): template sin
    # ningún think no es un modelo thinking, sin importar la arch.
    data = await _metadata_for(
        client, tmp_path,
        "Plain-8B-Q4_K_M.gguf", "qwen3moe",
        '{{ messages | join(" ") }}',
    )
    assert data["thinking_support"] is False
    assert data["enable_thinking_kwarg"] is False
    assert data["suggested_preset"] == "instruct"


async def test_metadata_think_only_template(client, tmp_path):
    # Template con `think` pero sin `enable_thinking` (p.ej. Gemma 3):
    # se reconoce como thinking, pero el kwarg que envía la app no lo usa.
    data = await _metadata_for(
        client, tmp_path,
        "ThinkOnly-8B-Q4_K_M.gguf", "gemma3",
        "{% if think %}<startthink>{% endif %}",
    )
    assert data["thinking_support"] is True
    assert data["enable_thinking_kwarg"] is False
    assert data["suggested_preset"] == "thinking"


# --------------------------------------------------------------------------
# Campos de metadata para el estimador de VRAM + endpoint vram-estimate
# --------------------------------------------------------------------------


async def _model_with_kv(client, tmp_path, filename: str, arch: str, kv: dict[str, int]) -> str:
    model_dir = tmp_path / "models"
    model_dir.mkdir(exist_ok=True)
    write_gguf(model_dir / filename, arch, None, kv)
    await client.post("/api/config", json={"model_dirs": [str(model_dir)]})
    await scan_and_wait(client)
    return (await client.get("/api/models")).json()[0]["id"]


async def test_metadata_vram_fields_moe(client, tmp_path):
    data = await _metadata_for(
        client, tmp_path,
        "Moe-30B-A3B-Q4_K_M.gguf", "qwen3moe",
        None,
        kv={
            "block_count": 48,
            "attention.head_count": 32,
            "attention.head_count_kv": 8,
            "attention.head_size": 128,
            "embedding_length": 4096,
            "ffn_expert_count": 128,
            "ffn_expert_shared_count": 2,
        },
    )
    assert data["n_layer"] == 48
    assert data["n_head"] == 32
    assert data["n_head_kv"] == 8
    assert data["head_dim"] == 128
    assert data["is_moe"] is True
    assert data["ffn_expert_count"] == 128
    assert data["ffn_expert_shared_count"] == 2


async def test_metadata_vram_fields_dense(client, tmp_path):
    data = await _metadata_for(
        client, tmp_path,
        "Dense-8B-Q4_K_M.gguf", "llama",
        None,
        kv={
            "block_count": 32,
            "attention.head_count": 32,
            "embedding_length": 4096,
        },
    )
    assert data["n_head_kv"] is None
    assert data["head_dim"] is None
    assert data["is_moe"] is False


async def test_vram_estimate_endpoint_unknown_without_vram_config(client, tmp_path):
    model_id = await _model_with_kv(
        client, tmp_path,
        "Estimado-8B-Q4_K_M.gguf", "llama",
        {"block_count": 32, "attention.head_count": 32, "attention.head_count_kv": 8,
         "attention.head_size": 128, "embedding_length": 4096},
    )
    res = await client.get(f"/api/models/{model_id}/vram-estimate",
                           params={"n_ctx": 8192, "cache_type_k": "q4_0", "cache_type_v": "q4_0"})
    assert res.status_code == 200
    data = res.json()
    assert data["available"] is True
    # KV: 2×32×8×128 = 65536 elementos/token ×0.5625 B = 36 KiB × 8192 = 0.28 GiB.
    assert data["kv_gb"] == 0.28
    assert data["state"] == "unknown"
    assert data["pct"] is None


async def test_vram_estimate_endpoint_with_vram_config(client, tmp_path):
    model_id = await _model_with_kv(
        client, tmp_path,
        "Estimado-8B-Q4_K_M.gguf", "llama",
        {"block_count": 32, "attention.head_count": 32, "attention.head_count_kv": 8,
         "attention.head_size": 128, "embedding_length": 4096},
    )
    await client.post("/api/config", json={"hardware": {"vram_gb": 24}})
    res = await client.get(f"/api/models/{model_id}/vram-estimate",
                           params={"n_ctx": 8192, "cache_type_k": "q4_0", "cache_type_v": "q4_0"})
    assert res.status_code == 200
    data = res.json()
    assert data["available"] is True
    assert data["gpu_vram_gb"] == 24
    assert data["state"] == "comodo"
    assert data["pct"] is not None and data["pct"] < 100
    assert data["total_gb"] == data["weights_gb"] + data["kv_gb"] + data["compute_gb"]


async def test_vram_estimate_endpoint_empty_gguf_unavailable(client, sample_model_dir):
    # GGUF de 0 bytes (fixture estándar): mmap falla  disponible false, sin 500.
    await client.post("/api/config", json={"model_dirs": [str(sample_model_dir)]})
    await scan_and_wait(client)
    model_id = (await client.get("/api/models")).json()[0]["id"]
    res = await client.get(f"/api/models/{model_id}/vram-estimate")
    assert res.status_code == 200
    data = res.json()
    assert data["available"] is False
    assert data["error"]


# ---------------------------------------------------------------------------
# Metadata persistida en el inventario (models.json) + cache en memoria
# ---------------------------------------------------------------------------
#
# El constructor de GGUFReader toma decenas de segundos en archivos con
# tokenizer grande (gemma-4: ~30-55 s). Estos tests verifican el mecanismo
# que evita volver a pagar ese costo: el scan extrae la metadata desde el
# reader ya abierto (detección MTP/visión), la persiste en la entrada, y los
# endpoints/launcher la sirven sin abrir el archivo.

KNOWN_KV = {
    "block_count": 32,
    "embedding_length": 1024,
    "attention.head_count": 8,
    "attention.head_count_kv": 4,
    "attention.head_size": 128,
}
KNOWN_NAME = "Llama-3.1-8B-Q4_K_M.gguf"


def _write_real_gguf(directory: Path, name: str = KNOWN_NAME) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    return write_gguf(directory / name, "llama", None, kv=KNOWN_KV)


def _counting_read(monkeypatch) -> list:
    """Cuenta las llamadas a read_gguf_metadata (lectura real del archivo)."""
    calls: list = []
    original = models_module.read_gguf_metadata

    def counting(path):
        calls.append(path)
        return original(path)

    monkeypatch.setattr(models_module, "read_gguf_metadata", counting)
    return calls


async def _simulate_restart():
    """
    Pierde todo el estado en memoria: inventario y cache de metadata.

    Escribe además la versión de esquema, como haría el primer arranque real
    (load() la escribe al detectar versión vieja): sin eso, el "restart"
    simularía un esquema anterior y load() descartaría el cache a propósito.
    """
    await models_module._write_cache_schema_version(models_module.MODELS_CACHE_SCHEMA_VERSION)
    models_module.inventory._by_id = {}
    models_module.inventory._loaded = False
    models_module._METADATA_CACHE.clear()
    models_module._metadata_locks.clear()
    if models_module._warmup_task is not None and not models_module._warmup_task.done():
        models_module._warmup_task.cancel()
    models_module._warmup_task = None


async def test_scan_populates_entry_metadata(client, tmp_path, monkeypatch):
    # El scan abre el reader para MTP/visión y aprovecha para extraer y
    # persistir la metadata completa en la entrada: sin re-lecturas.
    model_dir = tmp_path / "models"
    _write_real_gguf(model_dir)
    calls = _counting_read(monkeypatch)

    await client.post("/api/config", json={"model_dirs": [str(model_dir)]})
    await scan_and_wait(client)

    listed = (await client.get("/api/models")).json()
    meta = listed[0]["metadata"]
    assert meta is not None
    assert meta["n_layer"] == 32
    assert meta["n_embd"] == 1024
    assert meta["n_head_kv"] == 4
    assert meta["error"] is None
    assert calls == []  # reuso del open de _detect_embedded_modules


async def test_metadata_endpoint_served_from_entry(client, tmp_path, monkeypatch):
    # /metadata responde desde el inventario persistido: zero I/O al archivo.
    model_dir = tmp_path / "models"
    _write_real_gguf(model_dir)
    calls = _counting_read(monkeypatch)

    await client.post("/api/config", json={"model_dirs": [str(model_dir)]})
    await scan_and_wait(client)
    calls.clear()

    model_id = (await client.get("/api/models")).json()[0]["id"]
    res = await client.get(f"/api/models/{model_id}/metadata")
    assert res.status_code == 200
    data = res.json()
    assert data["n_layer"] == 32
    assert data["error"] is None
    assert calls == []


async def test_cold_start_metadata_from_persisted_inventory(client, tmp_path, monkeypatch):
    # Restart del backend: estado en memoria vacío, models.json en disco.
    # /metadata debe responder sin abrir el archivo (cold start cero).
    model_dir = tmp_path / "models"
    _write_real_gguf(model_dir)

    await client.post("/api/config", json={"model_dirs": [str(model_dir)]})
    await scan_and_wait(client)

    await _simulate_restart()
    calls = _counting_read(monkeypatch)

    listed = (await client.get("/api/models")).json()
    assert len(listed) == 1
    assert listed[0]["metadata"]["n_layer"] == 32  # viaja en el JSON

    res = await client.get(f"/api/models/{listed[0]['id']}/metadata")
    assert res.status_code == 200
    assert res.json()["n_layer"] == 32
    assert calls == []  # nunca se reabrió el archivo


async def test_metadata_reread_when_file_changed(client, tmp_path, monkeypatch):
    # El archivo cambió tras el scan (mtime distinto): la metadata persistida
    # se descarta y se relee, como en el scan incremental.
    model_dir = tmp_path / "models"
    p = _write_real_gguf(model_dir)
    calls = _counting_read(monkeypatch)

    await client.post("/api/config", json={"model_dirs": [str(model_dir)]})
    await scan_and_wait(client)
    calls.clear()

    st = p.stat()
    os.utime(p, (st.st_atime, st.st_mtime + 10))

    model_id = (await client.get("/api/models")).json()[0]["id"]
    res = await client.get(f"/api/models/{model_id}/metadata")
    assert res.status_code == 200
    assert res.json()["n_layer"] == 32
    assert len(calls) == 1  # relectura por mtime cambiado


async def test_cached_read_hits_after_first(tmp_path, monkeypatch):
    # Fallback (entrada sin metadata): la cache en memoria evita re-leer.
    p = tmp_path / "m.gguf"
    write_gguf(p, "llama", None, kv=KNOWN_KV)
    calls = _counting_read(monkeypatch)

    m1 = await models_module.read_gguf_metadata_cached(p)
    m2 = await models_module.read_gguf_metadata_cached(p)
    assert len(calls) == 1
    assert m1 == m2
    assert m1["n_layer"] == 32


async def test_cached_read_invalidates_on_mtime_change(tmp_path, monkeypatch):
    p = tmp_path / "m.gguf"
    write_gguf(p, "llama", None, kv=KNOWN_KV)
    calls = _counting_read(monkeypatch)

    await models_module.read_gguf_metadata_cached(p)
    st = p.stat()
    os.utime(p, (st.st_atime, st.st_mtime + 10))
    await models_module.read_gguf_metadata_cached(p)
    assert len(calls) == 2


async def test_warmup_refills_memory_cache_for_entries_without_metadata(client, tmp_path, monkeypatch):
    # Entrada sin metadata (esquema viejo) + scan incremental (no re-parsea):
    # el warmup disparado en "complete" llena la cache en memoria.
    model_dir = tmp_path / "models"
    _write_real_gguf(model_dir)

    await client.post("/api/config", json={"model_dirs": [str(model_dir)]})
    await scan_and_wait(client)

    await _simulate_restart()
    await models_module.inventory.ensure_loaded()
    entry = models_module.inventory.list()[0]
    entry.metadata = None  # simular entrada de esquema viejo
    models_module.inventory._by_id[entry.id] = entry

    calls = _counting_read(monkeypatch)
    await scan_and_wait(client)  # incremental: mismo mtime, no re-parsea
    task = models_module._warmup_task
    assert task is not None
    await asyncio.wait_for(task, 5)
    assert len(calls) == 1
    assert str(model_dir / KNOWN_NAME) in models_module._METADATA_CACHE


# ---------------------------------------------------------------------------
# Parser rápido del header GGUF (blob, sin librería) + kv_data persistida
# ---------------------------------------------------------------------------
#
# El parser rápido lee el header como un bloque de bytes y lo recorre con un
# cursor (sin seeks: en CPython 3.14 un seek relativo negativo tras reads por
# chunks desincroniza el buffer de BufferedReader). Materializa todos los KVs
# escalares, salta los arrays de strings enormes (tokens/merges) y persiste
# kv_data + tensor_count en la entrada durante el scan.


def test_fast_header_matches_reader(tmp_path):
    # Paridad fast vs librería sobre un archivo escrito por el propio writer.
    p = write_gguf(tmp_path / "m.gguf", "llama", "{{ messages | join(' ') }}", kv=KNOWN_KV)

    header = models_module.read_gguf_header_fast(p)
    assert header is not None
    assert header["kvs"]["general.architecture"] == "llama"
    assert header["kvs"]["llama.block_count"] == 32
    assert header["kvs"]["llama.attention.head_count_kv"] == 4
    assert header["kvs"]["tokenizer.chat_template"] == "{{ messages | join(' ') }}"
    assert header["tensor_count"] == 0
    assert header["tensor_names"] == []

    from gguf import GGUFReader

    meta_fast = models_module._metadata_from_kv(header)
    meta_reader = models_module._metadata_from_reader(GGUFReader(str(p), "r"))
    for key in models_module._METADATA_KEYS:
        assert meta_fast[key] == meta_reader[key], key


def test_fast_header_skips_large_tokenizer(tmp_path):
    # Vocabulario + merges grandes: se saltan sin materializar y
    # tokenizer.ggml.tokens queda como COUNT (tamaño de vocabulario).
    from gguf import GGUFWriter

    p = tmp_path / "big.gguf"
    writer = GGUFWriter(str(p), "llama")
    writer.open_output_file()
    writer.add_token_list(["tok%d" % i for i in range(20000)])
    writer.add_token_merges(["a%d b%d %d" % (i, i + 1, i) for i in range(20000)])
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.close()

    header = models_module.read_gguf_header_fast(p)
    assert header is not None
    assert header["kvs"]["tokenizer.ggml.tokens"] == 20000
    assert "tokenizer.ggml.merges" not in header["kvs"]
    assert models_module._metadata_from_kv(header)["vocab_size"] == 20000


def test_fast_header_materializes_small_scalar_array(tmp_path):
    # Arrays de escalar pequeños se materializan como lista y _as_int toma
    # el primer elemento (head_count_kv por capa: todos iguales).
    from gguf import GGUFValueType, GGUFWriter

    p = tmp_path / "arr.gguf"
    writer = GGUFWriter(str(p), "llama")
    writer.open_output_file()
    writer.add_key_value(
        "llama.attention.head_count_kv", [4, 4, 4],
        GGUFValueType.ARRAY, GGUFValueType.INT32,
    )
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.close()

    header = models_module.read_gguf_header_fast(p)
    assert header is not None
    assert header["kvs"]["llama.attention.head_count_kv"] == [4, 4, 4]
    assert models_module._as_int([4, 4, 4]) == 4
    assert models_module._metadata_from_kv(header)["n_head_kv"] == 4


def test_fast_header_corrupt_or_truncated_returns_none(tmp_path):
    # Corrupto (magic OK pero basura) y truncado: None → cae a la librería.
    p = write_gguf(tmp_path / "ok.gguf", "llama", None, kv=KNOWN_KV)
    data = p.read_bytes()

    bad = tmp_path / "bad.gguf"
    bad.write_bytes(b"GGUF" + b"\x03" * 100)
    assert models_module.read_gguf_header_fast(bad) is None

    cut = tmp_path / "cut.gguf"
    cut.write_bytes(data[: len(data) // 2])
    assert models_module.read_gguf_header_fast(cut) is None


def test_metadata_falls_back_to_reader_when_fast_returns_none(tmp_path, monkeypatch):
    p = write_gguf(tmp_path / "m.gguf", "llama", None, kv=KNOWN_KV)
    monkeypatch.setattr(models_module, "read_gguf_header_fast", lambda path: None)
    meta = models_module.read_gguf_metadata(p)
    assert meta["error"] is None
    assert meta["n_layer"] == 32
    assert meta["tensor_count"] == 0
    assert meta["kv_data"]["llama.block_count"] == 32


async def test_scan_persists_kv_data_and_tensor_count(client, tmp_path):
    # El scan persiste TODOS los KVs escalares + count de tensores en la
    # entrada: aprovecha que es poco frecuente para no releer headers después.
    model_dir = tmp_path / "models"
    _write_real_gguf(model_dir)

    await client.post("/api/config", json={"model_dirs": [str(model_dir)]})
    await scan_and_wait(client)

    listed = (await client.get("/api/models")).json()
    meta = listed[0]["metadata"]
    assert meta["tensor_count"] == 0
    assert meta["kv_data"]["llama.block_count"] == 32
    assert meta["kv_data"]["llama.attention.head_count"] == 8
    assert meta["kv_data"]["llama.attention.head_count_kv"] == 4
