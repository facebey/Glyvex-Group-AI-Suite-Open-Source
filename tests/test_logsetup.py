"""
test_logsetup.py — logging de desarrollo por componente (data/logs/).

Los tests usan logs_dir temporal (tmp_path): nunca tocan data/logs/ real.
La fixture autouse limpia los RotatingFileHandler que setup() agrega a los
loggers (estado global de `logging`), para que cada test arranque limpio y
la suite no deje handlers colgando entre tests.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

import pytest

import logsetup


def _remove_glyvex_handlers() -> None:
    for _component, (_stem, logger_names) in logsetup.COMPONENTS.items():
        for name in logger_names:
            lg = logging.getLogger(name)
            for handler in list(lg.handlers):
                if isinstance(handler, RotatingFileHandler):
                    lg.removeHandler(handler)
                    handler.close()


@pytest.fixture(autouse=True)
def _clean_loggers():
    _remove_glyvex_handlers()
    yield
    _remove_glyvex_handlers()


def test_setup_crea_un_archivo_por_componento(tmp_path):
    logsetup.setup(logs_dir=tmp_path)
    expected = {f"{stem}.log" for _component, (stem, _loggers) in logsetup.COMPONENTS.items()}
    actual = {p.name for p in tmp_path.iterdir() if p.is_file()}
    assert expected <= actual


def test_cada_logger_ca_en_su_archivo(tmp_path):
    logsetup.setup(logs_dir=tmp_path)

    def owner_file(name: str) -> str:
        for _component, (stem, loggers) in logsetup.COMPONENTS.items():
            if name in loggers:
                return f"{stem}.log"
        raise AssertionError(f"logger sin componente: {name}")

    for _component, (_stem, logger_names) in logsetup.COMPONENTS.items():
        for name in logger_names:
            logging.getLogger(name).info("GLYVEX-MARKER %s", name)
        for name in logger_names:
            for handler in logging.getLogger(name).handlers:
                handler.flush()

    files = {
        p.name: p.read_text(encoding="utf-8")
        for p in tmp_path.iterdir()
        if p.is_file() and p.name.endswith(".log")
    }
    for _component, (_stem, logger_names) in logsetup.COMPONENTS.items():
        for name in logger_names:
            marker = f"GLYVEX-MARKER {name}"
            own = owner_file(name)
            assert marker in files[own], f"{name} no escribio en {own}"
            for other_name, other_text in files.items():
                if other_name != own:
                    assert marker not in other_text, (
                        f"{name} se coló en {other_name}"
                    )


def test_setup_es_idempotente(tmp_path):
    logsetup.setup(logs_dir=tmp_path)
    logsetup.setup(logs_dir=tmp_path)
    for _component, (_stem, logger_names) in logsetup.COMPONENTS.items():
        for name in logger_names:
            lg = logging.getLogger(name)
            assert len(lg.handlers) == 1, f"{name} tiene {len(lg.handlers)} handlers"
