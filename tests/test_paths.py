"""
test_paths.py — Contrato central del multi-instancia (P2).

paths.py es la única fuente de las rutas de la instancia:
_resolve_data_dir() decide dónde vive el estado de ESTA instancia
(GLYVEX_DATA_DIR) y main._cors_origins() decide qué orígenes pueden
hablar con ella. Todo lo demás (database, métricas, logs, adjuntos)
cuelga de esos dos valores, así que si estos fallan, todo falla.
"""

from __future__ import annotations

from pathlib import Path

import main as main_module
import paths


def test_data_dir_sin_variable_usa_default(monkeypatch):
    monkeypatch.delenv("GLYVEX_DATA_DIR", raising=False)
    assert paths._resolve_data_dir() == paths.BASE_DIR / "data"


def test_data_dir_solo_espacios_usa_default(monkeypatch):
    monkeypatch.setenv("GLYVEX_DATA_DIR", "   ")
    assert paths._resolve_data_dir() == paths.BASE_DIR / "data"


def test_data_dir_relativa_se_resuelve_contra_base_dir(monkeypatch):
    # Contra BASE_DIR, NO contra el cwd: uvicorn se lanza desde backend/
    # (start.sh), y resolver contra el cwd daría backend/data-testing.
    monkeypatch.setenv("GLYVEX_DATA_DIR", "./data-testing")
    assert paths._resolve_data_dir() == (paths.BASE_DIR / "data-testing").resolve()


def test_data_dir_absoluta_no_tooca_base_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GLYVEX_DATA_DIR", str(tmp_path))
    assert paths._resolve_data_dir() == tmp_path.resolve()


def test_cors_origins_default(monkeypatch):
    monkeypatch.delenv("GLYVEX_CORS_ORIGINS", raising=False)
    assert main_module._cors_origins() == ["http://localhost:5173"]


def test_cors_origins_lista_separada_por_comas(monkeypatch):
    monkeypatch.setenv(
        "GLYVEX_CORS_ORIGINS",
        "http://localhost:25173, http://127.0.0.1:25173",
    )
    assert main_module._cors_origins() == [
        "http://localhost:25173",
        "http://127.0.0.1:25173",
    ]


def test_cors_origins_solo_espacios_vuelve_al_default(monkeypatch):
    monkeypatch.setenv("GLYVEX_CORS_ORIGINS", " , ")
    assert main_module._cors_origins() == ["http://localhost:5173"]
