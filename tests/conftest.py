"""
conftest.py — fixtures globales para toda la suite de Glyvex-AI-Suite.

Los módulos de backend/ son archivos sueltos (no un paquete instalable) que
se importan entre sí por nombre plano (`from config import config`, etc.),
asumiendo que `backend/` está en sys.path — así es como uvicorn los corre en
producción (`cd backend && uvicorn main:app`). Para que estos mismos imports
funcionen bajo pytest, insertamos `backend/` en sys.path acá arriba, antes de
importar nada.

Aislamiento entre tests: config.py, models.py, launcher.py, metrics.py y
database.py guardan su estado en instancias singleton a nivel de módulo
(`config`, `inventory`, `manager`, `_engine`...), con rutas de archivo fijas
basadas en la ubicación del proyecto real. La fixture `_isolated_state`
(autouse) redirige esas rutas a `tmp_path`, levanta una SQLite **en memoria**
por test y resetea el estado en memoria antes de cada test, así ningún test
lee ni escribe sobre `data/` (ni sobre `data/glyvex.db`) del proyecto real ni
depende de lo que dejó un test anterior.

Templates: desde la Conversación C viven en SQLite. La fixture siembra la DB
de test con `database._sync_builtin_templates()`, que lee el JSON real
`data/templates/hw_templates.json` en modo solo lectura — los 5 templates
predefinidos quedan disponibles para los tests sin tocar nada en disco.

La única excepción intencional es `benchmark.prompt_sets`: se deja apuntando
a la carpeta real `backend/prompts/`, porque varios tests (test_benchmark.py)
necesitan validar el contenido real de esos 6 sets. Los tests que necesitan
correr un benchmark rápido y controlado inyectan un set efímero directamente
en memoria (fixture `custom_prompt_set`) en vez de tocar el disco.
"""

from __future__ import annotations

import asyncio
import itertools
import platform
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
BACKEND_DIR = PROJECT_ROOT / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from sqlalchemy import delete as sa_delete  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import attachments as attachments_module  # noqa: E402
import benchmark as benchmark_module  # noqa: E402
import chat as chat_module  # noqa: E402,F401  (importado para smoke-test de carga; no se referencia directo)
import config as config_module  # noqa: E402
import database as database_module  # noqa: E402
import launcher as launcher_module  # noqa: E402
import llm_metrics as llm_metrics_module  # noqa: E402
import main as main_module  # noqa: E402
import metrics as metrics_module  # noqa: E402
import models as models_module  # noqa: E402

from mock_llm_server import build_mock_app  # noqa: E402

MOCK_SERVER_PORT = 18080

# --------------------------------------------------------------------------
# App / cliente HTTP de test (httpx contra el ASGI app, sin servidor real)
# --------------------------------------------------------------------------


@pytest.fixture
def app():
    return main_module.app


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


# --------------------------------------------------------------------------
# Aislamiento de estado global (autouse) — ver docstring del módulo
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _isolated_state(tmp_path):
    # -- database.py: SQLite en memoria, no tocar data/glyvex.db real ----
    # aiosqlite + ":memory:" usa StaticPool, así que todas las sesiones
    # comparten la misma conexión (y por lo tanto la misma DB) durante el
    # test; al cerrar el engine la base desaparece.
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    test_session_factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    database_module._engine = test_engine
    database_module._session_factory = test_session_factory
    async with test_engine.begin() as conn:
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.run_sync(database_module.Base.metadata.create_all)

    # Semilla de templates (lee el JSON real en modo solo lectura y los
    # inserta en la DB en memoria de este test). _sync_builtin_templates
    # migra también entradas custom del JSON histórico (comportamiento
    # productivo); en el test eso importa el estado de runtime del usuario,
    # así que se quedan solo los predefinidos para que la suite sea
    # determinística sin importar lo que haya en data/templates/.
    await database_module._sync_builtin_templates()
    async with test_engine.begin() as conn:
        await conn.execute(
            sa_delete(database_module.HWTemplateRow).where(
                database_module.HWTemplateRow.builtin.is_(False)
            )
        )

    # -- config.py -----------------------------------------------------
    config_module.config.path = tmp_path / "config.json"
    config_module.config._data = config_module.config.defaults()

    # -- models.py: inventario vacío, cache en tmp ----------------------
    models_module.inventory.path = tmp_path / "models.json"
    models_module.inventory._by_id = {}
    models_module.inventory._loaded = True

    # -- models.py: cache de metadata + warmup ---------------------------
    # La cache está keyed por (mtime, size) de tmp_path, así que no debería
    # colar metadata entre tests, pero se limpia igualmente para que un test
    # que la inspeccione no vea basura de uno anterior. El warmup que el scan
    # de otro test haya dejado colgado se cancela para no operar sobre la DB
    # nueva de ESTE test.
    models_module._METADATA_CACHE.clear()
    models_module._metadata_locks.clear()
    if models_module._warmup_task is not None and not models_module._warmup_task.done():
        models_module._warmup_task.cancel()
    models_module._warmup_task = None

    # -- launcher.py: sin procesos activos entre tests -------------------
    launcher_module.manager._info = {}
    launcher_module.manager._handles = {}
    launcher_module.manager._log_buffers = {}
    launcher_module.manager._log_subscribers = {}
    launcher_module.manager._start_monotonic = {}
    launcher_module.manager._stopping = set()

    # -- launcher.py: cache del probe de binario (clave mtime+size) -------
    # Cada test usa un "binario" fake en su propio tmp_path, pero limpiar la
    # cache evita que un probe caro de un test anterior se reutilice si por
    # casualidad coinciden clave (mtime+size) o si un test la inspecciona.
    launcher_module._BINARY_CACHE.clear()

    # -- launcher.py: escritura de logs a tmp -----------------------------
    # El default de log_file es relativo al DATA_DIR de la instancia
    # ("data/logs/llama-server.log", con el prefijo "data/" aceptado por
    # compatibilidad); los tests de subprocess lanzan fake_llama_binary y
    # _pump_output volca su stdout a disco, así que con DATA_DIR/LOGS_DIR
    # apuntando a tmp la suite deja de appendear el data/logs/llama-server.log
    # del proyecto real.
    #
    # IMPORTANTE: resolve_log_path() resuelve y valida contra el DATA_DIR de
    # nivel de módulo, así que es ESE el nombre que hay que redirigir. Si se
    # redirigiera solo BASE_DIR (como antes), el patch dejaría de tener efecto
    # en silencio y los tests escribirían sobre el data/ real.
    launcher_module.DATA_DIR = tmp_path
    launcher_module.LOGS_DIR = tmp_path / "logs"

    # -- launcher.py: templates -> ya no usan disco, van a la DB aislada
    # (self.path se mantiene solo por compatibilidad; lo apuntamos a tmp
    #  para que ningún código legacy pueda escribir sobre el JSON real)
    launcher_module.templates.path = tmp_path / "hw_templates.json"

    # -- attachments.py: imágenes subidas a tmp ---------------------------
    # Ningún test sube adjuntos hoy, pero ATTACHMENTS_DIR apuntaba al
    # data/attachments real: el primer test que lo hiciera escribiría ahí y
    # dejaría basura fuera de tmp_path. Se redirige preventivamente.
    attachments_module.ATTACHMENTS_DIR = tmp_path / "attachments"

    # -- models.py: versión de esquema del cache a tmp ---------------------
    # _read/_write_cache_schema_version toman la ruta como ARGUMENTO POR
    # DEFECTO (path: Path = SCHEMA_VERSION_PATH), evaluado al importar: por
    # eso no alcanza con reasignar models_module.SCHEMA_VERSION_PATH, hay que
    # reescribir también el default de ambas funciones.
    models_module.SCHEMA_VERSION_PATH = tmp_path / "models_schema_version.json"
    for _fn in (models_module._read_cache_schema_version,
                models_module._write_cache_schema_version):
        _fn.__defaults__ = tuple(
            tmp_path / "models_schema_version.json" if isinstance(d, Path) else d
            for d in (_fn.__defaults__ or ())
        )

    # -- benchmark.py: runs a un directorio temporal ---------------------
    # (prompt_sets NO se toca: apunta a backend/prompts/ real a propósito)
    benchmark_module.RUNS_DIR = tmp_path / "benchmarks"

    # -- metrics.py: historial limpio y store de métricas en tmp ------------
    # El lifespan no corre con ASGITransport, así que el servicio de fondo no
    # arranca en los tests; el store se abre acá para probar /series y /query
    # sin tocar data/metrics.db.
    metrics_module.manager.history.clear()
    metrics_module.manager.configure_store(tmp_path / "metrics.db")

    # -- llm_metrics.py: sin buffers ni tasas de un test anterior -----------
    llm_metrics_module.manager.buffers.clear()
    llm_metrics_module.manager.rates = llm_metrics_module.RateTracker()
    llm_metrics_module.manager._aggregators.clear()
    llm_metrics_module.manager._models.clear()
    llm_metrics_module.manager._last_tg.clear()
    llm_metrics_module.manager._slots_status.clear()
    llm_metrics_module.manager._first_seen.clear()
    llm_metrics_module.manager._last_seen.clear()

    yield

    # -- teardown: no dejar procesos/tasks/engines colgando entre tests ----
    for process in list(launcher_module.manager._handles.values()):
        try:
            process.kill()
        except (ProcessLookupError, OSError):
            pass
    await launcher_module.manager.shutdown()

    await llm_metrics_module.manager.stop()
    await metrics_module.manager.stop()

    await test_engine.dispose()
    database_module._engine = None
    database_module._session_factory = None


# --------------------------------------------------------------------------
# tmp_config_dir — expone el directorio donde quedó redirigido config.json
# --------------------------------------------------------------------------


@pytest.fixture
def tmp_config_dir(tmp_path):
    """
    El autouse _isolated_state ya redirigió config.path a tmp_path/config.json.
    Este fixture solo expone ese directorio para que los tests puedan leer el
    archivo crudo del disco (ej. test_config_persists_to_disk).
    """
    return tmp_path


# --------------------------------------------------------------------------
# sample_model_dir — carpeta con 3 GGUF de 0 bytes para tests de M1
# --------------------------------------------------------------------------


@pytest.fixture
def sample_model_dir(tmp_path):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "Qwen3.8-27B-UD-Q4_K_XL.gguf").touch()
    (model_dir / "Llama-3.1-8B-Q4_K_M.gguf").touch()
    (model_dir / "Qwen3-VL-7B-mmproj.gguf").touch()
    return model_dir


# --------------------------------------------------------------------------
# fake_llama_binary — script shell inofensivo para tests de subprocess real
# --------------------------------------------------------------------------


@pytest.fixture
def fake_llama_binary(tmp_path):
    """
    "Binario" de mentira para probar el ciclo de vida real de un subprocess
    (asyncio.create_subprocess_exec) sin depender de un llama-server de
    verdad. Ignora todos los argv que le pase build_llama_server_command
    (son posicionales/flags que un script de shell/batch simplemente no
    lee), imprime un par de líneas (para test_logs_not_empty) y se queda
    "corriendo" para poder probar stop() sobre un proceso todavía vivo.

    Multiplataforma:
    - Windows: genera un .bat. Windows ejecuta .bat/.cmd directamente vía
      CreateProcess (resolución de asociación de extensión a nivel de OS)
      sin necesitar shell=True, así que asyncio.create_subprocess_exec lo
      puede lanzar igual que un binario.
    - Linux/Mac: script POSIX con shebang (#!/bin/sh), que el kernel
      ejecuta directamente al tener el bit +x.

    En ambos casos, el "dormir" se implementa como un busy-loop DENTRO del
    propio proceso (`while true; do :; done` / `:loop \n goto loop`) en vez
    de invocar `sleep`/`ping` como binario externo — así el proceso que
    lanzamos NO tiene hijos, y matarlo (terminate/kill) lo para por completo
    sin dejar nada huérfano corriendo de fondo.
    """
    if platform.system() == "Windows":
        script = tmp_path / "fake_llama_server.bat"
        script.write_text(
            "@echo off\n"
            "echo fake llama-server: starting\n"
            "echo fake llama-server: listening\n"
            ":loop\n"
            "goto loop\n",
            encoding="utf-8",
        )
        return str(script)

    script = tmp_path / "fake_llama_server.sh"
    script.write_text(
        "#!/bin/sh\n"
        "echo 'fake llama-server: starting'\n"
        "echo 'fake llama-server: listening'\n"
        "while true; do :; done\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return str(script)


# --------------------------------------------------------------------------
# make_probeable_binary / probeable_binary — "binarios" que responden
# --help y --version (para probe_binary) y hacen busy-loop con otros argv
# --------------------------------------------------------------------------


def _write_probeable_script(tmp_path: Path, name: str, help_lines: list[str], version_lines: list[str]) -> str:
    """
    Escrib un "binario" fake con dos comportamientos:
    - argv[1] == "--help"    -> imprime help_lines y sale.
    - argv[1] == "--version" -> imprime version_lines y sale.
    - cualquier otro argv    -> busy-loop (como fake_llama_binary), para que
      el launcher pueda lanzarlo y dejarlo "corriendo" si el test lo necesita.

    El texto de --help define qué flags esa "build" soporta: probe_binary
    extrae con regex todos los tokens --xxx de la salida. Multiplataforma
    como fake_llama_binary (.bat en Windows, sh con shebang en POSIX).
    """
    if platform.system() == "Windows":
        lines = ["@echo off", 'if "%~1"=="--help" (']
        lines += [f"  echo {h}" for h in help_lines]
        lines += ["  exit /b 0", ")", 'if "%~1"=="--version" (']
        lines += [f"  echo {v}" for v in version_lines]
        lines += ["  exit /b 0", ")",
                  "echo probeable fake llama-server: starting", ":loop", "goto loop"]
        script = tmp_path / name
        script.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
        return str(script)

    lines = ["#!/bin/sh", 'case "$1" in', "  --help)"]
    lines += [f"    echo '{h}'" for h in help_lines]
    lines += ["    exit 0 ;;", "  --version)"]
    lines += [f"    echo '{v}'" for v in version_lines]
    lines += ["    exit 0 ;;", "esac",
              "echo 'probeable fake llama-server: starting'", "while true; do :; done"]
    script = tmp_path / name
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    script.chmod(0o755)
    return str(script)


@pytest.fixture
def make_probeable_binary(tmp_path):
    """
    Fábrica de "binarios" probeables: cada llamada devuelve un archivo nuevo
    en tmp_path que responde --help/--version con el texto que el test defina
    (version_lines default: una línea con build b11009) y hace busy-loop con
    cualquier otro argv. Permite simular builds con distintos sets de flags
    (p. ej. una vieja sin --ctx-checkpoints, o una que le falta un flag
    crítico) sin depender de un llama.cpp real.
    """
    counter = itertools.count()

    def _make(
        help_lines: list[str],
        version_lines: tuple[str, ...] = ("llama.cpp version b11009",),
    ) -> str:
        n = next(counter)
        # La extensión importa: Windows ejecuta .bat vía CreateProcess
        # (asociación de extensión a nivel de OS) sin shell=True, y POSIX
        # necesita el shebang con bit +x.
        suffix = ".bat" if platform.system() == "Windows" else ".sh"
        return _write_probeable_script(
            tmp_path, f"probeable_{n}{suffix}", list(help_lines), list(version_lines)
        )

    return _make


@pytest.fixture
def probeable_binary(make_probeable_binary):
    """
    "Build b11009 clásica": trae los flags siempre presentes del comando
    (model, host, port, ctx-size, batch, gpu-layers, cache-type-k/v,
    flash-attn, metrics, jinja, chat-template-kwargs, reasoning, sampling,
    parallel, mlock, no-mmap, api-key, verbosity, fit) pero NO los de
    memoria de la 11003 (--ctx-checkpoints, --checkpoint-min-step,
    --cache-ram, --fit-target, --kv-unified, --no-reasoning-preserve).
    El probe real la detecta y el filtrado debe descartar esos flags nuevos
    en vez de lanzar un comando que la build vieja rechazaría.
    """
    # Algunas líneas llevan la ayuda oficial tras un salto de 2+ espacios
    # (formato llama-gen-docs) para probar el parseo de flag_help; el resto
    # quedan sin descripción. ASCII y sin paréntesis: el .bat de Windows no
    # tolera paréntesis dentro del bloque `if (...)`.
    return make_probeable_binary([
        "llama.cpp llama-server — usage",
        "--model PATH                       ruta del modelo GGUF",
        "--host 127.0.0.1",
        "--port 8080",
        "--ctx-size 4096                   tamano de la ventana de contexto",
        "--fit on                          auto-fit contexto y KV cache en VRAM",
        "--batch-size 512",
        "--ubatch-size 512",
        "--n-gpu-layers -1",
        "--cache-type-k q8_0",
        "--cache-type-v q8_0",
        "--flash-attn on",
        "--metrics",
        "--jinja",
        "--chat-template-kwargs JSON",
        "--reasoning on",
        "--reasoning-effort medium",
        "--reasoning-budget 8192",
        "--mlock",
        "--no-mmap",
        "--temp 0.7",
        "--top-p 0.8",
        "--top-k 20",
        "--min-p 0.0",
        "--presence-penalty 0.3",
        "--repeat-penalty 1.1",
        "--parallel 1",
        "--api-key KEY",
        "--verbosity 2",
    ])


# --------------------------------------------------------------------------
# mock_llama_server — servidor OpenAI-compatible real en :18080
# --------------------------------------------------------------------------


@pytest.fixture
async def mock_llama_server():
    app_instance = build_mock_app()
    config = uvicorn.Config(
        app_instance, host="127.0.0.1", port=MOCK_SERVER_PORT, log_level="critical"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())

    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.01)

    yield f"http://127.0.0.1:{MOCK_SERVER_PORT}"

    server.should_exit = True
    await task


# --------------------------------------------------------------------------
# custom_prompt_set — set de benchmark efímero, inyectado solo en memoria
# --------------------------------------------------------------------------


@pytest.fixture
async def custom_prompt_set():
    """
    Inyecta un PromptSet de 1 prompt directamente en benchmark.prompt_sets
    (sin tocar disco), pensado para correr contra mock_llama_server, cuya
    respuesta siempre es literalmente "Hola mundo". El prompt pide
    expected_keywords=["Hola", "cache"] a propósito: "Hola" va a aparecer
    (keywords_found) y "cache" no (keywords_missing), cubriendo ambos
    escenarios en un solo resultado.

    Llama a ensure_loaded() antes de inyectar el set efímero para garantizar
    que los 6 sets reales sigan disponibles sin importar el orden en que
    pytest corra los tests (si este fixture corriera antes que cualquier
    test que dispare la carga real, sin este await los sets reales nunca se
    cargarían para el resto de la sesión).
    """
    await benchmark_module.prompt_sets.ensure_loaded()

    test_set = benchmark_module.PromptSet(
        id="__test_set__",
        name="Test Set",
        description="Set efímero para tests, no persiste en disco",
        icon="🧪",
        prompts=[
            benchmark_module.PromptItem(
                id="t1",
                title="Prompt de test",
                prompt="Decime hola",
                expected_keywords=["Hola", "cache"],
                category="test",
                difficulty="easy",
            )
        ],
    )
    benchmark_module.prompt_sets._sets["__test_set__"] = test_set
    yield "__test_set__"
    benchmark_module.prompt_sets._sets.pop("__test_set__", None)
