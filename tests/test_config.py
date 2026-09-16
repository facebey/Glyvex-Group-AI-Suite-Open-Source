"""Tests de config.py — defaults, merge parcial, dot-notation, reset, persistencia."""

import json

import config as config_module


async def test_load_default_config(client):
    res = await client.get("/api/config")
    assert res.status_code == 200
    data = res.json()
    assert data["app"]["port"] == 7981
    assert data["app"]["theme"] == "dark"
    assert data["app"]["language"] == "es"
    assert data["model_dirs"] == []
    assert data["backends"]["llama_server"]["default_port"] == 8080


async def test_save_partial_config(client):
    res = await client.post("/api/config", json={"app": {"port": 9000}})
    assert res.status_code == 200
    data = res.json()

    # El campo tocado cambió...
    assert data["app"]["port"] == 9000
    # ...pero el resto de "app" y el resto del documento siguen con default
    # (merge parcial, no reemplazo del objeto completo).
    assert data["app"]["theme"] == "dark"
    assert data["app"]["language"] == "es"
    assert data["backends"]["llama_server"]["default_port"] == 8080
    assert data["model_dirs"] == []


def test_dot_notation_get():
    # El fixture autouse _isolated_state ya reseteó config a los defaults.
    assert config_module.config.get("app.port") == 7981
    assert config_module.config.get("backends.ollama.default_port") == 11434


def test_dot_notation_set():
    config_module.config.set("app.port", 9000)
    assert config_module.config.get("app.port") == 9000
    # El resto del árbol "app" no se ve afectado por un set anidado puntual.
    assert config_module.config.get("app.theme") == "dark"


async def test_reset_config(client):
    await client.post("/api/config", json={"app": {"port": 1234}, "model_dirs": ["/tmp/x"]})

    res = await client.post("/api/config/reset")
    assert res.status_code == 200
    data = res.json()

    assert data["app"]["port"] == 7981
    assert data["model_dirs"] == []
    assert data["backends"]["ollama"]["default_port"] == 11434


async def test_config_persists_to_disk(client, tmp_config_dir):
    res = await client.post("/api/config", json={"app": {"port": 4242}, "model_dirs": ["/models"]})
    assert res.status_code == 200

    raw = (tmp_config_dir / "config.json").read_text(encoding="utf-8")
    on_disk = json.loads(raw)

    assert on_disk["app"]["port"] == 4242
    assert on_disk["model_dirs"] == ["/models"]
