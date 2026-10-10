"""Runtime hook: en el bundle sin consola (PyInstaller), stdout/stderr van a un log en DATA_DIR/logs."""
import os
import sys

_frozen = getattr(sys, "frozen", False)


def _log_file():
    raw = os.environ.get("GLYVEX_DATA_DIR", "").strip()
    if raw:
        data_dir = raw
    else:
        # Misma lógica de default que paths._frozen_default_data_dir (los
        # logs deben vivir junto al resto del estado, no en otra parte).
        local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
        if local_appdata:
            data_dir = os.path.join(local_appdata, "Glyvex-AI-Suite", "data")
        elif sys.platform == "linux":
            xdg_data_home = os.environ.get("XDG_DATA_HOME", "").strip()
            base = (xdg_data_home or os.path.join(os.path.expanduser("~"),
                                                  ".local", "share"))
            data_dir = os.path.join(base, "Glyvex-AI-Suite", "data")
        else:
            data_dir = os.path.join(os.path.expanduser("~"),
                                    "Glyvex-AI-Suite", "data")
    log_dir = os.path.join(data_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    return open(os.path.join(log_dir, "sidecar-console.log"), "a",
                encoding="utf-8", buffering=1)


# El exe de produccion no lleva consola, asi que frozen => stdout/stderr no
# tienen destino util: si siempre a log. (isatty no sirve de detector en
# Windows con streams dummy: puede reportar True contra NUL o handles de
# tipo UNKNOWN segun como se lance el exe.)
# Para depurar en consola se corre en dev: python main.py (no frozen).
#
# Excepcion: GLYVEX_STDOUT_PIPE=1 (lo exporta la shell Tauri). Ahí stdout ES
# la pipe que Rust lee para el marker #PORT_ASSIGNED:<puerto>#, asi que solo
# stderr va al log.
if _frozen:
    try:
        if os.environ.get("GLYVEX_STDOUT_PIPE", "").strip().lower() in ("1", "true", "yes"):
            sys.stderr = _log_file()
        else:
            _fh = _log_file()
            sys.stdout = _fh
            sys.stderr = _fh
    except Exception:
        pass
