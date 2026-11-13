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


def _force_driver(monkeypatch, major):
    """Fija el major del driver NVIDIA detectado (RT-15B). None simula sin
    NVML o sin dato legible."""
    monkeypatch.setattr(runtime_module, "_system_driver_major", lambda: major)


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


def test_extract_zip_single_file_flat(tmp_path):
    # Regresión del heurístico de root (RT-10 B1): un zip PLANO de UN solo
    # archivo no es una carpeta raíz. El heurístico antiguo veía un solo top
    # y se lo comía como prefix → no extraía nada.
    z = _make_zip(tmp_path, {"ggml-vulkan.dll": b"VK"})
    target = tmp_path / "out"
    target.mkdir()

    runtime_module._extract_zip(z, target)

    assert (target / "ggml-vulkan.dll").read_bytes() == b"VK"


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


def test_extract_targz_single_file_flat(tmp_path):
    # Mismo caso que test_extract_zip_single_file_flat en tar.gz.
    a = _make_targz(tmp_path, {"libggml-vulkan.so": (b"VK", 0o644)})
    target = tmp_path / "out"
    target.mkdir()

    runtime_module._extract_targz(a, target)

    assert (target / "libggml-vulkan.so").read_bytes() == b"VK"


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


def test_select_base_source_official_b12359_verifiable(monkeypatch):
    # Bump b12359: la fuente es la build oficial de la release (los archives
    # propios "glyvex" eran específicos de b11009).
    _force_windows(monkeypatch)
    source = runtime_module._select_base_source()
    files = runtime_module._platform_files(source["files"])
    assert source is not None
    assert source["id"] == "official"
    assert len(files) == 1
    assert all(len(f["sha256"]) == 64 for f in files)
    assert all("b12359" in f["url"] for f in files)
    assert all(f["url"].endswith(".zip") for f in files)


def test_select_accel_source_windows_default_cuda12(monkeypatch):
    # RT-15/RT-15B: sin NVML (ni arch ni driver) Windows elige por defecto el
    # bundle 12.x (win-cuda-12.4), que sí soporta Pascal; 13.x requiere arch
    # Blackwell o driver >= gate.
    _force_windows(monkeypatch)
    _force_cc(monkeypatch, None)
    _force_driver(monkeypatch, None)
    source = runtime_module._select_accel_source("nvidia")
    files = runtime_module._platform_files(source["files"])
    assert source is not None
    assert source["id"] == "official-cuda-12.4"
    assert source["cuda_major"] == 12
    assert all(len(f["sha256"]) == 64 for f in files)
    assert len(files) == 2
    assert all("b12359" in f["url"] for f in files)
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
    # Turing sm_75 con driver por debajo del gate (<590): 13.x no inicializaría
    # la GPU → 12.x. RT-15B: el driver decide aquí, no la arquitectura sola.
    _force_windows(monkeypatch)
    _force_cc(monkeypatch, (7, 5))
    _force_driver(monkeypatch, 570)
    assert runtime_module._select_accel_source("nvidia")["id"] == "official-cuda-12.4"


def test_select_accel_source_ampere_cuda13_with_modern_driver_windows(monkeypatch):
    # RT-15B: Ampere sm_86 (RTX 3090) con driver moderno (p. ej. 621) → CUDA
    # 13.5 preferido: la 30 serie está oficialmente soportada por 13.x y el
    # build 13.5 compila sm_86.
    _force_windows(monkeypatch)
    _force_cc(monkeypatch, (8, 6))
    _force_driver(monkeypatch, 621)
    source = runtime_module._select_accel_source("nvidia")
    assert source["id"] == "official"
    assert source["cuda_major"] == 13
    assert all("cuda-13.5" in f["url"] for f in runtime_module._platform_files(source["files"]))


def test_select_accel_source_ampere_cuda12_with_old_driver_windows(monkeypatch):
    # RT-15B: la misma Ampere con driver < gate → 12.4 (13.5 no arrancaría).
    _force_windows(monkeypatch)
    _force_cc(monkeypatch, (8, 6))
    _force_driver(monkeypatch, 570)
    assert runtime_module._select_accel_source("nvidia")["id"] == "official-cuda-12.4"


def test_select_accel_source_ampere_cuda13_with_modern_driver_linux(monkeypatch):
    # RT-15B en Linux: Ampere + driver r59x → 13.5 linux (.tar.gz de "official").
    _force_linux(monkeypatch)
    _force_cc(monkeypatch, (8, 6))
    _force_driver(monkeypatch, 591)
    source = runtime_module._select_accel_source("nvidia")
    assert source["id"] == "official"
    assert all("cuda-13.5" in f["url"] for f in runtime_module._platform_files(source["files"]))


def test_select_accel_source_ampere_cuda12_8_with_580_driver_linux(monkeypatch):
    # RT-15B en Linux: driver 580 queda POR DEBAJO del gate (el build 13.5 se
    # validó solo con r590+) → se baja CUDA 12.8, la ruta de mayor compatibilidad.
    _force_linux(monkeypatch)
    _force_cc(monkeypatch, (8, 6))
    _force_driver(monkeypatch, 580)
    assert runtime_module._select_accel_source("nvidia")["id"] == "official-cuda-12.8"


def test_select_accel_source_blackwell_uses_cuda13_windows(monkeypatch):
    # Blackwell sm_120 (cc 12.0): solo 13.x la compila → "official" (win 13.5).
    _force_windows(monkeypatch)
    _force_cc(monkeypatch, (12, 0))
    source = runtime_module._select_accel_source("nvidia")
    assert source["id"] == "official"
    assert source["cuda_major"] == 13
    assert all("cuda-13.5" in f["url"] for f in runtime_module._platform_files(source["files"]))


def test_select_accel_source_blackwell_uses_cuda13_linux(monkeypatch):
    # Blackwell en Linux → 13.x (los .tar.gz linux de "official").
    _force_linux(monkeypatch)
    _force_cc(monkeypatch, (10, 0))
    source = runtime_module._select_accel_source("nvidia")
    assert source["id"] == "official"
    assert all("cuda-13.5" in f["url"] for f in runtime_module._platform_files(source["files"]))


def test_required_cuda_major_by_architecture(monkeypatch):
    # RT-15: 13 solo con Blackwell (cc major >= 10) o arch < 7.5 → 12, en el
    # resto y sin NVML. Con driver ilegible (None) la preferencia RT-15B no
    # aplica: conducta legada intacta.
    _force_driver(monkeypatch, None)
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


def test_required_cuda_major_driver_gate(monkeypatch):
    # RT-15B: con driver legible, 13 se prefiere solo en el rango de
    # arquitectura que CUDA 13 soporta (7.5 ≤ cc < 10) Y con driver >= gate.
    cases = {
        ((6, 1), 621): 12,    # Pascal: driver alto NO la habilita (13 la tiró)
        ((7, 4), 621): 12,    # Volta: innegociable por arquitectura
        ((7, 5), 590): 13,    # Turing justo en el gate → 13
        ((7, 5), 589): 12,    # Turing bajo el gate → 12
        ((8, 6), 621): 13,    # Ampere (3090) con driver moderno → 13 preferido
        ((8, 6), 580): 12,    # Ampere con driver docs-mínimo pero bajo el gate
        ((8, 6), None): 12,   # driver ilegible → safe default 12
        ((9, 0), 591): 13,    # Hopper con driver r59x
        ((12, 0), None): 13,  # Blackwell no necesita driver legible: 12.x no la compila
    }
    for (cc, drv), expected in cases.items():
        _force_cc(monkeypatch, cc)
        _force_driver(monkeypatch, drv)
        assert runtime_module._required_cuda_major() == expected, (cc, drv)


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
    # sus archives (.zip vs .tar.gz). Sin NVML (RT-15/RT-15B safe default 12)
    # para que la fuente elegida sea determinista en cualquier máquina.
    _force_cc(monkeypatch, None)
    _force_driver(monkeypatch, None)
    _force_linux(monkeypatch)
    base_files = runtime_module._platform_files(
        runtime_module._select_base_source()["files"]
    )
    assert [f["url"].rsplit("/", 1)[-1] for f in base_files] == [
        "llama-b12359-bin-ubuntu-x64.tar.gz",
    ]
    accel = runtime_module._select_accel_source("nvidia")
    # Sin NVML → safe default 12 → en Linux la primera fuente verificable
    # (CUDA 13.5 exigiría arch ≥7.5 con driver >= gate).
    assert accel["id"] == "official-cuda-12.8"
    accel_files = runtime_module._platform_files(accel["files"])
    assert [f["url"].rsplit("/", 1)[-1] for f in accel_files] == [
        "llama-b12359-bin-ubuntu-cuda-12.8-x64.tar.gz",
        "cudart-llama-b12359-bin-ubuntu-cuda-12.8-x64.tar.gz",
    ]

    _force_windows(monkeypatch)
    base_files = runtime_module._platform_files(
        runtime_module._select_base_source()["files"]
    )
    assert [f["url"].rsplit("/", 1)[-1] for f in base_files] == [
        "llama-b12359-bin-win-cpu-x64.zip",
    ]
    accel_files = runtime_module._platform_files(
        runtime_module._select_accel_source("nvidia")["files"]
    )
    assert len(accel_files) == 2
    assert all(f["url"].endswith(".zip") for f in accel_files)


def test_select_accel_source_none_for_family_without_package(monkeypatch):
    # Sin paquete para la familia → None (runtime degradado a CPU). B1
    # (RT-10): AMD tiene paquete Vulkan en Windows (validado en hardware por
    # el protocolo) y también en Linux desde el bump b12359 (validación en
    # hardware pendiente); Intel y la familia "cpu" siguen sin paquete.
    _force_windows(monkeypatch)
    assert runtime_module._select_accel_source("intel") is None
    assert runtime_module._select_accel_source("cpu") is None
    _force_linux(monkeypatch)
    assert runtime_module._select_accel_source("intel") is None
    assert runtime_module._select_accel_source("cpu") is None


def test_select_accel_source_amd_vulkan(monkeypatch):
    # RT-10 B1: AMD ofrece la build Vulkan pinnada por el protocolo
    # PRUEBA-VULKAN-AMD-b11827.md (re-pineada sin cambios de ruta en b12359).
    # Sin tag cuda_major: se elige en la 1ª pasada para cualquier arquitectura.
    _force_windows(monkeypatch)
    _force_cc(monkeypatch, None)
    src = runtime_module._select_accel_source("amd")
    assert src["id"] == "official-vulkan"
    files = runtime_module._platform_files(src["files"])
    assert [f["url"].rsplit("/", 1)[-1] for f in files] == [
        "llama-b12359-bin-win-vulkan-x64.zip",
    ]
    assert all(f["sha256"] for f in files)


def test_select_accel_source_amd_vulkan_linux(monkeypatch):
    # Bump b12359: AMD en Linux pasa a tener paquete oficial ubuntu-vulkan
    # (matriz §9.3 del análisis de bump). Validación en hardware real aún
    # pendiente: el catálogo lo declara, los docs no lo anuncian hasta probarlo.
    _force_linux(monkeypatch)
    _force_cc(monkeypatch, None)
    src = runtime_module._select_accel_source("amd")
    assert src["id"] == "official-vulkan"
    files = runtime_module._platform_files(src["files"])
    assert [f["url"].rsplit("/", 1)[-1] for f in files] == [
        "llama-b12359-bin-ubuntu-vulkan-x64.tar.gz",
    ]
    assert [f["sha256"] for f in files] == [
        "19b60f00395a3f4954e46a5037695bc1d843404fc9d3124f82a612ecd8fbe9f0",
    ]


def test_select_accel_source_amd_choice_rocm(monkeypatch):
    # RT-10 B2: la elección explícita "rocm" devuelve el paquete ROCm 10.0
    # pinnado por la Parte 2 de PRUEBA-VULKAN-AMD-b11827.md. Sin elección el
    # default sigue siendo Vulkan (orden de ACCEL_SOURCES: cubre toda AMD).
    _force_windows(monkeypatch)
    _force_cc(monkeypatch, None)
    assert runtime_module._select_accel_source("amd")["id"] == "official-vulkan"
    src = runtime_module._select_accel_source("amd", "rocm")
    assert src["id"] == "official-rocm-10.0"
    files = runtime_module._platform_files(src["files"])
    assert [f["url"].rsplit("/", 1)[-1] for f in files] == [
        "llama-b12359-bin-win-rocm-10.0-x64.zip",
    ]
    assert [f["sha256"] for f in files] == [
        "d5021cebbd3bd50a7cab3b9f121a887e45df76c360ecfa927940aac3bb64a982",
    ]


def test_select_accel_source_amd_choice_rocm_linux(monkeypatch):
    # Bump b12359: la elección ROCm también existe en Linux (ubuntu-rocm).
    _force_linux(monkeypatch)
    _force_cc(monkeypatch, None)
    src = runtime_module._select_accel_source("amd", "rocm")
    assert src["id"] == "official-rocm-10.0"
    files = runtime_module._platform_files(src["files"])
    assert [f["url"].rsplit("/", 1)[-1] for f in files] == [
        "llama-b12359-bin-ubuntu-rocm-10.0-x64.tar.gz",
    ]
    assert [f["sha256"] for f in files] == [
        "2d934c367e613ec67a5f78dffa450ee30bc3a6024f559b70c207896ee4bc9108",
    ]


def test_select_accel_source_choice_without_package_degrades(monkeypatch):
    # RT-10 B2: una ruta elegida que no existe para la familia/plataforma →
    # None, sin fallback implícito a otra ruta (la UI explica el degradado).
    _force_windows(monkeypatch)
    _force_cc(monkeypatch, None)
    assert runtime_module._select_accel_source("amd", "cuda") is None
    _force_linux(monkeypatch)
    assert runtime_module._select_accel_source("amd", "sycl") is None


def test_accel_choices_lists_amd_paths(monkeypatch):
    # RT-10 B2 / bump b12359: AMD ofrece dos rutas seleccionables en Windows
    # Y en Linux (ubuntu-vulkan/rocm). Las familias sin rutas seleccionables
    # (NVIDIA se fija por cuda_major; Intel sin package) ofrecen lista vacía.
    _force_windows(monkeypatch)
    assert runtime_module.accel_choices("amd") == ["vulkan", "rocm"]
    assert runtime_module.accel_choices("nvidia") == []
    assert runtime_module.accel_choices("intel") == []
    _force_linux(monkeypatch)
    assert runtime_module.accel_choices("amd") == ["vulkan", "rocm"]


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
        json.dumps({"state": "ready", "build": "b11827", "source": "official"}),
        encoding="utf-8",
    )

    st = runtime_module.runtime_status()

    assert st["state"] == "ready"
    assert st["binary_path"] == str(d / runtime_module.BINARY_NAME)
    assert st["build"] == "b11827"
    assert st["source"] == "official"
    # RT-16: meta legacy sin accel_source ni accel_flavor → None (la UI pinta
    # el badge sin sufijo; no hay dato de qué aceleración quedó instalada).
    assert st["accel_flavor"] is None
    assert st["size_mb"] is not None


def test_status_accel_flavor_from_meta_and_fallbacks(monkeypatch):
    # RT-16: manda el flavor escrito al instalar; sin él se deriva del catálogo
    # por accel_source; fuente desconocida o ausente → None (badge sin sufijo).
    _force_windows(monkeypatch)
    d = runtime_module.runtime_dir()
    d.mkdir(parents=True)
    (d / runtime_module.BINARY_NAME).write_bytes(b"FAKE-EXE")
    meta_path = d / runtime_module.META_FILENAME

    def write_meta(meta: dict):
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        return runtime_module.runtime_status()["accel_flavor"]

    # Meta nueva: el campo guarda lo que se instaló, aunque el catálogo luego
    # cambie de idea (el pin no se resuelve al revén).
    assert write_meta({
        "state": "ready", "build": "b11827", "source": "official",
        "accel": "nvidia", "accel_source": "official-cuda-12.4",
        "accel_flavor": "cuda-12.4",
    }) == "cuda-12.4"
    # Meta legacy: se deriva del catálogo.
    assert write_meta({
        "state": "ready", "build": "b11827", "source": "official",
        "accel": "nvidia", "accel_source": "official-cuda-12.4",
    }) == "cuda-12.4"
    # Sin aceleración (degradado) pero con flavor "cpu" en la meta: pasa tal cual.
    assert write_meta({
        "state": "ready", "build": "b11827", "source": "official",
        "accel": None, "accel_source": None, "accel_flavor": "cpu",
    }) == "cpu"
    # Meta sin flavor ni accel_source, o con un id de un pin viejo: None.
    assert write_meta({"state": "ready", "build": "b11827", "source": "official"}) is None
    assert write_meta({
        "state": "ready", "build": "b11146", "source": "official",
        "accel": "nvidia", "accel_source": "official-cuda-11.8",
    }) is None


def test_accel_flavors_declared_in_catalog(monkeypatch):
    # RT-16: toda fuente del catálogo real declara su flavor estable — el
    # frontend nunca lo parsea del id (inestable entre pins).
    expected = {
        "official-cuda-12.4": "cuda-12.4",
        "official-cuda-12.8": "cuda-12.8",
        "official": "cuda-13.5",
        "official-vulkan": "vulkan",
        "official-rocm-10.0": "rocm",
    }
    for source_id, flavor in expected.items():
        assert runtime_module._accel_flavor_from_catalog(source_id) == flavor
    assert runtime_module._accel_flavor_from_catalog("inexistente") is None
    assert runtime_module._accel_flavor_from_catalog(None) is None


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
        json.dumps({"state": "ready", "build": "b11827", "source": "official"}),
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
# _gpu_via_sys_pci (bump b12359: familia AMD en Linux)
# --------------------------------------------------------------------------


def _make_sysfs_device(root, name, cls, vendor):
    d = root / name
    d.mkdir(parents=True)
    (d / "class").write_text(cls + "\n", encoding="ascii")
    (d / "vendor").write_text(vendor + "\n", encoding="ascii")


# Nombres de directorio SIN los dos puntos del formato sysfs real
# ("0000:03:00.0"): NTFS reserva ':' (streams de datos alternativos) y no
# permite crear esas carpetas. El backend no parsea el nombre — solo lee
# class/vendor — así que el fixture es equivalente en cualquier SO.


def _fake_sysfs(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime_module, "_SYS_PCI_ROOT", str(tmp_path))
    monkeypatch.setattr(runtime_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(runtime_module, "_gpu_via_pynvml", lambda: None)
    monkeypatch.setattr(runtime_module, "_gpu_via_video_controller", lambda: None)


def test_gpu_via_sys_pci_detects_amd_display(monkeypatch, tmp_path):
    _fake_sysfs(monkeypatch, tmp_path)
    _make_sysfs_device(tmp_path, "0000-03-00.0", "0x030000", "0x1002")  # AMD VGA
    _make_sysfs_device(tmp_path, "0000-01-00.0", "0x010601", "0x1002")  # SATA AMD (no display)
    assert runtime_module._gpu_via_sys_pci() == "AMD"
    assert runtime_module.detect_gpu_family() == "amd"


def test_gpu_via_sys_pci_ignores_non_display_and_unknown_vendor(monkeypatch, tmp_path):
    _fake_sysfs(monkeypatch, tmp_path)
    _make_sysfs_device(tmp_path, "0000-00-00.0", "0x060100", "0x8086")  # ISA bridge
    _make_sysfs_device(tmp_path, "0000-04-00.0", "0x030000", "0x1234")  # display unknown
    assert runtime_module._gpu_via_sys_pci() is None
    assert runtime_module.detect_gpu_family() == "cpu"


def test_gpu_via_sys_pci_missing_root_or_windows_is_none(monkeypatch, tmp_path):
    _fake_sysfs(monkeypatch, tmp_path / "inexistente")
    assert runtime_module._gpu_via_sys_pci() is None
    monkeypatch.setattr(runtime_module.platform, "system", lambda: "Windows")
    assert runtime_module._gpu_via_sys_pci() is None


# --------------------------------------------------------------------------
# download_runtime — 2 etapas (base + aceleración) con degradación
# --------------------------------------------------------------------------


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _stub_sources(monkeypatch, base_body: bytes, accel_body: bytes | None, family: str,
                  accel_tag: str | None = None):
    """BASE/ACCEL con URLs fake + sha256 reales de los bodies en memoria.
    accel_tag (RT-10 B2): tag "accel" de la fuente fake para probar elecciones
    explícitas vulkan/rocm; sin tag el default del catálogo la ignora."""
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
            "accel": accel_tag,
            "flavor": "fake-flavor",
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
    # RT-16: la meta registra el flavor declarado por la fuente.
    assert meta["accel_flavor"] == "fake-flavor"
    assert st["accel_flavor"] == "fake-flavor"
    assert set(meta["files"]) == {"base.zip", "accel.zip"}


async def test_download_two_stage_with_accel_amd(monkeypatch, tmp_path):
    # RT-10 B1: familia amd dispara la etapa 2 con el paquete Vulkan (mismo
    # flujo que CUDA): la meta registra accel="amd" y la dll extraida.
    _setup_download(monkeypatch, tmp_path, "amd")
    bodies = _stub_sources(
        monkeypatch,
        _zip_bytes({"llama-server.exe": b"EXE", "llama.dll": b"LIB"}),
        _zip_bytes({"ggml-vulkan.dll": b"VK"}),
        "amd",
    )
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=bodies[req.url]))

    st = await runtime_module.download_runtime()

    assert st["state"] == "ready"
    assert st["accel"] == "amd"
    assert st["accel_error"] is None
    d = runtime_module.runtime_dir()
    assert (d / "llama-server.exe").read_bytes() == b"EXE"
    assert (d / "ggml-vulkan.dll").read_bytes() == b"VK"
    meta = json.loads((d / runtime_module.META_FILENAME).read_text(encoding="utf-8"))
    assert meta["accel"] == "amd"
    assert meta["accel_source"] == "fake-accel"


async def test_download_two_stage_amd_rocm_choice(monkeypatch, tmp_path):
    # RT-10 B2: la elección explícita de ROCm descarga la etapa 2 con el
    # paquete ROCm (mismo flujo que Vulkan/CUDA) y la meta registra la fuente
    # elegida; la UI lo lee vía status.accel_source.
    _setup_download(monkeypatch, tmp_path, "amd")
    bodies = _stub_sources(
        monkeypatch,
        _zip_bytes({"llama-server.exe": b"EXE", "llama.dll": b"LIB"}),
        _zip_bytes({"ggml-hip.dll": b"HIP", "rocblas.dll": b"RB"}),
        "amd",
        accel_tag="rocm",
    )
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=bodies[req.url]))

    st = await runtime_module.download_runtime(accel_choice="rocm")

    assert st["state"] == "ready"
    assert st["accel"] == "amd"
    assert st["accel_source"] == "fake-accel"
    assert st["accel_error"] is None
    d = runtime_module.runtime_dir()
    assert (d / "llama-server.exe").read_bytes() == b"EXE"
    assert (d / "ggml-hip.dll").read_bytes() == b"HIP"
    meta = json.loads((d / runtime_module.META_FILENAME).read_text(encoding="utf-8"))
    assert meta["accel_source"] == "fake-accel"


async def test_download_choice_unavailable_degrades_with_error(monkeypatch, tmp_path):
    # RT-10 B2: elegir una ruta sin paquete para la familia deja el runtime
    # operativo en CPU base con accel_error explícito (no cae silenciosamente
    # a otra ruta): el usuario eligió y debe ver por qué no se aplicó.
    _setup_download(monkeypatch, tmp_path, "amd")
    bodies = _stub_sources(
        monkeypatch,
        _zip_bytes({"llama-server.exe": b"EXE", "llama.dll": b"LIB"}),
        None,
        "amd",
    )
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=bodies[req.url]))

    st = await runtime_module.download_runtime(accel_choice="rocm")

    assert st["state"] == "ready"
    assert st["accel"] is None
    assert st["accel_source"] is None
    # RT-16: un runtime degradado registra flavor "cpu".
    assert st["accel_flavor"] == "cpu"
    assert "rocm" in (st["accel_error"] or "")
    d = runtime_module.runtime_dir()
    assert (d / "llama-server.exe").read_bytes() == b"EXE"
    meta = json.loads((d / runtime_module.META_FILENAME).read_text(encoding="utf-8"))
    assert "rocm" in (meta["accel_error"] or "")
    assert meta["accel_flavor"] == "cpu"


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
    assert st["accel_flavor"] == "cpu"  # RT-16: degradado → flavor "cpu"
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
