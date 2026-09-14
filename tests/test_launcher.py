"""Tests de launcher.py — construcción de comandos, templates en SQLite, ciclo de vida de procesos."""

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
    assert "--draft-model" in cmd and cmd[cmd.index("--draft-model") + 1] == "/models/draft.gguf"
    assert "--draft" in cmd and cmd[cmd.index("--draft") + 1] == "6"
    assert "--mmproj" in cmd and cmd[cmd.index("--mmproj") + 1] == "/models/mmproj.gguf"
    assert "--lora" in cmd and cmd[cmd.index("--lora") + 1] == "/models/lora.gguf"
    assert "--lora-scale" in cmd and cmd[cmd.index("--lora-scale") + 1] == "0.8"
    assert "--api-key" in cmd and cmd[cmd.index("--api-key") + 1] == "secret"
    assert "--log-file" in cmd and cmd[cmd.index("--log-file") + 1] == "data/logs/test.log"


def test_build_command_no_optional():
    cfg = launcher_module.LaunchConfig(model_id="m", backend="llama_server")
    cmd = launcher_module.build_llama_server_command(cfg, "/opt/llama-server", "/models/model.gguf")

    assert "--draft-model" not in cmd
    assert "--draft" not in cmd
    assert "--mmproj" not in cmd
    assert "--lora" not in cmd
    assert "--lora-scale" not in cmd
    assert "--mlock" not in cmd  # use_mlock=False por default
    assert "--no-mmap" not in cmd  # use_mmap=True por default -> no se agrega el flag negativo
    assert "--api-key" not in cmd  # api_key="" por default


def test_build_command_with_mtp():
    cfg = launcher_module.LaunchConfig(model_id="m", mtp_draft_model="/models/draft.gguf", n_draft=8)
    cmd = launcher_module.build_llama_server_command(cfg, "/opt/llama-server", "/models/model.gguf")

    assert "--draft-model" in cmd
    assert cmd[cmd.index("--draft-model") + 1] == "/models/draft.gguf"
    assert "--draft" in cmd
    assert cmd[cmd.index("--draft") + 1] == "8"


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
