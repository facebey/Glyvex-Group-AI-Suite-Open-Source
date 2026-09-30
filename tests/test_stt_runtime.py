"""
test_stt_runtime.py — T2.2.6: tests del núcleo stt_runtime.py (catálogo,
estados, download, probe, reset).

Todo mockeado, sin red real: mismo patrón que test_runtime.py — el download
corre contra un fake server vía httpx.MockTransport (sha256 real del body),
la plataforma se fuerza con monkeypatch y los zips se construyen en memoria.
Los binarios probeables reutilizan la fábrica de conftest.
"""

from __future__ import annotations

import hashlib
import io
import json
import platform
import zipfile
from pathlib import Path

import httpx
import pytest

import stt_runtime as stt


def _force_windows(monkeypatch):
    monkeypatch.setattr(stt.platform, "system", lambda: "Windows")


def _force_non_windows(monkeypatch):
    monkeypatch.setattr(stt.platform, "system", lambda: "Linux")


def _patch_transport(monkeypatch, handler):
    """_download_file (runtime.py) usa el módulo httpx compartido: parchear
    httpx.AsyncClient alcanza también a stt_runtime."""
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


# Set mínimo completo, como lo trae el zip oficial (raíz única que el
# extract debe descartar).
_MIN_SET = {
    "whisper-cli.exe": b"EXE",
    "whisper.dll": b"WDLL",
    "ggml.dll": b"GDLL",
    "ggml-base.dll": b"GBDLL",
    "ggml-cpu-x64.dll": b"CPUDLL",
}


def _stub_source(monkeypatch, body: bytes, sha256: str | None = None):
    url = "http://fake/whisper-bin-x64.zip"
    monkeypatch.setattr(
        stt, "STT_SOURCES",
        [{"id": "fake-official", "files": [
            {"url": url, "sha256": sha256 or hashlib.sha256(body).hexdigest()}]}],
    )
    return url


def _setup_download(monkeypatch, tmp_path):
    _force_windows(monkeypatch)
    # STT_BINARY_NAME se fija en el import: en CI no-Windows valía
    # "whisper-cli" y el archive fake trae el .exe.
    monkeypatch.setattr(stt, "STT_BINARY_NAME", "whisper-cli.exe")
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)


# --------------------------------------------------------------------------
# Catálogo / fuente
# --------------------------------------------------------------------------


def test_select_source_official_b5130_verifiable():
    source = stt._select_stt_source()
    assert source is not None
    assert source["id"] == "official"
    assert all(len(f["sha256"]) == 64 for f in source["files"])
    assert all("b5130" in f["url"] for f in source["files"])


def test_select_source_none_when_unverifiable(monkeypatch):
    monkeypatch.setattr(
        stt, "STT_SOURCES",
        [{"id": "fake", "files": [{"url": "http://fake/a.zip", "sha256": ""}]}],
    )
    assert stt._select_stt_source() is None


# --------------------------------------------------------------------------
# can_download
# --------------------------------------------------------------------------


def test_can_download_windows_with_source(monkeypatch):
    _force_windows(monkeypatch)
    assert stt.can_download() is True


def test_can_download_false_on_non_windows(monkeypatch):
    _force_non_windows(monkeypatch)
    assert stt.can_download() is False


def test_can_download_false_without_source(monkeypatch):
    _force_windows(monkeypatch)
    monkeypatch.setattr(
        stt, "STT_SOURCES",
        [{"id": "fake", "files": [{"url": "http://fake/a.zip", "sha256": ""}]}],
    )
    assert stt.can_download() is False


# --------------------------------------------------------------------------
# stt_runtime_status — estados
# --------------------------------------------------------------------------


def test_status_missing_when_no_dir(monkeypatch, tmp_path):
    _force_windows(monkeypatch)
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)
    st = stt.stt_runtime_status()
    assert st["pin"] == stt.STT_PIN
    assert st["state"] == "missing"
    assert st["missing_files"] == []


def test_status_unsupported_on_non_windows(monkeypatch):
    _force_non_windows(monkeypatch)
    assert stt.stt_runtime_status()["state"] == "unsupported"


def test_status_downloading_when_flag_set(monkeypatch):
    _force_windows(monkeypatch)
    monkeypatch.setattr(stt, "_downloading", True)
    assert stt.stt_runtime_status()["state"] == "downloading"


def test_status_ready_with_full_set(monkeypatch, tmp_path):
    _force_windows(monkeypatch)
    # STT_BINARY_NAME se fija en el import: en CI no-Windows valía
    # "whisper-cli" y el set mínimo escrito trae el .exe.
    monkeypatch.setattr(stt, "STT_BINARY_NAME", "whisper-cli.exe")
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)
    d = stt.stt_runtime_dir()
    d.mkdir(parents=True)
    for name, data in _MIN_SET.items():
        (d / name).write_bytes(data)
    (d / stt.META_FILENAME).write_text(
        json.dumps({"state": "ready", "source": "official"}), encoding="utf-8"
    )

    st = stt.stt_runtime_status()

    assert st["state"] == "ready"
    assert st["binary_path"] == str(d / stt.STT_BINARY_NAME)
    assert st["source"] == "official"
    assert st["size_mb"] is not None
    assert st["missing_files"] == []


def test_status_incomplete_when_cpu_backend_missing(monkeypatch, tmp_path):
    _force_windows(monkeypatch)
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)
    d = stt.stt_runtime_dir()
    d.mkdir(parents=True)
    for name in ("whisper-cli.exe", "whisper.dll", "ggml.dll", "ggml-base.dll"):
        (d / name).touch()

    st = stt.stt_runtime_status()

    assert st["state"] == "error"
    assert st["missing_files"] == ["ggml-cpu-*.dll"]
    assert "incompleto" in st["error"]


def test_status_error_from_meta_wins(monkeypatch, tmp_path):
    # Tras un download fallido la causa real de la meta gana al
    # "incompleto" genérico.
    _force_windows(monkeypatch)
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)
    d = stt.stt_runtime_dir()
    d.mkdir(parents=True)
    (d / stt.META_FILENAME).write_text(
        json.dumps({"state": "error", "error": "sha256 inválido para whisper-bin-x64.zip"}),
        encoding="utf-8",
    )

    st = stt.stt_runtime_status()

    assert st["state"] == "error"
    assert "sha256 inválido" in st["error"]


def test_status_broken_meta_ignored_with_partial_set(monkeypatch, tmp_path):
    # Meta corrupta no se parsea (no "error" de meta, no crash): con un set
    # parcial queda el "incompleto" genérico reportando qué falta.
    _force_windows(monkeypatch)
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)
    d = stt.stt_runtime_dir()
    d.mkdir(parents=True)
    (d / stt.META_FILENAME).write_text("{no-json", encoding="utf-8")

    st = stt.stt_runtime_status()

    assert st["state"] == "error"
    assert st["missing_files"] == list(stt.STT_REQUIRED_FILES) + list(stt.STT_REQUIRED_GLOBS)


# --------------------------------------------------------------------------
# reset_stt_runtime
# --------------------------------------------------------------------------


def test_reset_noop_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)
    assert stt.reset_stt_runtime() is False


def test_reset_removes_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)
    d = stt.stt_runtime_dir()
    d.mkdir(parents=True)
    (d / stt.STT_BINARY_NAME).touch()

    assert stt.reset_stt_runtime() is True
    assert not d.exists()


# --------------------------------------------------------------------------
# probe_stt_binary
# --------------------------------------------------------------------------


async def test_probe_true_with_probeable_binary(make_probeable_binary):
    # --help de whisper-cli va por STDERR: el probe debe capturar ambos
    # streams para no marcar un binario sano como fallido.
    binary = make_probeable_binary(["usage: whisper-cli [options] file0 file1 ..."])
    assert await stt.probe_stt_binary(binary) is True


async def test_probe_false_when_binary_missing():
    assert await stt.probe_stt_binary(r"C:\no-existe\whisper-cli.exe") is False


async def test_probe_false_when_binary_exits_nonzero(tmp_path):
    if platform.system() == "Windows":
        script = tmp_path / "failing.bat"
        script.write_text("@echo off\r\nexit /b 1\r\n", encoding="utf-8")
    else:
        script = tmp_path / "failing.sh"
        script.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        script.chmod(0o755)

    assert await stt.probe_stt_binary(str(script)) is False


# --------------------------------------------------------------------------
# download_stt_runtime
# --------------------------------------------------------------------------


async def test_download_success(monkeypatch, tmp_path):
    _setup_download(monkeypatch, tmp_path)
    body = _zip_bytes({f"whisper-bin-x64/{k}": v for k, v in _MIN_SET.items()})
    _stub_source(monkeypatch, body)
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=body))
    events: list[float] = []

    st = await stt.download_stt_runtime(lambda pct, _d: events.append(pct))

    assert st["state"] == "ready"
    assert st["source"] == "fake-official"
    d = stt.stt_runtime_dir()
    assert (d / "whisper-cli.exe").read_bytes() == b"EXE"
    assert (d / "ggml-cpu-x64.dll").read_bytes() == b"CPUDLL"
    assert not (d / "whisper-bin-x64").exists()  # raíz única descartada
    assert events[-1] == 100.0
    meta = json.loads((d / stt.META_FILENAME).read_text(encoding="utf-8"))
    assert meta["state"] == "ready"
    assert not (tmp_path / "runtime" / "stt" / f".download-{stt.STT_PIN}").exists()


async def test_download_sha_mismatch_is_error(monkeypatch, tmp_path):
    _setup_download(monkeypatch, tmp_path)
    body = _zip_bytes(_MIN_SET)
    _stub_source(monkeypatch, body, sha256="0" * 64)
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=body))

    with pytest.raises(RuntimeError, match="sha256"):
        await stt.download_stt_runtime()

    assert stt.stt_runtime_status()["state"] == "error"
    assert not (tmp_path / "runtime" / "stt" / f".download-{stt.STT_PIN}").exists()


async def test_download_incomplete_archive_is_error(monkeypatch, tmp_path):
    # Archive sin ningún ggml-cpu-*.dll: extrae pero el set queda incompleto.
    _setup_download(monkeypatch, tmp_path)
    incomplete = {k: v for k, v in _MIN_SET.items() if not k.startswith("ggml-cpu-")}
    body = _zip_bytes(incomplete)
    _stub_source(monkeypatch, body)
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=body))

    with pytest.raises(RuntimeError, match="incompleto"):
        await stt.download_stt_runtime()

    assert stt.stt_runtime_status()["state"] == "error"


async def test_download_preserves_models(monkeypatch, tmp_path):
    # Una re-descarga del motor NO borra los modelos ya descargados (T2.2b).
    _setup_download(monkeypatch, tmp_path)
    target = stt.stt_runtime_dir()
    target.mkdir(parents=True)
    (target / "models").mkdir()
    (target / "models" / "ggml-base.bin").write_bytes(b"MODEL")
    body = _zip_bytes(_MIN_SET)
    _stub_source(monkeypatch, body)
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=body))

    st = await stt.download_stt_runtime()

    assert st["state"] == "ready"
    assert (target / "models" / "ggml-base.bin").read_bytes() == b"MODEL"


async def test_download_http_error_is_error(monkeypatch, tmp_path):
    _setup_download(monkeypatch, tmp_path)
    _stub_source(monkeypatch, _zip_bytes(_MIN_SET))
    _patch_transport(monkeypatch, lambda req: httpx.Response(500, text="boom"))

    with pytest.raises(httpx.HTTPStatusError):
        await stt.download_stt_runtime()

    assert stt.stt_runtime_status()["state"] == "error"


async def test_download_rejects_non_windows(monkeypatch, tmp_path):
    _force_non_windows(monkeypatch)
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)
    with pytest.raises(RuntimeError, match="Windows-only"):
        await stt.download_stt_runtime()


async def test_download_concurrent_rejected(monkeypatch, tmp_path):
    _force_windows(monkeypatch)
    monkeypatch.setattr(stt, "DATA_DIR", tmp_path)
    monkeypatch.setattr(stt, "_downloading", True)
    with pytest.raises(RuntimeError, match="en curso"):
        await stt.download_stt_runtime()
