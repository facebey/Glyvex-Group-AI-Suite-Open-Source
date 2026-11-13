"""Tests de spec multi-modo (LL-4): selector spec_type, modos ngram, migración
legacy MTP, degradación en builds viejas y detección de rechazo real de la
especulación en el log de llama-server (U5). Unitarios puros (sin red); el
integration test del pump usa un subprocess local de Python, sin GPU."""

import asyncio
import sys

import pytest
from pydantic import ValidationError

import launcher as launcher_module


def _build(cfg):
    return launcher_module.build_llama_server_command(
        cfg, "/opt/llama-server", "/models/model.gguf"
    )


# --------------------------------------------------------------------------
# Modos draft no-MTP (dflash / simple / eagle3): mismo plano de flags
# --------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["draft-dflash", "draft-simple", "draft-eagle3"])
def test_draft_modes_emit_native_spec_type(mode):
    cfg = launcher_module.LaunchConfig(
        model_id="m", spec_type=mode, draft_model="/models/draft.gguf", n_draft=6
    )
    cmd = _build(cfg)
    assert cmd[cmd.index("--spec-type") + 1] == mode
    assert cmd[cmd.index("--spec-draft-model") + 1] == "/models/draft.gguf"
    assert cmd[cmd.index("--spec-draft-n-max") + 1] == "6"
    # Pisos y KV del draft: comportamiento idéntico al MTP (comunes a todos
    # los modos draft).
    assert cmd[cmd.index("--spec-draft-type-k") + 1] == "q8_0"
    assert cmd[cmd.index("--spec-draft-type-v") + 1] == "q8_0"
    assert "--spec-draft-n-min" not in cmd
    assert "--spec-draft-p-min" not in cmd


def test_draft_mtp_new_style_with_draft_model():
    """Camino nuevo (sin campos legacy): spec_type explícito + draft_model."""
    cfg = launcher_module.LaunchConfig(
        model_id="m", spec_type="draft-mtp", draft_model="/models/draft.gguf"
    )
    cmd = _build(cfg)
    assert cmd[cmd.index("--spec-type") + 1] == "draft-mtp"
    assert cmd[cmd.index("--spec-draft-model") + 1] == "/models/draft.gguf"


def test_draft_mtp_new_style_embedded():
    """draft-mtp embebido, camino nuevo: activa spec sin archivo de draft."""
    cfg = launcher_module.LaunchConfig(model_id="m", spec_type="draft-mtp", mtp_embedded=True)
    cmd = _build(cfg)
    assert cmd[cmd.index("--spec-type") + 1] == "draft-mtp"
    assert "--spec-draft-model" not in cmd


def test_draft_floors_apply_to_all_draft_modes():
    cfg = launcher_module.LaunchConfig(
        model_id="m", spec_type="draft-dflash", draft_model="/models/d.gguf",
        n_draft_min=2, p_draft_min=0.75,
    )
    cmd = _build(cfg)
    assert cmd[cmd.index("--spec-draft-n-min") + 1] == "2"
    assert cmd[cmd.index("--spec-draft-p-min") + 1] == "0.75"


# --------------------------------------------------------------------------
# Validaciones cross-field
# --------------------------------------------------------------------------

def test_draft_mtp_without_draft_or_embedded_rejected():
    with pytest.raises(ValidationError, match="draft-mtp"):
        launcher_module.LaunchConfig(model_id="m", spec_type="draft-mtp")


@pytest.mark.parametrize("mode", ["draft-dflash", "draft-simple", "draft-eagle3"])
def test_draft_modes_require_draft_model(mode):
    with pytest.raises(ValidationError, match="draft_model"):
        launcher_module.LaunchConfig(model_id="m", spec_type=mode)


def test_mtp_embedded_alone_is_valid():
    cfg = launcher_module.LaunchConfig(model_id="m", spec_type="draft-mtp", mtp_embedded=True)
    assert cfg.spec_type == "draft-mtp"


# --------------------------------------------------------------------------
# Modos ngram (sin modelo draft ni VRAM extra)
# --------------------------------------------------------------------------

def test_ngram_simple_defaults_not_emitted():
    """Sin parámetros = defaults del binario: solo se emite el selector."""
    cfg = launcher_module.LaunchConfig(model_id="m", spec_type="ngram-simple")
    cmd = _build(cfg)
    assert cmd[cmd.index("--spec-type") + 1] == "ngram-simple"
    for flag in ("--spec-ngram-simple-size-n", "--spec-ngram-simple-size-m",
                 "--spec-ngram-simple-min-hits", "--spec-draft-model",
                 "--spec-draft-n-max"):
        assert flag not in cmd


def test_ngram_simple_with_params():
    cfg = launcher_module.LaunchConfig(
        model_id="m", spec_type="ngram-simple",
        ngram_size_n=8, ngram_size_m=32, ngram_min_hits=2,
    )
    cmd = _build(cfg)
    assert cmd[cmd.index("--spec-ngram-simple-size-n") + 1] == "8"
    assert cmd[cmd.index("--spec-ngram-simple-size-m") + 1] == "32"
    assert cmd[cmd.index("--spec-ngram-simple-min-hits") + 1] == "2"


def test_ngram_mod_defaults_not_emitted():
    cfg = launcher_module.LaunchConfig(model_id="m", spec_type="ngram-mod")
    cmd = _build(cfg)
    assert cmd[cmd.index("--spec-type") + 1] == "ngram-mod"
    for flag in ("--spec-ngram-mod-n-min", "--spec-ngram-mod-n-max",
                 "--spec-ngram-mod-n-match", "--spec-draft-model"):
        assert flag not in cmd


def test_ngram_mod_with_params():
    cfg = launcher_module.LaunchConfig(
        model_id="m", spec_type="ngram-mod",
        ngram_n_min=32, ngram_n_max=48, ngram_n_match=16,
    )
    cmd = _build(cfg)
    assert cmd[cmd.index("--spec-ngram-mod-n-min") + 1] == "32"
    assert cmd[cmd.index("--spec-ngram-mod-n-max") + 1] == "48"
    assert cmd[cmd.index("--spec-ngram-mod-n-match") + 1] == "16"


def test_ngram_simple_size_m_must_exceed_size_n():
    with pytest.raises(ValidationError, match="ngram_size_m"):
        launcher_module.LaunchConfig(
            model_id="m", spec_type="ngram-simple", ngram_size_n=12, ngram_size_m=12
        )


def test_ngram_mod_max_must_not_be_below_min():
    with pytest.raises(ValidationError, match="ngram_n_max"):
        launcher_module.LaunchConfig(
            model_id="m", spec_type="ngram-mod", ngram_n_min=48, ngram_n_max=32
        )


@pytest.mark.parametrize("mode", ["ngram-simple", "ngram-mod"])
def test_ngram_params_below_one_rejected(mode):
    fields = (["ngram_size_n", "ngram_size_m", "ngram_min_hits"] if mode == "ngram-simple"
              else ["ngram_n_min", "ngram_n_max", "ngram_n_match"])
    for field in fields:
        with pytest.raises(ValidationError, match=field):
            launcher_module.LaunchConfig(model_id="m", spec_type=mode, **{field: 0})


# --------------------------------------------------------------------------
# Migración legacy (configs/templates guardados con campos MTP viejos)
# --------------------------------------------------------------------------

def test_legacy_mtp_draft_model_migrates_to_spec_type():
    cfg = launcher_module.LaunchConfig(model_id="m", mtp_draft_model="/models/d.gguf")
    assert cfg.spec_type == "draft-mtp"
    assert cfg.draft_model == "/models/d.gguf"
    assert not hasattr(cfg, "mtp_draft_model")
    cmd = _build(cfg)
    assert cmd[cmd.index("--spec-type") + 1] == "draft-mtp"
    assert cmd[cmd.index("--spec-draft-model") + 1] == "/models/d.gguf"


def test_legacy_mtp_embedded_migrates_to_spec_type():
    cfg = launcher_module.LaunchConfig(model_id="m", mtp_embedded=True)
    assert cfg.spec_type == "draft-mtp"


def test_explicit_spec_none_wins_over_legacy_fields():
    """Un payload nuevo con spec_type="none" NO debe reactivar la especulación
    aunque arrastre campos legacy truthy (intención explícita del usuario)."""
    cfg = launcher_module.LaunchConfig(
        model_id="m", spec_type="none", mtp_draft_model="/models/d.gguf", mtp_embedded=True
    )
    assert cfg.spec_type == "none"
    cmd = _build(cfg)
    assert "--spec-type" not in cmd
    assert "--spec-draft-model" not in cmd


def test_draft_flags_gated_by_mode():
    """Campos draft con valores pero spec_type="none": no se emite nada."""
    cfg = launcher_module.LaunchConfig(model_id="m", n_draft=8, n_draft_min=3, p_draft_min=0.5)
    cmd = _build(cfg)
    assert "--spec-type" not in cmd
    assert "--spec-draft-n-max" not in cmd


# --------------------------------------------------------------------------
# Degradación en builds viejas (filter_command)
# --------------------------------------------------------------------------

def test_filter_command_drops_ngram_flags_without_eating_next_token():
    """Build vieja que conoce --spec-type pero no los flags ngram: se descartan
    flag+valor y el comando sigue siendo válido."""
    cmd = [
        "bin", "--model", "m.gguf",
        "--spec-type", "ngram-simple",
        "--spec-ngram-simple-size-n", "8",
        "--spec-ngram-simple-size-m", "32",
        "--ctx-size", "8192",
    ]
    supported = {"--model", "--spec-type", "--ctx-size"}
    out, dropped = launcher_module.filter_command(cmd, supported)
    assert out == ["bin", "--model", "m.gguf", "--spec-type", "ngram-simple", "--ctx-size", "8192"]
    assert dropped == ["--spec-ngram-simple-size-n", "--spec-ngram-simple-size-m"]


def test_filter_command_keeps_spec_type_value_attached():
    """El valor de --spec-type ("ngram-mod") no es un flag: viaja con él."""
    cmd = ["bin", "--spec-type", "ngram-mod", "--spec-ngram-mod-n-min", "32"]
    out, dropped = launcher_module.filter_command(cmd, {"--spec-type"})
    assert out == ["bin", "--spec-type", "ngram-mod"]
    assert dropped == ["--spec-ngram-mod-n-min"]


# --------------------------------------------------------------------------
# U5 — Detección del rechazo REAL de la especulación en el log (detect_spec_rejection)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("line, kind", [
    # Familias de fallo de carga del draft (llama.cpp: common_init / llama_context).
    ("common_init_from_params: failed to load draft model: 'C:\\models\\draft.gguf'", "draft_load"),
    ("main: could not load the draft model", "draft_load"),
    ("llama_context: failed to create speculative context with draft model", "draft_load"),
    # Vocab incompatible — siempre con el draft/spec en la misma línea.
    ("llama_spec_init: vocab mismatch between main model and draft model", "vocab_mismatch"),
    ("main: vocabulary of the draft model does not match the target model", "vocab_mismatch"),
    ("llama_spec: target model vocab is incompatible with the draft", "vocab_mismatch"),
    # Build que no conoce el modo pedido.
    ("main: unknown --spec-type: 'draft-eagle3'", "spec_type_unsupported"),
    ("error: spec-type 'ngram-mod' is not supported by this build", "spec_type_unsupported"),
    # Draft declarado incompatible sin fallo de carga explícito.
    ("llama_spec: draft model is incompatible with the main model", "draft_incompatible"),
    ("main model is incompatible with the draft model", "draft_incompatible"),
])
def test_detect_spec_rejection(line, kind):
    hit = launcher_module.detect_spec_rejection(line)
    assert hit is not None and hit["kind"] == kind
    assert hit["line"] == line


@pytest.mark.parametrize("line", [
    # Logs normales de un arranque/funcionamiento sano: NUNCA activan el banner.
    "load: load time = 1234.56 ms",
    "load: draft model loaded from 'mtp.gguf'",
    "spec:  0.00 accepted 0.00 draft 0.00",
    "llm_load_vocab: special tokens cache size = 32000",
    # vocab mismatch de OTRO subsistema (sin draft/spec): no es un rechazo LL-4.
    "main: vocab mismatch with mmproj",
    # Un binario desconocido que solo dice "spec" o "draft" suelto.
    "build: using draft features from upstream",
    "update_slots: all slots are idle",
    "",
])
def test_detect_spec_rejection_ignores_normal_logs(line):
    assert launcher_module.detect_spec_rejection(line) is None


def _make_process_info(pid: str, backend: str = "llama_server"):
    return launcher_module.ProcessInfo(
        process_id=pid, model_id="m", model_name="fake", backend=backend,
        state="starting", host="127.0.0.1", port=8765, launch_config={},
    )


async def test_pump_records_spec_rejection_and_dedups(tmp_path):
    """Integration real del pump: un subprocess emite el rechazo como lo haría
    llama-server; ProcessInfo.spec_rejections queda visible para /status."""
    pid = "proc-u5-1"
    info = _make_process_info(pid)
    launcher_module.manager._info[pid] = info
    rejection = "common_init_from_params: failed to load draft model: 'x.gguf'"
    script = "; ".join([
        "print('load: model = fake')",
        f"print({rejection!r})",
        f"print({rejection!r})",  # repetida exacta: no debe duplicarse
    ])
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", script,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    await launcher_module.manager._pump_output(pid, proc, tmp_path / "logs" / "u5.log")
    await proc.wait()

    assert [r.kind for r in info.spec_rejections] == ["draft_load"]
    assert rejection in info.spec_rejections[0].line
    # El banner sale de lo que realmente se expone por la API (response_model
    # ProcessInfo): el campo viaja serializado.
    dumped = info.model_dump()
    assert dumped["spec_rejections"][0]["kind"] == "draft_load"
    # Y el log en disco sigue escribiéndose igual que antes (no se comió el write).
    assert rejection in (tmp_path / "logs" / "u5.log").read_text(encoding="utf-8")


async def test_pump_caps_distinct_rejections_and_ignores_non_llama(tmp_path):
    # llama_server: muchas variantes del mismo fallo → como mucho SPEC_REJECTION_MAX.
    pid = "proc-u5-2"
    info = _make_process_info(pid)
    launcher_module.manager._info[pid] = info
    lines = [f"main: failed to load draft model: 'v{i}.gguf'" for i in range(8)]
    script = "; ".join(f"print({line!r})" for line in lines)
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", script,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    await launcher_module.manager._pump_output(pid, proc, tmp_path / "logs" / "u5b.log")
    await proc.wait()
    assert len(info.spec_rejections) == launcher_module.SPEC_REJECTION_MAX

    # ollama: el detector no se aplica a otros backends (formato de log distinto).
    pid2 = "proc-u5-3"
    info2 = _make_process_info(pid2, backend="ollama")
    launcher_module.manager._info[pid2] = info2
    proc2 = await asyncio.create_subprocess_exec(
        sys.executable, "-c", f"print({lines[0]!r})",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    await launcher_module.manager._pump_output(pid2, proc2, tmp_path / "logs" / "u5c.log")
    await proc2.wait()
    assert info2.spec_rejections == []
