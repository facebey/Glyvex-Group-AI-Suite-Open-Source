"""Tests AG-2 U1+U2 — servidor MCP de la Suite: tools de lectura (suite_status,
list_models, estimate_vram), tools de escritura con safety rails (launch_model,
stop_model), gate por config y montaje JSON-RPC completo.

Convención de transporte (heredada del spike U0, ver Files/Docs/design/
ag-2-mcp-server.md §7):
- Las tools se prueban in-process con mcp.call_tool(): no toca el
  session_manager y los errores de negocio llegan como ToolError.
- El hilo JSON-RPC completo (session_manager.run(), que es UNA sola vez por
  instancia del singleton) lo abre únicamente test_mcp_wire_end_to_end; por eso
  test_main_lifespan deja el toggle MCP off.
"""

import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import config as config_module
import launcher as launcher_module
import mcp_server as mcp_module
import metrics as metrics_module

from helpers import scan_and_wait, write_gguf

PROTOCOL = "2025-06-18"
# Host 127.0.0.1: el middleware de seguridad del SDK (DNS-rebinding) rechaza el
# "testserver" de httpx con 421. En producción el Host siempre es el puerto
# local descubierto (127.0.0.1:<port>), que el SDK acepta por defecto.
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Host": "127.0.0.1:7981"}


def _rpc(method: str, params: dict | None = None, req_id: int = 1) -> dict:
    payload = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        payload["params"] = params
    return payload


async def _call(name: str, arguments: dict | None = None) -> dict:
    """Llamada in-process a la tool (sin HTTP ni session_manager): el SDK
    serializa el dict devuelto como TextContent JSON, igual que en el hilo."""
    result = await mcp_module.mcp.call_tool(name, arguments or {})
    return json.loads(result.content[0].text)


async def _model_with_kv(client, tmp_path, filename: str, arch: str, kv: dict[str, int]) -> str:
    model_dir = tmp_path / "models"
    model_dir.mkdir(exist_ok=True)
    write_gguf(model_dir / filename, arch, None, kv)
    await client.post("/api/config", json={"model_dirs": [str(model_dir)]})
    await scan_and_wait(client)
    return (await client.get("/api/models")).json()[0]["id"]


async def test_mcp_tools_registered():
    tools = await mcp_module.mcp.list_tools()
    names = {t.name for t in tools}
    assert {"suite_status", "list_models", "estimate_vram", "launch_model", "stop_model"} == names


async def test_list_models_proyecta_camas_utiles(client, sample_model_dir):
    await client.post("/api/config", json={"model_dirs": [str(sample_model_dir)]})
    await scan_and_wait(client)

    data = await _call("list_models")
    assert data["count"] == 3
    item = next(m for m in data["models"] if m["filename"] == "Llama-3.1-8B-Q4_K_M.gguf")
    assert {"id", "filename", "path", "size_gb", "quantization", "format"} <= set(item)
    # Los campos internos de gestión NO viajan en la lista...
    assert "mtime" not in item
    assert "metadata" not in item
    # ...y el id es el que aceptan las demás tools.
    est = await _call("estimate_vram", {"model_id": item["id"]})
    assert est["available"] is False  # 0 bytes: sin metadata legible


async def test_list_models_include_metadata(client, tmp_path):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    write_gguf(
        model_dir / "Mini-1B-Q8_0.gguf", "llama", None,
        kv={"block_count": 4, "attention.head_count": 8, "attention.head_count_kv": 2,
            "attention.head_size": 64, "embedding_length": 256},
    )
    await client.post("/api/config", json={"model_dirs": [str(model_dir)]})
    await scan_and_wait(client)

    data = await _call("list_models", {"include_metadata": True})
    assert data["count"] == 1
    meta = data["models"][0]["metadata"]
    assert meta is not None
    assert meta.get("n_layer") == 4


async def test_estimate_vram_con_state_y_notas(client, tmp_path):
    model_id = await _model_with_kv(
        client, tmp_path, "Estimado-8B-Q4_K_M.gguf", "llama",
        {"block_count": 32, "attention.head_count": 32, "attention.head_count_kv": 8,
         "attention.head_size": 128, "embedding_length": 4096},
    )
    await client.post("/api/config", json={"hardware": {"vram_gb": 24}})

    data = await _call("estimate_vram", {
        "model_id": model_id, "n_ctx": 8192,
        "cache_type_k": "q4_0", "cache_type_v": "q4_0",
    })
    assert data["available"] is True
    # Mismos números que el endpoint REST (test_models): 2×32×8×128 elem/token
    # × 0.5625 B × 8192 = 0.28 GiB de KV.
    assert data["kv_gb"] == 0.28
    assert data["gpu_vram_gb"] == 24
    assert data["state"] == "comodo"
    assert data["total_gb"] == round(data["weights_gb"] + data["kv_gb"] + data["compute_gb"], 2)
    # Honestidad obligatoria (LoadCard): las limitaciones conocidas viajan con la
    # estimación para que el agente no decida sobre un número incompleto.
    joined = " ".join(data["notes"])
    assert "MoE" in joined and "MTP" in joined
    # Leyenda de totales por contexto: útil para que el agente elija n_ctx.
    assert {row["n_ctx"] for row in data["legend"]} and all("total_gb" in row for row in data["legend"])


async def test_estimate_vram_modelo_inexisterno_es_tool_error(client):
    with pytest.raises(ToolError) as exc:
        await _call("estimate_vram", {"model_id": "no-existe-9999"})
    assert "no encontrado" in str(exc.value).lower()


async def test_estimate_vram_gguf_ilegible_disponible_false(client, sample_model_dir):
    # Mismo contrato que el endpoint: archivo ilegible responde disponible:false
    # con error legible (no es un ToolError: el modelo puede reintentar con otro).
    await client.post("/api/config", json={"model_dirs": [str(sample_model_dir)]})
    await scan_and_wait(client)
    model_id = (await client.get("/api/models")).json()[0]["id"]

    data = await _call("estimate_vram", {"model_id": model_id})
    assert data["available"] is False
    assert data["error"]


async def test_suite_status_sin_y_con_procesos(client, monkeypatch):
    monkeypatch.setattr(
        metrics_module.manager.collector, "collect_gpu",
        lambda: (None, "no GPU detectable"),
    )

    empty = await _call("suite_status")
    assert empty["active_processes"] == []
    assert empty["summary"]["active_count"] == 0
    # Sin GPU real en tests (convención: ningún test toca el mundo real), el
    # error de degradación pasa tal cual para que el agente lo vea.
    assert empty["gpu"] is None
    assert empty["gpu_error"] == "no GPU detectable"

    proc = launcher_module.ProcessInfo(
        process_id="proc-test-1", model_id="m1", model_name="Llama-3.1-8B",
        backend="llama_server", state="running", host="127.0.0.1", port=8080,
        launch_config={},
    )
    launcher_module.manager._info["proc-test-1"] = proc

    data = await _call("suite_status")
    assert [p["process_id"] for p in data["active_processes"]] == ["proc-test-1"]
    assert data["summary"]["active_count"] == 1
    assert data["summary"]["running_models"] == ["Llama-3.1-8B"]


async def test_mcp_wire_end_to_end(client):
    """Único test que abre el hilo JSON-RPC completo: session_manager.run() es
    una sola vez por instancia del singleton (test_main_lifespan mantiene el
    toggle off para no chocar). Verifica el montaje real bajo main.app:
    tools/list y tools/call por POST autónomo (stateless) y el error de
    negocio como isError:true legible."""
    async with mcp_module.mcp.session_manager.run():
        r = await client.post("/mcp/", json=_rpc("tools/list"), headers=MCP_HEADERS)
        assert r.status_code == 200
        names = {t["name"] for t in r.json()["result"]["tools"]}
        assert {"suite_status", "list_models", "estimate_vram"} <= names

        r = await client.post(
            "/mcp/",
            json=_rpc("tools/call", {"name": "estimate_vram",
                                     "arguments": {"model_id": "no-existe-9999"}}, 2),
            headers=MCP_HEADERS,
        )
        assert r.status_code == 200  # el protocolo responde 200: el error vive en isError
        result = r.json()["result"]
        assert result["isError"] is True
        assert "no-existe-9999" in result["content"][0]["text"]


async def test_mcp_gate_off_responde_404(client):
    assert config_module.config.get("mcp_server.enabled") is True  # default ON

    r = await client.post("/api/config", json={"mcp_server": {"enabled": False}})
    assert r.status_code == 200

    r = await client.post("/mcp/", json=_rpc("tools/list"), headers=MCP_HEADERS)
    assert r.status_code == 404
    assert "mcp_server.enabled" in r.json()["detail"]


async def test_ruta_pelada_redirige_a_slash_final(client):
    # El Mount solo matchea /mcp/... : la ruta pelada la cubre la ruta 307 de
    # main.py (307 conserva método y body; los clientes fetch lo siguen).
    r = await client.post("/mcp", json=_rpc("tools/list"))
    assert r.status_code == 307
    assert r.headers.get("location") == "/mcp/"


# ---------------------------------------------------------------------------
# U2 — launch_model / stop_model con safety rails (diseño §3.3).
# ---------------------------------------------------------------------------

_MINI_KV = {"block_count": 4, "attention.head_count": 8, "attention.head_count_kv": 2,
            "attention.head_size": 64, "embedding_length": 256}
_BIG_KV = {"block_count": 32, "attention.head_count": 32, "attention.head_count_kv": 8,
           "attention.head_size": 128, "embedding_length": 4096}


@pytest.fixture(autouse=True)
def _no_toca_gpu_real(monkeypatch):
    # Convención de tests: nada toca la GPU real. El preflight de launch_model
    # ejerce así la ruta de fallback (clasificación contra hardware.vram_gb), o
    # se salta cuando no está configurado.
    monkeypatch.setattr(
        metrics_module.manager.collector, "collect_gpu",
        lambda: (None, "no GPU detectable"),
    )


async def _stub_manager_start(monkeypatch, state: str = "starting") -> dict:
    """Captura el LaunchConfig que arma launch_model sin spawnear nada: los
    tests de rails afirman sobre la config y la decisión, no sobre el proceso."""
    captured: dict = {}

    async def fake_start(cfg, process_id=None):
        captured["cfg"] = cfg
        return launcher_module.ProcessInfo(
            process_id="proc-mcp-1", model_id=cfg.model_id, model_name=cfg.model_id,
            backend=cfg.backend, state=state, host=cfg.host, port=cfg.port,
            launch_config=cfg.model_dump(),
        )

    monkeypatch.setattr(launcher_module.manager, "start", fake_start)
    return captured


async def _save_template(client, name: str, params: dict) -> None:
    r = await client.post("/api/launcher/templates", json={"name": name, "params": params})
    assert r.status_code == 200


async def _model_id_by_filename(client, filename: str) -> str:
    return next(m["id"] for m in (await client.get("/api/models")).json() if m["filename"] == filename)


async def test_launch_model_rechaza_modelo_inexistente_enumera_ids(client, sample_model_dir):
    await client.post("/api/config", json={"model_dirs": [str(sample_model_dir)]})
    await scan_and_wait(client)
    known = {m["id"] for m in (await client.get("/api/models")).json()}

    with pytest.raises(ToolError) as exc:
        await _call("launch_model", {"model_id": "no-existe-9999", "template": "test-base"})
    msg = str(exc.value)
    assert "no está en el inventario" in msg
    assert any(mid in msg for mid in known)


async def test_launch_model_rechaza_plantilla_inexistente_enumera_nombres(client, tmp_path):
    model_id = await _model_with_kv(client, tmp_path, "Mini-1B-Q8_0.gguf", "llama", _MINI_KV)

    with pytest.raises(ToolError) as exc:
        await _call("launch_model", {"model_id": model_id, "template": "Sin-such-plantilla"})
    msg = str(exc.value)
    assert "no existe" in msg
    # ensure_loaded sincroniza las plantillas predefinidas: el mensaje las lista.
    names = [t["name"] for t in (await client.get("/api/launcher/templates")).json()]
    assert names and any(n in msg for n in names)


async def test_launch_model_whitelist_de_overrides_exacta(client, tmp_path, monkeypatch):
    model_id = await _model_with_kv(client, tmp_path, "Mini-1B-Q8_0.gguf", "llama", _MINI_KV)
    await _stub_manager_start(monkeypatch)
    await _save_template(client, "test-base", {})

    with pytest.raises(ToolError) as exc:
        await _call("launch_model", {"model_id": model_id, "template": "test-base",
                                     "overrides": {"temperature": 0.7, "backend": "ollama"}})
    # backend NO es overridable: lo define la plantilla (nada de ollama por sorpresa).
    msg = str(exc.value)
    assert "backend, temperature" in msg and "Solo se aceptan" in msg


async def test_launch_model_n_ctx_limitado_a_dos_veces_la_plantilla(client, tmp_path, monkeypatch):
    model_id = await _model_with_kv(client, tmp_path, "Mini-1B-Q8_0.gguf", "llama", _MINI_KV)
    captured = await _stub_manager_start(monkeypatch)
    await _save_template(client, "test-ctx", {"n_ctx": 32768})

    for bad in (256, 100000):
        with pytest.raises(ToolError) as exc:
            await _call("launch_model", {"model_id": model_id, "template": "test-ctx",
                                         "overrides": {"n_ctx": bad}})
        assert "entre 512 y 65536" in str(exc.value)

    data = await _call("launch_model", {"model_id": model_id, "template": "test-ctx",
                                        "overrides": {"n_ctx": 65536}})
    assert captured["cfg"].n_ctx == 65536
    assert data["applied_config"]["n_ctx"] == 65536

    # Plantilla sin n_ctx declarado: techo = 2× el default de LaunchConfig.
    await _save_template(client, "test-no-ctx", {})
    await _call("launch_model", {"model_id": model_id, "template": "test-no-ctx",
                                 "overrides": {"n_ctx": 100000}})
    assert captured["cfg"].n_ctx == 100000


async def test_launch_model_n_gpu_layers_nunca_mas_gpu_que_la_plantilla(client, tmp_path, monkeypatch):
    model_id = await _model_with_kv(client, tmp_path, "Mini-1B-Q8_0.gguf", "llama", _MINI_KV)
    captured = await _stub_manager_start(monkeypatch)
    await _save_template(client, "test-ngl", {"gpu_mode": "hybrid", "n_gpu_layers": 10})

    # Base acotada a 10: -1 (todas) y 11 superan la plantilla; 5 está dentro.
    for bad in (-1, 11):
        with pytest.raises(ToolError) as exc:
            await _call("launch_model", {"model_id": model_id, "template": "test-ngl",
                                         "overrides": {"n_gpu_layers": bad}})
        assert "nunca más capas en GPU" in str(exc.value)
    await _call("launch_model", {"model_id": model_id, "template": "test-ngl",
                                 "overrides": {"n_gpu_layers": 5}})
    assert captured["cfg"].n_gpu_layers == 5

    # Base sin límite (-1 = todo en GPU): cualquier >= 0 reduce el offload y vale;
    # por debajo de -1 no existe semántica.
    await _save_template(client, "test-all-gpu", {"n_gpu_layers": -1})
    await _call("launch_model", {"model_id": model_id, "template": "test-all-gpu",
                                 "overrides": {"n_gpu_layers": 7}})
    assert captured["cfg"].n_gpu_layers == 7
    with pytest.raises(ToolError):
        await _call("launch_model", {"model_id": model_id, "template": "test-all-gpu",
                                     "overrides": {"n_gpu_layers": -2}})


async def test_launch_model_override_choca_con_validador_cruzado(client, tmp_path, monkeypatch):
    # n_gpu_layers=0 pasa la whitelist, pero choca con gpu_mode='gpu_only': lo
    # rechaza el validator cruzado de LaunchConfig (los mismos que Apply por /api).
    model_id = await _model_with_kv(client, tmp_path, "Mini-1B-Q8_0.gguf", "llama", _MINI_KV)
    await _stub_manager_start(monkeypatch)
    await _save_template(client, "test-gpu-only", {"gpu_mode": "gpu_only", "n_gpu_layers": -1})

    with pytest.raises(ToolError) as exc:
        await _call("launch_model", {"model_id": model_id, "template": "test-gpu-only",
                                     "overrides": {"n_gpu_layers": 0}})
    assert "configuración final es inválida" in str(exc.value)
    assert "gpu_mode" in str(exc.value)


async def test_launch_model_mtp_true_solo_con_draft_real_del_modelo(client, tmp_path, monkeypatch):
    model_id = await _model_with_kv(client, tmp_path, "Mini-1B-Q8_0.gguf", "llama", _MINI_KV)
    captured = await _stub_manager_start(monkeypatch)
    await _save_template(client, "test-mtp", {})

    # Sin tensores nextn ni draft en la carpeta: el override mtp se rechaza.
    with pytest.raises(ToolError) as exc:
        await _call("launch_model", {"model_id": model_id, "template": "test-mtp",
                                     "overrides": {"mtp": True}})
    assert "no tiene MTP" in str(exc.value)

    # Draft sidecar en la misma carpeta (regla mtp_companion_file): mtp=True lo
    # engancha como mtp_draft_model.
    (tmp_path / "models" / "mtp-draft.gguf").touch()
    await scan_and_wait(client)
    data = await _call("launch_model", {"model_id": model_id, "template": "test-mtp",
                                        "overrides": {"mtp": True}})
    draft = data["applied_config"]["mtp_draft_model"]
    assert draft and draft.lower().endswith("mtp-draft.gguf")
    assert captured["cfg"].mtp_draft_model == draft

    # false desactiva (aunque la plantilla o el escaneo lo hubieran dejado activo).
    data = await _call("launch_model", {"model_id": model_id, "template": "test-mtp",
                                        "overrides": {"mtp": False}})
    assert data["applied_config"]["mtp_draft_model"] is None
    assert captured["cfg"].mtp_draft_model is None


async def test_launch_model_mmproj_true_solo_con_sidecar_real(client, tmp_path, monkeypatch):
    captured = await _stub_manager_start(monkeypatch)
    await _save_template(client, "test-mmproj", {})

    # Sin sidecar en la carpeta: el override mmproj se rechaza.
    dir_a = tmp_path / "sin-mmproj"
    dir_a.mkdir()
    write_gguf(dir_a / "Vision-7B-Q4_K_M.gguf", "llama", None, _MINI_KV)
    await client.post("/api/config", json={"model_dirs": [str(dir_a)]})
    await scan_and_wait(client)
    model_id = await _model_id_by_filename(client, "Vision-7B-Q4_K_M.gguf")
    with pytest.raises(ToolError) as exc:
        await _call("launch_model", {"model_id": model_id, "template": "test-mmproj",
                                     "overrides": {"mmproj": True}})
    assert "no tiene un archivo mmproj" in str(exc.value)

    # Con el sidecar PRESENTE al escanear: la entry carga mmproj_path (el scan
    # es quien lo descubre, regla _find_mmproj) y el override lo aplica.
    dir_b = tmp_path / "con-mmproj"
    dir_b.mkdir()
    write_gguf(dir_b / "Con-MM-7B-Q4_K_M.gguf", "llama", None, _MINI_KV)
    (dir_b / "Con-MM-7B-mmproj.gguf").touch()
    await client.post("/api/config", json={"model_dirs": [str(dir_b)]})
    await scan_and_wait(client)
    model_id = await _model_id_by_filename(client, "Con-MM-7B-Q4_K_M.gguf")
    data = await _call("launch_model", {"model_id": model_id, "template": "test-mmproj",
                                        "overrides": {"mmproj": True}})
    applied = data["applied_config"]["mmproj_path"]
    assert applied and applied.lower().endswith("con-mm-7b-mmproj.gguf")
    assert captured["cfg"].mmproj_path == applied


async def test_launch_model_preflight_no_cabe_bloquea_y_force_lo_deja_pasar(client, tmp_path, monkeypatch):
    model_id = await _model_with_kv(client, tmp_path, "Grande-8B-Q4_K_M.gguf", "llama", _BIG_KV)
    await client.post("/api/config", json={"hardware": {"vram_gb": 1}})
    captured = await _stub_manager_start(monkeypatch)
    await _save_template(client, "test-big", {"n_ctx": 65536})

    # KV de 65536 tokens ≈ 4.6 GB contra una GPU declarada de 1 GB: no_cabe.
    with pytest.raises(ToolError) as exc:
        await _call("launch_model", {"model_id": model_id, "template": "test-big"})
    assert "no cabe" in str(exc.value) and "force=true" in str(exc.value)
    assert "cfg" not in captured  # nunca llegó a manager.start

    data = await _call("launch_model", {"model_id": model_id, "template": "test-big",
                                        "force": True})
    assert data["vram_preflight"]["basis"] == "config_vram_gb"
    assert data["vram_preflight"]["blocked"] is True
    assert captured["cfg"] is not None


async def test_launch_model_preflight_snapshot_vivo_es_autoridad(client, tmp_path, monkeypatch):
    model_id = await _model_with_kv(client, tmp_path, "Grande-8B-Q4_K_M.gguf", "llama", _BIG_KV)
    await client.post("/api/config", json={"hardware": {"vram_gb": 24}})
    gpu = metrics_module.GpuMetrics(
        index=0, name="RTX Fake 24GB", vram_used_mb=23600, vram_total_mb=24000,
        vram_free_mb=400, vram_percent=98.3,
    )
    monkeypatch.setattr(metrics_module.manager.collector, "collect_gpu", lambda: ([gpu], None))
    captured = await _stub_manager_start(monkeypatch)
    await _save_template(client, "test-live", {"n_ctx": 65536, "cache_type_k": "q4_0",
                                               "cache_type_v": "q4_0"})

    # Contra la config declarada (24 GB) la estimación cabe; lo que manda es el
    # snapshot vivo: 400 MB libres reales no cubren el lanzamiento.
    with pytest.raises(ToolError) as exc:
        await _call("launch_model", {"model_id": model_id, "template": "test-live"})
    assert "MB libres" in str(exc.value)
    assert "cfg" not in captured

    data = await _call("launch_model", {"model_id": model_id, "template": "test-live",
                                        "force": True})
    assert data["vram_preflight"]["basis"] == "live_gpu_snapshot"
    assert data["vram_preflight"]["blocked"] is True


async def test_launch_model_en_starting_devuelve_consigna_de_poll(client, tmp_path, monkeypatch):
    model_id = await _model_with_kv(client, tmp_path, "Mini-1B-Q8_0.gguf", "llama", _MINI_KV)
    await _stub_manager_start(monkeypatch, state="starting")
    await _save_template(client, "test-start", {})

    data = await _call("launch_model", {"model_id": model_id, "template": "test-start"})
    assert data["process_id"] == "proc-mcp-1" and data["state"] == "starting"
    assert "suite_status" in data["next_step"]
    # Sin hardware.vram_gb configurado y sin GPU real, el preflight no puede
    # juzgar: no bloquea y el estado de la estimación queda 'unknown'.
    assert data["vram_preflight"]["state"] == "unknown"
    assert data["vram_preflight"]["blocked"] is False

    # Descubribilidad de plantillas: suite_status lista los nombres exactos.
    status = await _call("suite_status")
    assert "test-start" in status["launch_templates"]


async def test_launch_model_y_stop_model_feliz_contramodelo_mock(client, sample_model_dir, mock_llama_server):
    await client.post("/api/config", json={"model_dirs": [str(sample_model_dir)]})
    await scan_and_wait(client)
    model_id = await _model_id_by_filename(client, "Llama-3.1-8B-Q4_K_M.gguf")
    # Mismo patrón que test_launcher: backend lm_studio contra el mock en :18080.
    await _save_template(client, "test-lmstudio", {"backend": "lm_studio", "host": "127.0.0.1",
                                                   "port": 18080})

    data = await _call("launch_model", {"model_id": model_id, "template": "test-lmstudio"})
    assert data["state"] == "running"  # el mock responde 200 en /health
    assert "next_step" not in data

    stopped = await _call("stop_model", {"process_id": data["process_id"]})
    assert stopped["state"] == "stopped"


async def test_stop_model_id_desconocido_enumera_activos(client):
    proc = launcher_module.ProcessInfo(
        process_id="proc-live-1", model_id="m1", model_name="Llama-3.1-8B",
        backend="llama_server", state="running", host="127.0.0.1", port=8080,
        launch_config={},
    )
    launcher_module.manager._info["proc-live-1"] = proc

    with pytest.raises(ToolError) as exc:
        await _call("stop_model", {"process_id": "no-existe"})
    msg = str(exc.value)
    assert "no encontrado" in msg.lower()
    assert "proc-live-1" in msg
