"""Tests de metrics_store.py — escritura, compactación, retención y consultas."""

from metrics_store import (
    ROLLUP_LAG_S,
    MetricsStore,
    Retention,
    SampleWindow,
)

T0 = 1_780_000_000 // 3600 * 3600  # una hora en punto, para cuentas exactas


def _store(tmp_path, retention=None) -> MetricsStore:
    store = MetricsStore(tmp_path / "metrics.db", retention)
    store.open()
    return store


def _fill_raw(store: MetricsStore, key: str, start: int, seconds: int, process_id: str = "",
              value_at=lambda ts: 50.0):
    windows = [
        SampleWindow(key=key, ts=ts, avg=value_at(ts), min=value_at(ts) - 1, max=value_at(ts) + 1)
        for ts in range(start, start + seconds, 5)
    ]
    store.write_raw(
        windows,
        scope="llm" if process_id else "hw",
        process_id=process_id,
        units={key: "" if process_id else "°C"},
    )


def test_series_hw_no_se_duplican(tmp_path):
    store = _store(tmp_path)
    _fill_raw(store, "gpu.0.temp_c", T0, 10)
    store.close()

    # Reabrir limpia el cache: el INSERT OR IGNORE tiene que encontrar la
    # serie existente (con process_id NULL, UNIQUE no deduplicaría).
    store = _store(tmp_path)
    _fill_raw(store, "gpu.0.temp_c", T0 + 10, 10)
    series = store.list_series()
    assert len(series) == 1
    assert series[0]["unit"] == "°C"
    assert series[0]["first_ts"] == T0
    store.close()


def test_consumption_totals_mergea_reinicios(tmp_path):
    # MON-4: corrida 1 acumula 100→250; el llama-server reinicia con el mismo
    # process_id y arranca la corrida 2 en 40→90. El total de vida mergea:
    # 250 de la corrida 1 + 90 de la corrida 2 = 340.
    store = _store(tmp_path)
    seq = {T0: 100.0, T0 + 5: 250.0, T0 + 10: 40.0, T0 + 15: 90.0}
    windows = [
        SampleWindow(key="llm.prompt_tokens_total", ts=ts, avg=v, min=v, max=v)
        for ts, v in seq.items()
    ]
    store.write_raw(windows, scope="llm", process_id="p1", model_name="M1",
                    units={"llm.prompt_tokens_total": "tokens"})
    res = store.consumption_totals("p1", ["llm.prompt_tokens_total", "llm.tokens_predicted_total"])
    e = res["llm.prompt_tokens_total"]
    assert e["total"] == 340 and e["runs"] == 2
    assert e["model_name"] == "M1"
    assert e["first_ts"] == T0 and e["last_ts"] == T0 + 15
    # Key o proceso sin muestras: None, no 0.
    assert res["llm.tokens_predicted_total"] is None
    missing = store.consumption_totals("pX", ["llm.prompt_tokens_total"])
    assert missing["llm.prompt_tokens_total"] is None
    store.close()


def test_consumption_series_all_suma_todas_las_instancias(tmp_path):
    # MON-5: una fila por cada serie llm de la db, con el merge de reinicios
    # por proceso. El scope hw queda afuera aunque comparta la key.
    store = _store(tmp_path)

    def write(pid, model, key, seq):
        windows = [SampleWindow(key=key, ts=ts, avg=v, min=v, max=v) for ts, v in seq.items()]
        store.write_raw(windows, scope="llm", process_id=pid, model_name=model,
                        units={key: "tokens"})

    write("p1", "M1", "llm.prompt_tokens_total", {T0: 100.0, T0 + 5: 250.0, T0 + 10: 40.0, T0 + 15: 90.0})
    write("p1", "M1", "llm.tokens_predicted_total", {T0: 10.0, T0 + 5: 30.0})
    write("p2", "M2", "llm.prompt_tokens_total", {T0 + 100: 50.0, T0 + 105: 80.0})
    store.write_raw(  # mismo key en scope hw: no forma parte del gran total.
        [SampleWindow(key="llm.prompt_tokens_total", ts=T0, avg=999.0, min=999.0, max=999.0)],
        scope="hw",
    )

    keys = ["llm.prompt_tokens_total", "llm.tokens_predicted_total", "llm.prompt_tokens_cached_total"]
    rows = store.consumption_series_all(keys)
    assert len(rows) == 3  # p1×2 + p2×1; la serie hw se descarta
    by = {(r["process_id"], r["key"]): r for r in rows}
    p1_prompt = by[("p1", "llm.prompt_tokens_total")]
    assert p1_prompt["total"] == 340 and p1_prompt["runs"] == 2
    assert p1_prompt["model_name"] == "M1"
    assert p1_prompt["first_ts"] == T0 and p1_prompt["last_ts"] == T0 + 15
    assert by[("p1", "llm.tokens_predicted_total")]["total"] == 30
    assert by[("p2", "llm.prompt_tokens_total")]["total"] == 80
    assert by[("p2", "llm.prompt_tokens_total")]["model_name"] == "M2"
    # Key sin series para ese proceso: no aparece la fila (no None).
    assert ("p2", "llm.tokens_predicted_total") not in by

    # DB vacía o sin keys: lista vacía, no error.
    empty = _store(tmp_path.parent / "empty.db")
    assert empty.consumption_series_all(keys) == []
    assert empty.consumption_series_all([]) == []
    store.close()
    empty.close()


def test_rollup_a_minuto_preserva_min_y_max(tmp_path):
    store = _store(tmp_path)
    # Un pico de 95 °C en una sola ventana de 5 s dentro del minuto.
    _fill_raw(store, "gpu.0.temp_c", T0, 120, value_at=lambda ts: 95.0 if ts == T0 + 30 else 60.0)
    store.rollup(now=T0 + 120 + ROLLUP_LAG_S)

    res = store.query(["gpu.0.temp_c"], T0, T0 + 120, now=T0 + 3 * 86400, max_points=600)
    assert res["tier"] == "1m"
    first_minute = res["series"]["gpu.0.temp_c"][0]
    assert first_minute["t"] == T0
    assert first_minute["max"] == 96.0          # 95 + 1 del max de la ventana
    assert first_minute["min"] == 59.0
    assert 60.0 < first_minute["avg"] < 95.0
    store.close()


def test_rollup_es_idempotente_y_respeta_el_margen(tmp_path):
    store = _store(tmp_path)
    _fill_raw(store, "cpu.total_pct", T0, 180)

    # A los 170 s el margen deja compactar solo los minutos T0 y T0+60:
    # until = (170 - 15) // 60 * 60 = 120. El minuto en curso espera.
    store.rollup(now=T0 + 170)
    store.rollup(now=T0 + 170)

    # Consulta a 3 días: raw ya no cubre ese inicio, se lee el nivel de 1 min.
    res = store.query(["cpu.total_pct"], T0, T0 + 180, now=T0 + 3 * 86400)
    assert res["tier"] == "1m"
    points = res["series"]["cpu.total_pct"]
    assert [p["t"] for p in points] == [T0, T0 + 60]
    assert all(p["avg"] == 50.0 for p in points)   # repetir no duplica ni altera

    store.rollup(now=T0 + 180 + ROLLUP_LAG_S)
    res = store.query(["cpu.total_pct"], T0, T0 + 180, now=T0 + 3 * 86400)
    assert [p["t"] for p in res["series"]["cpu.total_pct"]] == [T0, T0 + 60, T0 + 120]
    store.close()


def test_rollup_a_hora(tmp_path):
    store = _store(tmp_path)
    _fill_raw(store, "ram.pct", T0, 7200, value_at=lambda ts: 40.0 if ts < T0 + 3600 else 80.0)
    store.rollup(now=T0 + 7200 + ROLLUP_LAG_S)

    res = store.query(["ram.pct"], T0, T0 + 7200, now=T0 + 60 * 86400, max_points=100)
    assert res["tier"] == "1h"
    points = res["series"]["ram.pct"]
    assert [p["t"] for p in points] == [T0, T0 + 3600]
    assert points[0]["avg"] == 40.0
    assert points[1]["avg"] == 80.0
    store.close()


def test_purge_por_nivel(tmp_path):
    retention = Retention(raw_s=3600, m1_s=86400, h1_s=10 * 86400)
    store = _store(tmp_path, retention)
    _fill_raw(store, "cpu.total_pct", T0, 600)
    store.rollup(now=T0 + 600 + ROLLUP_LAG_S)

    deleted = store.purge(now=T0 + 7200)
    assert deleted["raw"] == 120               # 600 s / 5 s, más viejo que 1 h
    assert deleted["1m"] == 0                  # dentro de 1 día
    assert deleted["series"] == 0              # todavía tiene samples en 1m
    store.close()


def test_purge_borra_series_huerfanas(tmp_path):
    """Un proceso purgado no sigue apareciendo en list_series para siempre."""
    retention = Retention(raw_s=3600, m1_s=86400, h1_s=10 * 86400)
    store = _store(tmp_path, retention)
    _fill_raw(store, "cpu.total_pct", T0, 10)
    store.write_raw(
        [SampleWindow(key="llm.tg_tps", ts=T0, avg=50.0, min=49.0, max=51.0)],
        scope="llm", units={"llm.tg_tps": "t/s"},
        process_id="proc-antiguo", model_name="old.gguf",
    )

    deleted = store.purge(now=T0 + 2 * 3600)
    assert deleted["raw"] == 3                 # 2 HW + 1 LLM, todos fuera de retención
    assert deleted["series"] == 2
    assert store.list_series() == []
    store.close()


def test_eleccion_de_nivel_segun_rango():
    store = MetricsStore(":memory:")
    now = T0
    assert store.choose_tier(now - 3600, now, now, 600) == ("raw", 10)
    assert store.choose_tier(now - 6 * 3600, now, now, 600)[0] == "raw"
    assert store.choose_tier(now - 48 * 3600, now, now, 600)[0] == "raw"
    assert store.choose_tier(now - 3 * 86400, now, now, 600)[0] == "1m"
    assert store.choose_tier(now - 31 * 86400, now, now, 600)[0] == "1h"
    # Aunque el rango sea corto, si empieza antes de la retención de raw no
    # hay datos finos: se usa el nivel que todavía los tiene.
    assert store.choose_tier(now - 3 * 86400, now - 3 * 86400 + 600, now, 600)[0] == "1m"


def test_query_limita_puntos(tmp_path):
    store = _store(tmp_path)
    _fill_raw(store, "gpu.0.util_pct", T0, 6 * 3600)
    res = store.query(["gpu.0.util_pct"], T0, T0 + 6 * 3600, now=T0 + 6 * 3600, max_points=300)
    assert res["tier"] == "raw"
    assert res["resolution_s"] == 75
    assert len(res["series"]["gpu.0.util_pct"]) <= 300
    store.close()


def test_series_por_proceso_separadas(tmp_path):
    store = _store(tmp_path)
    for pid in ("proc-a", "proc-b"):
        store.write_raw(
            [SampleWindow("llm.tg_tps", T0, 100.0 if pid == "proc-a" else 40.0, 1, 1)],
            scope="llm", process_id=pid, model_name=f"model-{pid}",
        )
    a = store.query(["llm.tg_tps"], T0, T0 + 5, now=T0 + 5, process_id="proc-a")
    b = store.query(["llm.tg_tps"], T0, T0 + 5, now=T0 + 5, process_id="proc-b")
    assert a["series"]["llm.tg_tps"][0]["avg"] == 100.0
    assert b["series"]["llm.tg_tps"][0]["avg"] == 40.0
    assert len(store.list_series(scope="llm")) == 2


def test_delete_process_borra_todos_los_niveles(tmp_path):
    store = _store(tmp_path)
    _fill_raw(store, "gpu.0.temp_c", T0, 300)
    for pid in ("proc-a", "proc-b"):
        _fill_raw(store, "llm.tg_tps", T0, 300, process_id=pid)
    store.rollup(now=T0 + 2 * 3600)
    store.rollup(now=T0 + 24 * 3600)

    assert store.delete_process("proc-inexistente") == 0
    assert store.delete_process("") == 0
    assert store.delete_process("proc-a") == 1

    # Ni rastro en ningún nivel, ni en list_series.
    res = store.query(["llm.tg_tps"], T0, T0 + 300, now=T0 + 300, process_id="proc-a")
    assert res["series"]["llm.tg_tps"] == []
    llm_series = store.list_series(scope="llm")
    assert [s["process_id"] for s in llm_series] == ["proc-b"]
    # Lo del otro proceso y el hardware siguen intactos.
    b = store.query(["llm.tg_tps"], T0, T0 + 300, now=T0 + 300, process_id="proc-b")
    assert b["series"]["llm.tg_tps"]
    hw = store.query(["gpu.0.temp_c"], T0, T0 + 300, now=T0 + 300)
    assert hw["series"]["gpu.0.temp_c"]
    store.close()
    store.close()
