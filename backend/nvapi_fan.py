"""
nvapi_fan.py — Control manual de ventilador para GPUs NVIDIA en Windows via
NvAPI Direct.

En Windows, NVML rechaza la escritura del fan en GeForce (NOT_SUPPORTED) y el
driver 580 elimino los exports clasicos NvAPI_* de nvapi64.dll: la unica via
pura Python es el mecanismo nvapi_QueryInterface, que resuelve punteros de
funcion por hash propietario del nombre. IDs y layouts de struct tomados de la
tabla publica LibreHardwareMonitorLib/Interop/NvApi.cs (proyecto
LibreHardwareMonitor) — el algoritmo de hash es propietario de NVIDIA y no se
puede calcular, solo tabular.

Validado en RTX 3090 + driver 580.178.04 (2026-10-09):
- GetStatus (RPM) y GetControl leen sin admin.
- SetControl (modo manual) exige admin; sin el devuelve -137
  (NVAPI_INVALID_USER_PRIVILEGE).
- Read-back: GetControl refleja de inmediato el valor fijado. El porcentaje de
  NVML arrastra mucho (no se usa para verificar).
"""

from __future__ import annotations

import ctypes
import platform
import time
from typing import Any

__all__ = [
    "NvApiFan",
    "NVAPI_OK",
    "NVAPI_ERROR_NOT_SUPPORTED",
    "NVAPI_ERROR_INVALID_USER_PRIVILEGE",
    "FAN_MODE_AUTO",
    "FAN_MODE_MANUAL",
]

NVAPI_OK = 0
NVAPI_ERROR_NOT_SUPPORTED = -104
NVAPI_ERROR_INVALID_USER_PRIVILEGE = -137

FAN_MODE_AUTO = 0
FAN_MODE_MANUAL = 1

_FUNC_INITIALIZED = 0x0150E828
_FUNC_ENUM_PHYSICAL_GPUS = 0xE5AC921F
_FUNC_FAN_COOLERS_GET_STATUS = 0x35AED5E8
_FUNC_FAN_COOLERS_GET_CONTROL = 0x814B209F
_FUNC_FAN_COOLERS_SET_CONTROL = 0xA58971A5

_MAX_COOLERS = 32
_MAX_GPUS = 16


class _GpuHandle(ctypes.Structure):
    _fields_ = [("ptr", ctypes.c_void_p)]


class _StatusItem(ctypes.Structure):
    _fields_ = [
        ("CoolerId", ctypes.c_uint),
        ("CurrentRpm", ctypes.c_uint),
        ("CurrentMinLevel", ctypes.c_uint),
        ("CurrentMaxLevel", ctypes.c_uint),
        ("CurrentLevel", ctypes.c_uint),
        ("_res", ctypes.c_uint * 8),
    ]


class _CoolersStatus(ctypes.Structure):
    _fields_ = [
        ("Version", ctypes.c_uint),
        ("Count", ctypes.c_uint),
        ("_r1", ctypes.c_ulonglong),
        ("_r2", ctypes.c_ulonglong),
        ("_r3", ctypes.c_ulonglong),
        ("_r4", ctypes.c_ulonglong),
        ("Items", _StatusItem * _MAX_COOLERS),
    ]


class _ControlItem(ctypes.Structure):
    _fields_ = [
        ("CoolerId", ctypes.c_uint),
        ("Level", ctypes.c_uint),
        ("ControlMode", ctypes.c_uint),
        ("_res", ctypes.c_uint * 8),
    ]


class _CoolerControl(ctypes.Structure):
    _fields_ = [
        ("Version", ctypes.c_uint),
        ("_reserved", ctypes.c_uint),
        ("Count", ctypes.c_uint),
        ("_reserved2", ctypes.c_uint * 8),
        ("Items", _ControlItem * _MAX_COOLERS),
    ]


def _versioned(struct: type, major: int = 1) -> int:
    # El driver exige Version = sizeof(struct) | (major << 16).
    return (ctypes.sizeof(struct) | (major << 16)) & 0xFFFFFFFF


def _clamp_level(percent: int, min_level: int, max_level: int) -> int:
    # El driver hace clamp propio (p. ej. minimo 30 en una 3090): si no se
    # anticipa, fijar 20 "falla" en el read-back aunque el fan haya respondido.
    return max(min_level, min(max_level, percent))


class NvApiFan:
    """Adapter perezoso: la carga real de la DLL ocurre en `_ensure()`.
    Instanciarlo nunca falla; si no hay Windows/DLL/GPUs, `available` es
    False y `open_error` explica por que."""

    def __init__(self) -> None:
        self._ensured = False
        self._ok = False
        self._open_error: str | None = None
        self._dll: Any = None
        self._fn: dict[int, Any] = {}
        self._handles: list[_GpuHandle] = []
        self._kept: list[Any] = []

    @property
    def available(self) -> bool:
        return self._ensure()

    @property
    def open_error(self) -> str | None:
        self._ensure()
        return self._open_error

    def _ensure(self) -> bool:
        if self._ensured:
            return self._ok
        self._ensured = True
        if platform.system() != "Windows":
            self._open_error = "NvAPI Direct solo está disponible en Windows"
            return False
        try:
            dll = ctypes.CDLL("nvapi64.dll")
        except OSError:
            self._open_error = "nvapi64.dll no encontrada"
            return False
        self._dll = dll
        dll.nvapi_QueryInterface.restype = ctypes.c_void_p
        dll.nvapi_QueryInterface.argtypes = [ctypes.c_uint]
        try:
            for fid in (
                _FUNC_INITIALIZED,
                _FUNC_ENUM_PHYSICAL_GPUS,
                _FUNC_FAN_COOLERS_GET_STATUS,
                _FUNC_FAN_COOLERS_GET_CONTROL,
                _FUNC_FAN_COOLERS_SET_CONTROL,
            ):
                ptr = dll.nvapi_QueryInterface(fid)
                if not ptr:
                    raise RuntimeError(f"nvapi_QueryInterface devolvió NULL para {fid:#x}")
                self._fn[fid] = self._make_fn(fid, ptr)
            init = self._fn[_FUNC_INITIALIZED]
            rc = init()
            if rc != NVAPI_OK:
                raise RuntimeError(f"NvAPI_Initialize falló: {rc}")
            enum = self._fn[_FUNC_ENUM_PHYSICAL_GPUS]
            handles = (_GpuHandle * _MAX_GPUS)()
            count = ctypes.c_int(_MAX_GPUS)
            rc = enum(handles, ctypes.byref(count))
            if rc != NVAPI_OK or count.value <= 0:
                raise RuntimeError(f"NvAPI_EnumPhysicalGPUs falló: {rc} (count={count.value})")
            self._handles = [handles[i] for i in range(count.value)]
        except (RuntimeError, OSError) as exc:
            self._open_error = str(exc)
            return False
        self._ok = True
        return True

    def _make_fn(self, fid: int, ptr: int) -> Any:
        argtypes = {
            _FUNC_INITIALIZED: [],
            _FUNC_ENUM_PHYSICAL_GPUS: [ctypes.POINTER(_GpuHandle), ctypes.POINTER(ctypes.c_int)],
            _FUNC_FAN_COOLERS_GET_STATUS: [_GpuHandle, ctypes.POINTER(_CoolersStatus)],
            _FUNC_FAN_COOLERS_GET_CONTROL: [_GpuHandle, ctypes.POINTER(_CoolerControl)],
            _FUNC_FAN_COOLERS_SET_CONTROL: [_GpuHandle, ctypes.POINTER(_CoolerControl)],
        }[fid]
        fn = ctypes.CFUNCTYPE(ctypes.c_int, *argtypes)(ptr)
        self._kept.append(fn)
        return fn

    @property
    def gpu_count(self) -> int:
        return len(self._handles) if self._ensure() else 0

    def fan_status(self, index: int) -> dict[str, int] | None:
        """Estado del cooler principal de la GPU: rpm, rango de nivel y nivel
        actual. None si no hay adapter, el indice no existe o la llamada falla.
        `current_level` es poco fiable en modo manual (el driver lo deja en 0);
        el RPM es la fuente de verdad."""
        if not self._ensure() or index >= len(self._handles):
            return None
        status = _CoolersStatus()
        status.Version = _versioned(_CoolersStatus)
        rc = self._fn[_FUNC_FAN_COOLERS_GET_STATUS](self._handles[index], ctypes.byref(status))
        if rc != NVAPI_OK or status.Count == 0:
            return None
        item = status.Items[0]
        return {
            "rpm": item.CurrentRpm,
            "min_level": item.CurrentMinLevel,
            "max_level": item.CurrentMaxLevel,
            "current_level": item.CurrentLevel,
        }

    def fan_control(self, index: int) -> list[tuple[int, int]] | None:
        """[(control_mode, level), ...] por cooler, o None si la llamada falla."""
        if not self._ensure() or index >= len(self._handles):
            return None
        control = self._control_struct(index)
        if control is None:
            return None
        return [(control.Items[i].ControlMode, control.Items[i].Level) for i in range(control.Count)]

    def _control_struct(self, index: int) -> _CoolerControl | None:
        control = _CoolerControl()
        control.Version = _versioned(_CoolerControl)
        rc = self._fn[_FUNC_FAN_COOLERS_GET_CONTROL](self._handles[index], ctypes.byref(control))
        if rc != NVAPI_OK:
            return None
        return control

    def set_manual(self, index: int, percent: int, tolerance: int = 2,
                   readback_s: float = 1.0) -> tuple[bool, str | None]:
        """(ok, error). Fija todos los coolers de la GPU a modo manual + nivel
        pedido (clamp al rango que reporta el driver) y lo verifica con
        read-back de GetControl. Claves de error estables: fan_no_privilege,
        fan_not_supported, fan_no_change, nvapi_error_<codigo>."""
        if not self._ensure():
            return False, "nvapi_no_disponible"
        if index >= len(self._handles):
            return False, f"GPU {index} no encontrada"
        status = self.fan_status(index)
        if status is None:
            return False, "fan_not_supported"
        target = _clamp_level(percent, status["min_level"], status["max_level"])

        control = self._control_struct(index)
        if control is None or control.Count == 0:
            return False, "fan_not_supported"
        for i in range(control.Count):
            control.Items[i].ControlMode = FAN_MODE_MANUAL
            control.Items[i].Level = target
        rc = self._fn[_FUNC_FAN_COOLERS_SET_CONTROL](self._handles[index], ctypes.byref(control))
        if rc == NVAPI_ERROR_INVALID_USER_PRIVILEGE:
            return False, "fan_no_privilege"
        if rc == NVAPI_ERROR_NOT_SUPPORTED:
            return False, "fan_not_supported"
        if rc != NVAPI_OK:
            return False, f"nvapi_error_{rc}"

        time.sleep(readback_s)
        readback = self.fan_control(index)
        if readback is None:
            return False, "fan_no_change"
        for mode, level in readback:
            if mode != FAN_MODE_MANUAL or abs(level - target) > tolerance:
                return False, "fan_no_change"
        return True, None
