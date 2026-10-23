"""Tests de agent_bridge.py — AG-1 (integracion OpenCode).

Todo contra un config temporal redirigido con OPENCODE_CONFIG: nunca contra
el ~/.config/opencode real del usuario. Los tests unitarios usan el puerto
39080 sin nada escuchando: /props falla y el limit sale de la n_ctx de la
config, que es deterministico sin servers reales.
"""

import json

import pytest

import agent_bridge
import launcher as launcher_module
from helpers import scan_and_wait, wait_until


@pytest.fixture(autouse=True)
def bridge_config(tmp_path, monkeypatch):
    agent_bridge._reset_for_tests()
    path = tmp_path / "opencode.jsonc"
    monkeypatch.setenv("OPENCODE_CONFIG", str(path))
    yield path
    agent_bridge._reset_for_tests()


@pytest.fixture
def bridge_on(monkeypatch):
    # Via unitaria: el toggle se enciende en el modulo. Los tests que van por
    # API usan el toggle real via POST /api/config (config aislada por test).
    monkeypatch.setattr(agent_bridge, "enabled", lambda: True)


def _info(pid="proc-1", model="test-model", port=39080, host="127.0.0.1", **overrides):
    launch = {"n_ctx": 65536, "api_key": "", "mmproj_path": None}
    launch.update(overrides)
    return launcher_module.ProcessInfo(
        process_id=pid,
        model_id=model,
        model_name=model,
        backend="llama_server",
        state="running",
        host=host,
        port=port,
        launch_config=launch,
    )


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path, data):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# Config V1 similar al real del usuario: providers con npm/options y modelos
# con modalities/limit. Incluye la clave exacta "glyvex", que el bridge debe
# dejar intacta (podria ser un bloque armado a mano).
V1_USER = {
    "provider": {
        "Glyvex-AI": {
            "npm": "@ai-sdk/openai-compatible",
            "name": "Glyvex-AI",
            "options": {"baseURL": "http://192.168.50.55:8080/v1", "apiKey": "k-real"},
            "models": {"m1": {"name": "m1", "limit": {"context": 8192, "output": 4096}}},
        },
        "glyvex": {
            "npm": "@ai-sdk/openai-compatible",
            "name": "Manual",
            "options": {"baseURL": "http://127.0.0.1:1234/v1", "apiKey": "local"},
            "models": {},
        },
    },
    "disabled_providers": [],
    "theme": "dark",
}

V2_USER = {
    "$schema": "https://opencode.ai/config.json",
    "providers": {
        "other": {
            "name": "Other",
            "package": "@opencode/ai/providers/openai-compatible",
            "settings": {"baseURL": "http://127.0.0.1:1111/v1", "apiKey": "local"},
            "models": {"m1": {"name": "m1"}},
        }
    },
}


async def test_publish_creates_v2_canonical_when_file_missing(bridge_on, bridge_config):
    assert await agent_bridge.publish(_info())
    data = _read(bridge_config)
    assert data["$schema"] == "https://opencode.ai/config.json"
    provider = data["providers"]["glyvex"]
    assert provider["package"] == agent_bridge.PACKAGE_V2
    assert provider["settings"]["baseURL"] == "http://127.0.0.1:39080/v1"
    entry = provider["models"]["test-model"]
    assert entry["modelID"] == "test-model"
    # Sin api_key en el launch el bloque queda autocontenido con el placeholder.
    assert entry["settings"] == {"baseURL": "http://127.0.0.1:39080/v1", "apiKey": "local"}
    assert entry["capabilities"] == {"tools": True, "input": ["text"], "output": ["text"]}
    assert entry["limit"] == {"context": 65536, "output": 8192}


async def test_publish_v1_mirrors_style_and_preserves_user(bridge_on, bridge_config):
    _write_json(bridge_config, V1_USER)
    assert await agent_bridge.publish(_info(api_key="k-secret"))
    data = _read(bridge_config)
    # Los bloques del usuario quedan identicos: el bridge solo agrega la suya.
    assert data["provider"]["Glyvex-AI"] == V1_USER["provider"]["Glyvex-AI"]
    assert data["provider"]["glyvex"] == V1_USER["provider"]["glyvex"]
    assert data["disabled_providers"] == [] and data["theme"] == "dark"
    assert "$schema" not in data
    block = data["provider"]["glyvex-39080"]
    assert block["npm"] == agent_bridge.NPM_V1
    assert block["name"] == agent_bridge.PROVIDER_NAME
    assert block["options"] == {"baseURL": "http://127.0.0.1:39080/v1", "apiKey": "k-secret"}
    entry = block["models"]["test-model"]
    assert entry["modalities"] == {"input": ["text"], "output": ["text"]}
    assert entry["limit"] == {"context": 65536, "output": 8192}
    # V1 no debe recibir los campos V2 (capabilities/modelID).
    assert "capabilities" not in entry and "modelID" not in entry


async def test_publish_v2_preserves_other_providers_and_declares_vision(
    bridge_on, bridge_config
):
    _write_json(bridge_config, V2_USER)
    await agent_bridge.publish(_info(mmproj_path="C:/models/mmproj.gguf"))
    data = _read(bridge_config)
    assert data["providers"]["other"] == V2_USER["providers"]["other"]
    entry = data["providers"]["glyvex"]["models"]["test-model"]
    # Vision solo porque el launch trajo mmproj_path.
    assert entry["capabilities"]["input"] == ["text", "image"]


async def test_no_mmproj_declares_text_only(bridge_on, bridge_config):
    await agent_bridge.publish(_info())
    entry = _read(bridge_config)["providers"]["glyvex"]["models"]["test-model"]
    assert entry["capabilities"]["input"] == ["text"]


async def test_limits_bound_output_and_model_key_collision(bridge_on, bridge_config):
    await agent_bridge.publish(_info(pid="a", n_ctx=4096))
    await agent_bridge.publish(_info(pid="b", n_ctx=None))
    models = _read(bridge_config)["providers"]["glyvex"]["models"]
    # output = ctx//2 con piso 1024 y tope 8192 (el rango de los bloques
    # manuales del usuario); sin ctx del launch ni /props, fallback conservador.
    assert models["test-model"]["limit"] == {"context": 4096, "output": 2048}
    assert models["test-model-2"]["limit"] == {
        "context": agent_bridge.FALLBACK_CONTEXT,
        "output": agent_bridge.FALLBACK_CONTEXT // 2,
    }


async def test_multi_server_v1_one_provider_per_port(bridge_on, bridge_config):
    _write_json(bridge_config, V1_USER)
    await agent_bridge.publish(_info(pid="a", model="model-a", port=39080))
    await agent_bridge.publish(_info(pid="b", model="model-b", port=39081))
    data = _read(bridge_config)
    # V1 admite una sola baseURL por provider: un provider por endpoint, como
    # hace el config manual del usuario. Las claves del usuario siguen intactas.
    assert set(data["provider"]) == {"Glyvex-AI", "glyvex", "glyvex-39080", "glyvex-39081"}
    assert "model-a" in data["provider"]["glyvex-39080"]["models"]
    assert "model-b" in data["provider"]["glyvex-39081"]["models"]


async def test_unpublish_removes_entries_then_the_provider_key(bridge_on, bridge_config):
    _write_json(bridge_config, V1_USER)
    await agent_bridge.publish(_info(pid="a", model="model-a", port=39080))
    await agent_bridge.publish(_info(pid="b", model="model-b", port=39080))
    assert await agent_bridge.unpublish("a")
    models = _read(bridge_config)["provider"]["glyvex-39080"]["models"]
    assert list(models) == ["model-b"]
    assert await agent_bridge.unpublish("b")
    # Sin modelos propios: nuestra clave desaparece; las del usuario no se tocan.
    assert set(_read(bridge_config)["provider"]) == {"Glyvex-AI", "glyvex"}


async def test_unpublish_unpublished_is_noop(bridge_on, bridge_config):
    _write_json(bridge_config, V1_USER)
    assert not await agent_bridge.unpublish("noexiste")
    assert _read(bridge_config) == V1_USER


async def test_toggle_off_does_not_touch_the_file(bridge_config):
    # Via real sin POST /api/config: el default del esquema es OFF.
    assert not agent_bridge.enabled()
    assert not await agent_bridge.publish(_info())
    assert not bridge_config.exists()


async def test_corrupt_file_is_reported_and_never_touched(bridge_on, bridge_config):
    raw = '{"provider": {"broken": ,,,}'
    bridge_config.write_text(raw, encoding="utf-8")
    assert not await agent_bridge.publish(_info())
    assert bridge_config.read_text(encoding="utf-8") == raw
    status = agent_bridge.bridge_status()
    assert status["last_error"] and "parseable" in status["last_error"]


async def test_jsonc_is_read_and_rewritten_strict_with_backup(bridge_on, bridge_config):
    # El bridge lee JSONC (comentarios y comas finales) pero escribe JSON
    # estricto, como hace el migrador de OpenCode; el original queda en .bak.
    raw = (
        '// comentarios para OpenCode\n{"theme": "dark", /* inline */\n'
        '"provider": {"llamacpp": {"npm": "@ai-sdk/openai-compatible",\n'
        '"options": {"baseURL": "http://127.0.0.1:8080/v1"},},}}\n'
    )
    bridge_config.write_text(raw, encoding="utf-8")
    assert await agent_bridge.publish(_info())
    data = _read(bridge_config)
    assert data["theme"] == "dark"
    assert data["provider"]["llamacpp"]["options"]["baseURL"] == "http://127.0.0.1:8080/v1"
    assert data["provider"]["glyvex-39080"]["models"]["test-model"]
    cleaned = bridge_config.read_text(encoding="utf-8")
    assert "comentarios" not in cleaned and "inline" not in cleaned
    # Un solo .bak por sesion, con el contenido original intacto.
    backups = list(bridge_config.parent.glob("opencode.jsonc.bak-ag1-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == raw
    await agent_bridge.publish(_info(pid="segundo", model="otro-model"))
    assert len(list(bridge_config.parent.glob("opencode.jsonc.bak-ag1-*"))) == 1


async def test_backup_lands_in_existing_backup_dir(bridge_on, bridge_config, tmp_path):
    (tmp_path / "backup").mkdir()
    bridge_config.write_text('{"theme": "light"}', encoding="utf-8")
    await agent_bridge.publish(_info())
    assert list((tmp_path / "backup").glob("*.bak-ag1-*"))
    assert not list(tmp_path.glob("*.bak-ag1-*"))


async def test_cleanup_all_removes_ours_and_keeps_manual_glyvex(bridge_on, bridge_config):
    _write_json(bridge_config, V1_USER)
    await agent_bridge.publish(_info())
    await agent_bridge.cleanup_all()
    assert set(_read(bridge_config)["provider"]) == {"Glyvex-AI", "glyvex"}


async def test_cleanup_all_never_touched_leaves_file_untouched(bridge_on, bridge_config):
    # La instancia nunca publico (toggle OFF la mayor parte de su vida): no
    # debe reescribir el config del usuario solo por el shutdown.
    _write_json(bridge_config, V1_USER)
    await agent_bridge.cleanup_all()
    assert _read(bridge_config) == V1_USER


async def test_status_endpoint_off_by_default(client, bridge_config):
    data = (await client.get("/api/agent-bridge/status")).json()
    assert data["enabled"] is False
    assert data["published"] == []
    assert data["bridge_keys"] == []


async def test_api_toggle_off_cleans_published_blocks(client, bridge_config):
    await client.post("/api/config", json={"agent_bridge": {"enabled": True}})
    await agent_bridge.publish(_info())
    data = _read(bridge_config)
    assert "glyvex" in data["providers"]
    await client.post("/api/config", json={"agent_bridge": {"enabled": False}})
    # Apagar el toggle por la API retira de inmediato lo publicado.
    status = agent_bridge.bridge_status()
    assert status["bridge_keys"] == [] and status["published"] == []
    assert "glyvex" not in _read(bridge_config).get("providers", {})


async def test_launch_endpoint_reports_missing_cli(client, monkeypatch):
    # La suite no puede depender del CLI real: se mockea la detección.
    monkeypatch.setattr(agent_bridge.shutil, "which", lambda name: None)
    data = (await client.post("/api/agent-bridge/launch")).json()
    assert data == {"launched": False, "error": "opencode_cli_not_found"}


async def test_launch_endpoint_spawns_cli(client, monkeypatch):
    calls = []

    class FakePopen:
        def __init__(self, argv, **kwargs):
            calls.append((argv, kwargs))
            self.pid = 4242

    monkeypatch.setattr(agent_bridge.shutil, "which", lambda name: r"C:\fake\opencode.cmd")
    monkeypatch.setattr(agent_bridge.subprocess, "Popen", FakePopen)
    data = (await client.post("/api/agent-bridge/launch")).json()
    assert data["launched"] is True and data["pid"] == 4242
    assert calls[0][0] == [r"C:\fake\opencode.cmd"]


async def test_launch_endpoint_survives_spawn_error(client, monkeypatch):
    def boom(argv, **kwargs):
        raise OSError("spawn failed")

    monkeypatch.setattr(agent_bridge.shutil, "which", lambda name: r"C:\fake\opencode.cmd")
    monkeypatch.setattr(agent_bridge.subprocess, "Popen", boom)
    data = (await client.post("/api/agent-bridge/launch")).json()
    assert data == {"launched": False, "error": "launch_failed"}


async def test_launch_publishes_on_running_and_stop_removes(
    client, sample_model_dir, mock_llama_server, fake_llama_binary, bridge_config
):
    await client.post(
        "/api/config",
        json={
            "model_dirs": [str(sample_model_dir)],
            "backends": {"llama_server": {"binary_path": fake_llama_binary, "enabled": True}},
            "agent_bridge": {"enabled": True},
        },
    )
    await scan_and_wait(client)
    model_id = (await client.get("/api/models")).json()[0]["id"]

    # host/port apuntan al mock (responde /health); el health check promueve
    # "starting" -> "running" y ahi el bridge publica el bloque.
    launched = (
        await client.post(
            "/api/launcher/launch",
            json={"model_id": model_id, "backend": "llama_server", "host": "127.0.0.1", "port": 18080},
        )
    ).json()
    process_id = launched["process_id"]

    async def is_published():
        if not bridge_config.exists():
            return False
        data = _read(bridge_config)
        provider = data.get("providers", {}).get("glyvex")
        return provider is not None and len(provider.get("models", {})) == 1

    assert await wait_until(is_published, timeout=5.0)
    entry = next(iter(_read(bridge_config)["providers"]["glyvex"]["models"].values()))
    assert ":18080" in entry["settings"]["baseURL"]
    # El mock no expone /props: el limit sale de la n_ctx del launch (default).
    assert entry["limit"] == {"context": 65536, "output": 8192}

    await client.post(f"/api/launcher/stop/{process_id}")

    async def is_removed():
        status = agent_bridge.bridge_status()
        return not status["published"] and not status["bridge_keys"]

    assert await wait_until(is_removed, timeout=5.0)
    assert "glyvex" not in _read(bridge_config).get("providers", {})


async def test_lm_studio_publishes_and_stop_unpublishes(
    client, sample_model_dir, mock_llama_server, bridge_config
):
    # Sin proceso propio: start() publica cuando el health confirma que esta up
    # y stop() retira en su rama process=None.
    await client.post(
        "/api/config",
        json={"model_dirs": [str(sample_model_dir)], "agent_bridge": {"enabled": True}},
    )
    await scan_and_wait(client)
    model_id = (await client.get("/api/models")).json()[0]["id"]

    launched = (
        await client.post(
            "/api/launcher/launch",
            json={"model_id": model_id, "backend": "lm_studio", "host": "127.0.0.1", "port": 18080},
        )
    ).json()
    assert launched["state"] == "running"
    provider = _read(bridge_config)["providers"]["glyvex"]
    assert ":18080" in provider["settings"]["baseURL"]

    await client.post(f"/api/launcher/stop/{launched['process_id']}")
    assert agent_bridge.bridge_status()["bridge_keys"] == []
    assert "glyvex" not in _read(bridge_config).get("providers", {})
