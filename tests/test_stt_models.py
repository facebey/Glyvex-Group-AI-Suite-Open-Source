"""
test_stt_models.py — T2.2b.5: tests de los modelos STT (catálogo, estado,
download, delete) en stt_runtime.py.

Todo mockeado, sin red real: mismo patrón que test_stt_runtime.py — la red
se simula con httpx.MockTransport (sha256 real del body) y los modelos fake
se inyectan en STT_MODELS con tamaños diminutos para no escribir MB en tmp.
"""

from __future__ import annotations

import hashlib

import httpx
import pytest

import stt_runtime as stt


def _patch_transport(monkeypatch, handler):
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def _add_fake_model(monkeypatch, name: str = "fake", size: int = 5) -> None:
    body = b"12345"[:size]
    monkeypatch.setitem(
        stt.STT_MODELS,
        name,
        {
            "label": "Fake",
            "filename": "ggml-" + name + ".bin",
            "url": "http://fake/ggml-" + name + ".bin",
            "sha256": hashlib.sha256(body).hexdigest(),
            "size": size,
        },
    )


# --------------------------------------------------------------------------
# Catálogo
# --------------------------------------------------------------------------


def test_catalog_integrity():
    for name, spec in stt.STT_MODELS.items():
        assert spec["label"]
        assert spec["filename"].startswith("ggml-") and spec["filename"].endswith(".bin")
        assert spec["size"] > 0
        if spec.get("parts"):
            assert spec["url"] is None and spec["sha256"] is None
            assert sum(p["size"] for p in spec["parts"]) == spec["size"]
            for part in spec["parts"]:
                assert part["url"] and len(part["sha256"]) == 64
        else:
            assert spec["url"] and len(spec["sha256"]) == 64


def test_catalog_models_from_plan():
    # Aprobado con Fabian: la fuente verificable trae estos cuatro (sin
    # tiny ni large-v3-turbo; desviación de D6 documentada).
    assert set(stt.STT_MODELS) == {"base", "small", "medium", "large-v3"}
    assert stt.STT_DEFAULT_MODEL in stt.STT_MODELS


def test_model_spec_unknown_raises():
    with pytest.raises(ValueError, match="desconocido"):
        stt.stt_model_spec("tiny")


def test_model_path(monkeypatch, tmp_path):
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)
    assert stt.stt_model_path("base").name == "ggml-base.bin"
    assert stt.stt_model_path("base").parent == stt.stt_models_dir()


# --------------------------------------------------------------------------
# stt_model_status / stt_models_status
# --------------------------------------------------------------------------


def test_models_status_all_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)

    result = stt.stt_models_status()

    assert result["default"] == "base"
    assert result["source"] == stt.STT_MODELS_SOURCE_ID
    assert {m["name"] for m in result["models"]} == set(stt.STT_MODELS)
    assert all(m["state"] == "missing" for m in result["models"])


def test_model_status_ready_when_size_matches(monkeypatch, tmp_path):
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)
    _add_fake_model(monkeypatch)

    stt.stt_model_path("fake").parent.mkdir(parents=True)
    stt.stt_model_path("fake").write_bytes(b"12345")

    st = stt.stt_model_status("fake")

    assert st["state"] == "ready"
    assert st["size_mb"] is not None
    assert st["error"] is None


def test_model_status_error_when_size_differs(monkeypatch, tmp_path):
    # Tamaño distinto al catálogo = descarga cortada/corrupta.
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)
    _add_fake_model(monkeypatch)

    stt.stt_model_path("fake").parent.mkdir(parents=True)
    stt.stt_model_path("fake").write_bytes(b"12")

    st = stt.stt_model_status("fake")

    assert st["state"] == "error"
    assert "dañado" in st["error"]


def test_model_status_downloading_flag_wins(monkeypatch, tmp_path):
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)
    _add_fake_model(monkeypatch)
    monkeypatch.setattr(stt, "_downloading_models", {"fake"})

    assert stt.stt_model_status("fake")["state"] == "downloading"


def test_model_status_error_from_meta(monkeypatch, tmp_path):
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)
    _add_fake_model(monkeypatch)
    meta = {"fake": {"state": "error", "error": "sha256 invalido para ggml-fake.bin"}}
    stt._write_models_meta(meta)

    st = stt.stt_model_status("fake")

    assert st["state"] == "error"
    assert "sha256 invalido" in st["error"]


def test_model_status_broken_meta_ignored(monkeypatch, tmp_path):
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)
    _add_fake_model(monkeypatch)
    meta_dir = stt.stt_models_dir()
    meta_dir.mkdir(parents=True)
    (meta_dir / stt.MODELS_META_FILENAME).write_text("{no-json", encoding="utf-8")

    assert stt.stt_model_status("fake")["state"] == "missing"


# --------------------------------------------------------------------------
# download_stt_model
# --------------------------------------------------------------------------


async def test_download_model_success(monkeypatch, tmp_path):
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)
    _add_fake_model(monkeypatch)
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=b"12345"))
    events: list[float] = []

    st = await stt.download_stt_model("fake", lambda pct, _d: events.append(pct))

    assert st["state"] == "ready"
    assert stt.stt_model_path("fake").read_bytes() == b"12345"
    assert events[-1] == 100.0
    assert not (tmp_path / "runtime" / "stt" / ".model-download").exists()


async def test_download_model_sha_mismatch_is_error(monkeypatch, tmp_path):
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)
    _add_fake_model(monkeypatch)
    monkeypatch.setitem(
        stt.STT_MODELS, "fake",
        {
            "label": "Fake", "filename": "ggml-fake.bin",
            "url": "http://fake/ggml-fake.bin",
            "sha256": "0" * 64, "size": 5,
        },
    )
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=b"12345"))

    with pytest.raises(RuntimeError, match="sha256"):
        await stt.download_stt_model("fake")

    assert stt.stt_model_status("fake")["state"] == "error"
    assert not (tmp_path / "runtime" / "stt" / ".model-download").exists()


async def test_download_model_parts_assembled(monkeypatch, tmp_path):
    # Modelo partido (como large-v3): 2 descargas, sha por parte y
    # concatenación final.
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)
    part_a, part_b = b"123", b"45"
    monkeypatch.setitem(
        stt.STT_MODELS,
        "parts",
        {
            "label": "Parts",
            "filename": "ggml-parts.bin",
            "url": None,
            "sha256": None,
            "size": len(part_a) + len(part_b),
            "parts": [
                {"filename": "ggml-parts.bin.part-aa",
                 "url": "http://fake/ggml-parts.bin.part-aa",
                 "sha256": hashlib.sha256(part_a).hexdigest(),
                 "size": len(part_a)},
                {"filename": "ggml-parts.bin.part-ab",
                 "url": "http://fake/ggml-parts.bin.part-ab",
                 "sha256": hashlib.sha256(part_b).hexdigest(),
                 "size": len(part_b)},
            ],
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = part_a if request.url.path.endswith("part-aa") else part_b
        return httpx.Response(200, content=body)

    _patch_transport(monkeypatch, handler)

    st = await stt.download_stt_model("parts")

    assert st["state"] == "ready"
    assert stt.stt_model_path("parts").read_bytes() == part_a + part_b
    assert not (tmp_path / "runtime" / "stt" / ".model-download").exists()


async def test_download_model_no_redownload_when_complete(monkeypatch, tmp_path):
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)
    _add_fake_model(monkeypatch)
    stt.stt_model_path("fake").parent.mkdir(parents=True)
    stt.stt_model_path("fake").write_bytes(b"12345")

    def _no_net(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no debe tocar la red")

    _patch_transport(monkeypatch, _no_net)

    st = await stt.download_stt_model("fake")

    assert st["state"] == "ready"
    assert stt.stt_model_path("fake").read_bytes() == b"12345"


async def test_download_model_concurrent_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)
    _add_fake_model(monkeypatch)
    monkeypatch.setattr(stt, "_downloading_models", {"fake"})

    with pytest.raises(RuntimeError, match="en curso"):
        await stt.download_stt_model("fake")


async def test_download_model_unknown_name_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)

    with pytest.raises(ValueError, match="desconocido"):
        await stt.download_stt_model("tiny")


# --------------------------------------------------------------------------
# delete_stt_model
# --------------------------------------------------------------------------


def test_delete_model_removes_file_and_meta(monkeypatch, tmp_path):
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)
    _add_fake_model(monkeypatch)
    p = stt.stt_model_path("fake")
    p.parent.mkdir(parents=True)
    p.write_bytes(b"12345")
    stt._write_models_meta({"fake": {"state": "error", "error": "x"}})

    assert stt.delete_stt_model("fake") is True
    assert not p.exists()
    assert stt._read_models_meta() == {}


def test_delete_model_noop_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)
    _add_fake_model(monkeypatch)

    assert stt.delete_stt_model("fake") is False


def test_delete_model_rejected_while_downloading(monkeypatch, tmp_path):
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)
    _add_fake_model(monkeypatch)
    p = stt.stt_model_path("fake")
    p.parent.mkdir(parents=True)
    p.write_bytes(b"12345")
    monkeypatch.setattr(stt, "_downloading_models", {"fake"})

    with pytest.raises(RuntimeError, match="descargando"):
        stt.delete_stt_model("fake")
    assert p.exists()


def test_delete_model_removes_empty_models_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)
    _add_fake_model(monkeypatch)
    p = stt.stt_model_path("fake")
    p.parent.mkdir(parents=True)
    p.write_bytes(b"12345")

    stt.delete_stt_model("fake")

    assert not stt.stt_models_dir().exists()
