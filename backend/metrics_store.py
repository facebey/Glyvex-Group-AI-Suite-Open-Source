"""
metrics_store.py — Histórico de métricas en SQLite con tres niveles de resolución.

Base aparte (`<DATA_DIR>/metrics.db`, ver paths.py): el poller escribe cada pocos segundos y no
debe competir con chat y benchmarks en `glyvex.db`; además el histórico se
puede borrar sin tocar nada más.

Por qué sqlite3 de la librería estándar y no SQLAlchemy como database.py:
acá todo es SQL de series temporales (INSERT … SELECT con GROUP BY, tablas
WITHOUT ROWID, borrado por rango) y el ORM no aporta nada. Todas las
operaciones son síncronas y cortas; metrics.py las llama con
asyncio.to_thread, y un lock serializa el acceso a la conexión.

Esquema (formato "angosto": una fila por serie y ventana de tiempo, así un
sensor nuevo — la junction de una AMD, un fan más de LHM — no requiere
migración):

    metric_series  (id, key, scope, process_id, model_name, unit)
    samples_raw    (series_id, ts, avg, min, max)   ventanas de 5 s
    samples_1m     (series_id, ts, avg, min, max)   1 minuto
    samples_1h     (series_id, ts, avg, min, max)   1 hora

`ts` es epoch en segundos (entero): índice más chico y rangos más rápidos
que ISO8601. Cada fila guarda promedio, mínimo y máximo de su ventana, así
un pico de temperatura de 2 s sobrevive a la compactación.
"""

from __future__ import annotations

import math
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

RAW_STEP_S = 5
TIERS: tuple[tuple[str, int], ...] = (
    ("raw", RAW_STEP_S),
    ("1m", 60),
    ("1h", 3600),
)
TABLE = {"raw": "samples_raw", "1m": "samples_1m", "1h": "samples_1h"}

# Margen antes de compactar una ventana: el poller vuelca cada 5 s, así que
# un minuto recién cerrado todavía puede recibir su última ventana.
ROLLUP_LAG_S = 15


@dataclass(frozen=True)
class Retention:
    raw_s: int = 48 * 3600
    m1_s: int = 30 * 86400
    h1_s: int = 365 * 86400

    def for_tier(self, tier: str) -> int:
        return {"raw": self.raw_s, "1m": self.m1_s, "1h": self.h1_s}[tier]


@dataclass(frozen=True)
class SampleWindow:
    key: str
    ts: int
    avg: float
    min: float
    max: float


_SCHEMA = """
CREATE TABLE IF NOT EXISTS metric_series (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT NOT NULL,
    scope       TEXT NOT NULL,
    -- '' para series de hardware: con NULL, UNIQUE no deduplica en SQLite.
    process_id  TEXT NOT NULL DEFAULT '',
    model_name  TEXT,
    unit        TEXT NOT NULL DEFAULT '',
    UNIQUE (key, process_id)
);
CREATE TABLE IF NOT EXISTS metric_meta (
    name   TEXT PRIMARY KEY,
    value  INTEGER NOT NULL
) WITHOUT ROWID;
"""

_SAMPLES = """
CREATE TABLE IF NOT EXISTS {table} (
    series_id  INTEGER NOT NULL,
    ts         INTEGER NOT NULL,
    avg        REAL NOT NULL,
    min        REAL NOT NULL,
    max        REAL NOT NULL,
    PRIMARY KEY (series_id, ts)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_{table}_ts ON {table} (ts);
"""


class MetricsStore:
    def __init__(self, path: Path | str, retention: Retention | None = None) -> None:
        self.path = Path(path) if str(path) != ":memory:" else path
        self.retention = retention or Retention()
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._series_cache: dict[tuple[str, str], int] = {}

    # -- ciclo de vida ---------------------------------------------------------

    def open(self) -> None:
        with self._lock:
            if self._conn is not None:
                return
            if isinstance(self.path, Path):
                self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA)
            for table in TABLE.values():
                conn.executescript(_SAMPLES.format(table=table))
            self._conn = conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
            self._series_cache.clear()

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("MetricsStore no está abierto")
        return self._conn

    # -- series ---------------------------------------------------------------

    def _series_id(
        self, conn: sqlite3.Connection, key: str, *, scope: str, unit: str,
        process_id: str, model_name: str | None,
    ) -> int:
        cache_key = (key, process_id)
        cached = self._series_cache.get(cache_key)
        if cached is not None:
            return cached
        conn.execute(
            "INSERT OR IGNORE INTO metric_series (key, scope, process_id, model_name, unit) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, scope, process_id, model_name, unit),
        )
        row = conn.execute(
            "SELECT id FROM metric_series WHERE key = ? AND process_id = ?", (key, process_id)
        ).fetchone()
        self._series_cache[cache_key] = row[0]
        return row[0]

    def list_series(self, scope: str | None = None) -> list[dict[str, Any]]:
        """Series con su rango de datos disponible (primer y último ts en cualquier nivel)."""
        sql = """
            SELECT s.key, s.scope, s.process_id, s.model_name, s.unit,
                   MIN(t.first_ts), MAX(t.last_ts)
            FROM metric_series s
            LEFT JOIN (
                SELECT series_id, MIN(ts) AS first_ts, MAX(ts) AS last_ts FROM samples_raw GROUP BY series_id
                UNION ALL
                SELECT series_id, MIN(ts), MAX(ts) FROM samples_1m GROUP BY series_id
                UNION ALL
                SELECT series_id, MIN(ts), MAX(ts) FROM samples_1h GROUP BY series_id
            ) t ON t.series_id = s.id
            {where}
            GROUP BY s.id
            ORDER BY s.scope, s.process_id, s.key
        """
        where, params = ("WHERE s.scope = ?", (scope,)) if scope else ("", ())
        with self._lock:
            rows = self._db().execute(sql.format(where=where), params).fetchall()
        return [
            {
                "key": r[0], "scope": r[1], "process_id": r[2] or None, "model_name": r[3],
                "unit": r[4], "first_ts": r[5], "last_ts": r[6],
            }
            for r in rows
        ]

    # -- escritura ------------------------------------------------------------

    def write_raw(
        self,
        windows: Iterable[SampleWindow],
        *,
        scope: str = "hw",
        units: dict[str, str] | None = None,
        process_id: str = "",
        model_name: str | None = None,
    ) -> int:
        units = units or {}
        items = list(windows)
        if not items:
            return 0
        with self._lock:
            conn = self._db()
            conn.execute("BEGIN")
            try:
                rows = [
                    (
                        self._series_id(conn, w.key, scope=scope, unit=units.get(w.key, ""),
                                        process_id=process_id, model_name=model_name),
                        w.ts, w.avg, w.min, w.max,
                    )
                    for w in items
                ]
                conn.executemany(
                    "INSERT OR REPLACE INTO samples_raw (series_id, ts, avg, min, max) VALUES (?, ?, ?, ?, ?)",
                    rows,
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                # Un id cacheado de una transacción que no llegó a commitear no existe.
                self._series_cache.clear()
                raise
        return len(rows)

    # -- compactación y retención --------------------------------------------

    def _meta_get(self, conn: sqlite3.Connection, name: str) -> int | None:
        row = conn.execute("SELECT value FROM metric_meta WHERE name = ?", (name,)).fetchone()
        return row[0] if row else None

    def _meta_set(self, conn: sqlite3.Connection, name: str, value: int) -> None:
        conn.execute(
            "INSERT INTO metric_meta (name, value) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET value = excluded.value",
            (name, value),
        )

    def _rollup_step(self, conn: sqlite3.Connection, src: str, dst: str, step: int, now: int) -> int:
        """
        Compacta ventanas completas de `src` en `dst`. Es idempotente: vuelve a
        calcular desde la marca guardada, así un corte a mitad no deja huecos.
        """
        mark_name = f"rollup_{dst}_until"
        until = ((now - ROLLUP_LAG_S) // step) * step
        since = self._meta_get(conn, mark_name)
        if since is None:
            first = conn.execute(f"SELECT MIN(ts) FROM {TABLE[src]}").fetchone()[0]
            if first is None:
                return 0
            since = (first // step) * step
        if until <= since:
            return 0
        # Promedio de promedios: correcto porque dentro de un nivel todas las
        # ventanas tienen la misma duración.
        cur = conn.execute(
            f"""
            INSERT OR REPLACE INTO {TABLE[dst]} (series_id, ts, avg, min, max)
            SELECT series_id, (ts / ?) * ?, AVG(avg), MIN(min), MAX(max)
            FROM {TABLE[src]}
            WHERE ts >= ? AND ts < ?
            GROUP BY series_id, (ts / ?) * ?
            """,
            (step, step, since, until, step, step),
        )
        self._meta_set(conn, mark_name, until)
        return cur.rowcount

    def rollup(self, now: int) -> dict[str, int]:
        with self._lock:
            conn = self._db()
            conn.execute("BEGIN")
            try:
                done = {
                    "1m": self._rollup_step(conn, "raw", "1m", 60, now),
                    "1h": self._rollup_step(conn, "1m", "1h", 3600, now),
                }
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return done

    def purge(self, now: int) -> dict[str, int]:
        deleted: dict[str, int] = {}
        with self._lock:
            conn = self._db()
            for tier, table in TABLE.items():
                cutoff = now - self.retention.for_tier(tier)
                deleted[tier] = conn.execute(f"DELETE FROM {table} WHERE ts < ?", (cutoff,)).rowcount
            # Series sin samples en ningún nivel: sin esto un proceso purgado
            # seguiría apareciendo en list_series (y en /processes) para siempre.
            deleted["series"] = conn.execute(
                "DELETE FROM metric_series WHERE id NOT IN ("
                "SELECT series_id FROM samples_raw"
                " UNION SELECT series_id FROM samples_1m"
                " UNION SELECT series_id FROM samples_1h)"
            ).rowcount
        # Si se borró alguna, el cache de ids quedó con referencias muertas.
        self._series_cache.clear()
        return deleted

    def delete_process(self, process_id: str) -> int:
        """
        Borra todas las muestras (los tres niveles) y las series de un proceso.
        Devuelve cuántas series se eliminaron (0 si el proceso no tenía nada).
        """
        if not process_id:
            return 0
        with self._lock:
            conn = self._db()
            conn.execute("BEGIN")
            try:
                ids = [
                    r[0]
                    for r in conn.execute(
                        "SELECT id FROM metric_series WHERE process_id = ?", (process_id,)
                    )
                ]
                if not ids:
                    conn.execute("COMMIT")
                    return 0
                placeholders = ",".join("?" * len(ids))
                for table in TABLE.values():
                    conn.execute(f"DELETE FROM {table} WHERE series_id IN ({placeholders})", ids)
                deleted = conn.execute(
                    f"DELETE FROM metric_series WHERE id IN ({placeholders})", ids
                ).rowcount
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                self._series_cache.clear()
                raise
        self._series_cache.clear()
        return deleted

    # -- lectura --------------------------------------------------------------

    def choose_tier(self, start: int, end: int, now: int, max_points: int) -> tuple[str, int]:
        """
        Nivel más fino que todavía tiene datos de `start` según la retención, y
        el paso de agrupación para no pasar de `max_points` (múltiplo del paso
        del nivel).

        No se salta a un nivel más grueso para ahorrar filas: los niveles de
        1 min y 1 h se completan con la compactación periódica, así que los
        últimos minutos de un rango reciente solo existen en raw. Agrupar raw
        en SQL es barato (48 h son ~35 mil filas por serie).
        """
        span = max(1, end - start)
        tier, base = TIERS[-1]
        for name, step in TIERS:
            if start >= now - self.retention.for_tier(name):
                tier, base = name, step
                break
        bucket = max(base, math.ceil(span / max_points / base) * base)
        return tier, bucket

    def query(
        self,
        keys: list[str],
        start: int,
        end: int,
        *,
        now: int,
        process_id: str = "",
        max_points: int = 600,
    ) -> dict[str, Any]:
        max_points = max(10, min(max_points, 5000))
        tier, bucket = self.choose_tier(start, end, now, max_points)
        table = TABLE[tier]
        series: dict[str, list[dict[str, float]]] = {key: [] for key in keys}
        if not keys:
            return {"tier": tier, "resolution_s": bucket, "series": series}

        placeholders = ",".join("?" for _ in keys)
        sql = f"""
            SELECT s.key, (t.ts / ?) * ? AS b, AVG(t.avg), MIN(t.min), MAX(t.max)
            FROM {table} t
            JOIN metric_series s ON s.id = t.series_id
            WHERE s.key IN ({placeholders}) AND s.process_id = ? AND t.ts >= ? AND t.ts < ?
            GROUP BY s.key, b
            ORDER BY s.key, b
        """
        params = (bucket, bucket, *keys, process_id, start, end)
        with self._lock:
            rows = self._db().execute(sql, params).fetchall()
        for key, ts, avg, mn, mx in rows:
            series[key].append({"t": ts, "avg": round(avg, 3), "min": round(mn, 3), "max": round(mx, 3)})
        return {"tier": tier, "resolution_s": bucket, "series": series}
