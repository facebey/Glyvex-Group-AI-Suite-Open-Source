"""Tests de fan_control.py — lógica pura de decisión (sin GPU ni adapters)."""

import pytest

import fan_control
from fan_control import curve_error, curve_target_pct, should_apply_curve


def test_curve_target_extremos():
    pts = [(45, 30), (70, 80)]
    assert curve_target_pct(20, pts) == 30   # bajo el primer punto: el extremo
    assert curve_target_pct(45, pts) == 30
    assert curve_target_pct(70, pts) == 80
    assert curve_target_pct(105, pts) == 80  # sobre el último: el extremo


def test_curve_target_interpolacion():
    pts = [(45, 30), (70, 80)]
    assert curve_target_pct(46, pts) == 32
    assert curve_target_pct(57, pts) == 54
    assert curve_target_pct(58, pts) == 56
    assert curve_target_pct(69, pts) == 78


def test_curve_target_multi_tramo():
    pts = [(40, 30), (60, 50), (80, 90)]
    assert curve_target_pct(50, pts) == 40   # tramo 1
    assert curve_target_pct(70, pts) == 70   # tramo 2 (no el punto final)


def test_curve_target_un_punto_constante():
    assert curve_target_pct(80, [(50, 60)]) == 60


def test_curve_target_sin_puntos():
    with pytest.raises(ValueError):
        curve_target_pct(50, [])


def test_should_apply_curve_anti_oscilacion():
    assert should_apply_curve(None, 40) is True    # el primer apply siempre
    assert should_apply_curve(40, 42) is False     # cambio menor al delta
    assert should_apply_curve(40, 44) is False
    assert should_apply_curve(40, 45) is True      # delta exacto
    assert should_apply_curve(40, 34) is True      # hacia abajo cuenta igual
    # Si la escritura falló, last no se actualiza: el mismo target se reintenta.
    assert should_apply_curve(None, 40) is True


def test_curve_error_forma():
    assert curve_error([(45, 30), (70, 80)]) is None
    assert curve_error([(45, 30)]) is None
    assert curve_error([]) == "fan_curve_points_count"
    assert curve_error([(40, 30), (50, 40), (60, 50), (70, 60),
                        (80, 70), (90, 80), (100, 90)]) == "fan_curve_points_count"


def test_curve_error_orden():
    assert curve_error([(70, 80), (45, 30)]) == "fan_curve_points_order"
    assert curve_error([(45, 30), (45, 40)]) == "fan_curve_points_order"


def test_curve_error_rangos():
    assert curve_error([(10, 30)]) == "fan_curve_point_range"    # temp < 20
    assert curve_error([(120, 30)]) == "fan_curve_point_range"   # temp > 105
    assert curve_error([(45, 130)]) == "fan_curve_point_range"   # pct > 100
    assert curve_error([(45, -1)]) == "fan_curve_point_range"
