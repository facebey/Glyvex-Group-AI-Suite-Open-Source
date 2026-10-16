"""
fan_control.py — Lógica pura del control automático de ventilador por curva
de temperatura (MON-3). Vive separado de metrics.py para poder probar la
decisión de la curva sin GPU ni adapters: interpolación y anti-oscilación.

La curva es "de software": el driver NVIDIA no expone una curva configurable
por API pública (NvAPI solo tiene modo auto/manual), así que el ciclo de
métricas lee la temperatura y aplica el porcentaje objetivo con el set manual.
Por eso el engine exige el adapter NvAPI: sin él no hay forma documentada de
devolver el fan a automático al cerrar la app (lo que motivó el feature).
"""

from __future__ import annotations

# Re-aplicar solo si el objetivo se mueve al menos este tanto por ciento:
# evita la oscilación cuando la temperatura baila alrededor de un umbral.
FAN_CURVE_MIN_DELTA_PCT = 5
# Con la escritura rechazada (p. ej. sin admin) el ciclo reintenta; tras este
# número de fallos consecutivos la curva se auto-deshabilita para no castigar
# el driver ni llenar el log.
FAN_CURVE_MAX_CONSECUTIVE_FAILURES = 3
# Límites de la curva editable desde la UI.
FAN_CURVE_MIN_POINTS = 1
FAN_CURVE_MAX_POINTS = 6
FAN_CURVE_TEMP_MIN_C = 20
FAN_CURVE_TEMP_MAX_C = 105


def curve_target_pct(temp_c: int, points: list[tuple[int, int]]) -> int:
    """(temp_c, pct) ordenados ascendente → pct objetivo por interpolación
    lineal. Fuera del rango de puntos se mantiene el extremo más cercano."""
    if not points:
        raise ValueError("la curva necesita al menos un punto")
    if temp_c <= points[0][0]:
        return points[0][1]
    if temp_c >= points[-1][0]:
        return points[-1][1]
    for (t0, p0), (t1, p1) in zip(points, points[1:]):
        if temp_c <= t1:
            frac = (temp_c - t0) / (t1 - t0)
            return round(p0 + (p1 - p0) * frac)
    return points[-1][1]


def should_apply_curve(last_target: int | None, target: int,
                       min_delta: int = FAN_CURVE_MIN_DELTA_PCT) -> bool:
    """Primer apply siempre; después solo si el objetivo cambió lo suficiente.
    Si el driver ignoró la escritura, last_target no se actualiza y el
    siguiente ciclo reintenta."""
    if last_target is None:
        return True
    return abs(target - last_target) >= min_delta


def curve_error(points: list[tuple[int, int]]) -> str | None:
    """Clave de error estable o None si la curva es válida: cantidad de
    puntos en rango y temperaturas estrictamente crecientes."""
    if not FAN_CURVE_MIN_POINTS <= len(points) <= FAN_CURVE_MAX_POINTS:
        return "fan_curve_points_count"
    prev_t: int | None = None
    for temp_c, pct in points:
        if not FAN_CURVE_TEMP_MIN_C <= temp_c <= FAN_CURVE_TEMP_MAX_C:
            return "fan_curve_point_range"
        if not 0 <= pct <= 100:
            return "fan_curve_point_range"
        if prev_t is not None and temp_c <= prev_t:
            return "fan_curve_points_order"
        prev_t = temp_c
    return None
