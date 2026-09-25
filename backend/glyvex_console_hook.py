"""Runtime hook: en el bundle sin consola (PyInstaller), stdout/stderr van a un log en DATA_DIR/logs."""
import os
import sys

_frozen = getattr(sys, "frozen", False)


# El exe de produccion no lleva consola, asi que frozen => stdout/stderr no
# tienen destino util: si siempre a log. (isatty no sirve de detector en
# Windows con streams dummy: puede reportar True contra NUL o handles de
# tipo UNKNOWN segun como se lance el exe.)
# Para depurar en consola se corre en dev: python main.py (no frozen).
if _frozen:
    try:
        raw = os.environ.get("GLYVEX_DATA_DIR", "").strip()
        if raw:
            data_dir = raw
        else:
            local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
            data_dir = os.path.join(local_appdata or os.path.expanduser("~"),
                                    "Glyvex-AI-Suite", "data")
        log_dir = os.path.join(data_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)
        _fh = open(os.path.join(log_dir, "sidecar-console.log"), "a",
                   encoding="utf-8", buffering=1)
        sys.stdout = _fh
        sys.stderr = _fh
    except Exception:
        pass
