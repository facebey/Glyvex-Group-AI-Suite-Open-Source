"""Tests del lifespan de main.py — arranque/shutdown de la app completa."""

import asyncio
import json
import threading
from contextlib import asynccontextmanager

import agent_bridge
import attachments as attachments_module
import config as config_module
import logsetup as logsetup_module
import main as main_module
import metrics as metrics_module


async def test_lifespan_purge_huérfanos_fuera_del_event_loop(monkeypatch, tmp_path):
    """D7: purge_orphans hace I/O de disco; debe correr en un hilo worker,
    no en el thread del event loop (con muchas imágenes subidas, un unlink
    por archivo en el loop congela el arranque)."""
    calls = []

    def fake_purge(keep_ids):
        calls.append((set(keep_ids), threading.current_thread()))
        return len(keep_ids)

    async def fake_ids():
        return {"a1", "b2"}

    monkeypatch.setattr(main_module, "db_all_attachment_ids", fake_ids)
    monkeypatch.setattr(attachments_module, "purge_orphans", fake_purge)
    monkeypatch.setattr(logsetup_module, "setup", lambda: tmp_path)
    monkeypatch.setattr(metrics_module, "METRICS_DB_PATH", tmp_path / "metrics.db")
    # AG-2: el hilo MCP real (lifespan + session_manager.run()) se cubre en
    # tests/test_mcp_server.py, y ese run() es una sola vez por instancia del
    # singleton. Acá el toggle queda off para poder ejercitar el lifespan
    # completo sin chocar con esa cobertura.
    monkeypatch.setitem(config_module.config._data["mcp_server"], "enabled", False)

    async with main_module.lifespan(main_module.app):
        pass

    assert len(calls) == 1
    keep_ids, worker = calls[0]
    assert keep_ids == {"a1", "b2"}
    assert worker is not threading.current_thread(), "purge corrió en el event loop"


async def test_lifespan_registers_mcp_and_removes_on_shutdown(monkeypatch, tmp_path):
    """AG-2 U3: con el puerto real fijado por __main__ (app.state.glyvex_port),
    el arranque escribe la entrada mcp.glyvex con la URL real y el shutdown la
    retira. session_manager.run() real se cubre en tests/test_mcp_server.py y es
    una sola vez por singleton: acá se reemplaza el run() de SU CLASE (restaurado
    por monkeypatch) para ejercitar el flujo sin consumirlo."""
    opencode_path = tmp_path / "opencode.json"
    monkeypatch.setenv("OPENCODE_CONFIG", str(opencode_path))
    monkeypatch.setattr(logsetup_module, "setup", lambda: tmp_path)
    monkeypatch.setattr(metrics_module, "METRICS_DB_PATH", tmp_path / "metrics.db")
    monkeypatch.setitem(config_module.config._data["mcp_server"], "enabled", True)

    session_manager = main_module.mcp_server.mcp.session_manager

    @asynccontextmanager
    async def fake_run(self):
        yield

    monkeypatch.setattr(type(session_manager), "run", fake_run)
    # El puerto real existe solo tras la escalera de __main__; en tests se fija a
    # mano. monkeypatch lo retira al terminar (no debe quedar en el app singleton).
    monkeypatch.setattr(main_module.app.state, "glyvex_port", 39099, raising=False)
    agent_bridge._reset_for_tests()

    async with main_module.lifespan(main_module.app):
        data = json.loads(opencode_path.read_text(encoding="utf-8"))
        assert data["mcp"]["glyvex"] == {
            "type": "remote",
            "url": "http://127.0.0.1:39099/mcp/",
            "enabled": True,
            "oauth": False,
        }

    # Shutdown: cleanup_all retira la entrada (el server /mcp murió con la app).
    data = json.loads(opencode_path.read_text(encoding="utf-8"))
    assert "mcp" not in data
    agent_bridge._reset_for_tests()
