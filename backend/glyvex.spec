# -*- mode: python ; coding: utf-8 -*-
# glyvex.spec — Sidecar PyInstaller para el bundle de Tauri (T-1, Fase 1).
#
# Uso: `pyinstaller glyvex.spec` desde backend/ (build.ps1 lo automatiza).
# Salida: dist/glyvex-backend/ (onedir) — glyvex-backend.exe + _internal/ +
# los recursos de datas/ (frontend/dist y data/templates).
#
# onedir (no onefile): arranque sin extracción a temp, más simple de diagnosticar
# y el Tauri sidecar puede apuntar directo al exe.
#
# console=False desde el instalador NSIS (T5): stdout/stderr los redirige el
# runtime hook glyvex_console_hook.py a DATA_DIR/logs/sidecar-console.log.
#
# Exclusiones: la cadena nativa de faster-whisper (STT motor `whisper`) NO va
# en el bundle — ver requirements-optional.txt. El STT de la app empaquetada
# usa whisper.cpp binario (Fase 2 del plan Tauri); hasta ahí el motor
# `whisper` reporta "no instalado" (probe de stt.py:110).

import sys

datas = [
    # El backend sirve el SPA: el WebView2 de Tauri carga http://127.0.0.1:7981.
    ("../frontend/dist", "frontend/dist"),
    # Semilla de templates de hardware (database.py / launcher.py).
    ("../data/templates/hw_templates.json", "data/templates"),
    # Sets versionados de benchmark (benchmark.py: PROMPTS_DIR = BUNDLE_DIR/
    # "backend"/"prompts"). Sin esto el step 2 del Benchmark queda vacío en
    # la app instalada: /api/benchmark/sets devuelve [].
    ("./prompts", "backend/prompts"),
]

hiddenimports = [
    # uvicorn resuelve loop/protocol/lifespan dinámicamente por nombre
    # (import_from_string en uvicorn/config.py).
    "uvicorn.loops.asyncio",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    # SQLAlchemy resuelve el dialecto por importlib (create_async_engine
    # "sqlite+aiosqlite://", database.py:57).
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.sqlite.aiosqlite",
    # El paquete aiosqlite solo se importa dinámicamente dentro de
    # import_dbapi() del dialecto de SQLAlchemy: PyInstaller no lo ve.
    "aiosqlite",
    "aiosqlite.core",
    "aiosqlite.dbapi",
    "aiosqlite.parity",
]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["glyvex_console_hook.py"],
    excludes=[
        # Cadena nativa de STT (faster-whisper): fuera del bundle por diseño.
        "faster_whisper",
        "ctranslate2",
        "onnxruntime",
        "onnx",
        "av",
        "tokenizers",
        "huggingface_hub",
        "tqdm",
        "flatbuffers",
        "protobuf",
        # gguf + numpy (~55 MB: numpy.libs, primp.pyd, DLLs en raiz): solo el
        # FALLBACK lento de read_gguf_metadata lo usa; el parser rapido de
        # models.py (puro, sin numpy) cubre los headers reales. Si el fallback
        # no encuentra la libreria devuelve _metadata_error(...) sin romper.
        "gguf",
        "numpy",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="glyvex-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="../assets/glyvex.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="glyvex-backend",
)
