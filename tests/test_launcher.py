"""Tests de launcher.py — construcción de comandos, templates en SQLite, ciclo de vida de procesos."""

import pytest

import launcher as launcher_module
from helpers import scan_and_wait, wait_until

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
        use_mlock=True,
        use_mmap=False,
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
    assert "--mlock" in cmd
    assert "--no-mmap" in cmd
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
    assert "--mlock" not in cmd  # use_mlock=False por default
    assert "--no-mmap" not in cmd  # use_mmap=True por default -> no se agrega el flag negativo
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
    p = launcher_module.resolve_log_path("data/logs/llama-server.log", "abc")
    assert p == (launcher_module.BASE_DIR / "data" / "logs" / "llama-server.log").resolve()


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
    """POST /api/launcher/launch con log_file fuera de BASE_DIR -> 400, sin proceso."""
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
    # Ni un proceso quedó registrado ni se escribió nada fuera de BASE_DIR
    assert (await client.get("/api/launcher/status")).json() == []
    assert not (launcher_module.BASE_DIR.parent / "outside.log").exists()


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


async def test_cannot_delete_builtin_template(client):
    """Los templates builtin no se pueden eliminar."""
    from database import db_upsert_template

    await db_upsert_template("Builtin Test", builtin=True, params={"n_ctx": 4096})

    res = await client.delete("/api/launcher/templates/Builtin Test")
    assert res.status_code == 403

    listed = (await client.get("/api/launcher/templates")).json()
    assert any(t["name"] == "Builtin Test" for t in listed)


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
    "n_batch": 512,
    "n_ubatch": 512,
    "n_gpu_layers": -1,
    "gpu_mode": "gpu_only",
    "cache_type_k": "q4_0",
    "cache_type_v": "q4_0",
    "flash_attn": True,
    "use_mlock": False,
    "use_mmap": True,
    "mtp_draft_model": None,
    "mtp_embedded": True,
    "n_draft": 5,
    "cache_type_k_draft": None,
    "cache_type_v_draft": None,
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
    "presence_penalty": 0.0,
    "repeat_penalty": 1.5,
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

    async def has_logs():
        logs = (await client.get(f"/api/launcher/logs/{process_id}")).json()
        return len(logs["lines"]) > 0

    assert await wait_until(has_logs, timeout=3.0)

    await client.post(f"/api/launcher/stop/{process_id}")
