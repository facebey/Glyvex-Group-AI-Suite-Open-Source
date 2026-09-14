"""Tests de models.py — scanner, extracción de metadata, mmproj, inventario incremental."""

from pathlib import Path

from helpers import scan_and_wait


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
