"""Tests de load_stats.py — A2F-2: parser tolerante del log de carga de
llama-server + integración de la card de carga en el launcher.

Los fixtures de log están escritos a mano a partir de los formatos que
llama.cpp imprime con --verbosity 4 en dos eras de build (llm_load_* vs
load:). No son volcados reales: el contrato de parseo se fija aquí; la
validación contra builds reales ocurre al lanzar modelos en la máquina
del usuario.
"""

import os
import threading
from collections import deque

import pytest

import launcher as launcher_module
from load_stats import parse_load_logs, process_rss_mib
from helpers import scan_and_wait, wait_until

# --------------------------------------------------------------------------
# Parser unitario (sin subprocess, sin red): fixtures de ambas eras
# --------------------------------------------------------------------------

# Build moderna (~b11146+): prefijos load: / ggml_*, offload por dispositivo.
MODERN_LOG = [
    "build: ggml CUDA build",  # no es versión: no debe parsearse como build
    "ggmlCUDA0:Initializing UI failed: call to 'cudaDeviceHCA' failed",
    "load: build = 11827 (a1b2c3d4)",
    "load: loading model 'D:/models/qwen/Qwen3-8B-Q5_K_M.gguf'",
    "llm_load_print_meta: model type = qwen",
    "load: view layers = 48",
    "load: number of view layers = 48",
    "load: number of layers = 63",
    " Device 0: NVIDIA GeForce RTX 3060: ; compute = 8.6",
    " Device 1: NVIDIA GeForce RTX 4090: ; compute = 8.9",
    "load: offloaded 40/63 layers to GPU device 0",
    "load: offloaded 12/63 layers to GPU device 1",
    "load: offloaded 11/63 layers to CPU",
    "kv_unified: size = 900.00 MiB, n_ctx = 8192",
    "llama_context: n_ctx                 = 8192",
    "llama_context: n_ctx_seq             = 8192",
    "load: KV buffer size = 150.00 MiB",
    "( size: 240.00 MiB, n_layers: 25 )",
    "load: compute buffer size = 1410.55 MiB",
    "load: CUDA0[0] buffer size = 795.85 MiB",
    "load: CUDA1[0] buffer size = 359.71 MiB",
    "load: CPU buffer size = 290.11 MiB",
    "load: model load time = 15210.55 ms",
    "main: server is listening on http://127.0.0.1:8080 - health endpoint: /health",
]


def test_parse_modern_format():
    stats = parse_load_logs(MODERN_LOG)

    assert stats.build == "b11827"
    # solo el nombre del archivo, no la ruta
    assert stats.model_filename == "Qwen3-8B-Q5_K_M.gguf"
    # "view layers" no cuenta como total; el total real es 63
    assert stats.total_layers == 63
    assert stats.gpu_layers == 52
    # 63 - 52: coincide con el "offloaded 11/63 layers to CPU" explícito
    assert stats.cpu_layers == 11

    # offload por dispositivo, ordenado por índice, con el nombre anunciado
    assert [(d.device_index, d.device_name, d.layers) for d in stats.devices] == [
        (0, "NVIDIA GeForce RTX 3060", 40),
        (1, "NVIDIA GeForce RTX 4090", 12),
    ]

    # inline "( size: ..., n_layers: ...)" tiene prioridad sobre los
    # candidates "buffer size"; estos últimos solo se usan si no hay inline.
    assert stats.kv_cache_mib == 240.0
    assert stats.kv_layers == 25

    # el contexto lo declara llama_context, no las variantes por-slot
    assert stats.context_tokens == 8192

    assert stats.compute_mib == 1410.55
    # "CPU buffer size" sí; "CUDA0[0] buffer size" son buffers de pesos y no
    # deben contaminar ni el KV ni el CPU buffer.
    assert stats.cpu_buffer_mib == 290.11
    assert stats.log_load_seconds == pytest.approx(15.21055)
    assert stats.notes == []

    # lo medido lo rellena el launcher, no el parser
    assert stats.ram_delta_mib is None
    assert stats.gpus == []
    assert stats.wall_load_seconds is None


# Build antigua (~b11003–b11145): prefijos llm_load_*, offload sin dispositivo.
LEGACY_LOG = [
    "version 1.0.0 (b11009)",
    "llama_model_loader: loaded meta data with 19 key-value pairs",
    "llama_model_load: loading model 'D:\\models\\llama3-8b.Q5_K_M.gguf'",
    "llm_load_print_meta: model type = llama",
    "llm_load_print_meta: n_layer = 35",
    "llm_load_tensors: ggml ctx size = 140.09 KiB",
    # variante intermedia "offloading N repeating layers": no es x/y y se ignora
    "llm_load_tensors: offloading 12 repeating layers to GPU",
    "llm_load_tensors: offloaded 23/35 layers to GPU",
    "llm_load_kv: KV buffer size = 150.00 MiB",
    "llama_context: n_ctx                 = 4096",
    "llm_load_tensors: compute buffer size = 320.44 MiB",
    "llm_load_tensors: CPU buffer size = 403.32 MiB",
]


def test_parse_legacy_format():
    stats = parse_load_logs(LEGACY_LOG)

    assert stats.build == "b11009"
    # rutas con backslashes: se normaliza y queda solo el nombre
    assert stats.model_filename == "llama3-8b.Q5_K_M.gguf"
    # el total viene de "n_layer =" (familia print_meta); el x/y del offload
    # también declara el total, pero cede si ya hay un valor explícito
    assert stats.total_layers == 35
    assert stats.gpu_layers == 23
    assert stats.cpu_layers == 12
    # sin "device N" en el log: un único bucket sin índice ni nombre
    assert [(d.device_index, d.device_name, d.layers) for d in stats.devices] == [
        (None, None, 23),
    ]
    # sin inline: se usa el "KV buffer size"
    assert stats.kv_cache_mib == 150.0
    assert stats.kv_layers is None
    assert stats.context_tokens == 4096
    assert stats.compute_mib == 320.44
    assert stats.cpu_buffer_mib == 403.32
    # las builds viejas no imprimían el tiempo de carga
    assert stats.log_load_seconds is None
    assert stats.notes == []


def test_parse_total_layers_from_offload_when_not_declared():
    # sin "number of layers" ni "n_layer": el denominador del offload X/Y lo declara
    stats = parse_load_logs(
        [
            "load: offloaded 20/40 layers to CUDA",
            "load: moving 20 layers to CPU",
        ]
    )
    assert stats.total_layers == 40
    assert stats.gpu_layers == 20
    assert stats.cpu_layers == 20
    # familia de backend (CUDA/Metal/Vulkan...) sin índice de device
    assert [(d.device_index, d.layers) for d in stats.devices] == [(None, 20)]


def test_parse_metal_device_name():
    stats = parse_load_logs(
        [
            "ggml metal initialized with device name Apple M1 Max",
            "load: offloaded 20/40 layers to Metal",
        ]
    )
    assert stats.devices[0].device_name == "Apple M1 Max"
    assert stats.devices[0].device_index is None
    assert stats.gpu_layers == 20


def test_parse_noise_lines_excluded():
    # "print_info"/"overridden" son debug interno de builds antiguas: repiten
    # datos obsoletos y no pueden fijar el total de capas.
    stats = parse_load_logs(
        [
            "llm_load_print_info: number of layers = 27",
            "load: n_layer = 27 overridden",
            "llm_load_tensors: offloaded 23/35 layers to GPU",
        ]
    )
    assert stats.total_layers == 35
    assert stats.gpu_layers == 23


def test_parse_context_tokens_ignores_slot_and_train_variants():
    # líneas reales de un arranque gemma-4 (era b11009): solo el n_ctx del
    # llama_context fija el contexto; n_ctx_seq es por-slot cuando hay varios
    # slots, y n_ctx_train/n_ctx_orig_yarn/n_ctx_slot describen otra cosa.
    stats = parse_load_logs(
        [
            "print_info: n_ctx_train           = 262144",
            "print_info: n_ctx_orig_yarn       = 262144",
            "266.37.348.230 I slot   operator(): id  0 | task 157421 | new prompt, n_ctx_slot = 65536",
            "0.14.101.410 I slot   load_model: id  0 | task -1 | new slot, n_ctx = 65536",
            "0.11.881.962 I llama_context: n_ctx                 = 131072",
            "0.11.881.962 I llama_context: n_ctx_seq             = 65536",
            "llama_context: n_ctx_seq (32768) < n_ctx_train (262144) -- the full capacity of the model will not be utilized",
        ]
    )
    assert stats.context_tokens == 131072


def test_parse_unrecognized_format_notes():
    # log de una build desconocida: ningún campo se inventa, la nota es un
    # código estable que la UI traduce (launcher.loadCardNoParse).
    stats = parse_load_logs(
        [
            "main: server is listening on http://127.0.0.1:8080",
            "slot promo: update_slots: id  0 | task 0 | processed 128 tokens",
            "metrics: requests 12, tokens 3041",
        ]
    )
    assert stats.notes == ["unrecognized_format"]
    assert stats.build is None
    assert stats.total_layers is None
    assert stats.gpu_layers is None
    assert stats.devices == []
    assert stats.kv_cache_mib is None
    assert stats.context_tokens is None
    assert stats.model_filename is None


def test_parse_build_announced_by_version_line():
    # "version 1.0.0 (b11827)" — la build entre paréntesis
    assert parse_load_logs(["version 1.0.0 (b11827) (deadbeef)"]).build == "b11827"
    # "build: b11009 (...)" — prefijo explícito con la build
    assert parse_load_logs(["build: b11009 (deadbeef)"]).build == "b11009"
    # "load: build = 11349 (hash)" — sin prefijo b en el log
    assert parse_load_logs(["load: build = 11349 (c00b1f51)"]).build == "b11349"


def test_process_rss_mib():
    # proceso vivo (el propio test) devuelve un RSS real > 0
    assert process_rss_mib(os.getpid()) > 0
    # pid sin proceso (fuera del rango válido del SO): None, nunca excepción
    assert process_rss_mib(999_999_999) is None
    assert process_rss_mib(None) is None


# --------------------------------------------------------------------------
# Formatos validados contra logs REALES (data/logs/llama-server.log de la
# máquina del usuario, arranques b11009): líneas copiadas literalmente del
# arranque real. Fijan el contrato de la build "build NNNN (hash)" y de los
# pesos por dispositivo "<DEVICE> model buffer size".
# --------------------------------------------------------------------------

REAL_B11009_LOG = [
    "0.00.161.585 I cmn  common_param: common_params_print_info: build 11009 (fb27a525d) with Clang 20.1.8 for Windows x86_64",
    "0.00.319.638 I srv    load_model: loading model 'C:\\LLM\\gguf\\Qwen3.8-27B\\Qwen3.8-27B-UD-Q4_K_XL.gguf'",
    "0.01.704.406 I load_tensors: loading model tensors, this can take a while... (load_mode = mmap)",
    "0.04.549.593 I load_tensors: offloaded 66/66 layers to GPU",
    "0.04.549.597 I load_tensors:   CPU_Mapped model buffer size =   682.03 MiB",
    "0.04.549.600 I load_tensors:        CUDA0 model buffer size = 15718.48 MiB",
    "0.13.577.850 I llama_context: n_ctx                 = 131072",
    "0.13.577.875 I llama_context: n_ctx_seq             = 131072",
    "0.13.598.567 I llama_kv_cache:      CUDA0 KV buffer size =  4352.00 MiB",
    "0.13.862.677 I sched_reserve:      CUDA0 compute buffer size =   720.28 MiB",
    "0.13.862.686 I sched_reserve:  CUDA_Host compute buffer size =   148.28 MiB",
]


def test_parse_real_b11009_startup_lines():
    stats = parse_load_logs(REAL_B11009_LOG)

    # era b11009: la build solo se anuncia en la línea print_info
    assert stats.build == "b11009"
    assert stats.model_filename == "Qwen3.8-27B-UD-Q4_K_XL.gguf"
    assert (stats.total_layers, stats.gpu_layers, stats.cpu_layers) == (66, 66, 0)
    # offload sin índice + pesos de UN único device: es la misma GPU, se
    # acoplan (era b11009: "offloaded ... to GPU" + "CUDA0 model buffer size")
    assert [(d.device_index, d.layers, d.model_buffer_mib) for d in stats.devices] == [
        (None, 66, 15718.48),
    ]
    # CPU_Mapped = pesos en host, aparte de los buffers de cómputo
    assert stats.cpu_model_buffer_mib == 682.03
    assert stats.kv_cache_mib == 4352.0
    assert stats.context_tokens == 131072
    assert stats.compute_mib == 720.28  # prevalece el mayor (CUDA0 sobre CUDA_Host)
    assert stats.log_load_seconds is None  # builds viejas: no imprimían el tiempo
    assert stats.notes == []


def test_parse_build_print_info_era_b11009():
    # variante real "build NNNN (hash)": se normaliza a "bNNNN". La rama de
    # build omite deliberadamente el filtro de noise: en esta era la build SOLO
    # aparece en common_params_print_info.
    assert parse_load_logs(
        [
            "common_params_print_info: build 11009 (fb27a525d) with Clang 20.1.8 for Windows x86_64"
        ]
    ).build == "b11009"


def test_parse_model_buffers_multi_device_and_orphan():
    stats = parse_load_logs(
        [
            "load: offloaded 30/63 layers to GPU device 0",
            "load: offloaded 20/63 layers to GPU device 1",
            "load_tensors:          CPU model buffer size =   149.22 MiB",
            "load_tensors:        CUDA0 model buffer size =  9391.18 MiB",
            "load_tensors:        CUDA1 model buffer size =  6112.44 MiB",
            # peso sin líneas de offload para ese índice: el device existe
            # (el log declaró pesos) pero sus capas quedan None
            "load_tensors:        CUDA2 model buffer size =   512.00 MiB",
        ]
    )
    assert [(d.device_index, d.layers, d.model_buffer_mib) for d in stats.devices] == [
        (0, 30, 9391.18),
        (1, 20, 6112.44),
        (2, None, 512.0),
    ]
    assert stats.cpu_model_buffer_mib == 149.22
    assert stats.gpu_layers == 50
    assert stats.total_layers == 63


def test_parse_model_buffers_without_offload_lines():
    stats = parse_load_logs(
        [
            "load_tensors:        CUDA0 model buffer size =  9391.18 MiB",
            "load_tensors:   CPU_Mapped model buffer size =   682.03 MiB",
        ]
    )
    # sin capas declaradas no se inventan: gpu_layers None, device con layers None
    assert stats.gpu_layers is None
    assert [(d.device_index, d.layers, d.model_buffer_mib) for d in stats.devices] == [
        (0, None, 9391.18),
    ]
    assert stats.cpu_model_buffer_mib == 682.03
    assert stats.notes == []


# --------------------------------------------------------------------------
# Integración: el launcher rellena la card (medido + parseado) ANTES de
# promover el proceso a "running", en el JSON de /api/launcher/status.
# --------------------------------------------------------------------------

# Lo que el .bat fake va a echoear al arrancar "en modo servir" (formato
# moderno simple, un solo device). Sin '%' ni paréntesis: en un .bat se
# imprimen literalmente salvo el '%', que es escape de variable.
LOAD_RUN_LINES = [
    "load: build = 11827",
    "load: loading model 'models/qwen3-8b.Q5_K_M.gguf'",
    "load: number of layers = 33",
    " Device 0: NVIDIA GeForce RTX 3060: ; compute = 8.6",
    "load: offloaded 24/33 layers to GPU device 0",
    "load: offloaded 9/33 layers to CPU",
    "load_tensors:   CPU_Mapped model buffer size = 682.03 MiB",
    "load_tensors:        CUDA0 model buffer size = 15718.48 MiB",
    "load: KV buffer size = 90.00 MiB",
    "llama_context: n_ctx                 = 8192",
    "load: compute buffer size = 512.25 MiB",
    "load: CPU buffer size = 120.50 MiB",
    "load: model load time = 4321.00 ms",
]


async def test_launch_llama_populates_load_card(
    client, sample_model_dir, make_probeable_binary, mock_llama_server, monkeypatch
):
    # Poll rápido: el health_check parchado responde False las primeras 3
    # llamadas para que las líneas de carga del .bat ya estén en el ring
    # buffer antes de la promoción (si no, la card se armaría con log vacío).
    monkeypatch.setattr(launcher_module, "HEALTH_CHECK_INTERVAL_S", 0.05)

    health_calls = {"n": 0}

    async def flaky_health(host: str, port: int) -> bool:
        health_calls["n"] += 1
        return health_calls["n"] > 3

    monkeypatch.setattr(launcher_module.manager, "health_check", flaky_health)

    # Snapshot determinista: el primer fetch es el baseline pre-spawn, el
    # segundo es el post-ready que toma _build_load_stats. Sin esto el delta
    # dependería del uso real de RAM de la máquina del test.
    snapshots = [
        {"ram_used_mib": 8000.0, "gpus": {0: {"name": "Fake RTX", "used_mib": 1200.0}}},
        {"ram_used_mib": 13500.0, "gpus": {0: {"name": "Fake RTX", "used_mib": 5400.0}}},
    ]
    taken = {"n": 0}

    def fake_snapshot(*args):
        snap = snapshots[min(taken["n"], len(snapshots) - 1)]
        taken["n"] += 1
        return snap

    monkeypatch.setattr(
        launcher_module.ModelProcessManager, "_usage_snapshot", staticmethod(fake_snapshot)
    )

    binary = make_probeable_binary(
        [
            # help mínimo PERO con los CRITICAL_FLAGS (--model/--host/--port/
            # --ctx-size): el launch los exige si el comando los emite; el
            # resto de flags del comando serán descartados por el filtro,
            # que es comportamiento esperado y no rompe el launch.
            "--model PATH ruta del modelo GGUF",
            "--host HOST escuchar en HOST",
            "--port PORT escuchar en PORT",
            "--ctx-size N size of the prompt context",
        ],
        run_lines=tuple(LOAD_RUN_LINES),
    )
    await client.post(
        "/api/config",
        json={
            "model_dirs": [str(sample_model_dir)],
            "backends": {"llama_server": {"binary_path": binary, "default_port": 8080, "enabled": True}},
        },
    )
    await scan_and_wait(client)
    model_id = (await client.get("/api/models")).json()[0]["id"]

    launched = (
        await client.post(
            "/api/launcher/launch",
            json={"model_id": model_id, "backend": "llama_server", "host": "127.0.0.1", "port": 18080},
        )
    ).json()
    process_id = launched["process_id"]

    async def is_running():
        s = (await client.get(f"/api/launcher/status/{process_id}")).json()
        return s["state"] == "running"

    assert await wait_until(is_running, timeout=10.0)
    status = (await client.get(f"/api/launcher/status/{process_id}")).json()

    # El poll que ve "running" debe ver YA la card: load_stats se adjunta
    # antes de voltear el estado, no después.
    stats = status["load_stats"]
    assert stats is not None

    # --- medido: delta entre los dos snapshots parchados ---
    assert stats["ram_before_mib"] == 8000.0
    assert stats["ram_after_mib"] == 13500.0
    assert stats["ram_delta_mib"] == 5500.0
    assert len(stats["gpus"]) == 1
    assert stats["gpus"][0]["name"] == "Fake RTX"
    assert stats["gpus"][0]["used_before_mib"] == 1200.0
    assert stats["gpus"][0]["used_after_mib"] == 5400.0
    assert stats["gpus"][0]["delta_mib"] == 4200.0
    # tiempo spawn->ready y RSS reales (del proceso .bat de verdad)
    assert stats["wall_load_seconds"] > 0
    assert stats["process_rss_mib"] > 0

    # --- estimado: parse del log que el propio fake imprimió ---
    assert stats["build"] == "b11827"
    assert stats["model_filename"] == "qwen3-8b.Q5_K_M.gguf"
    assert stats["total_layers"] == 33
    assert stats["gpu_layers"] == 24
    assert stats["cpu_layers"] == 9
    assert [(d["device_index"], d["device_name"], d["layers"]) for d in stats["devices"]] == [
        (0, "NVIDIA GeForce RTX 3060", 24),
    ]
    # pesos por dispositivo acoplados al device del offload (mismo índice)
    assert stats["devices"][0]["model_buffer_mib"] == 15718.48
    # pesos mapeados a host: campo propio, aparte del buffer de cómputo en CPU
    assert stats["cpu_model_buffer_mib"] == 682.03
    assert stats["kv_cache_mib"] == 90.0
    assert stats["context_tokens"] == 8192
    assert stats["compute_mib"] == 512.25
    assert stats["cpu_buffer_mib"] == 120.5
    assert stats["log_load_seconds"] == pytest.approx(4.321)
    assert stats["notes"] == []

    await client.post(f"/api/launcher/stop/{process_id}")


async def test_launch_ollama_has_no_load_card(
    client, sample_model_dir, probeable_binary, mock_llama_server, monkeypatch
):
    # ollama no emite el log de carga de llama.cpp: la card simplemente no
    # existe para ese backend (ni baseline ni parse).
    monkeypatch.setattr(launcher_module, "HEALTH_CHECK_INTERVAL_S", 0.05)

    await client.post(
        "/api/config",
        json={
            "model_dirs": [str(sample_model_dir)],
            "backends": {
                "llama_server": {"binary_path": probeable_binary, "default_port": 8080, "enabled": True},
                "ollama": {"binary_path": probeable_binary, "default_port": 11434, "enabled": True},
            },
        },
    )
    await scan_and_wait(client)
    model_id = (await client.get("/api/models")).json()[0]["id"]

    launched = (
        await client.post(
            "/api/launcher/launch",
            json={"model_id": model_id, "backend": "ollama", "host": "127.0.0.1", "port": 18080},
        )
    ).json()
    process_id = launched["process_id"]

    async def is_running():
        s = (await client.get(f"/api/launcher/status/{process_id}")).json()
        return s["state"] == "running"

    assert await wait_until(is_running, timeout=10.0)
    status = (await client.get(f"/api/launcher/status/{process_id}")).json()
    assert status["load_stats"] is None

    await client.post(f"/api/launcher/stop/{process_id}")


def test_build_load_stats_snapshot_con_apollo_mutando_el_deque(monkeypatch):
    # LOADCARD-NULL: _build_load_stats corre en un worker (asyncio.to_thread desde
    # _monitor_health) mientras el event loop sigue agregando líneas al deque vivo
    # (los modelos grandes aún loguean cuando /health responde). Iterar el deque
    # en vivo lanza "deque mutated during iteration"; el except de _monitor_health
    # la traga y la UI queda "running" sin card. El fix toma .copy() (una sola
    # llamada C, atómica bajo el GIL) y parsea la copia privada.
    mgr = launcher_module.manager
    monkeypatch.setattr(
        launcher_module.ModelProcessManager, "_usage_snapshot", lambda self: {}
    )
    monkeypatch.setattr(launcher_module, "process_rss_mib", lambda pid: 123.4)

    pid = "race-loadcard"
    info = launcher_module.ProcessInfo(
        process_id=pid,
        model_id="m1",
        model_name="M1",
        backend="llama_server",
        state="starting",
        host="127.0.0.1",
        port=18080,
        launch_config={},
    )
    mgr._log_buffers[pid] = deque(LOAD_RUN_LINES, maxlen=launcher_module.LOG_BUFFER_MAXLEN)

    stop = threading.Event()

    def spammer():
        # Simula a _pump_output: append continuo al deque vivo desde otra hebra.
        # Cicla el fixture para que la card siga siendo parseable sin importar
        # qué ventana dejó el maxlen durante la carrera.
        i = 0
        while not stop.is_set():
            mgr._log_buffers[pid].append(LOAD_RUN_LINES[i % len(LOAD_RUN_LINES)])
            i += 1

    t = threading.Thread(target=spammer, daemon=True)
    t.start()
    try:
        # Sin el snapshot, alguna de estas llamadas itera el deque mientras el
        # spammer muta -> IndexError intermitente (4/4 en modelos grandes reales).
        for _ in range(50):
            stats = mgr._build_load_stats(pid, info)
            assert stats is not None
            assert stats.build == "b11827"
            assert stats.total_layers == 33
            assert stats.process_rss_mib == 123.4
    finally:
        stop.set()
        t.join(timeout=5)
        mgr._log_buffers.pop(pid, None)
