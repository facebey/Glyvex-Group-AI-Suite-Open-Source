"""Tests de stream_metrics.py — cálculo de t/s, TTFT y contexto sin red."""

from stream_metrics import StreamMetrics, reasoning_from_delta


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _reasoning_then_short_answer(clock: FakeClock, m: StreamMetrics, *, with_usage: bool,
                                 with_timings: bool) -> None:
    """
    Caso de la captura: ~9 s de razonamiento en reasoning_content y una
    respuesta corta que llega en ráfaga. 164 tokens en total.
    """
    m.begin_round()
    clock.advance(0.2)                      # prompt processing
    for _ in range(150):                    # razonamiento a ~16.7 t/s
        m.mark_token(thinking=True)
        clock.advance(0.06)
    for i in range(14):                     # respuesta en ráfaga (~9 ms)
        m.mark_token(thinking=False)
        if i == 0:
            m.mark_answer()
        clock.advance(0.0007)
    final: dict = {"choices": []}
    if with_usage:
        final["usage"] = {"prompt_tokens": 20, "completion_tokens": 164}
    if with_timings:
        final["timings"] = {
            "prompt_n": 20, "prompt_ms": 150.0, "cache_n": 0,
            "predicted_n": 164, "predicted_ms": 9000.0,
        }
    m.observe_chunk(final)
    m.end_round()


def test_bug_de_la_captura_con_usage():
    clock = FakeClock()
    m = StreamMetrics(clock=clock)
    _reasoning_then_short_answer(clock, m, with_usage=True, with_timings=False)
    snap = m.snapshot()

    # Antes: 164 / 0.009 s ≈ 18.000 t/s y TTFT ≈ 9 s.
    assert snap["tps"] is not None and 10 < snap["tps"] < 25
    assert snap["ttft_ms"] == 200.0
    assert snap["ttft_answer_ms"] > 9000
    assert snap["tokens_total"] == 164
    assert snap["metrics_source"] == "usage"
    assert snap["context_tokens"] == 184


def test_timings_tienen_prioridad():
    clock = FakeClock()
    m = StreamMetrics(clock=clock)
    _reasoning_then_short_answer(clock, m, with_usage=True, with_timings=True)
    snap = m.snapshot()

    assert snap["tps"] == round(164 / 9.0, 1)
    assert snap["pp_tps"] == round(20 / 0.15, 1)
    assert snap["metrics_source"] == "timings"
    assert snap["context_tokens"] == 184


def test_proporcion_de_razonamiento():
    clock = FakeClock()
    m = StreamMetrics(clock=clock)
    _reasoning_then_short_answer(clock, m, with_usage=True, with_timings=False)
    snap = m.snapshot()
    assert snap["tokens_thinking"] == round(150 * 164 / 164)


def test_espera_de_tools_no_baja_tps():
    clock = FakeClock()
    m = StreamMetrics(clock=clock)

    for _ in range(2):  # dos rondas de 51 chunks a 50 t/s
        m.begin_round()
        clock.advance(0.1)
        for _ in range(51):
            m.mark_token(thinking=False)
            clock.advance(0.02)
        m.end_round()
        clock.advance(5.0)  # búsqueda web entre rondas

    snap = m.snapshot()
    assert snap["tokens_total"] == 102
    assert snap["tps"] == 50.0
    assert snap["metrics_source"] == "chunks"


def test_fuente_del_turno_es_la_peor_ronda():
    clock = FakeClock()
    m = StreamMetrics(clock=clock)

    m.begin_round()
    m.mark_token(thinking=False)
    clock.advance(0.1)
    m.mark_token(thinking=False)
    m.observe_chunk({"timings": {"predicted_n": 2, "predicted_ms": 100.0}})
    m.end_round()

    m.begin_round()
    m.mark_token(thinking=False)
    clock.advance(0.1)
    m.mark_token(thinking=False)
    m.end_round()

    assert m.snapshot()["metrics_source"] == "chunks"


def test_ronda_descartada_no_suma_y_resetea_ttft():
    clock = FakeClock()
    m = StreamMetrics(clock=clock)

    m.begin_round()
    clock.advance(0.5)
    m.mark_token(thinking=False)
    m.mark_answer()
    m.discard_round()

    m.begin_round()
    clock.advance(0.3)
    m.mark_token(thinking=False)
    m.mark_answer()
    m.end_round()

    snap = m.snapshot()
    assert snap["tokens_total"] == 1
    assert snap["ttft_ms"] == 800.0


def test_una_sola_muestra_no_inventa_velocidad():
    clock = FakeClock()
    m = StreamMetrics(clock=clock)
    m.begin_round()
    m.mark_token(thinking=False)
    m.end_round()
    assert m.snapshot()["tps"] is None


def test_snapshot_en_vivo_durante_la_ronda():
    clock = FakeClock()
    m = StreamMetrics(clock=clock)
    m.begin_round()
    for _ in range(11):
        m.mark_token(thinking=True)
        clock.advance(0.1)
    snap = m.snapshot()
    assert snap["tokens_total"] == 11
    assert snap["tokens_thinking"] == 11
    assert 8 < snap["tps"] < 10.5


def test_reasoning_from_delta():
    assert reasoning_from_delta({"reasoning_content": "a"}) == "a"
    assert reasoning_from_delta({"reasoning": "b"}) == "b"
    assert reasoning_from_delta({"content": "c"}) == ""
    assert reasoning_from_delta({"reasoning_content": None}) == ""
