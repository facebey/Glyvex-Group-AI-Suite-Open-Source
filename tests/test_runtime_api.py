"""
test_runtime_api.py — RT-3: API /api/runtime (status, download SSE, reset).

Cubre la capa API sin red real ni GPU: la plataforma se fuerza con
monkeypatch (como los tests de cascada de RT-2 en test_launcher.py) y la
descarga se simula reemplazando runtime._download_file con una que escribe
un zip real (para que el extract de download_runtime corra de verdad). Los
tests de red real contra un fake server y sha256 inválido son de RT-7.
"""

from __future__ import annotations

import io
import json
import zipfile

import runtime as runtime_module
from helpers import drain_sse


def _force_windows(monkeypatch):
    # runtime.py y runtime_api.py comparten el módulo stdlib platform, así
    # que parchear system afecta a ambos (mismo patrón que test_launcher.py).
    monkeypatch.setattr(runtime_module.platform, "system", lambda: "Windows")


def _force_non_windows(monkeypatch):
    monkeypatch.setattr(runtime_module.platform, "system", lambda: "Linux")


def _fake_base_source() -> list[dict]:
    # sha256 cualquiera: _download_file va mockeado, no se verifica.
    return [
        {"id": "test", "files": [{"url": "http://fake/test.zip", "sha256": "a" * 64}]},
    ]


def _zip_bytes() -> bytes:
    # Con folder raíz como el archive propio: _extract_zip solo strippea la
    # raíz cuando TODAS las entradas comparten UNA única carpeta; un zip con
    # un solo archivo plano haría strip del propio binario y quedaría vacío.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"llama.cpp/{runtime_module.BINARY_NAME}", b"FAKE-EXE")
    return buf.getvalue()


def _parse_sse(events: list[str]) -> list[dict]:
    return [json.loads(line.split("data:", 1)[1]) for line in events]


async def _post_download_stream(client) -> list[dict]:
    async with client.stream("POST", "/api/runtime/download") as res:
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        events = await drain_sse(res)
    return _parse_sse(events)


# --------------------------------------------------------------------------
# GET /api/runtime/status
# --------------------------------------------------------------------------


async def test_status_missing_on_windows(client, monkeypatch):
    _force_windows(monkeypatch)
    monkeypatch.setattr(runtime_module, "_gpu_via_pynvml", lambda: None)
    monkeypatch.setattr(runtime_module, "_gpu_via_video_controller", lambda: None)

    res = await client.get("/api/runtime/status")
    assert res.status_code == 200
    data = res.json()
    assert data["pin"] == "b11146"
    assert data["state"] == "missing"
    assert data["build"] is None
    assert data["gpu"] == "cpu"
    assert data["platform"] == "Windows"


async def test_status_unsupported_on_non_windows(client, monkeypatch):
    _force_non_windows(monkeypatch)

    res = await client.get("/api/runtime/status")
    assert res.status_code == 200
    data = res.json()
    assert data["state"] == "unsupported"
    assert data["platform"] == "Linux"


async def test_status_ready_when_binary_present(client, monkeypatch):
    _force_windows(monkeypatch)
    d = runtime_module.runtime_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / runtime_module.BINARY_NAME).touch()
    (d / runtime_module.META_FILENAME).write_text(
        json.dumps({"pin": "b11146", "state": "ready", "build": "b11146", "source": "official"}),
        encoding="utf-8",
    )

    res = await client.get("/api/runtime/status")
    data = res.json()
    assert data["state"] == "ready"
    assert data["build"] == "b11146"
    assert data["source"] == "official"
    assert data["size_mb"] == 0.0


# --------------------------------------------------------------------------
# detect_gpu — cascada pynvml → video controller → "cpu"
# --------------------------------------------------------------------------


def test_detect_gpu_prefers_pynvml(monkeypatch):
    monkeypatch.setattr(runtime_module, "_gpu_via_pynvml", lambda: "NVIDIA GeForce RTX 3060")
    monkeypatch.setattr(runtime_module, "_gpu_via_video_controller", lambda: "Intel UHD")
    assert runtime_module.detect_gpu() == "NVIDIA GeForce RTX 3060"


def test_detect_gpu_falls_back_to_video_controller(monkeypatch):
    monkeypatch.setattr(runtime_module, "_gpu_via_pynvml", lambda: None)
    monkeypatch.setattr(
        runtime_module, "_gpu_via_video_controller", lambda: "Intel(R) UHD Graphics"
    )
    assert runtime_module.detect_gpu() == "Intel(R) UHD Graphics"


def test_detect_gpu_cpu_when_nothing(monkeypatch):
    monkeypatch.setattr(runtime_module, "_gpu_via_pynvml", lambda: None)
    monkeypatch.setattr(runtime_module, "_gpu_via_video_controller", lambda: None)
    assert runtime_module.detect_gpu() == "cpu"


# --------------------------------------------------------------------------
# POST /api/runtime/download
# --------------------------------------------------------------------------


async def test_download_rejects_non_windows(client, monkeypatch):
    _force_non_windows(monkeypatch)

    res = await client.post("/api/runtime/download")
    assert res.status_code == 400
    assert "Windows-only" in res.json()["detail"]


async def test_download_conflict_when_already_downloading(client, monkeypatch):
    _force_windows(monkeypatch)
    monkeypatch.setattr(runtime_module, "_downloading", True)

    res = await client.post("/api/runtime/download")
    assert res.status_code == 409


async def test_download_no_verifiable_source(client, monkeypatch):
    _force_windows(monkeypatch)
    monkeypatch.setattr(
        runtime_module, "BASE_SOURCES",
        [{"id": "glyvex", "files": [{"url": "http://fake/y.zip", "sha256": ""}]}],
    )

    res = await client.post("/api/runtime/download")
    assert res.status_code == 400
    assert "sha256" in res.json()["detail"]


async def test_download_sse_success(client, monkeypatch):
    _force_windows(monkeypatch)
    monkeypatch.setattr(runtime_module, "BASE_SOURCES", _fake_base_source())
    monkeypatch.setattr(runtime_module, "detect_gpu", lambda: "cpu")

    async def fake_download(spec, dest, on_progress, index, total):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(_zip_bytes())
        on_progress(50.0, f"Descargando {dest.name}")
        on_progress(100.0, f"Verificado {dest.name}")

    monkeypatch.setattr(runtime_module, "_download_file", fake_download)

    parsed = await _post_download_stream(client)

    types = [e["type"] for e in parsed]
    assert types.count("progress") >= 2
    assert types[-1] == "done"
    assert parsed[-1]["status"]["state"] == "ready"
    assert runtime_module.runtime_status()["state"] == "ready"


async def test_download_sse_error_leaves_error_status(client, monkeypatch):
    _force_windows(monkeypatch)
    monkeypatch.setattr(runtime_module, "BASE_SOURCES", _fake_base_source())
    monkeypatch.setattr(runtime_module, "detect_gpu", lambda: "cpu")

    async def broken_download(spec, dest, on_progress, index, total):
        raise RuntimeError("red caída (simulada)")

    monkeypatch.setattr(runtime_module, "_download_file", broken_download)

    parsed = await _post_download_stream(client)

    assert parsed[-1]["type"] == "error"
    assert "red caída" in parsed[-1]["message"]
    assert runtime_module.runtime_status()["state"] == "error"


# --------------------------------------------------------------------------
# POST /api/runtime/reset
# --------------------------------------------------------------------------


async def test_reset_noop_when_missing(client, monkeypatch):
    _force_windows(monkeypatch)

    res = await client.post("/api/runtime/reset")
    assert res.status_code == 200
    data = res.json()
    assert data["reset"] is False
    assert data["status"]["state"] == "missing"


async def test_reset_removes_runtime_dir(client, monkeypatch):
    _force_windows(monkeypatch)
    d = runtime_module.runtime_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / runtime_module.BINARY_NAME).touch()

    res = await client.post("/api/runtime/reset")
    data = res.json()
    assert data["reset"] is True
    assert data["status"]["state"] == "missing"
    assert not d.exists()
