"""
test_runtime.py — RT-7: tests del núcleo runtime.py (descarga, extract,
fuente, estados).

Todo mockeado, sin red real: _download_file corre contra un fake server vía
httpx.MockTransport (el sha256 ES real — se calcula del body), la plataforma
se fuerza con monkeypatch (mismo patrón que test_runtime_api.py) y los zips
se construyen en memoria con zipfile. Los tests de la capa API (SSE, status
endpoint) viven en test_runtime_api.py.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

import runtime as runtime_module


def _force_windows(monkeypatch):
    monkeypatch.setattr(runtime_module.platform, "system", lambda: "Windows")


def _force_non_windows(monkeypatch):
    monkeypatch.setattr(runtime_module.platform, "system", lambda: "Linux")


def _patch_transport(monkeypatch, handler):
    """Reemplaza httpx.AsyncClient por uno con MockTransport(handler):
    _download_file usa el client INTERNO, así que la única forma de simular
    la red sin tocar sockets es interceptar la clase en el módulo."""
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(runtime_module.httpx, "AsyncClient", factory)


def _make_zip(tmp_path: Path, entries: dict[str, bytes]) -> Path:
    z = tmp_path / "archive.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return z


# --------------------------------------------------------------------------
# _download_file — fake server + sha256 real
# --------------------------------------------------------------------------


async def test_download_file_success(monkeypatch, tmp_path):
    body = b"payload-del-runtime"
    spec = {"url": "http://fake/test.zip", "sha256": hashlib.sha256(body).hexdigest()}
    events: list[tuple[float, str]] = []
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=body))

    dest = tmp_path / "test.zip"
    await runtime_module._download_file(
        spec, dest, lambda pct, detail: events.append((pct, detail)), 1, 1
    )

    assert dest.read_bytes() == body
    assert events[0] == (100.0, "Descargando test.zip")
    assert events[-1] == (100.0, "Verificado test.zip")


async def test_download_file_sha256_mismatch_cleans_up(monkeypatch, tmp_path):
    body = b"payload"
    spec = {"url": "http://fake/bad.zip", "sha256": "0" * 64}
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=body))

    dest = tmp_path / "bad.zip"
    with pytest.raises(RuntimeError, match="sha256"):
        await runtime_module._download_file(spec, dest, lambda pct, d: None, 1, 1)

    assert not dest.exists()


async def test_download_file_http_error_no_leftover(monkeypatch, tmp_path):
    spec = {"url": "http://fake/boom.zip", "sha256": hashlib.sha256(b"").hexdigest()}
    _patch_transport(monkeypatch, lambda req: httpx.Response(500, text="boom"))

    dest = tmp_path / "boom.zip"
    with pytest.raises(httpx.HTTPStatusError):
        await runtime_module._download_file(spec, dest, lambda pct, d: None, 1, 1)

    assert not dest.exists()


# --------------------------------------------------------------------------
# _extract_zip — raíz única, flat, zip-slip
# --------------------------------------------------------------------------


def test_extract_zip_strips_single_root_folder(tmp_path):
    # Como el archive propio: todas las entradas bajo UNA carpeta raíz,
    # que debe descartarse (layout plano en target).
    z = _make_zip(tmp_path, {
        "llama.cpp/llama-server.exe": b"EXE",
        "llama.cpp/cudart64_13.dll": b"DLL",
        "llama.cpp/sub/deep.dll": b"DEEP",
    })
    target = tmp_path / "out"
    target.mkdir()

    runtime_module._extract_zip(z, target)

    assert (target / "llama-server.exe").read_bytes() == b"EXE"
    assert (target / "cudart64_13.dll").read_bytes() == b"DLL"
    assert (target / "sub" / "deep.dll").read_bytes() == b"DEEP"
    assert not (target / "llama.cpp").exists()


def test_extract_zip_flat_stays_in_place(tmp_path):
    # Como los zips upstream: sin carpeta raíz (2+ tops → sin strip).
    z = _make_zip(tmp_path, {
        "llama-server.exe": b"EXE",
        "cudart64_13.dll": b"DLL",
    })
    target = tmp_path / "out"
    target.mkdir()

    runtime_module._extract_zip(z, target)

    assert (target / "llama-server.exe").read_bytes() == b"EXE"
    assert (target / "cudart64_13.dll").read_bytes() == b"DLL"


def test_extract_zip_rejects_entry_outside_target(tmp_path):
    # El guard NO dispara con "../evil.txt" sola (el prefix-strip la
    # neutralizaría); hace falta 2+ top-level names para que el prefix
    # quede vacío y la entrada relativa se resuelva fuera de target.
    z = _make_zip(tmp_path, {"good.txt": b"OK", "../evil.txt": b"EVIL"})
    target = tmp_path / "out"
    target.mkdir()

    with pytest.raises(RuntimeError, match="fuera del destino"):
        runtime_module._extract_zip(z, target)

    assert not (tmp_path / "evil.txt").exists()


# --------------------------------------------------------------------------
# _select_base_source / _select_accel_source / can_download (split RT-10)
# --------------------------------------------------------------------------


def test_select_base_source_official_b11146_verifiable():
    # Bump b11146: la fuente es la build oficial de la release (los archives
    # propios "glyvex" eran específicos de b11009).
    source = runtime_module._select_base_source()
    assert source is not None
    assert source["id"] == "official"
    assert all(len(f["sha256"]) == 64 for f in source["files"])
    assert all("b11146" in f["url"] for f in source["files"])


def test_select_accel_source_nvidia_official_b11146_verifiable():
    source = runtime_module._select_accel_source("nvidia")
    assert source is not None
    assert source["id"] == "official"
    assert all(len(f["sha256"]) == 64 for f in source["files"])
    assert len(source["files"]) == 2
    assert all("b11146" in f["url"] for f in source["files"])


def test_select_accel_source_none_for_family_without_package():
    # Fase A: solo nvidia tiene paquete. AMD/Intel/CPU → None (degradado).
    assert runtime_module._select_accel_source("amd") is None
    assert runtime_module._select_accel_source("intel") is None
    assert runtime_module._select_accel_source("cpu") is None


def test_select_sources_none_when_nothing_verifiable(monkeypatch):
    monkeypatch.setattr(runtime_module, "BASE_SOURCES", [
        {"id": "glyvex", "files": [{"url": "http://fake/a.zip", "sha256": ""}]},
    ])
    monkeypatch.setattr(runtime_module, "ACCEL_SOURCES", {
        "nvidia": [{"id": "glyvex", "files": [{"url": "http://fake/b.zip", "sha256": ""}]}],
    })
    assert runtime_module._select_base_source() is None
    assert runtime_module._select_accel_source("nvidia") is None


def test_can_download_windows_with_base(monkeypatch):
    _force_windows(monkeypatch)
    assert runtime_module.can_download() is True


def test_can_download_false_on_non_windows(monkeypatch):
    _force_non_windows(monkeypatch)
    assert runtime_module.can_download() is False


def test_can_download_false_without_base(monkeypatch):
    _force_windows(monkeypatch)
    monkeypatch.setattr(
        runtime_module, "BASE_SOURCES",
        [{"id": "glyvex", "files": [{"url": "http://fake/a.zip", "sha256": ""}]}],
    )
    assert runtime_module.can_download() is False


def test_can_download_true_without_accel_for_family(monkeypatch):
    # La aceleración es opcional: sin paquete para la familia el download
    # sigue disponible (runtime degradado a CPU, no error).
    _force_windows(monkeypatch)
    monkeypatch.setattr(runtime_module, "ACCEL_SOURCES", {})
    assert runtime_module.can_download() is True


# --------------------------------------------------------------------------
# reset_runtime
# --------------------------------------------------------------------------


def test_reset_runtime_noop_when_missing():
    assert runtime_module.reset_runtime() is False


def test_reset_runtime_removes_dir():
    d = runtime_module.runtime_dir()
    d.mkdir(parents=True)
    (d / runtime_module.BINARY_NAME).touch()

    assert runtime_module.reset_runtime() is True
    assert not d.exists()


# --------------------------------------------------------------------------
# runtime_status — estados
# --------------------------------------------------------------------------


def test_status_missing_on_windows(monkeypatch):
    _force_windows(monkeypatch)
    st = runtime_module.runtime_status()
    assert st["pin"] == runtime_module.RUNTIME_PIN
    assert st["state"] == "missing"
    assert st["binary_path"] is None
    assert st["build"] is None


def test_status_unsupported_on_non_windows(monkeypatch):
    _force_non_windows(monkeypatch)
    assert runtime_module.runtime_status()["state"] == "unsupported"


def test_status_downloading_when_flag_set(monkeypatch):
    _force_windows(monkeypatch)
    monkeypatch.setattr(runtime_module, "_downloading", True)
    assert runtime_module.runtime_status()["state"] == "downloading"


def test_status_ready_with_meta(monkeypatch):
    _force_windows(monkeypatch)
    d = runtime_module.runtime_dir()
    d.mkdir(parents=True)
    (d / runtime_module.BINARY_NAME).write_bytes(b"FAKE-EXE")
    (d / runtime_module.META_FILENAME).write_text(
        json.dumps({"state": "ready", "build": "b11146", "source": "official"}),
        encoding="utf-8",
    )

    st = runtime_module.runtime_status()

    assert st["state"] == "ready"
    assert st["binary_path"] == str(d / runtime_module.BINARY_NAME)
    assert st["build"] == "b11146"
    assert st["source"] == "official"
    assert st["size_mb"] is not None


def test_status_error_from_meta(monkeypatch):
    _force_windows(monkeypatch)
    d = runtime_module.runtime_dir()
    d.mkdir(parents=True)
    (d / runtime_module.META_FILENAME).write_text(
        json.dumps({"state": "error", "error": "sha256 inválido para x.zip"}),
        encoding="utf-8",
    )

    st = runtime_module.runtime_status()

    assert st["state"] == "error"
    assert "sha256 inválido" in st["error"]


def test_status_broken_meta_falls_to_missing(monkeypatch):
    _force_windows(monkeypatch)
    d = runtime_module.runtime_dir()
    d.mkdir(parents=True)
    (d / runtime_module.META_FILENAME).write_text("{no-json", encoding="utf-8")
    assert runtime_module.runtime_status()["state"] == "missing"


# --------------------------------------------------------------------------
# resolve_binary — cascada experto → runtime → None
# --------------------------------------------------------------------------


def test_resolve_binary_expert_path_wins():
    expert = "C:\\expert\\llama-server.exe"
    assert runtime_module.resolve_binary(expert) == expert


def test_resolve_binary_none_on_non_windows(monkeypatch):
    _force_non_windows(monkeypatch)
    assert runtime_module.resolve_binary() is None


def test_resolve_binary_ready_on_windows(monkeypatch):
    _force_windows(monkeypatch)
    d = runtime_module.runtime_dir()
    d.mkdir(parents=True)
    (d / runtime_module.BINARY_NAME).touch()
    assert runtime_module.resolve_binary() == str(d / runtime_module.BINARY_NAME)


def test_resolve_binary_none_when_missing(monkeypatch):
    _force_windows(monkeypatch)
    assert runtime_module.resolve_binary() is None


# --------------------------------------------------------------------------
# detect_gpu_family (gate de RT-10)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("gpu", "family"),
    [
        ("NVIDIA GeForce RTX 3090", "nvidia"),
        ("AMD Radeon RX 7800 XT", "amd"),
        ("Radeon Graphics", "amd"),
        ("Intel(R) Iris(R) Xe Graphics", "intel"),
        ("Intel(R) Arc(TM) A770", "intel"),
        ("cpu", "cpu"),
    ],
)
def test_detect_gpu_family(monkeypatch, gpu, family):
    monkeypatch.setattr(runtime_module, "detect_gpu", lambda: gpu)
    assert runtime_module.detect_gpu_family() == family


def test_detect_gpu_family_unknown_name_falls_to_cpu(monkeypatch):
    monkeypatch.setattr(runtime_module, "detect_gpu", lambda: "GPU Genérica XYZ")
    assert runtime_module.detect_gpu_family() == "cpu"


# --------------------------------------------------------------------------
# download_runtime — 2 etapas (base + aceleración) con degradación
# --------------------------------------------------------------------------


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _stub_sources(monkeypatch, base_body: bytes, accel_body: bytes | None, family: str):
    """BASE/ACCEL con URLs fake + sha256 reales de los bodies en memoria."""
    base_url = "http://fake/base.zip"
    bodies = {base_url: base_body}
    monkeypatch.setattr(
        runtime_module, "BASE_SOURCES",
        [{"id": "fake-base", "files": [
            {"url": base_url, "sha256": hashlib.sha256(base_body).hexdigest()}]}],
    )
    accel_sources: dict[str, list[dict]] = {}
    if accel_body is not None:
        accel_url = "http://fake/accel.zip"
        bodies[accel_url] = accel_body
        accel_sources[family] = [{
            "id": "fake-accel",
            "files": [{"url": accel_url,
                       "sha256": hashlib.sha256(accel_body).hexdigest()}],
        }]
    monkeypatch.setattr(runtime_module, "ACCEL_SOURCES", accel_sources)
    return bodies


def _setup_download(monkeypatch, tmp_path, family: str):
    _force_windows(monkeypatch)
    # BINARY_NAME se fija en el import (runtime.py:61): en CI no-Windows la
    # constante ya valio "llama-server", asi que el simulacro de Windows debe
    # forzarla tambien (el archive fake trae el .exe).
    monkeypatch.setattr(runtime_module, "BINARY_NAME", "llama-server.exe")
    monkeypatch.setattr(runtime_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(runtime_module, "detect_gpu", lambda: family.upper())


async def test_download_two_stage_with_accel(monkeypatch, tmp_path):
    _setup_download(monkeypatch, tmp_path, "nvidia")
    bodies = _stub_sources(
        monkeypatch,
        _zip_bytes({"llama-server.exe": b"EXE", "llama.dll": b"LIB"}),
        _zip_bytes({"ggml-cuda.dll": b"CUDA", "cublas64_13.dll": b"CB"}),
        "nvidia",
    )
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=bodies[req.url]))
    events: list[float] = []

    st = await runtime_module.download_runtime(lambda pct, _d: events.append(pct))

    assert st["state"] == "ready"
    assert st["accel"] == "nvidia"
    assert st["accel_error"] is None
    d = runtime_module.runtime_dir()
    assert (d / "llama-server.exe").read_bytes() == b"EXE"
    assert (d / "ggml-cuda.dll").read_bytes() == b"CUDA"
    assert events[0] <= 50.0  # la etapa base queda dentro de 0-50 %
    assert events[-1] == 100.0
    meta = json.loads((d / runtime_module.META_FILENAME).read_text(encoding="utf-8"))
    assert meta["accel"] == "nvidia"
    assert meta["accel_source"] == "fake-accel"
    assert set(meta["files"]) == {"base.zip", "accel.zip"}


async def test_download_accel_failure_degrades_to_cpu(monkeypatch, tmp_path):
    # El fallo de la ETAPA 2 nunca rompe el runtime: queda degradado a CPU
    # (base operativa), state "ready" + accel_error en la meta.
    _setup_download(monkeypatch, tmp_path, "nvidia")
    base_url = "http://fake/base.zip"
    accel_url = "http://fake/accel.zip"
    base_body = _zip_bytes({"llama-server.exe": b"EXE", "llama.dll": b"LIB"})
    monkeypatch.setattr(
        runtime_module, "BASE_SOURCES",
        [{"id": "fake-base", "files": [
            {"url": base_url, "sha256": hashlib.sha256(base_body).hexdigest()}]}],
    )
    monkeypatch.setattr(
        runtime_module, "ACCEL_SOURCES",
        {"nvidia": [{"id": "fake-accel", "files": [
            {"url": accel_url, "sha256": "0" * 64}]}]},
    )

    def handler(req):
        if req.url == accel_url:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, content=base_body)

    _patch_transport(monkeypatch, handler)

    st = await runtime_module.download_runtime()

    assert st["state"] == "ready"
    assert st["accel"] is None
    assert st["accel_error"] is not None
    d = runtime_module.runtime_dir()
    assert (d / "llama-server.exe").exists()
    # Sin restos de la etapa fallida.
    assert not (tmp_path / "runtime" / f".download-{runtime_module.RUNTIME_PIN}").exists()


async def test_download_cpu_family_no_accel_stage(monkeypatch, tmp_path):
    # Familia sin paquete (cpu): una sola etapa, ready sin aceleración y
    # sin error de aceleración (no es degradación por fallo).
    _setup_download(monkeypatch, tmp_path, "cpu")
    bodies = _stub_sources(
        monkeypatch, _zip_bytes({"llama-server.exe": b"EXE", "llama.dll": b"LIB"}),
        None, "cpu",
    )
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=bodies[req.url]))
    events: list[float] = []

    st = await runtime_module.download_runtime(lambda pct, _d: events.append(pct))

    assert st["state"] == "ready"
    assert st["accel"] is None
    assert st["accel_error"] is None
    assert events[-1] == 50.0  # sin etapa 2 el progreso topa en 50 %


async def test_download_base_failure_is_error(monkeypatch, tmp_path):
    # El fallo de la ETAPA 1 (motor base) SÍ es error: meta "error" y raise.
    _setup_download(monkeypatch, tmp_path, "cpu")
    base_body = _zip_bytes({"llama-server.exe": b"EXE"})
    monkeypatch.setattr(
        runtime_module, "BASE_SOURCES",
        [{"id": "fake-base", "files": [
            {"url": "http://fake/base.zip", "sha256": "0" * 64}]}],
    )
    monkeypatch.setattr(runtime_module, "ACCEL_SOURCES", {})
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=base_body))

    with pytest.raises(RuntimeError, match="sha256"):
        await runtime_module.download_runtime()

    assert runtime_module.runtime_status()["state"] == "error"
    assert not (tmp_path / "runtime" / f".download-{runtime_module.RUNTIME_PIN}").exists()
