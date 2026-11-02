"""B3: alerta de "fuga creciente" — trepa sostenida, no nivel absoluto.

El Monitor ya alerta con VRAM > 95% (nivel). Este módulo cubre la otra mitad:
la VRAM que CRECE SIN PARAR en el tiempo, señal de memoria que llama-server
retiene y no libera. Para no alertar sesiones normales:

- La carga del modelo no cuenta: un salto único grande deja la pendiente
  MEDIANA en ~0 (se juzga por pendiente mediana, no por el crecimiento total).
- Si la GPU estaba generando (util medio de la ventana sobre el umbral) el
  crecimiento es esperado — KV y activaciones — y no se alerta.
- Con historial insuficiente o una ventana demasiado corta no se opina:
  la alerta requiere datos completos.

Config: MonitorConfig.leak_alert (monitor.leak_alert.* en data/config.json).
"""

import statistics
from datetime import datetime, timezone

from config import config

DEFAULTS: dict[str, float | bool] = {
    "enabled": True,
    # Ventana sobre la que se evalúa la trepa.
    "window_min": 4.0,
    # Velocidad MEDIANA por intervalo (MB/min) que la trepa debe sostener.
    "slope_min_mb_per_min": 32.0,
    # Crecimiento neto mínimo dentro de la ventana.
    "growth_min_mb": 256.0,
    # Fracción de intervalos que no deben haber bajado.
    "monotonic_min_ratio": 0.85,
    # Util medio de la ventana (%) por encima del cual el crecimiento se
    # considera "esperado por actividad": no se alerta.
    "busy_util_pct": 25.0,
    # Huecos más largos que esto (poller detenido, historial reiniciado) no
    # cuentan como intervalo: sin esto, un salto entre dos bloques de datos
    # desconectados inflaría la pendiente.
    "max_gap_s": 30.0,
    # Mínimo de puntos para animarse a evaluar.
    "min_points": 8,
}


def load_config() -> dict:
    raw = config.get("monitor.leak_alert", {}) or {}
    cfg = dict(DEFAULTS)
    if isinstance(raw, dict):
        for key in cfg:
            value = raw.get(key)
            if value is None:
                continue
            cfg[key] = bool(value) if key == "enabled" else float(value)
    return cfg


def evaluate_series(points: list[tuple[float, float, float | None]], cfg: dict) -> dict | None:
    """Veredicto sobre la serie de UNA GPU: [(epoch_s, vram_used_mb, util_pct|None)]
    en orden cronológico. None = no hay fuga.

    Pura: los umbrales y el tiempo entran por argumentos (testeable sin mundo).
    """
    if len(points) < cfg["min_points"]:
        return None
    anchor = points[-1][0]
    window_s = cfg["window_min"] * 60.0
    pts = [p for p in points if p[0] >= anchor - window_s]
    if len(pts) < max(cfg["min_points"], 4):
        return None
    span = pts[-1][0] - pts[0][0]
    if span < window_s * 0.75:
        return None

    rates: list[float] = []  # MB/min por intervalo
    rises = 0
    pairs = 0
    for (t0, u0, _), (t1, u1, _) in zip(pts, pts[1:]):
        dt = t1 - t0
        if dt <= 0 or dt > cfg["max_gap_s"]:
            continue
        pairs += 1
        diff = u1 - u0
        if diff >= 0:
            rises += 1
        rates.append(diff / dt * 60.0)
    if pairs < cfg["min_points"] - 1:
        return None

    monotonic_ratio = rises / pairs
    if monotonic_ratio < cfg["monotonic_min_ratio"]:
        return None
    growth = pts[-1][1] - pts[0][1]
    if growth < cfg["growth_min_mb"]:
        return None
    # Pendiente MEDIANA, no promedio: una carga de modelo (salto único y luego
    # estable) deja la mediana en ~0 y no dispara; una trepa sostenida sí.
    slope = statistics.median(rates)
    if slope < cfg["slope_min_mb_per_min"]:
        return None

    utils = [p[2] for p in pts if p[2] is not None]
    avg_util = sum(utils) / len(utils) if utils else 0.0
    if avg_util >= cfg["busy_util_pct"]:
        return None

    return {
        "slope_mb_per_min": round(slope, 1),
        "growth_mb": round(growth, 1),
        "monotonic_ratio": round(monotonic_ratio, 2),
        "span_s": round(span, 1),
        "vram_used_mb": pts[-1][1],
        "n_points": len(pts),
    }


def evaluate_history(history, cfg: dict | None = None) -> list[dict]:
    """Agrupa el historial (MetricsSnapshots) por GPU y evalúa cada serie.

    Devuelve alertas ordenadas por pendiente (la peor primero). Vacío si no
    hay GPU, si el detector está deshabilitado o si ningún caso supera los
    umbrales.
    """
    cfg = cfg or load_config()
    if not cfg["enabled"]:
        return []
    series: dict[int, list[tuple[float, float, float | None]]] = {}
    gpu_meta: dict[int, dict] = {}
    for snap in history:
        try:
            ts = datetime.fromisoformat(snap.timestamp)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            epoch = ts.timestamp()
        except ValueError:
            continue
        for gpu in snap.gpu or []:
            if gpu.vram_total_mb <= 0:
                continue
            series.setdefault(gpu.index, []).append((epoch, gpu.vram_used_mb, gpu.gpu_utilization))
            gpu_meta[gpu.index] = {
                "gpu_index": gpu.index,
                "gpu_name": gpu.name,
                "vram_total_mb": gpu.vram_total_mb,
                "vram_percent": gpu.vram_percent,
            }
    alerts = []
    for index, points in series.items():
        verdict = evaluate_series(points, cfg)
        if verdict:
            alerts.append({**gpu_meta[index], **verdict})
    alerts.sort(key=lambda a: a["slope_mb_per_min"], reverse=True)
    return alerts
