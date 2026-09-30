"""
PERS-1: la webview vive en http://127.0.0.1:<puerto> y localStorage es por
origen — el puerto debe salir ESTABLE entre arranques. `main._bind_with_fallback`
prueba el puerto pedido, luego el vecindario (+1…+9) y al final uno dinámico.
"""
from __future__ import annotations

import socket

import main as main_module


def _free_host_port() -> tuple[str, int]:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return "127.0.0.1", port


def test_bind_with_fallback_usa_el_preferido_si_esta_libre():
    host, port = _free_host_port()
    sock, got = main_module._bind_with_fallback(host, port)
    assert got == port
    sock.close()


def test_bind_with_fallback_salta_el_preferido_ocupado():
    host, port = _free_host_port()
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind((host, port))
    try:
        sock, got = main_module._bind_with_fallback(host, port)
        assert got == port + 1
        sock.close()
    finally:
        blocker.close()


def test_bind_with_fallback_preferido_cero_pide_al_so():
    host, _ = _free_host_port()
    sock, got = main_module._bind_with_fallback(host, 0)
    assert got > 0
    sock.close()
