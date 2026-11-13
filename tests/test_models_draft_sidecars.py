"""Tests de LL-4 U2 — sidecars de draft generalizados (models.py).

Cobertura:
- agrupación por nombre: dflash-*.gguf -> draft_models (mtp/mmproj intactos);
- draft_companion_file por familia del modo (mtp/dflash) y None para los
  modos sin familia auto-detectable (simple/eagle3/ngram);
- campos de vocabulario en la metadata (tokenizer_model / added_tokens_count),
  por el parser rápido y por el fallback GGUFReader;
- preflight de vocabulario base↔draft: aviso heurístico que NUNCA bloquea,
  path manual fuera del inventario, y lectura vía cache de metadata.
"""

from pathlib import Path

import models as models_module

from helpers import scan_and_wait, write_gguf

# base + sidecars en UNA misma carpeta (nombres con los tokens mezclados,
# como los GGUF reales: el token va en medio del nombre).
BASE_NAME = "Qwen3-8B-Q4_K_M.gguf"
MTP_NAME = "qwen3-8b-mtp-tensor.gguf"
DFLASH_NAME = "Qwen3-8B-dflash-q8_0.gguf"
MMPROJ_NAME = "vision-encoder-mmproj.gguf"


def _mixed_dir(tmp_path) -> Path:
    d = tmp_path / "models"
    d.mkdir()
    for name in (BASE_NAME, MTP_NAME, DFLASH_NAME, MMPROJ_NAME):
        (d / name).touch()
    return d


def _entry_for(client_json, filename):
    model_id = next(m["id"] for m in client_json if m["filename"] == filename)
    return models_module.inventory.get(model_id)


def _write_vocab_gguf(
    path,
    arch: str = "llama",
    tokenizer_model: str = "llama3",
    n_tokens: int = 128,
    n_added: int = 4,
):
    """GGUF real con tokenizer propio: tokenizer.ggml.model (string) +
    arrays de strings tokens/added_tokens, para probar el conteo sin
    materializar el vocabulario."""
    from gguf import GGUFWriter

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    writer = GGUFWriter(str(path), arch)
    writer.open_output_file()
    writer.add_string("tokenizer.ggml.model", tokenizer_model)
    writer.add_array("tokenizer.ggml.tokens", [f"tok{i}" for i in range(n_tokens)])
    writer.add_array("tokenizer.ggml.added_tokens", [f"<|added{i}|>" for i in range(n_added)])
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.close()
    return path


# ---------------------------------------------------------------------------
# Clasificación y pairing por carpeta
# ---------------------------------------------------------------------------


async def test_groups_classify_dflash_in_draft_models(client, tmp_path):
    # La clasificación existente queda intacta: dflash NO puede robarle
    # archivos a mtp_models ni a mmproj_models.
    d = _mixed_dir(tmp_path)
    await scan_and_wait(client, model_dirs=[str(d)])

    groups = (await client.get("/api/models/groups")).json()
    assert len(groups) == 1
    g = groups[0]
    assert [m["filename"] for m in g["base_models"]] == [BASE_NAME]
    assert [m["filename"] for m in g["mtp_models"]] == [MTP_NAME]
    assert [m["filename"] for m in g["draft_models"]] == [DFLASH_NAME]
    assert [m["filename"] for m in g["mmproj_models"]] == [MMPROJ_NAME]


async def test_draft_companion_file_selects_by_family(client, tmp_path):
    # Cada modo recibe el sidecar de SU familia (dflash-mode → dflash file,
    # mtp-mode → mtp file); los modos sin archivo/familia devuelven None.
    d = _mixed_dir(tmp_path)
    await scan_and_wait(client, model_dirs=[str(d)])
    listed = (await client.get("/api/models")).json()
    base = _entry_for(listed, BASE_NAME)

    assert (models_module.draft_companion_file(base, "draft-dflash") or "").endswith(DFLASH_NAME)
    assert (models_module.draft_companion_file(base, "draft-mtp") or "").endswith(MTP_NAME)
    for modo in ("draft-simple", "draft-eagle3", "ngram-simple", "ngram-mod", "none"):
        assert models_module.draft_companion_file(base, modo) is None
    # El alias mtp sigue sirviendo (semáforo/MCP lo usan).
    assert (models_module.mtp_companion_file(base) or "").endswith(MTP_NAME)


async def test_draft_companion_requires_same_folder(client, tmp_path):
    # El dflash está en OTRA carpeta: no es companion de este modelo.
    d1 = tmp_path / "models"
    d1.mkdir()
    (d1 / BASE_NAME).touch()
    d2 = tmp_path / "drafts"
    d2.mkdir()
    (d2 / DFLASH_NAME).touch()
    await scan_and_wait(client, model_dirs=[str(d1), str(d2)])
    listed = (await client.get("/api/models")).json()
    base = _entry_for(listed, BASE_NAME)

    assert models_module.draft_companion_file(base, "draft-dflash") is None


# ---------------------------------------------------------------------------
# Campos de vocabulario en la metadata (preflight feeding)
# ---------------------------------------------------------------------------


async def test_metadata_vocab_fields_via_fast_parser(tmp_path):
    p = _write_vocab_gguf(tmp_path / "base.gguf")
    meta = await models_module.read_gguf_metadata_cached(p)
    assert meta["error"] is None
    assert meta["architecture"] == "llama"
    assert meta["tokenizer_model"] == "llama3"
    assert meta["added_tokens_count"] == 4  # conteo, sin materializar el array
    assert meta["vocab_size"] == 128


async def test_metadata_vocab_fields_via_reader_fallback(tmp_path, monkeypatch):
    # Mismos campos por el camino GGUFReader (si el parser rápido falla, la
    # app no puede perder el preflight).
    p = _write_vocab_gguf(tmp_path / "base.gguf")
    monkeypatch.setattr(models_module, "read_gguf_header_fast", lambda path: None)
    meta = models_module.read_gguf_metadata(p)
    assert meta["error"] is None
    assert meta["tokenizer_model"] == "llama3"
    assert meta["added_tokens_count"] == 4


# ---------------------------------------------------------------------------
# Preflight base↔draft (endpoint de aviso, nunca bloqueo)
# ---------------------------------------------------------------------------


async def _scan_base(client, tmp_path) -> str:
    d = tmp_path / "models"
    _write_vocab_gguf(d / BASE_NAME)
    await scan_and_wait(client, model_dirs=[str(d)])
    return next(m["id"] for m in (await client.get("/api/models")).json())


async def test_preflight_matching_vocab_ok(client, tmp_path):
    base_id = await _scan_base(client, tmp_path)
    draft = _write_vocab_gguf(tmp_path / "draft" / DFLASH_NAME)

    res = await client.get(
        f"/api/models/{base_id}/draft-preflight", params={"draft_path": str(draft)}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["available"] is True
    assert data["ok"] is True
    assert data["mismatches"] == []
    assert data["unverified"] == []  # los tres campos presentes en ambos lados


async def test_preflight_warns_on_vocab_mismatch(client, tmp_path):
    # Vocabularios distintos: aviso con los valores concretos, pero la app
    # NO bloquea el lanzamiento (el ground-truth es el log de llama-server).
    base_id = await _scan_base(client, tmp_path)
    draft = _write_vocab_gguf(
        tmp_path / "draft" / DFLASH_NAME,
        arch="qwen3",
        tokenizer_model="gpt2",
        n_added=9,
    )

    res = await client.get(
        f"/api/models/{base_id}/draft-preflight", params={"draft_path": str(draft)}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["available"] is True
    assert data["ok"] is False
    flagged = {m["field"]: m for m in data["mismatches"]}
    assert flagged["general.architecture"] == {"field": "general.architecture", "base": "llama", "draft": "qwen3"}
    assert flagged["tokenizer.ggml.model"]["draft"] == "gpt2"
    assert flagged["tokenizer.ggml.added_tokens (cantidad)"] == {
        "field": "tokenizer.ggml.added_tokens (cantidad)",
        "base": 4,
        "draft": 9,
    }
    assert data["unverified"] == []


async def test_preflight_draft_without_tokenizer_is_unverified_not_blocked(client, tmp_path):
    # Los GGUF draft suelen no traer tokenizer propio: ausencia NO es
    # disidencia → los campos se reportan como sin verificar y ok sigue True.
    base_id = await _scan_base(client, tmp_path)
    (tmp_path / "draft").mkdir(exist_ok=True)
    draft = write_gguf(tmp_path / "draft" / "plain-draft.gguf", "llama", None)

    res = await client.get(
        f"/api/models/{base_id}/draft-preflight", params={"draft_path": str(draft)}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["available"] is True
    assert data["ok"] is True
    assert data["mismatches"] == []
    # La architecture SÍ está en ambos lados (llama) y coincide: se compara.
    # El draft sin tokenizer deja sin verificar solo los dos campos de vocab.
    assert data["unverified"] == [
        "tokenizer.ggml.model",
        "tokenizer.ggml.added_tokens (cantidad)",
    ]


async def test_preflight_manual_path_outside_inventory(client, tmp_path):
    # El path manual de la UI siempre manda: el draft ni siquiera tiene que
    # estar en un directorio escaneado.
    base_id = await _scan_base(client, tmp_path)
    draft = _write_vocab_gguf(tmp_path / "descarga-manual" / "eagle3-por-hf.gguf")

    res = await client.get(
        f"/api/models/{base_id}/draft-preflight", params={"draft_path": str(draft)}
    )
    assert res.status_code == 200
    assert res.json()["ok"] is True


async def test_preflight_unreadable_draft_is_honest_error(client, tmp_path):
    base_id = await _scan_base(client, tmp_path)

    missing = tmp_path / "draft" / "gone.gguf"
    res = await client.get(
        f"/api/models/{base_id}/draft-preflight", params={"draft_path": str(missing)}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["available"] is False
    assert "draft" in data["error"]

    corrupt = tmp_path / "draft" / "not-gguf.gguf"
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_bytes(b"totalmente ilegible")
    res = await client.get(
        f"/api/models/{base_id}/draft-preflight", params={"draft_path": str(corrupt)}
    )
    assert res.status_code == 200
    assert res.json()["available"] is False


async def test_preflight_404_only_for_unknown_model(client, tmp_path):
    await _scan_base(client, tmp_path)
    res = await client.get(
        "/api/models/no-existe/draft-preflight", params={"draft_path": "cualquier.gguf"}
    )
    assert res.status_code == 404


async def test_preflight_serves_draft_metadata_from_cache(client, tmp_path, monkeypatch):
    # Segunda llamada al mismo par: el draft se lee del disco UNA sola vez
    # (read_gguf_metadata_cached).
    base_id = await _scan_base(client, tmp_path)
    draft = _write_vocab_gguf(tmp_path / "draft" / DFLASH_NAME)
    calls = []
    original = models_module.read_gguf_metadata

    def counting(path):
        calls.append(str(path))
        return original(path)

    monkeypatch.setattr(models_module, "read_gguf_metadata", counting)

    url = f"/api/models/{base_id}/draft-preflight"
    for _ in range(2):
        res = await client.get(url, params={"draft_path": str(draft)})
        assert res.status_code == 200
        assert res.json()["ok"] is True

    # Ni la base (metadata persistida por el scan calienta la cache) ni el
    # draft se releyeron: una sola lectura real para el draft.
    assert calls == [str(draft)]
