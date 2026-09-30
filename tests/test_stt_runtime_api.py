"""
test_stt_runtime_api.py — T2.2.6 (parte API): /api/stt/runtime (status,
download SSE, reset).

Mismo patrón que test_runtime_api.py, pero con DATA_DIR aislado en tmp_path
(nunca toca data/ real) y la red simulada vía httpx.MockTransport (el
download corre de verdad contra el fake server, con sha256 real del body).
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import httpx

import stt_runtime as stt
from helpers import drain_sse


def _force_windows(monkeypatch):
    monkeypatch.setattr(stt.platform, "system", lambda: "Windows")


def _force_non_windows(monkeypatch):
    monkeypatch.setattr(stt.platform, "system", lambda: "Linux")


def _patch_transport(monkeypatch, handler):
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


_MIN_SET = {
    "whisper-cli.exe": b"EXE",
    "whisper.dll": b"WDLL",
    "ggml.dll": b"GDLL",
    "ggml-base.dll": b"GBDLL",
    "ggml-cpu-x64.dll": b"CPUDLL",
}


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _stub_source(monkeypatch, body: bytes, sha256: str | None = None):
    monkeypatch.setattr(
        stt, "STT_SOURCES",
        [{"id": "fake-official", "files": [
            {"url": "http://fake/whisper-bin-x64.zip",
             "sha256": sha256 or hashlib.sha256(body).hexdigest()}]}],
    )


def _setup(monkeypatch, tmp_path):
    _force_windows(monkeypatch)
    monkeypatch.setattr(stt, "STT_BINARY_NAME", "whisper-cli.exe")
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)


def _parse_sse(events: list[str]) -> list[dict]:
    return [json.loads(line.split("data:", 1)[1]) for line in events]


async def _post_download_stream(client) -> list[dict]:
    async with client.stream("POST", "/api/stt/runtime/download") as res:
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        events = await drain_sse(res)
    return _parse_sse(events)


# --------------------------------------------------------------------------
# GET /api/stt/runtime/status
# --------------------------------------------------------------------------


async def test_status_missing_on_windows(client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)

    res = await client.get("/api/stt/runtime/status")
    assert res.status_code == 200
    data = res.json()
    assert data["pin"] == stt.STT_PIN
    assert data["state"] == "missing"
    assert "probe_ok" not in data


async def test_status_unsupported_on_non_windows(client, monkeypatch, tmp_path):
    _force_non_windows(monkeypatch)
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)

    res = await client.get("/api/stt/runtime/status")
    assert res.status_code == 200
    assert res.json()["state"] == "unsupported"


async def test_status_ready_includes_probe(client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    d = stt.stt_runtime_dir()
    d.mkdir(parents=True)
    for name, data in _MIN_SET.items():
        (d / name).write_bytes(data)
    (d / stt.META_FILENAME).write_text(
        json.dumps({"state": "ready", "source": "fake-official"}), encoding="utf-8"
    )

    async def _fake_probe(p=None):
        return True

    monkeypatch.setattr(stt, "probe_stt_binary", _fake_probe)

    res = await client.get("/api/stt/runtime/status")
    data = res.json()
    assert data["state"] == "ready"
    assert data["probe_ok"] is True
    assert data["binary_path"].endswith("whisper-cli.exe")


# --------------------------------------------------------------------------
# POST /api/stt/runtime/download
# --------------------------------------------------------------------------


async def test_download_rejects_non_windows(client, monkeypatch, tmp_path):
    _force_non_windows(monkeypatch)
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)

    res = await client.post("/api/stt/runtime/download")
    assert res.status_code == 400
    assert "Windows-only" in res.json()["detail"]


async def test_download_conflict_when_already_downloading(client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(stt, "_downloading", True)

    res = await client.post("/api/stt/runtime/download")
    assert res.status_code == 409


async def test_download_no_verifiable_source(client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(
        stt, "STT_SOURCES",
        [{"id": "fake", "files": [{"url": "http://fake/a.zip", "sha256": ""}]}],
    )

    res = await client.post("/api/stt/runtime/download")
    assert res.status_code == 400
    assert "sha256" in res.json()["detail"]


async def test_download_sse_success(client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    body = _zip_bytes({f"whisper-bin-x64/{k}": v for k, v in _MIN_SET.items()})
    _stub_source(monkeypatch, body)
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=body))

    parsed = await _post_download_stream(client)

    types = [e["type"] for e in parsed]
    assert types.count("progress") >= 2
    assert types[-1] == "done"
    assert parsed[-1]["status"]["state"] == "ready"
    assert max(e["pct"] for e in parsed if e["type"] == "progress") == 100.0
    assert stt.stt_runtime_status()["state"] == "ready"


async def test_download_sse_error_leaves_error_status(client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    body = _zip_bytes(_MIN_SET)
    _stub_source(monkeypatch, body, sha256="0" * 64)
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=body))

    parsed = await _post_download_stream(client)

    assert parsed[-1]["type"] == "error"
    assert "sha256" in parsed[-1]["message"]
    assert stt.stt_runtime_status()["state"] == "error"


# --------------------------------------------------------------------------
# POST /api/stt/runtime/reset
# --------------------------------------------------------------------------


async def test_reset_noop_when_missing(client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)

    res = await client.post("/api/stt/runtime/reset")
    assert res.status_code == 200
    data = res.json()
    assert data["reset"] is False
    assert data["status"]["state"] == "missing"


async def test_reset_removes_runtime_dir(client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    d = stt.stt_runtime_dir()
    d.mkdir(parents=True)
    (d / stt.STT_BINARY_NAME).touch()

    res = await client.post("/api/stt/runtime/reset")
    data = res.json()
    assert data["reset"] is True
    assert data["status"]["state"] == "missing"
    assert not d.exists()


# --------------------------------------------------------------------------
# Modelos (T2.2b)
# --------------------------------------------------------------------------


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


async def _post_model_download_stream(client, name: str) -> list[dict]:
    async with client.stream("POST", "/api/stt/runtime/models/" + name + "/download") as res:
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        events = await drain_sse(res)
    return _parse_sse(events)


async def test_models_catalog(client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)

    res = await client.get("/api/stt/runtime/models")
    assert res.status_code == 200
    data = res.json()
    assert data["default"] == "base"
    assert {m["name"] for m in data["models"]} == set(stt.STT_MODELS)
    assert all(m["state"] == "missing" for m in data["models"])


async def test_model_download_unknown_404(client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)

    res = await client.post("/api/stt/runtime/models/tiny/download")
    assert res.status_code == 404
    assert "desconocido" in res.json()["detail"]


async def test_model_download_rejects_non_windows(client, monkeypatch, tmp_path):
    _force_non_windows(monkeypatch)
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)

    res = await client.post("/api/stt/runtime/models/base/download")
    assert res.status_code == 400
    assert "Windows-only" in res.json()["detail"]


async def test_model_download_conflict_when_downloading(client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(stt, "_downloading_models", {"base"})

    res = await client.post("/api/stt/runtime/models/base/download")
    assert res.status_code == 409


async def test_model_download_sse_success(client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    _add_fake_model(monkeypatch)
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=b"12345"))

    parsed = await _post_model_download_stream(client, "fake")

    types = [e["type"] for e in parsed]
    assert types.count("progress") >= 2
    assert types[-1] == "done"
    assert parsed[-1]["status"]["state"] == "ready"
    assert max(e["pct"] for e in parsed if e["type"] == "progress") == 100.0
    assert stt.stt_model_status("fake")["state"] == "ready"


async def test_model_delete_success(client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    _add_fake_model(monkeypatch)
    p = stt.stt_model_path("fake")
    p.parent.mkdir(parents=True)
    p.write_bytes(b"12345")

    res = await client.delete("/api/stt/runtime/models/fake")
    assert res.status_code == 200
    data = res.json()
    assert data["deleted"] is True
    assert data["status"]["state"] == "missing"
    assert not p.exists()


async def test_model_delete_noop_when_missing(client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)

    res = await client.delete("/api/stt/runtime/models/base")
    assert res.status_code == 200
    assert res.json()["deleted"] is False


async def test_model_delete_unknown_404(client, monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)

    res = await client.delete("/api/stt/runtime/models/tiny")
    assert res.status_code == 404
