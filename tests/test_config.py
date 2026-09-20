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


# --------------------------------------------------------------------------
# display y exports
# --------------------------------------------------------------------------

import json as _json  # noqa: E402
import re as _re  # noqa: E402
from pathlib import Path as _Path  # noqa: E402


def test_plantilla_predeterminada_coincide_con_el_frontend():
    """DEFAULT_HIDDEN_METRICS (backend) == PRESETS.default (frontend/src/lib/metricsDisplay.js)."""
    import config as config_module

    js = (_Path(__file__).resolve().parent.parent / "frontend" / "src" / "lib" / "metricsDisplay.js").read_text(
        encoding="utf-8")
    match = _re.search(r"export const PRESETS = \{\s*default:\s*(\[[^\]]*\])", js)
    assert match, "no se encontró PRESETS.default en metricsDisplay.js"
    frontend_default = _json.loads(match.group(1))
    assert frontend_default == config_module.DEFAULT_HIDDEN_METRICS

    # Y todas sus claves existen en el catálogo del frontend.
    for key in frontend_default:
        assert f'key: "{key}"' in js


def test_defaults_de_display_y_exports():
    import config as config_module

    data = config_module.Config().defaults()
    assert data["display"] == {"preset": "default", "hidden": config_module.DEFAULT_HIDDEN_METRICS}
    assert data["exports"]["prometheus_enabled"] is False
    assert data["exports"]["influx"]["enabled"] is False


def test_redaccion_de_secretos_en_el_log():
    import main

    red = main._redact_secrets({
        "attachments": {"image_tokens_estimate": 1024},
        "exports": {"prometheus_token": "t", "influx": {"token": "abc", "password": "p", "url": "http://x"}},
        "tools": {"brave_api_key": "k", "tavily_api_key": ""},
    })
    assert red["attachments"]["image_tokens_estimate"] == 1024        # contiene "token" pero no es secreto
    assert red["exports"]["prometheus_token"] == "<redactado>"
    assert red["exports"]["influx"] == {"token": "<redactado>", "password": "<redactado>", "url": "http://x"}
    assert red["tools"] == {"brave_api_key": "<redactado>", "tavily_api_key": ""}
