"""Tests de launcher.py — construcción de comandos, templates en SQLite, ciclo de vida de procesos."""

import asyncio

import pytest
from pydantic import ValidationError

import launcher as launcher_module
from helpers import scan_and_wait, wait_until, write_gguf

# --------------------------------------------------------------------------
# Construcción de comandos (funciones puras, sin red ni subprocess)
# --------------------------------------------------------------------------


def test_build_command_all_params():
    cfg = launcher_module.LaunchConfig(
        model_id="m",
        backend="llama_server",
        n_ctx=32768,
        n_batch=1024,
        n_ubatch=512,
        n_gpu_layers=-1,
        gpu_mode="gpu_only",
        cache_type_k="q8_0",
        cache_type_v="q8_0",
        flash_attn=True,
        load_mode="mlock",
        mtp_draft_model="/models/draft.gguf",
        n_draft=6,
        mmproj_path="/models/mmproj.gguf",
        lora_path="/models/lora.gguf",
        lora_scale=0.8,
        host="0.0.0.0",
        port=9090,
        n_threads=8,
        n_parallel=2,
        api_key="secret",
        log_file="data/logs/test.log",
    )
    cmd = launcher_module.build_llama_server_command(cfg, "/opt/llama-server", "/models/model.gguf")

    assert cmd[0] == "/opt/llama-server"
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "/models/model.gguf"
    assert "--ctx-size" in cmd and cmd[cmd.index("--ctx-size") + 1] == "32768"
    assert "--flash-attn" in cmd
    # use_mlock/use_mmap ya no existen: se unifican en --load-mode (b11009).
    assert "--load-mode" in cmd and cmd[cmd.index("--load-mode") + 1] == "mlock"
    assert "--spec-type" in cmd and cmd[cmd.index("--spec-type") + 1] == "draft-mtp"
    assert "--spec-draft-model" in cmd and cmd[cmd.index("--spec-draft-model") + 1] == "/models/draft.gguf"
    assert "--spec-draft-n-max" in cmd and cmd[cmd.index("--spec-draft-n-max") + 1] == "6"
    assert "--mmproj" in cmd and cmd[cmd.index("--mmproj") + 1] == "/models/mmproj.gguf"
    assert "--lora" in cmd and cmd[cmd.index("--lora") + 1] == "/models/lora.gguf"
    assert "--lora-scale" in cmd and cmd[cmd.index("--lora-scale") + 1] == "0.8"
    assert "--api-key" in cmd and cmd[cmd.index("--api-key") + 1] == "secret"
    # El launcher es el único que escribe el log (_pump_output): llama-server
    # no recibe --log-file, así no hay líneas duplicadas.
    assert "--log-file" not in cmd


def test_idle_repetidos_se_colapsan():
    lines = [
        "slot print_timing: id 0 | task 5 | eval time = 258.52 ms / 6 tokens",
        "0.10.000.001 I srv update_slots: all slots are idle",
        "0.11.000.002 I srv update_slots: all slots are idle",
        "0.12.000.003 I srv update_slots: all slots are idle",
        "slot launch_slot_: id 0 | task 6 | processing task",
        "0.20.000.004 I srv update_slots: all slots are idle",
    ]
    previous = False
    forwarded = []
    for line in lines:
        forward, previous = launcher_module.should_forward_log_line(line, previous)
        if forward:
            forwarded.append(line)
    # Queda un idle por cada fin de actividad; los repetidos del poller no.
    assert forwarded == [lines[0], lines[1], lines[4], lines[5]]


def test_rotacion_de_log_por_tamano(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher_module, "LOG_ROTATE_MAX_BYTES", 100)
    log = tmp_path / "proc.log"
    log.write_text("x" * 150)

    assert launcher_module.rotate_log_if_needed(log) is True
    assert not log.exists()
    backup = tmp_path / "proc.log.1"
    assert backup.read_text() == "x" * 150

    log.write_text("ok")
    assert launcher_module.rotate_log_if_needed(log) is False

    # Segunda rotación: se pisa la backup anterior.
    log.write_text("y" * 150)
    assert launcher_module.rotate_log_if_needed(log) is True
    assert backup.read_text() == "y" * 150


def test_build_command_metrics_flag():
    cfg = launcher_module.LaunchConfig(model_id="m", backend="llama_server")
    cmd = launcher_module.build_llama_server_command(cfg, "/opt/llama-server", "/models/model.gguf")
    assert cmd.count("--metrics") == 1

    # Defensivo: si algún día se reutiliza con otro backend, no se agrega.
    cfg_ollama = launcher_module.LaunchConfig(model_id="m", backend="ollama")
    cmd_ollama = launcher_module.build_llama_server_command(cfg_ollama, "/opt/x", "/models/model.gguf")
    assert "--metrics" not in cmd_ollama


def test_build_command_no_optional():
    cfg = launcher_module.LaunchConfig(model_id="m", backend="llama_server")
    cmd = launcher_module.build_llama_server_command(cfg, "/opt/llama-server", "/models/model.gguf")

    assert "--spec-type" not in cmd
    assert "--spec-draft-model" not in cmd
    assert "--spec-draft-n-max" not in cmd
    assert "--mmproj" not in cmd
    assert "--lora" not in cmd
    assert "--lora-scale" not in cmd
    # load_mode=auto por default -> no se envía el flag (los viejos
    # --mlock/--no-mmap ya no existen en la build).
    assert "--load-mode" not in cmd
    assert "--api-key" not in cmd  # api_key="" por default


def test_build_command_with_mtp():
    cfg = launcher_module.LaunchConfig(model_id="m", mtp_draft_model="/models/draft.gguf", n_draft=8)
    cmd = launcher_module.build_llama_server_command(cfg, "/opt/llama-server", "/models/model.gguf")

    assert "--spec-type" in cmd
    assert cmd[cmd.index("--spec-type") + 1] == "draft-mtp"
    assert "--spec-draft-model" in cmd
    assert cmd[cmd.index("--spec-draft-model") + 1] == "/models/draft.gguf"
    assert "--spec-draft-n-max" in cmd
    assert cmd[cmd.index("--spec-draft-n-max") + 1] == "8"


def test_build_command_with_mtp_embedded():
    """MTP embebido en el propio .gguf: activa spec pero sin archivo de draft."""
    cfg = launcher_module.LaunchConfig(model_id="m", mtp_embedded=True, n_draft=4)
    cmd = launcher_module.build_llama_server_command(cfg, "/opt/llama-server", "/models/model.gguf")

    assert "--spec-type" in cmd and cmd[cmd.index("--spec-type") + 1] == "draft-mtp"
    assert "--spec-draft-model" not in cmd
    assert "--spec-draft-n-max" in cmd and cmd[cmd.index("--spec-draft-n-max") + 1] == "4"


def test_build_command_with_mtp_embedded_draft_cache():
    """Los cache types del draft son flags propios (--spec-draft-type-k/-v)."""
    cfg = launcher_module.LaunchConfig(
        model_id="m", mtp_embedded=True,
        cache_type_k_draft="q8_0", cache_type_v_draft="q8_0",
    )
    cmd = launcher_module.build_llama_server_command(cfg, "/opt/llama-server", "/models/model.gguf")

    assert "--spec-draft-type-k" in cmd and cmd[cmd.index("--spec-draft-type-k") + 1] == "q8_0"
    assert "--spec-draft-type-v" in cmd and cmd[cmd.index("--spec-draft-type-v") + 1] == "q8_0"


def test_build_command_with_mmproj():
    cfg = launcher_module.LaunchConfig(model_id="m", mmproj_path="/models/mmproj.gguf")
    cmd = launcher_module.build_llama_server_command(cfg, "/opt/llama-server", "/models/model.gguf")

    assert "--mmproj" in cmd
    assert cmd[cmd.index("--mmproj") + 1] == "/models/mmproj.gguf"


def test_build_command_advanced_params():
    """Los parámetros avanzados se incluyen correctamente en el comando."""
    cfg = launcher_module.LaunchConfig(
        model_id="m",
        rope_freq_base=10000.0,
        rope_scaling_type="yarn",
        yarn_ext_factor=1.0,
        numa=True,
        no_kv_offload=True,
        cache_reuse=64,
        defrag_thold=0.1,
        grp_attn_n=4,
        grp_attn_w=256,
    )
    cmd = launcher_module.build_llama_server_command(cfg, "/opt/llama-server", "/models/m.gguf")

    assert "--rope-freq-base" in cmd
    assert "--rope-scaling" in cmd and cmd[cmd.index("--rope-scaling") + 1] == "yarn"
    assert "--yarn-ext-factor" in cmd
    assert "--numa" in cmd
    assert "--no-kv-offload" in cmd
    assert "--cache-reuse" in cmd and cmd[cmd.index("--cache-reuse") + 1] == "64"
    assert "--defrag-thold" in cmd
    assert "--grp-attn-n" in cmd and cmd[cmd.index("--grp-attn-n") + 1] == "4"
    assert "--grp-attn-w" in cmd and cmd[cmd.index("--grp-attn-w") + 1] == "256"


def test_build_command_advanced_params_defaults():
    """Con valores default, los flags avanzados NO aparecen en el comando."""
    cfg = launcher_module.LaunchConfig(model_id="m")
    cmd = launcher_module.build_llama_server_command(cfg, "/opt/llama-server", "/models/m.gguf")

    assert "--rope-freq-base" not in cmd
    assert "--rope-scaling" not in cmd
    assert "--yarn-ext-factor" not in cmd
    assert "--numa" not in cmd
    assert "--no-kv-offload" not in cmd
    assert "--cache-reuse" not in cmd
    assert "--defrag-thold" not in cmd
    assert "--grp-attn-n" not in cmd
    assert "--grp-attn-w" not in cmd


# --------------------------------------------------------------------------
# C3: validación de log_file contra path traversal
#
# cfg.log_file es donde _pump_output guarda la salida del proceso (llama-server
# ya no recibe --log-file), así que resolve_log_path() se llama en start()
# antes de lanzar. Válido => path resuelto dentro de BASE_DIR;
# vacío => fallback LOGS_DIR/<process_id>.log; traversal => 400.
# --------------------------------------------------------------------------


def test_resolve_log_path_relative_inside():
    # El prefijo "data/" del default histórico se reinterpreta contra el
    # DATA_DIR de la instancia, que es lo que se mueve con GLYVEX_DATA_DIR.
    p = launcher_module.resolve_log_path("data/logs/llama-server.log", "abc")
    assert p == (launcher_module.DATA_DIR / "logs" / "llama-server.log").resolve()


def test_resolve_log_path_relative_to_data_dir():
    """Sin el prefijo "data/", la ruta cuelga igual del DATA_DIR."""
    p = launcher_module.resolve_log_path("logs/otro.log", "abc")
    assert p == (launcher_module.DATA_DIR / "logs" / "otro.log").resolve()


def test_resolve_log_path_empty_falls_back():
    assert launcher_module.resolve_log_path("", "abc") == launcher_module.LOGS_DIR / "abc.log"


@pytest.mark.parametrize(
    "evil",
    [
        "../../outside.log",
        "data/../../outside.log",
        "/tmp/outside.log",  # absoluto fuera del proyecto (raíz en el disco actual)
    ],
)
def test_resolve_log_path_traversal_rejected(evil):
    with pytest.raises(launcher_module.HTTPException) as exc_info:
        launcher_module.resolve_log_path(evil, "abc")
    assert exc_info.value.status_code == 400
    assert "log_file" in exc_info.value.detail


async def test_launch_log_file_traversal_rejected(client, sample_model_dir):
    """POST /api/launcher/launch con log_file fuera de DATA_DIR -> 400, sin proceso."""
    await client.post("/api/config", json={"model_dirs": [str(sample_model_dir)]})
    await scan_and_wait(client)
    model_id = (await client.get("/api/models")).json()[0]["id"]

    res = await client.post(
        "/api/launcher/launch",
        json={
            "model_id": model_id,
            "backend": "llama_server",
            "host": "127.0.0.1",
            "port": 39998,
            "log_file": "../../outside.log",
        },
    )
    assert res.status_code == 400
    assert "log_file" in res.json()["detail"]
    # Ni un proceso quedó registrado ni se escribió nada fuera de DATA_DIR
    assert (await client.get("/api/launcher/status")).json() == []
    assert not (launcher_module.DATA_DIR.parent / "outside.log").exists()


# --------------------------------------------------------------------------
# Templates (ahora persistidos en SQLite — tabla hw_templates)
# --------------------------------------------------------------------------


async def test_templates_loaded(client):
    res = await client.get("/api/launcher/templates")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 5
    names = {t["name"] for t in data}
    assert "RTX 3090 — Máximo rendimiento" in names
    assert "CPU only — 64GB RAM" in names


async def test_save_custom_template(client):
    payload = {"name": "Mi template", "builtin": False, "params": {"n_ctx": 4096}}
    res = await client.post("/api/launcher/templates", json=payload)
    assert res.status_code == 200

    listed = (await client.get("/api/launcher/templates")).json()
    assert any(t["name"] == "Mi template" for t in listed)
    assert len(listed) == 6  # 5 default + 1 custom


async def test_delete_custom_template(client):
    await client.post(
        "/api/launcher/templates",
        json={"name": "Temporal", "builtin": False, "params": {}},
    )

    res = await client.delete("/api/launcher/templates/Temporal")
    assert res.status_code == 200

    listed = (await client.get("/api/launcher/templates")).json()
    assert all(t["name"] != "Temporal" for t in listed)
    assert len(listed) == 5


async def test_templates_in_db(client):
    """Los templates se guardan y leen desde SQLite."""
    res = await client.post(
        "/api/launcher/templates",
        json={"name": "DB Test Template", "builtin": False, "params": {"n_ctx": 8192}},
    )
    assert res.status_code == 200

    # El listado sale de la DB, no de un dict en memoria ni del JSON
    listed = (await client.get("/api/launcher/templates")).json()
    assert any(t["name"] == "DB Test Template" for t in listed)

    from database import db_get_template

    stored = await db_get_template("DB Test Template")
    assert stored is not None
    assert stored["params"]["n_ctx"] == 8192
    assert stored["builtin"] is False


# F5: snapshot P1.5 en templates — params es dict libre, el payload del
# frontend viaja completo (toggles por grupo + auto_mode) y roundtrippa.
async def test_template_snapshot_toggle_state_roundtrip(client):
    snapshot = {
        "n_ctx": 8192,
        "auto_mode": False,
        "toggles": {"n_ctx": True, "n_batch": False, "n_ubatch": True, "fit": False},
    }
    res = await client.post(
        "/api/launcher/templates",
        json={"name": "P1.5 Snapshot", "builtin": False, "params": snapshot},
    )
    assert res.status_code == 200

    listed = (await client.get("/api/launcher/templates")).json()
    tpl = next(t for t in listed if t["name"] == "P1.5 Snapshot")
    assert tpl["params"]["auto_mode"] is False
    assert tpl["params"]["toggles"] == {
        "n_ctx": True, "n_batch": False, "n_ubatch": True, "fit": False,
    }


async def test_cannot_delete_builtin_template(client):
    """Los templates builtin no se pueden eliminar."""
    from database import db_upsert_template

    await db_upsert_template("Builtin Test", builtin=True, params={"n_ctx": 4096})

    res = await client.delete("/api/launcher/templates/Builtin Test")
    assert res.status_code == 403

    listed = (await client.get("/api/launcher/templates")).json()
    assert any(t["name"] == "Builtin Test" for t in listed)


async def test_cannot_overwrite_builtin_template(client):
    """Guardar con el nombre de un builtin queda bloqueado: la UI prellena
    ese nombre al elegir el template, y sin la guarda se lo pisaría."""
    res = await client.post(
        "/api/launcher/templates",
        json={"name": "CPU only — 64GB RAM", "builtin": False, "params": {"n_ctx": 4096}},
    )
    assert res.status_code == 409

    listed = (await client.get("/api/launcher/templates")).json()
    tpl = next(t for t in listed if t["name"] == "CPU only — 64GB RAM")
    assert tpl["builtin"] is True


async def test_delete_custom_template_from_db(client):
    """Borrar un template custom lo elimina de la DB."""
    from database import db_get_template

    await client.post(
        "/api/launcher/templates",
        json={"name": "Para Borrar", "builtin": False, "params": {}},
    )
    res = await client.delete("/api/launcher/templates/Para Borrar")
    assert res.status_code == 200

    listed = (await client.get("/api/launcher/templates")).json()
    assert all(t["name"] != "Para Borrar" for t in listed)
    assert await db_get_template("Para Borrar") is None


async def test_delete_unknown_template_404(client):
    res = await client.delete("/api/launcher/templates/No Existe")
    assert res.status_code == 404


async def test_builtin_templates_listed_first(client):
    """El orden del listado es: builtin primero, después alfabético."""
    await client.post(
        "/api/launcher/templates",
        json={"name": "AAA custom", "builtin": False, "params": {}},
    )
    listed = (await client.get("/api/launcher/templates")).json()
    assert listed[-1]["name"] == "AAA custom"
    assert all(t["builtin"] for t in listed[:-1])


# --------------------------------------------------------------------------
# Ciclo de vida de procesos
#
# Para launch/stop usamos backend="lm_studio": no dispara un subprocess real
# (según el diseño de M2, LM Studio solo hace un health check contra
# host/puerto), así que apuntándolo a mock_llama_server podemos probar
# launch/status/stop de punta a punta sin necesitar un binario real.
#
# Para status starting->running y logs sí necesitamos un subprocess real
# (fake_llama_binary), porque esos dos casos son específicos del flujo
# llama_server/ollama (estado inicial "starting" + captura de stdout).
# --------------------------------------------------------------------------


async def test_launch_returns_process_id(client, sample_model_dir, mock_llama_server):
    await client.post("/api/config", json={"model_dirs": [str(sample_model_dir)]})
    await scan_and_wait(client)
    model_id = (await client.get("/api/models")).json()[0]["id"]

    res = await client.post(
        "/api/launcher/launch",
        json={"model_id": model_id, "backend": "lm_studio", "host": "127.0.0.1", "port": 18080},
    )
    assert res.status_code == 200
    data = res.json()
    assert "process_id" in data and data["process_id"]
    assert data["state"] == "running"  # mock_llama_server responde 200 en /health


# Réplica del payload que arma el frontend (DEFAULT_LAUNCH_CONFIG de
# Launcher.jsx + la normalización "" -> null en handleLaunch). Si el backend
# y el frontend divergen en tipos (p. ej. CacheType | None vs ""), esto explota
# con 422 en vez de a media de usar la app.
FRONTEND_LAUNCH_PAYLOAD = {
    "backend": "llama_server",
    "n_ctx": 65536,
    "n_batch": 2048,
    "n_ubatch": 512,
    "n_gpu_layers": -1,
    "gpu_mode": "gpu_only",
    "cache_type_k": "q4_0",
    "cache_type_v": "q4_0",
    "flash_attn": True,
    "load_mode": "auto",
    "mtp_draft_model": None,
    "mtp_embedded": False,
    "n_draft": 5,
    "cache_type_k_draft": "q8_0",
    "cache_type_v_draft": "q8_0",
    "mmproj_path": None,
    "lora_path": None,
    "lora_scale": 1.0,
    "thinking_enabled": False,
    "budget_tokens": 8192,
    "sampling_preset": "instruct",
    "temperature": 0.7,
    "top_p": 0.80,
    "top_k": 20,
    "min_p": 0.0,
    "presence_penalty": 0.3,
    "repeat_penalty": 1.1,
    "rope_freq_base": 0,
    "rope_scaling_type": "none",
    "yarn_ext_factor": -1,
    "numa": False,
    "no_kv_offload": False,
    "cache_reuse": 0,
    "defrag_thold": -1,
    "grp_attn_n": 1,
    "grp_attn_w": 512,
    "jinja": True,
    "reasoning_effort": "none",
    "ctx_checkpoints": 8,
    "checkpoint_min_step": 16384,
    "cache_ram_mib": 8192,
    "fit_target_mib": 0,
    "fit": True,
    "auto_mode": False,
    "no_reasoning_preserve": False,
    "kv_unified": False,
    "kv_unified_per_slot": 0,
    "sleep_idle_seconds": 0,
    "warmup": True,
    "lazy_mode": "auto",
    "reasoning_budget": -1,
    "host": "127.0.0.1",
    "port": 18080,
    "n_threads": -1,
    "n_parallel": 1,
    "api_key": "",
    "log_file": "data/logs/llama-server.log",
}


async def test_launch_accepts_frontend_payload(client, sample_model_dir, mock_llama_server):
    await client.post("/api/config", json={"model_dirs": [str(sample_model_dir)]})
    await scan_and_wait(client)
    model_id = (await client.get("/api/models")).json()[0]["id"]

    res = await client.post(
        "/api/launcher/launch",
        json={**FRONTEND_LAUNCH_PAYLOAD, "model_id": model_id, "backend": "lm_studio"},
    )
    assert res.status_code == 200, f"422: el payload del frontend ya no valida ({res.json()})"
    data = res.json()
    assert data["state"] == "running"

    await client.post(f"/api/launcher/stop/{data['process_id']}")


async def test_stop_process(client, sample_model_dir, mock_llama_server):
    await client.post("/api/config", json={"model_dirs": [str(sample_model_dir)]})
    await scan_and_wait(client)
    model_id = (await client.get("/api/models")).json()[0]["id"]

    launched = (
        await client.post(
            "/api/launcher/launch",
            json={"model_id": model_id, "backend": "lm_studio", "host": "127.0.0.1", "port": 18080},
        )
    ).json()

    res = await client.post(f"/api/launcher/stop/{launched['process_id']}")
    assert res.status_code == 200
    assert res.json()["state"] == "stopped"


async def test_status_starting_to_running(client, sample_model_dir, mock_llama_server, fake_llama_binary):
    await client.post(
        "/api/config",
        json={
            "model_dirs": [str(sample_model_dir)],
            "backends": {"llama_server": {"binary_path": fake_llama_binary, "default_port": 8080, "enabled": True}},
        },
    )
    await scan_and_wait(client)
    model_id = (await client.get("/api/models")).json()[0]["id"]

    # host/port apuntan al mock (que sí responde /health); el subprocess real
    # (fake_llama_binary) es independiente de ese puerto, solo aporta el PID
    # y el estado "starting" inicial que el health check después promueve.
    launched = (
        await client.post(
            "/api/launcher/launch",
            json={"model_id": model_id, "backend": "llama_server", "host": "127.0.0.1", "port": 18080},
        )
    ).json()
    process_id = launched["process_id"]

    async def is_running():
        status = (await client.get(f"/api/launcher/status/{process_id}")).json()
        return status["state"] == "running"

    assert await wait_until(is_running, timeout=5.0)

    await client.post(f"/api/launcher/stop/{process_id}")


async def test_logs_not_empty(client, sample_model_dir, fake_llama_binary):
    await client.post(
        "/api/config",
        json={
            "model_dirs": [str(sample_model_dir)],
            "backends": {"llama_server": {"binary_path": fake_llama_binary, "default_port": 8080, "enabled": True}},
        },
    )
    await scan_and_wait(client)
    model_id = (await client.get("/api/models")).json()[0]["id"]

    launched = (
        await client.post(
            "/api/launcher/launch",
            # Puerto arbitrario sin nada escuchando: no nos importa el health
            # check acá, solo que el subprocess escriba stdout.
            json={"model_id": model_id, "backend": "llama_server", "host": "127.0.0.1", "port": 39999},
        )
    ).json()
    process_id = launched["process_id"]
    assert launched["warning"] is None  # puerto libre: sin aviso de pre-vuelo

    async def has_logs():
        logs = (await client.get(f"/api/launcher/logs/{process_id}")).json()
        return len(logs["lines"]) > 0

    assert await wait_until(has_logs, timeout=3.0)

    await client.post(f"/api/launcher/stop/{process_id}")


# --------------------------------------------------------------------------
# Pre-vuelo: aviso cuando el puerto ya está ocupado por algo que esta
# instancia no lanzó (trampa del huérfano: otra instancia o --reload)
# --------------------------------------------------------------------------


async def test_launch_warns_when_port_held_by_foreign_llama_server(
    client, sample_model_dir, fake_llama_binary
):
    """Server que responde /props (llama.cpp) en el puerto de destino: el
    launch no se bloquea, pero el proceso nace con el aviso específico."""
    import uvicorn
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def _props(request):
        return JSONResponse({"total_slots": 1})

    app = Starlette(routes=[Route("/props", _props)])
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=39998, log_level="critical")
    )
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.01)
    try:
        await client.post(
            "/api/config",
            json={
                "model_dirs": [str(sample_model_dir)],
                "backends": {"llama_server": {"binary_path": fake_llama_binary, "default_port": 8080, "enabled": True}},
            },
        )
        await scan_and_wait(client)
        model_id = (await client.get("/api/models")).json()[0]["id"]

        res = await client.post(
            "/api/launcher/launch",
            json={"model_id": model_id, "backend": "llama_server", "host": "127.0.0.1", "port": 39998},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["warning"]
        assert "llama-server" in data["warning"]
        assert "39998" in data["warning"]

        await client.post(f"/api/launcher/stop/{data['process_id']}")
    finally:
        server.should_exit = True
        await task


async def test_launch_warns_when_port_held_by_unknown_listener(
    client, sample_model_dir, fake_llama_binary
):
    """Puerto ocupado por algo que no habla llama.cpp ni Ollama (solo TCP):
    aviso genérico, sin falsos positivos de 'llama-server'."""
    async def _accept(reader, writer):
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(_accept, host="127.0.0.1", port=39997)
    try:
        await client.post(
            "/api/config",
            json={
                "model_dirs": [str(sample_model_dir)],
                "backends": {"llama_server": {"binary_path": fake_llama_binary, "default_port": 8080, "enabled": True}},
            },
        )
        await scan_and_wait(client)
        model_id = (await client.get("/api/models")).json()[0]["id"]

        res = await client.post(
            "/api/launcher/launch",
            json={"model_id": model_id, "backend": "llama_server", "host": "127.0.0.1", "port": 39997},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["warning"]
        assert "llama-server" not in data["warning"]

        await client.post(f"/api/launcher/stop/{data['process_id']}")
    finally:
        server.close()
        await server.wait_closed()


# --------------------------------------------------------------------------
# Build 11003: filter_command / mask_command (funciones puras)
#
# El probe de capacidades (abajo) detecta qué long-flags soporta el binario
# configurado; filter_command descarta los que no conoce (con su valor) para
# que una build vieja no muera con "unknown argument", y mask_command
# enmascara la api-key antes de exponer el argv por la API o el log.
# --------------------------------------------------------------------------


def test_filter_command_drops_unknown_flag_with_value():
    cmd = [
        "bin", "--model", "m.gguf",
        "--ctx-checkpoints", "8",
        "--flash-attn", "on", "--metrics",
    ]
    supported = {"--model", "--flash-attn", "--metrics"}
    out, dropped = launcher_module.filter_command(cmd, supported)
    assert out == ["bin", "--model", "m.gguf", "--flash-attn", "on", "--metrics"]
    assert dropped == ["--ctx-checkpoints"]


def test_filter_command_boolean_flag_dropped_alone():
    # Un flag de BOOLEAN_FLAGS, al descartarse, NO puede comerse el token
    # siguiente aunque no empiece con "--". --no-reasoning-preserve es un
    # booleano real de la build; lo seguimos de un valor posicional (4096)
    # para que sea el check de BOOLEAN_FLAGS lo que lo salve, no el hecho de
    # que el siguiente token sea otro flag.
    cmd = ["bin", "--no-reasoning-preserve", "4096", "--ctx-size", "8192"]
    out, dropped = launcher_module.filter_command(cmd, {"--ctx-size"})
    assert out == ["bin", "4096", "--ctx-size", "8192"]
    assert dropped == ["--no-reasoning-preserve"]


def test_filter_command_trailing_unknown_flag():
    # Flag desconocido al final, sin token siguiente: sin IndexError.
    out, dropped = launcher_module.filter_command(["bin", "--fit-target"], {"--model"})
    assert out == ["bin"]
    assert dropped == ["--fit-target"]


def test_filter_command_dropped_flag_consumes_negative_value():
    # El valor "-1" no empieza con "--": se toma como valor del flag
    # descartado y no queda huérfano en el comando.
    cmd = ["bin", "--defrag-thold", "-1", "--metrics"]
    out, dropped = launcher_module.filter_command(cmd, {"--metrics"})
    assert out == ["bin", "--metrics"]
    assert dropped == ["--defrag-thold"]


def test_filter_command_empty_supported_noop():
    # Sin probe (supported vacío) no se toca nada: el error, si lo hay, lo
    # da el propio llama-server en su log.
    cmd = ["bin", "--ctx-checkpoints", "8", "--model", "m.gguf"]
    out, dropped = launcher_module.filter_command(cmd, set())
    assert out == cmd
    assert dropped == []


def test_filter_command_binary_never_filtered():
    out, _ = launcher_module.filter_command(["/opt/llama-server", "--ctx-checkpoints", "8"], set())
    assert out[0] == "/opt/llama-server"


def test_mask_command_masks_api_key():
    cmd = ["bin", "--api-key", "sekret123", "--model", "m.gguf"]
    out = launcher_module.mask_command(cmd)
    assert out == ["bin", "--api-key", "********", "--model", "m.gguf"]
    # La lista original no se modifica: el comando real sigue intacto.
    assert cmd == ["bin", "--api-key", "sekret123", "--model", "m.gguf"]


def test_mask_command_without_secret_noop():
    cmd = ["bin", "--model", "m.gguf", "--metrics"]
    assert launcher_module.mask_command(cmd) == cmd


# --------------------------------------------------------------------------
# Build 11003: probe_binary (capacidades del binario)
#
# probe_binary corre `--help` y `--version` una vez por build (cache por
# mtime+size del archivo) y devuelve los long-flags soportados. Si el probe
# falla, probed=False y NADIE filtra nada.
# --------------------------------------------------------------------------


def test_parse_flag_help_llama_gen_docs_format():
    help_text = (
        "general options:\n"
        "  -h, --help                            show this help message and exit\n"
        "  -m, --model PATH                      model path (default: none)\n"
        "      --host 127.0.0.1                  IP address to listen (default: 127.0.0.1)\n"
        "      --no-reasoning-preserve\n"
        "context & memory:\n"
        "      --ctx-size 4096                   size of the context window (default: 4096)\n"
        "      --fit on                          auto-fit context and KV cache to VRAM\n"
    )
    m = launcher_module._parse_flag_help(help_text)
    # Los aliases apuntan a la misma ayuda; los paréntesis quedan en el texto.
    assert m["--help"] == "show this help message and exit"
    assert m["--model"] == "model path (default: none)"
    assert m["--host"] == "IP address to listen (default: 127.0.0.1)"
    assert m["--ctx-size"] == "size of the context window (default: 4096)"
    assert m["--fit"] == "auto-fit context and KV cache to VRAM"
    # Opción sin descripción -> "" (el flag SÍ está soportado).
    assert m["--no-reasoning-preserve"] == ""
    # Los headers de sección no crean claves.
    assert "general" not in m and "context" not in m


async def test_probe_binary_parses_help_and_version(probeable_binary):
    info = await launcher_module.probe_binary(probeable_binary)
    assert info.probed is True
    assert info.path == probeable_binary
    assert info.build == "b11009"
    assert "b11009" in info.version_line
    # Los flags salen ordenados y sin repetidos.
    assert info.flags == sorted(set(info.flags))
    assert "--model" in info.flags
    assert "--ctx-size" in info.flags
    # Los flags nuevos de memoria NO están en el --help de esta "build".
    assert "--ctx-checkpoints" not in info.flags
    assert "--cache-ram" not in info.flags
    # P1.5: flag_help trae la ayuda oficial de cada flag ("", si no la trae).
    assert info.flag_help["--model"] == "ruta del modelo GGUF"
    assert info.flag_help["--ctx-size"] == "tamano de la ventana de contexto"
    assert info.flag_help["--fit"] == "auto-fit contexto y KV cache en VRAM"
    assert info.flag_help["--jinja"] == ""
    assert set(info.flag_help) == set(info.flags)


async def test_probe_binary_uses_cache(probeable_binary):
    info1 = await launcher_module.probe_binary(probeable_binary)
    info2 = await launcher_module.probe_binary(probeable_binary)
    assert info1 is info2
    assert len(launcher_module._BINARY_CACHE) == 1


async def test_probe_binary_reprobes_when_binary_changes(probeable_binary):
    """El auto-update reemplaza el binario EN LA MISMA RUTA: cambia size/mtime
    y la cache debe invalidarse, sino filtraría contra la build vieja."""
    first = await launcher_module.probe_binary(probeable_binary)
    assert first.probed
    with open(probeable_binary, "ab") as f:
        f.write(b"\n")  # simula reemplazo del archivo
    second = await launcher_module.probe_binary(probeable_binary)
    assert second.probed
    assert len(launcher_module._BINARY_CACHE) == 2


async def test_probe_binary_missing_binary_fails_gracefully(tmp_path):
    info = await launcher_module.probe_binary(str(tmp_path / "no-existe.exe"))
    assert info.probed is False
    assert info.flags == []
    # Set vacío = "sin probe": el filtrado no toca nada.
    assert await launcher_module.probe_supported_flags(str(tmp_path / "no-existe.exe")) == set()


# --------------------------------------------------------------------------
# Build 11003: validaciones de LaunchConfig (FA_QUANTS + rangos nuevos)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("k,v", [("q4_0", "q8_0"), ("f16", "q4_0"), ("q8_0", "f16")])
def test_fa_kv_whitelist_rejects_asymmetric(k, v):
    with pytest.raises(ValidationError) as exc_info:
        launcher_module.LaunchConfig(model_id="m", flash_attn=True, cache_type_k=k, cache_type_v=v)
    assert "simétrico" in str(exc_info.value)


@pytest.mark.parametrize("k,v", sorted(launcher_module.FA_KV_WHITELIST))
def test_fa_kv_whitelist_allows_symmetric(k, v):
    cfg = launcher_module.LaunchConfig(model_id="m", flash_attn=True, cache_type_k=k, cache_type_v=v)
    assert (cfg.cache_type_k, cfg.cache_type_v) == (k, v)


def test_fa_kv_whitelist_draft_mixed_rejected():
    # None = f16 (default de llama-server): el par q8_0/f16 queda afuera del
    # whitelist, igual que los del modelo principal.
    with pytest.raises(ValidationError):
        launcher_module.LaunchConfig(
            model_id="m", mtp_embedded=True,
            cache_type_k_draft="q8_0", cache_type_v_draft=None,
        )


def test_fa_kv_whitelist_draft_defaults_ok():
    cfg = launcher_module.LaunchConfig(model_id="m", mtp_embedded=True)
    assert (cfg.cache_type_k_draft, cfg.cache_type_v_draft) == ("q8_0", "q8_0")


def test_fa_kv_whitelist_off_allows_asymmetric():
    cfg = launcher_module.LaunchConfig(
        model_id="m", flash_attn=False, cache_type_k="q4_0", cache_type_v="q8_0",
    )
    assert (cfg.cache_type_k, cfg.cache_type_v) == ("q4_0", "q8_0")


def test_legacy_cache_types_migrated_in_silence():
    # Templates/configs guardados con tipos fuera del schema no rompen el
    # lanzamiento: se migran con warning en vez de 422.
    cfg = launcher_module.LaunchConfig(
        model_id="m", cache_type_k="q4_1", cache_type_v_draft="q5_0",
    )
    assert cfg.cache_type_k == "q4_0"
    assert cfg.cache_type_v_draft == "q8_0"


@pytest.mark.parametrize(
    "field,bad",
    [
        ("ctx_checkpoints", -1),
        ("checkpoint_min_step", 256),
        ("cache_ram_mib", -1),
        ("fit_target_mib", -1),
        ("budget_tokens", -2),
    ],
)
def test_new_memory_field_ranges_rejected(field, bad):
    with pytest.raises(ValidationError):
        launcher_module.LaunchConfig(model_id="m", **{field: bad})


def test_new_memory_field_defaults_valid():
    cfg = launcher_module.LaunchConfig(model_id="m")
    assert cfg.ctx_checkpoints == 8
    assert cfg.checkpoint_min_step == 16384
    assert cfg.cache_ram_mib == 8192
    assert cfg.fit_target_mib == 0
    assert cfg.kv_unified is False
    assert cfg.no_reasoning_preserve is False
    assert cfg.n_batch == 2048


# --------------------------------------------------------------------------
# Build 11003: build_llama_server_command con los flags nuevos
# --------------------------------------------------------------------------


def test_build_command_memory_flags_explicit():
    cfg = launcher_module.LaunchConfig(
        model_id="m",
        ctx_checkpoints=4,
        checkpoint_min_step=8192,
        cache_ram_mib=4096,
        fit_target_mib=2048,
        kv_unified=True,
    )
    cmd = launcher_module.build_llama_server_command(cfg, "/opt/llama-server", "/models/m.gguf")
    assert cmd[cmd.index("--ctx-checkpoints") + 1] == "4"
    assert cmd[cmd.index("--checkpoint-min-step") + 1] == "8192"
    assert cmd[cmd.index("--cache-ram") + 1] == "4096"
    assert cmd[cmd.index("--fit-target") + 1] == "2048"
    assert "--kv-unified" in cmd


def test_build_command_memory_flags_defaults():
    cfg = launcher_module.LaunchConfig(model_id="m")
    cmd = launcher_module.build_llama_server_command(cfg, "/opt/llama-server", "/models/m.gguf")
    # Van siempre: son los grandes consumidores de VRAM/RAM que la UI acota.
    assert cmd[cmd.index("--ctx-checkpoints") + 1] == "8"
    assert cmd[cmd.index("--checkpoint-min-step") + 1] == "16384"
    assert cmd[cmd.index("--cache-ram") + 1] == "8192"
    # Off por default.
    assert "--fit-target" not in cmd
    assert "--kv-unified" not in cmd


def test_build_command_threads_and_numa_mode():
    cmd = launcher_module.build_llama_server_command(
        launcher_module.LaunchConfig(model_id="m"), "/opt/llama-server", "/models/m.gguf")
    # n_threads=-1 (auto) no se pasa explícitamente.
    assert "--threads" not in cmd
    assert "--numa" not in cmd

    cfg = launcher_module.LaunchConfig(model_id="m", n_threads=4, numa=True)
    cmd = launcher_module.build_llama_server_command(cfg, "/opt/llama-server", "/models/m.gguf")
    assert cmd[cmd.index("--threads") + 1] == "4"
    # La build 11003 exige el modo de --numa.
    assert cmd[cmd.index("--numa") + 1] == "distribute"


def test_build_command_vram_multi_modelo_explicit():
    cfg = launcher_module.LaunchConfig(
        model_id="m",
        warmup=False,
        sleep_idle_seconds=300,
        kv_unified_per_slot=65536,
        lazy_mode="on",
    )
    cmd = launcher_module.build_llama_server_command(cfg, "/opt/llama-server", "/models/m.gguf")
    assert "--no-warmup" in cmd
    assert cmd[cmd.index("--sleep-idle-seconds") + 1] == "300"
    assert cmd[cmd.index("--kv-unified-per-slot") + 1] == "65536"
    assert cmd[cmd.index("--lazy-mode") + 1] == "on"


def test_build_command_vram_multi_modelo_defaults():
    cmd = launcher_module.build_llama_server_command(
        launcher_module.LaunchConfig(model_id="m"), "/opt/llama-server", "/models/m.gguf")
    # Defaults del binario: warmup on, sin sleep/per-slot/lazy (auto).
    # Solo se envía lo que difiere, así ninguno aparece.
    assert "--no-warmup" not in cmd
    assert "--warmup" not in cmd
    assert "--sleep-idle-seconds" not in cmd
    assert "--kv-unified-per-slot" not in cmd
    assert "--lazy-mode" not in cmd


def test_build_command_thinking_flag_full():
    cfg = launcher_module.LaunchConfig(
        model_id="m", thinking_enabled=True, budget_tokens=4096, reasoning_effort="medium",
    )
    cmd = launcher_module.build_llama_server_command(cfg, "/opt/llama-server", "/models/m.gguf")
    # reasoning_effort va por el flag nativo --reasoning-effort (b11009), ya no
    # por chat_template_kwargs: el flag es la versión server-level del mismo.
    assert cmd[cmd.index("--reasoning-effort") + 1] == "medium"
    # thinking_enabled va por el flag nativo --reasoning: el kwarg
    # enable_thinking en --chat-template-kwargs quedó deprecado en llama.cpp.
    assert cmd[cmd.index("--reasoning") + 1] == "on"
    # budget_tokens → --reasoning-budget nativo (reasoning_budget=-1).
    assert cmd[cmd.index("--reasoning-budget") + 1] == "4096"
    assert "--chat-template-kwargs" not in cmd


def test_build_command_reasoning_budget_precedence():
    # reasoning_budget explícito tiene prioridad sobre budget_tokens:
    # --reasoning-budget se emite una sola vez, con el valor explícito.
    cfg = launcher_module.LaunchConfig(
        model_id="m", thinking_enabled=True, budget_tokens=8192, reasoning_budget=4096)
    cmd = launcher_module.build_llama_server_command(cfg, "/opt/llama-server", "/models/m.gguf")
    assert cmd.count("--reasoning-budget") == 1
    assert cmd[cmd.index("--reasoning-budget") + 1] == "4096"


def test_build_command_reasoning_effort_none_not_sent():
    # 'none' (default) no emite el flag: mantiene el default del template.
    cfg = launcher_module.LaunchConfig(model_id="m")
    cmd = launcher_module.build_llama_server_command(cfg, "/opt/llama-server", "/models/m.gguf")
    assert "--reasoning-effort" not in cmd
    assert "--reasoning-budget" not in cmd


def test_build_command_reasoning_budget_native():
    cfg = launcher_module.LaunchConfig(model_id="m", reasoning_budget=4096)
    cmd = launcher_module.build_llama_server_command(cfg, "/opt/llama-server", "/models/m.gguf")
    assert cmd[cmd.index("--reasoning-budget") + 1] == "4096"
    # -1 (default) no se envía.
    cmd_default = launcher_module.build_llama_server_command(
        launcher_module.LaunchConfig(model_id="m"), "/opt/llama-server", "/models/m.gguf")
    assert "--reasoning-budget" not in cmd_default


def test_build_command_thinking_flag_defaults():
    cfg = launcher_module.LaunchConfig(model_id="m")
    cmd = launcher_module.build_llama_server_command(cfg, "/opt/llama-server", "/models/m.gguf")
    # --reasoning va siempre (con --jinja) para que el template tenga el valor
    # explícito: off por default, on con thinking_enabled.
    assert cmd[cmd.index("--reasoning") + 1] == "off"
    assert "--chat-template-kwargs" not in cmd


def test_build_command_thinking_budget_negative_not_sent():
    cfg = launcher_module.LaunchConfig(model_id="m", thinking_enabled=True, budget_tokens=-1)
    cmd = launcher_module.build_llama_server_command(cfg, "/opt/llama-server", "/models/m.gguf")
    assert cmd[cmd.index("--reasoning") + 1] == "on"
    assert "--reasoning-budget" not in cmd


def test_build_command_thinking_without_jinja_not_sent():
    cfg = launcher_module.LaunchConfig(model_id="m", thinking_enabled=True, jinja=False)
    cmd = launcher_module.build_llama_server_command(cfg, "/opt/llama-server", "/models/m.gguf")
    assert "--reasoning" not in cmd
    assert "--jinja" not in cmd


def test_build_command_no_reasoning_preserve():
    cfg = launcher_module.LaunchConfig(model_id="m", no_reasoning_preserve=True)
    cmd = launcher_module.build_llama_server_command(cfg, "/opt/llama-server", "/models/m.gguf")
    assert "--no-reasoning-preserve" in cmd

    cmd_default = launcher_module.build_llama_server_command(
        launcher_module.LaunchConfig(model_id="m"), "/opt/llama-server", "/models/m.gguf")
    assert "--no-reasoning-preserve" not in cmd_default


# --------------------------------------------------------------------------
# P1.5: toggles por flag — OFF (None) = el builder no emite el flag
# --------------------------------------------------------------------------


TOGGLE_FIELDS = (
    "n_ctx", "n_batch", "n_ubatch", "cache_type_k", "cache_type_v",
    "rope_scaling_type", "rope_freq_base", "yarn_ext_factor",
    "cache_reuse", "defrag_thold", "grp_attn_n", "grp_attn_w",
    "cache_ram_mib", "fit", "fit_target_mib",
    "checkpoint_min_step", "ctx_checkpoints", "n_parallel", "n_threads",
)


def _toggled_flag_value(cmd: list[str], flag: str) -> str | None:
    if flag not in cmd:
        return None
    return cmd[cmd.index(flag) + 1]


def _all_toggles_off_config(**overrides) -> "launcher_module.LaunchConfig":
    base = {field: None for field in TOGGLE_FIELDS}
    base.update(overrides)
    return launcher_module.LaunchConfig(model_id="m", **base)


def test_all_toggles_off_emits_none_of_the_toggled_flags():
    cmd = launcher_module.build_llama_server_command(
        _all_toggles_off_config(), "/opt/llama-server", "/models/m.gguf")
    for flag in ("--ctx-size", "--batch-size", "--ubatch-size",
                 "--cache-type-k", "--cache-type-v",
                 "--rope-scaling", "--rope-freq-base", "--yarn-ext-factor",
                 "--cache-reuse", "--defrag-thold", "--grp-attn-n", "--grp-attn-w",
                 "--cache-ram", "--fit", "--fit-target", "--checkpoint-min-step",
                 "--ctx-checkpoints", "--parallel", "--threads", "--host"):
        assert flag not in cmd, f"{flag} no debe emitirse con su toggle OFF"
    # Inquebrantable: --model y --port siempre van.
    assert "--model" in cmd and "--port" in cmd


@pytest.mark.parametrize("overrides,flag", [
    ({"n_ctx": 4096}, "--ctx-size"),
    ({"n_batch": 1024}, "--batch-size"),
    ({"n_ubatch": 1024}, "--ubatch-size"),
    ({"cache_type_k": "f16", "cache_type_v": "f16"}, "--cache-type-k"),
    ({"mmproj_path": "/models/mm.gguf"}, "--mmproj"),
    ({"rope_scaling_type": "yarn", "rope_freq_base": 10000.0, "yarn_ext_factor": 460.8},
     "--rope-scaling"),
    ({"cache_reuse": 128}, "--cache-reuse"),
    ({"defrag_thold": 0.5}, "--defrag-thold"),
    ({"grp_attn_n": 2, "grp_attn_w": 1024}, "--grp-attn-n"),
    ({"cache_ram_mib": 4096}, "--cache-ram"),
    ({"fit": True}, "--fit"),
    ({"fit_target_mib": 2048}, "--fit-target"),
    ({"checkpoint_min_step": 8192}, "--checkpoint-min-step"),
    ({"ctx_checkpoints": 4}, "--ctx-checkpoints"),
    ({"n_parallel": 2}, "--parallel"),
    ({"n_threads": 8}, "--threads"),
    ({"host": "0.0.0.0"}, "--host"),
])
def test_toggle_on_sends_flag(overrides, flag):
    cmd = launcher_module.build_llama_server_command(
        _all_toggles_off_config(**overrides), "/opt/llama-server", "/models/m.gguf")
    assert flag in cmd


@pytest.mark.parametrize("field,flag", [
    ("n_ctx", "--ctx-size"),
    ("n_batch", "--batch-size"),
    ("n_ubatch", "--ubatch-size"),
    ("cache_type_k", "--cache-type-k"),
    ("cache_type_v", "--cache-type-v"),
    ("ctx_checkpoints", "--ctx-checkpoints"),
    ("checkpoint_min_step", "--checkpoint-min-step"),
    ("cache_ram_mib", "--cache-ram"),
    ("n_parallel", "--parallel"),
])
def test_toggle_roundtrip_default_sends_none_omits(field, flag):
    cmd_on = launcher_module.build_llama_server_command(
        launcher_module.LaunchConfig(model_id="m"), "/opt/llama-server", "/models/m.gguf")
    assert flag in cmd_on
    cmd_off = launcher_module.build_llama_server_command(
        _all_toggles_off_config(**{field: None}), "/opt/llama-server", "/models/m.gguf")
    assert flag not in cmd_off


def test_fit_default_on_off_omits():
    cmd = launcher_module.build_llama_server_command(
        launcher_module.LaunchConfig(model_id="m"), "/opt/llama-server", "/models/m.gguf")
    assert _toggled_flag_value(cmd, "--fit") == "on"
    cmd_off = launcher_module.build_llama_server_command(
        _all_toggles_off_config(fit=None), "/opt/llama-server", "/models/m.gguf")
    assert "--fit" not in cmd_off


def test_rope_group_off_sends_nothing_even_with_values():
    cfg = _all_toggles_off_config(
        rope_scaling_type=None, rope_freq_base=10000.0, yarn_ext_factor=460.8)
    cmd = launcher_module.build_llama_server_command(cfg, "/opt/llama-server", "/models/m.gguf")
    for flag in ("--rope-scaling", "--rope-freq-base", "--yarn-ext-factor"):
        assert flag not in cmd


def test_host_default_not_sent_custom_sent():
    cmd = launcher_module.build_llama_server_command(
        launcher_module.LaunchConfig(model_id="m"), "/opt/llama-server", "/models/m.gguf")
    assert "--host" not in cmd
    cmd2 = launcher_module.build_llama_server_command(
        launcher_module.LaunchConfig(model_id="m", host="0.0.0.0"),
        "/opt/llama-server", "/models/m.gguf")
    assert _toggled_flag_value(cmd2, "--host") == "0.0.0.0"


# F4: modo automático — comando estricto -m + --port (defaults de build).


def test_auto_mode_strict_command():
    cfg = launcher_module.LaunchConfig(model_id="m", auto_mode=True)
    cmd = launcher_module.build_llama_server_command(cfg, "/opt/llama-server", "/models/m.gguf")
    assert cmd == [
        "/opt/llama-server",
        "--model", "/models/m.gguf",
        "--port", "8080",
        "--verbosity", "4",
        "--metrics",
    ]


def test_auto_mode_ignores_every_tuning_field():
    # Con todo explícito, auto_mode gana: ni un flag de tuning en el argv.
    cfg = _all_toggles_off_config(
        auto_mode=True, n_ctx=None, n_batch=None,
        n_ubatch=1024, n_gpu_layers=33, cache_type_k=None, cache_type_v=None,
        flash_attn=True, temperature=1.3, top_p=0.5, jinja=False,
        reasoning_effort="high", reasoning_budget=4096,
        mtp_embedded=True, n_draft=8,
        mmproj_path="/models/mm.gguf", lora_path="/models/lora.gguf",
        warmup=False, sleep_idle_seconds=60,
    )
    cmd = launcher_module.build_llama_server_command(cfg, "/opt/llama-server", "/models/m.gguf")
    tuning_flags = (
        "--ctx-size", "--batch-size", "--ubatch-size", "--n-gpu-layers",
        "--cache-type-k", "--cache-type-v", "--flash-attn", "--temp", "--top-p",
        "--jinja", "--no-jinja", "--reasoning", "--reasoning-effort",
        "--reasoning-budget",
        "--spec-type", "--mmproj", "--lora", "--no-warmup", "--sleep-idle-seconds",
        "--ctx-checkpoints", "--checkpoint-min-step", "--cache-ram", "--fit",
        "--fit-target", "--parallel", "--threads", "--chat-template-kwargs",
    )
    for flag in tuning_flags:
        assert flag not in cmd, f"{flag} no debe emitirse en modo automático"
    assert cmd[1:3] == ["--model", "/models/m.gguf"]
    assert "--port" in cmd and "--metrics" in cmd and "--verbosity" in cmd


def test_auto_mode_sends_host_only_when_not_default():
    cmd = launcher_module.build_llama_server_command(
        launcher_module.LaunchConfig(model_id="m", auto_mode=True),
        "/opt/llama-server", "/models/m.gguf")
    assert "--host" not in cmd
    cmd2 = launcher_module.build_llama_server_command(
        launcher_module.LaunchConfig(model_id="m", auto_mode=True, host="0.0.0.0"),
        "/opt/llama-server", "/models/m.gguf")
    assert _toggled_flag_value(cmd2, "--host") == "0.0.0.0"


# --------------------------------------------------------------------------
# Build 11003: endpoints /preview-command y /backend-info
#
# El binario probeable simula una build que NO trae los flags nuevos de
# memoria: el probe los detecta como no soportados y el filtrado/preview los
# reporta en `dropped` para que la UI lo avise antes de lanzar.
# --------------------------------------------------------------------------


async def _first_model_id(client, sample_model_dir) -> str:
    await client.post("/api/config", json={"model_dirs": [str(sample_model_dir)]})
    await scan_and_wait(client)
    return (await client.get("/api/models")).json()[0]["id"]


async def _configure_llama_binary(client, binary_path):
    await client.post(
        "/api/config",
        json={
            "backends": {
                "llama_server": {
                    "binary_path": binary_path,
                    "default_port": 8080,
                    "enabled": True,
                }
            }
        },
    )


async def test_backend_info_requires_binary(client):
    res = await client.get("/api/launcher/backend-info")
    assert res.status_code == 400
    assert "binary_path" in res.json()["detail"]


async def test_backend_info_returns_probe(client, probeable_binary):
    await _configure_llama_binary(client, probeable_binary)
    res = await client.get("/api/launcher/backend-info")
    assert res.status_code == 200
    data = res.json()
    assert data["probed"] is True
    assert data["build"] == "b11009"
    assert "--model" in data["flags"]
    assert "--ctx-checkpoints" not in data["flags"]
    # P1.5: el mapa de ayuda oficial viaja en la respuesta para los tooltips.
    assert data["flag_help"]["--ctx-size"] == "tamano de la ventana de contexto"


async def test_preview_command_unknown_model(client, probeable_binary):
    await _configure_llama_binary(client, probeable_binary)
    res = await client.post("/api/launcher/preview-command", json={"model_id": "no-existe"})
    assert res.status_code == 404


async def test_preview_command_requires_binary(client, sample_model_dir):
    model_id = await _first_model_id(client, sample_model_dir)
    res = await client.post("/api/launcher/preview-command", json={"model_id": model_id})
    assert res.status_code == 400
    assert "binary_path" in res.json()["detail"]


async def test_preview_command_masks_key_and_reports_dropped(client, sample_model_dir, probeable_binary):
    model_id = await _first_model_id(client, sample_model_dir)
    await _configure_llama_binary(client, probeable_binary)

    res = await client.post(
        "/api/launcher/preview-command",
        json={"model_id": model_id, "api_key": "sekret123", "n_parallel": 2},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["probed"] is True
    assert data["build"] == "b11009"
    cmd = data["command"]
    # La api-key nunca sale en claro por la API.
    assert "--api-key" in cmd
    assert cmd[cmd.index("--api-key") + 1] == "********"
    assert "sekret123" not in cmd
    # Los flags nuevos no están en la "build" fake: el filtrado los descarta
    # y el preview los reporta para que la UI lo avise.
    assert set(data["dropped"]) == {"--ctx-checkpoints", "--checkpoint-min-step", "--cache-ram"}
    assert "--ctx-checkpoints" not in cmd
    assert "--model" in cmd


async def test_preview_command_lm_studio_empty(client, sample_model_dir):
    model_id = await _first_model_id(client, sample_model_dir)
    res = await client.post(
        "/api/launcher/preview-command",
        json={"model_id": model_id, "backend": "lm_studio"},
    )
    assert res.status_code == 200
    assert res.json()["command"] == []


async def test_preview_command_ollama(client, sample_model_dir):
    model_id = await _first_model_id(client, sample_model_dir)
    res = await client.post(
        "/api/launcher/preview-command",
        json={"model_id": model_id, "backend": "ollama"},
    )
    assert res.status_code == 200
    data = res.json()
    # Sin probe: el comando de ollama tal cual (binario + "run" + modelo).
    assert len(data["command"]) == 3
    assert data["command"][1] == "run"
    assert data["dropped"] == []
    assert data["probed"] is False


async def test_launch_with_probe_filters_flags_and_masks_command(client, sample_model_dir, probeable_binary):
    """E2E: el launch real filtra contra el probe y expone el argv (mascarado)
    en `command`, para que la UI muestre qué se ejecutó de verdad."""
    model_id = await _first_model_id(client, sample_model_dir)
    await _configure_llama_binary(client, probeable_binary)

    launched = (
        await client.post(
            "/api/launcher/launch",
            json={"model_id": model_id, "api_key": "sekret123", "port": 39997},
        )
    ).json()
    process_id = launched["process_id"]
    assert launched["state"] == "starting"

    cmd = launched["command"]
    assert cmd, "el proceso debe exponer su argv (ya filtrado y enmascarado)"
    assert cmd[0] == probeable_binary
    # api-key enmascarada.
    assert cmd[cmd.index("--api-key") + 1] == "********"
    assert "sekret123" not in cmd
    # Flags nuevos descartados por el probe de la "build" vieja.
    assert "--ctx-checkpoints" not in cmd
    assert "--checkpoint-min-step" not in cmd
    assert "--cache-ram" not in cmd
    # Clásicos intactos.
    assert "--model" in cmd
    assert "--metrics" in cmd

    res = await client.post(f"/api/launcher/stop/{process_id}")
    assert res.status_code == 200


async def test_launch_rejects_binary_missing_critical_flag(client, sample_model_dir, make_probeable_binary):
    """Si la build no conoce un flag crítico (--model/--host/--port/--ctx-size)
    es un llama.cpp incompatible: 400 claro en vez de comando mutilado."""
    model_id = await _first_model_id(client, sample_model_dir)
    bad_binary = make_probeable_binary([
        "--model PATH",
        "--host 127.0.0.1",
        "--ctx-size 4096",
        # Le falta --port.
    ])
    await _configure_llama_binary(client, bad_binary)

    res = await client.post("/api/launcher/launch", json={"model_id": model_id})
    assert res.status_code == 400
    assert "críticos" in res.json()["detail"]
    assert "--port" in res.json()["detail"]
    # No quedó ningún proceso registrado.
    assert (await client.get("/api/launcher/status")).json() == []


# --------------------------------------------------------------------------
# thinking-aware: el builder solo envía --reasoning si el chat_template del
# modelo lee enable_thinking (detectado del header GGUF).
# --------------------------------------------------------------------------


def test_build_command_thinking_flag_gate():
    cfg = launcher_module.LaunchConfig(
        model_id="m",
        backend="llama_server",
        jinja=True,
        thinking_enabled=True,
        budget_tokens=4096,
    )
    with_flag = launcher_module.build_llama_server_command(cfg, "/opt/llama-server", "/models/m.gguf")
    assert "--reasoning" in with_flag

    without_flag = launcher_module.build_llama_server_command(
        cfg, "/opt/llama-server", "/models/m.gguf", enable_thinking_supported=False
    )
    assert "--reasoning" not in without_flag
    assert "--reasoning-budget" not in without_flag


async def test_preview_command_skips_reasoning_for_plain_template(client, probeable_binary, tmp_path):
    model_dir = tmp_path / "m"
    model_dir.mkdir()
    write_gguf(model_dir / "Plain-8B-Q4_K_M.gguf", "llama", "{{ messages | join(' ') }}")
    await client.post("/api/config", json={"model_dirs": [str(model_dir)]})
    await scan_and_wait(client)
    model_id = (await client.get("/api/models")).json()[0]["id"]
    await _configure_llama_binary(client, probeable_binary)

    res = await client.post(
        "/api/launcher/preview-command",
        json={"model_id": model_id, "thinking_enabled": True, "budget_tokens": 4096},
    )
    assert res.status_code == 200
    cmd = res.json()["command"]
    assert "--reasoning" not in cmd


async def test_preview_command_sends_reasoning_for_thinking_template(client, probeable_binary, tmp_path):
    model_dir = tmp_path / "m"
    model_dir.mkdir()
    write_gguf(model_dir / "Thinker-8B-Q4_K_M.gguf", "qwen3", "{% if enable_thinking %}<|think|>{% endif %}")
    await client.post("/api/config", json={"model_dirs": [str(model_dir)]})
    await scan_and_wait(client)
    model_id = (await client.get("/api/models")).json()[0]["id"]
    await _configure_llama_binary(client, probeable_binary)

    res = await client.post(
        "/api/launcher/preview-command",
        json={"model_id": model_id, "thinking_enabled": True, "budget_tokens": 4096},
    )
    assert res.status_code == 200
    cmd = res.json()["command"]
    assert cmd[cmd.index("--reasoning") + 1] == "on"
    assert cmd[cmd.index("--reasoning-budget") + 1] == "4096"
