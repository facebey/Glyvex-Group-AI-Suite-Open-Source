"""
logsetup.py — Logging de desarrollo por componente (data/logs/<componente>.log).

Un RotatingFileHandler por componente (5 MB x 3 backups, UTF-8). `setup()` es
idempotente: si el logger ya tiene un RotatingFileHandler, no le agrega otro.
Se llama al inicio del lifespan de main.py (no en import), así la suite de
tests (ASGITransport, sin lifespan) nunca toca data/logs/ real.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from paths import DATA_DIR

LOGS_DIR = DATA_DIR / "logs"

MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3
FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

# componente -> (nombre del archivo sin extension, loggers que caen en el).
# "models"/"tools"/"stt" aceptan el nombre plano o el "glyvex.*" para no
# depender de como se nombre el logger en el modulo.
COMPONENTS: dict[str, tuple[str, list[str]]] = {
    "app": ("app", ["glyvex.app"]),
    "chat": ("chat", ["glyvex.chat"]),
    "launcher": ("launcher", ["glyvex.launcher"]),
    "benchmark": ("benchmark", ["benchmark"]),
    "models": ("models", ["models", "glyvex.models"]),
    "tools": ("tools", ["tools", "glyvex.tools"]),
    "stt": ("stt", ["stt", "glyvex.stt"]),
    "database": ("database", ["database"]),
}


def _attach(logger_name: str, path: Path, level: int) -> None:
    lg = logging.getLogger(logger_name)
    if any(isinstance(h, RotatingFileHandler) for h in lg.handlers):
        return
    lg.setLevel(level)
    handler = RotatingFileHandler(
        path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(FORMAT))
    lg.addHandler(handler)


def setup(logs_dir: Path | str | None = None, level: int = logging.INFO) -> Path:
    """
    Configura un RotatingFileHandler por componente y devuelve el directorio
    de logs. Llamarla varias veces no duplica handlers.
    """
    logs_dir = Path(logs_dir) if logs_dir is not None else LOGS_DIR
    logs_dir.mkdir(parents=True, exist_ok=True)
    for _component, (stem, logger_names) in COMPONENTS.items():
        path = logs_dir / f"{stem}.log"
        for name in logger_names:
            _attach(name, path, level)
    return logs_dir
