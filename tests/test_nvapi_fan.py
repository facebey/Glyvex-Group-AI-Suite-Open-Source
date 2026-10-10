"""Tests de nvapi_fan.py — el adapter se prueba con guards y helpers puros:
la suite nunca carga nvapi64.dll ni toca una GPU real."""

import ctypes

import nvapi_fan as nvapi_module
from nvapi_fan import (
    NvApiFan,
    _CoolerControl,
    _CoolersStatus,
    _clamp_level,
    _versioned,
)


def test_clamp_level():
    assert _clamp_level(50, 30, 100) == 50
    assert _clamp_level(10, 30, 100) == 30  # el driver hace piso a 30
    assert _clamp_level(150, 30, 100) == 100


def test_versioned_layout():
    # Version = sizeof(struct) | (major << 16), como exige el driver (tabla LHM).
    assert _versioned(_CoolersStatus) == (ctypes.sizeof(_CoolersStatus) | (1 << 16))
    assert _versioned(_CoolerControl) == (ctypes.sizeof(_CoolerControl) | (1 << 16))


def test_sin_windows_no_disponible(monkeypatch):
    monkeypatch.setattr(nvapi_module.platform, "system", lambda: "Linux")
    fan = NvApiFan()
    assert fan.available is False
    assert fan.open_error is not None
    ok, err = fan.set_manual(0, 50)
    assert (ok, err) == (False, "nvapi_no_disponible")


def test_sin_dll_no_disponible(monkeypatch):
    monkeypatch.setattr(nvapi_module.platform, "system", lambda: "Windows")

    def _boom(name):
        raise OSError(f"{name} no encontrada")

    monkeypatch.setattr(nvapi_module.ctypes, "CDLL", _boom)
    fan = NvApiFan()
    assert fan.available is False
    assert "nvapi64.dll" in fan.open_error
    ok, err = fan.set_manual(0, 50)
    assert (ok, err) == (False, "nvapi_no_disponible")


def test_index_fuera_de_rango(monkeypatch):
    # Estado inyectado: adapter "abierto" pero sin GPUs enumeradas; la DLL
    # nunca participa.
    fan = NvApiFan()
    fan._ensured = True
    fan._ok = True
    fan._handles = []
    ok, err = fan.set_manual(0, 50)
    assert (ok, err) == (False, "GPU 0 no encontrada")
    assert fan.fan_status(0) is None
    assert fan.fan_control(0) is None
