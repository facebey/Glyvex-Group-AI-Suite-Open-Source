"""Tests del lifespan de main.py — arranque/shutdown de la app completa."""

import asyncio
import threading

import attachments as attachments_module
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

    async with main_module.lifespan(main_module.app):
        pass

    assert len(calls) == 1
    keep_ids, worker = calls[0]
    assert keep_ids == {"a1", "b2"}
    assert worker is not threading.current_thread(), "purge corrió en el event loop"
