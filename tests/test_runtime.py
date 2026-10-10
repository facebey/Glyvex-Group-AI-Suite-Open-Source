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

import asyncio
import hashlib
import io
import json
import os
import tarfile
import zipfile
from pathlib import Path

import httpx
import pytest

import runtime as runtime_module


def _force_windows(monkeypatch):
    monkeypatch.setattr(runtime_module.platform, "system", lambda: "Windows")


def _force_linux(monkeypatch):
    monkeypatch.setattr(runtime_module.platform, "system", lambda: "Linux")


def _force_unsupported(monkeypatch):
    """Plataforma fuera del soporte v1 (Windows x64 + Linux x64, RT-14)."""
    monkeypatch.setattr(runtime_module.platform, "system", lambda: "Darwin")


def _force_cc(monkeypatch, cc):
    """Fija la compute capability máxima detectada (RT-15). None simula sin
    NVML / GPU ausente / driver sin NVML."""
    monkeypatch.setattr(runtime_module, "_max_gpu_compute_capability", lambda: cc)


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


def _make_targz(
    tmp_path: Path,
    entries: dict[str, tuple[bytes, int]],
    symlinks: dict[str, str] | None = None,
) -> Path:
    p = tmp_path / "archive.tar.gz"
    with tarfile.open(p, "w:gz") as tf:
        for name, (data, mode) in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = mode
            tf.addfile(info, io.BytesIO(data))
        for name, linkname in (symlinks or {}).items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = linkname
            info.mode = 0o777
            tf.addfile(info)
    return p


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


def test_extract_zip_rejects_sibling_dir_bypass(tmp_path):
    # startswith de strings dejaría pasar un directorio hermano cuyo nombre
    # empieza con el del target (out-evil vs out): la contención correcta es
    # is_relative_to (SEC-1.b, F-b1).
    z = _make_zip(tmp_path, {"good.txt": b"OK", "../out-evil/evil.txt": b"EVIL"})
    target = tmp_path / "out"
    target.mkdir()

    with pytest.raises(RuntimeError, match="fuera del destino"):
        runtime_module._extract_zip(z, target)

    assert not (tmp_path / "out-evil").exists()


# --------------------------------------------------------------------------
# _extract_targz — raíz única, flat, tar-slip, bit executable (RT-14)
# --------------------------------------------------------------------------


def test_extract_targz_strips_single_root_folder_and_exec_bit(tmp_path):
    a = _make_targz(tmp_path, {
        "llama.cpp/llama-server": (b"BIN", 0o755),
        "llama.cpp/libllama.so": (b"LIB", 0o644),
        "llama.cpp/sub/deep.so": (b"DEEP", 0o644),
    })
    target = tmp_path / "out"
    target.mkdir()

    runtime_module._extract_targz(a, target)

    assert (target / "llama-server").read_bytes() == b"BIN"
    assert (target / "libllama.so").read_bytes() == b"LIB"
    assert (target / "sub" / "deep.so").read_bytes() == b"DEEP"
    assert not (target / "llama.cpp").exists()
    # En Windows los mode bits no son significativos: se verifica solo en
    # POSIX (el bit executable importa en Linux, no en NTFS).
    if os.name != "nt":
        assert (target / "llama-server").stat().st_mode & 0o100
        assert not (target / "libllama.so").stat().st_mode & 0o100


def test_extract_targz_flat_stays_in_place(tmp_path):
    a = _make_targz(tmp_path, {
        "llama-server": (b"BIN", 0o755),
        "libllama.so": (b"LIB", 0o644),
    })
    target = tmp_path / "out"
    target.mkdir()

    runtime_module._extract_targz(a, target)

    assert (target / "llama-server").read_bytes() == b"BIN"
    assert (target / "libllama.so").read_bytes() == b"LIB"


def test_extract_targz_rejects_entry_outside_target(tmp_path):
    # Mismo caso límite que el zip: 2+ tops → sin prefix → la entrada
    # relativa se resuelve fuera de target y el guard la rechaza.
    a = _make_targz(tmp_path, {
        "good.txt": (b"OK", 0o644),
        "../evil.txt": (b"EVIL", 0o644),
    })
    target = tmp_path / "out"
    target.mkdir()

    with pytest.raises(RuntimeError, match="fuera del destino"):
        runtime_module._extract_targz(a, target)

    assert not (tmp_path / "evil.txt").exists()


def test_extract_targz_preserves_symlinks(tmp_path):
    # RT-14: los tarballs oficiales empaquetan los SONAME como symlinks
    # (libllama-common.so.0 -> .so.0.5.0); sin ellos el loader dinámico
    # falla con "cannot open shared object file" (exit 127).
    a = _make_targz(
        tmp_path,
        {"llama.cpp/libllama-common.so.0.5.0": (b"REAL", 0o755)},
        symlinks={
            "llama.cpp/libllama-common.so.0": "libllama-common.so.0.5.0",
            "llama.cpp/libllama-common.so": "libllama-common.so.0",
        },
    )
    target = tmp_path / "out"
    target.mkdir()

    runtime_module._extract_targz(a, target)

    assert (target / "libllama-common.so.0.5.0").read_bytes() == b"REAL"
    link = target / "libllama-common.so.0"
    assert link.is_symlink()
    assert link.read_bytes() == b"REAL"
    # Cadena de dos saltos (symlink -> symlink -> file) también resuelve.
    assert (target / "libllama-common.so").is_symlink()
    assert (target / "libllama-common.so").read_bytes() == b"REAL"


def test_extract_targz_rejects_symlink_escaping_target(tmp_path):
    # El linkname (no el nombre del entry) escaparía del destino: guard
    # específico además del tar-slip sobre el nombre.
    target = tmp_path / "out"
    target.mkdir()
    for linkname in ("../evil-link", "/etc/passwd"):
        a = _make_targz(
            tmp_path,
            {"good.txt": (b"OK", 0o644), "other.txt": (b"OK2", 0o644)},
            symlinks={"link-bad": linkname},
        )
        with pytest.raises(RuntimeError, match="Symlink de tarball fuera del destino"):
            runtime_module._extract_targz(a, target)


def test_extract_archive_dispatches_by_suffix(tmp_path, monkeypatch):
    calls: list[tuple[str, Path]] = []
    monkeypatch.setattr(
        runtime_module, "_extract_zip",
        lambda a, t: calls.append(("zip", a)),
    )
    monkeypatch.setattr(
        runtime_module, "_extract_targz",
        lambda a, t: calls.append(("targz", a)),
    )
    z = tmp_path / "a.zip"
    g = tmp_path / "b.tar.gz"

    runtime_module._extract_archive(z, tmp_path)
    runtime_module._extract_archive(g, tmp_path)

    assert calls == [("zip", z), ("targz", g)]


# --------------------------------------------------------------------------
# _select_base_source / _select_accel_source / can_download (split RT-10)
# --------------------------------------------------------------------------


def test_select_base_source_official_b11349_verifiable(monkeypatch):
    # Bump b11349: la fuente es la build oficial de la release (los archives
    # propios "glyvex" eran específicos de b11009).
    _force_windows(monkeypatch)
    source = runtime_module._select_base_source()
    files = runtime_module._platform_files(source["files"])
    assert source is not None
    assert source["id"] == "official"
    assert len(files) == 1
    assert all(len(f["sha256"]) == 64 for f in files)
    assert all("b11349" in f["url"] for f in files)
    assert all(f["url"].endswith(".zip") for f in files)


def test_select_accel_source_windows_default_cuda12(monkeypatch):
    # RT-15: sin NVML (no se detecta la arch) Windows elige por defecto el
    # bundle 12.x (win-cuda-12.4), que sí soporta Pascal; 13.x solo Blackwell.
    _force_windows(monkeypatch)
    source = runtime_module._select_accel_source("nvidia")
    files = runtime_module._platform_files(source["files"])
    assert source is not None
    assert source["id"] == "official-cuda-12.4"
    assert source["cuda_major"] == 12
    assert all(len(f["sha256"]) == 64 for f in files)
    assert len(files) == 2
    assert all("b11349" in f["url"] for f in files)
    assert all(f["url"].endswith(".zip") for f in files)
    assert all("cuda-12.4" in f["url"] for f in files)


def test_select_accel_source_pascal_uses_cuda12_windows(monkeypatch):
    # 1080 Ti = Pascal sm_61: 12.x (win-cuda-12.4). CUDA 13.x la tiró.
    _force_windows(monkeypatch)
    _force_cc(monkeypatch, (6, 1))
    assert runtime_module._select_accel_source("nvidia")["id"] == "official-cuda-12.4"


def test_select_accel_source_maxwell_uses_cuda12_windows(monkeypatch):
    # Maxwell sm_50 también → 12.x.
    _force_windows(monkeypatch)
    _force_cc(monkeypatch, (5, 2))
    assert runtime_module._select_accel_source("nvidia")["id"] == "official-cuda-12.4"


def test_select_accel_source_turing_uses_cuda12_windows(monkeypatch):
    # Turing sm_75: sin Blackwell el default es 12.x (13.x también correría).
    _force_windows(monkeypatch)
    _force_cc(monkeypatch, (7, 5))
    assert runtime_module._select_accel_source("nvidia")["id"] == "official-cuda-12.4"


def test_select_accel_source_blackwell_uses_cuda13_windows(monkeypatch):
    # Blackwell sm_120 (cc 12.0): solo 13.x la compila → "official" (win 13.4).
    _force_windows(monkeypatch)
    _force_cc(monkeypatch, (12, 0))
    source = runtime_module._select_accel_source("nvidia")
    assert source["id"] == "official"
    assert source["cuda_major"] == 13
    assert all("cuda-13.4" in f["url"] for f in runtime_module._platform_files(source["files"]))


def test_select_accel_source_blackwell_uses_cuda13_linux(monkeypatch):
    # Blackwell en Linux → 13.x (los .tar.gz linux de "official").
    _force_linux(monkeypatch)
    _force_cc(monkeypatch, (10, 0))
    source = runtime_module._select_accel_source("nvidia")
    assert source["id"] == "official"
    assert all("cuda-13.4" in f["url"] for f in runtime_module._platform_files(source["files"]))


def test_required_cuda_major_by_architecture(monkeypatch):
    # RT-15: 13 solo con Blackwell (cc major >= 10); 12 en el resto y sin NVML.
    cases = {
        None: 12,
        (5, 2): 12,   # Maxwell
        (6, 1): 12,   # Pascal
        (7, 5): 12,   # Turing
        (8, 9): 12,   # Ampere
        (9, 0): 12,   # Hopper
        (10, 0): 13,  # Blackwell sm_100
        (12, 0): 13,  # Blackwell sm_120
    }
    for cc, expected in cases.items():
        _force_cc(monkeypatch, cc)
        assert runtime_module._required_cuda_major() == expected, cc


def test_cuda_note_only_for_pre_turing_arch(monkeypatch):
    # RT-15: el aviso solo se emite para Maxwell/Pascal/Volta (cc < 7.5).
    for cc, expected in {
        (6, 1): "6.1",   # Pascal
        (5, 2): "5.2",   # Maxwell
        (7, 0): "7.0",   # Volta
        (7, 5): None,    # Turing (13.x sí la soporta)
        (9, 0): None,    # Hopper
        None: None,      # sin NVML
    }.items():
        _force_cc(monkeypatch, cc)
        assert runtime_module._cuda_note() == expected, cc


def test_select_accel_source_stub_without_cuda_major(monkeypatch):
    # Fallback de 2ª pasada: fuente sin tag cuda_major (fixtures/legado)
    # sigue siendo seleccionable cuando no hay fuente del major requerido.
    _force_windows(monkeypatch)
    _force_cc(monkeypatch, (6, 1))
    monkeypatch.setattr(runtime_module, "ACCEL_SOURCES", {
        "nvidia": [{"id": "stub", "files": [
            {"url": "http://fake/b.zip", "sha256": "a" * 64, "platforms": ["win"]},
        ]}],
    })
    assert runtime_module._select_accel_source("nvidia")["id"] == "stub"


def test_platform_files_selects_current_platform(monkeypatch):
    # RT-14: el catálogo es compartido win+linux; cada plataforma ve solo
    # sus archives (.zip vs .tar.gz).
    _force_linux(monkeypatch)
    base_files = runtime_module._platform_files(
        runtime_module._select_base_source()["files"]
    )
    assert [f["url"].rsplit("/", 1)[-1] for f in base_files] == [
        "llama-b11349-bin-ubuntu-x64.tar.gz",
    ]
    accel = runtime_module._select_accel_source("nvidia")
    # Linux elige 12.8 (primera fuente): CUDA 13.4 exige driver >=590.
    assert accel["id"] == "official-cuda-12.8"
    accel_files = runtime_module._platform_files(accel["files"])
    assert [f["url"].rsplit("/", 1)[-1] for f in accel_files] == [
        "llama-b11349-bin-ubuntu-cuda-12.8-x64.tar.gz",
        "cudart-llama-b11349-bin-ubuntu-cuda-12.8-x64.tar.gz",
    ]

    _force_windows(monkeypatch)
    base_files = runtime_module._platform_files(
        runtime_module._select_base_source()["files"]
    )
    assert [f["url"].rsplit("/", 1)[-1] for f in base_files] == [
        "llama-b11349-bin-win-cpu-x64.zip",
    ]
    accel_files = runtime_module._platform_files(
        runtime_module._select_accel_source("nvidia")["files"]
    )
    assert len(accel_files) == 2
    assert all(f["url"].endswith(".zip") for f in accel_files)


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


def test_can_download_false_on_unsupported_platform(monkeypatch):
    _force_unsupported(monkeypatch)
    assert runtime_module.can_download() is False


def test_can_download_linux_with_base(monkeypatch):
    _force_linux(monkeypatch)
    assert runtime_module.can_download() is True


def test_platform_supported_matrix(monkeypatch):
    def force(system, machine):
        monkeypatch.setattr(runtime_module.platform, "system", lambda: system)
        monkeypatch.setattr(runtime_module.platform, "machine", lambda: machine)

    force("Windows", "AMD64")
    assert runtime_module.platform_supported() is True
    force("Linux", "x86_64")
    assert runtime_module.platform_supported() is True
    force("Linux", "aarch64")
    assert runtime_module.platform_supported() is False
    force("Darwin", "x86_64")
    assert runtime_module.platform_supported() is False


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


def test_status_unsupported_on_unsupported_platform(monkeypatch):
    _force_unsupported(monkeypatch)
    assert runtime_module.runtime_status()["state"] == "unsupported"


def test_status_missing_on_linux(monkeypatch):
    _force_linux(monkeypatch)
    st = runtime_module.runtime_status()
    assert st["state"] == "missing"


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
        json.dumps({"state": "ready", "build": "b11349", "source": "official"}),
        encoding="utf-8",
    )

    st = runtime_module.runtime_status()

    assert st["state"] == "ready"
    assert st["binary_path"] == str(d / runtime_module.BINARY_NAME)
    assert st["build"] == "b11349"
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


def test_status_outdated_with_previous_build(monkeypatch):
    # RT-12: el pin actual no está descargado pero queda una build anterior
    # gestionada en disco → estado "outdated" (no el "missing" genérico).
    _force_windows(monkeypatch)
    old = runtime_module.runtime_dir().parent / "llama.cpp-b11146"
    old.mkdir(parents=True)
    (old / "llama-server.exe").write_bytes(b"OLD")

    st = runtime_module.runtime_status()

    assert st["state"] == "outdated"
    assert st["previous_builds"] == [{"pin": "b11146", "size_mb": 0.0}]
    assert st["binary_path"] is None


def test_status_missing_without_previous_build(monkeypatch):
    # RT-12: sin build anterior el estado sigue siendo "missing" y
    # previous_builds queda vacío.
    _force_windows(monkeypatch)
    st = runtime_module.runtime_status()

    assert st["state"] == "missing"
    assert st["previous_builds"] == []


def test_status_ready_not_outdated_even_with_previous_build(monkeypatch):
    # RT-12: con el runtime del pin actual listo el estado es "ready" aunque
    # queden builds anteriores (keep_previous): no se confunde con outdated.
    _force_windows(monkeypatch)
    old = runtime_module.runtime_dir().parent / "llama.cpp-b11146"
    old.mkdir(parents=True)
    (old / "llama-server.exe").write_bytes(b"OLD")
    d = runtime_module.runtime_dir()
    d.mkdir(parents=True)
    (d / runtime_module.BINARY_NAME).write_bytes(b"EXE")
    (d / runtime_module.META_FILENAME).write_text(
        json.dumps({"state": "ready", "build": "b11349", "source": "official"}),
        encoding="utf-8",
    )

    st = runtime_module.runtime_status()

    assert st["state"] == "ready"
    assert st["previous_builds"] == []


# --------------------------------------------------------------------------
# resolve_binary — cascada experto → runtime → None
# --------------------------------------------------------------------------


def test_resolve_binary_expert_path_wins():
    expert = "C:\\expert\\llama-server.exe"
    assert runtime_module.resolve_binary(expert) == expert


def test_resolve_binary_none_on_unsupported_platform(monkeypatch):
    _force_unsupported(monkeypatch)
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


# --------------------------------------------------------------------------
# download_runtime — cleanup de huérfanos (un bump de pin no deja la vieja)
# --------------------------------------------------------------------------


async def test_download_cleanup_removes_orphaned_previous_build(monkeypatch, tmp_path):
    # Un bump de pin deja la dir vieja (llama.cpp-<old>) huérfana y duplica el
    # disco (~700 MB). Por defecto (keep_previous=False) el download la borra.
    _setup_download(monkeypatch, tmp_path, "cpu")
    old = tmp_path / "runtime" / "llama.cpp-b11146"
    old.mkdir(parents=True)
    (old / "llama-server.exe").write_bytes(b"OLD")
    (old / runtime_module.META_FILENAME).write_text(
        json.dumps({"state": "ready", "build": "b11146", "source": "official"}),
        encoding="utf-8",
    )
    bodies = _stub_sources(
        monkeypatch, _zip_bytes({"llama-server.exe": b"EXE", "llama.dll": b"LIB"}),
        None, "cpu",
    )
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=bodies[req.url]))

    st = await runtime_module.download_runtime()

    assert st["state"] == "ready"
    assert runtime_module.runtime_dir().exists()
    assert not old.exists()  # huérfano eliminado (reemplazo por defecto)


async def test_download_keep_previous_preserves_orphan(monkeypatch, tmp_path):
    # keep_previous=True conserva la build anterior (base del rollback futuro,
    # feature empresarial): se paga el doble de disco a cambio de poder volver.
    _setup_download(monkeypatch, tmp_path, "cpu")
    old = tmp_path / "runtime" / "llama.cpp-b11146"
    old.mkdir(parents=True)
    (old / "llama-server.exe").write_bytes(b"OLD")
    bodies = _stub_sources(
        monkeypatch, _zip_bytes({"llama-server.exe": b"EXE", "llama.dll": b"LIB"}),
        None, "cpu",
    )
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=bodies[req.url]))

    st = await runtime_module.download_runtime(keep_previous=True)

    assert st["state"] == "ready"
    assert old.exists()  # conservada para rollback
    assert (old / "llama-server.exe").read_bytes() == b"OLD"


async def test_download_accel_stage_cancelled_error_propagates(monkeypatch, tmp_path):
    # F-e3 (SEC-1.e): si el cliente corta la conexión en la etapa de
    # aceleración, el CancelledError debe propagarse — el viejo
    # `except BaseException` lo tragaba como "degradación a CPU" y dejaba
    # la task viva contra la cancelación cooperativa.
    _setup_download(monkeypatch, tmp_path, "nvidia")
    # 2 entradas en el zip base: _extract_zip descarta la "raíz" cuando el
    # archive tiene UNA sola entrada de tope (heurística de folder raíz).
    bodies = _stub_sources(
        monkeypatch, _zip_bytes({"llama-server.exe": b"EXE", "llama.dll": b"LIB"}),
        _zip_bytes({"ggml-cuda.dll": b"CUDA"}), "nvidia",
    )
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=bodies[req.url]))

    real_stage = runtime_module._download_stage

    async def stage_cancels_on_accel(files, work, on_progress, lo, hi):
        if lo > 0:
            raise asyncio.CancelledError()
        await real_stage(files, work, on_progress, lo, hi)

    monkeypatch.setattr(runtime_module, "_download_stage", stage_cancels_on_accel)

    with pytest.raises(asyncio.CancelledError):
        await runtime_module.download_runtime()

    assert runtime_module._downloading is False
    # La etapa base ya extrajo el binario ANTES de la de aceleración, así que
    # el runtime queda instalable y usable en CPU: runtime_status prioriza la
    # presencia del binario ("ready", accel=None) sobre el meta "error".
    st = runtime_module.runtime_status()
    assert st["state"] == "ready"
    assert st["accel"] is None
