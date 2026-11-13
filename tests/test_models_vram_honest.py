"""Tests de LL-4 U6 — estimación honesta: capas reales del draft y visión dinámica.

Cobertura:
- vram_estimate._clamp_draft_layers (vía estimate_draft_aux_gb): el clamp
  declarado (max_layers = capas MTP que el base declara) y el guard físico
  (header copiado del base ⇒ peso implicado ≫ archivo real), sus no-disparos
  (draft honesto, quant más alto tolerado, dims distintas) y la hibridación
  (al cortar se elimina el mapa SSM: un draft solo tiene capas de atención);
- la aproximación sin metadata se dimensiona con max_layers;
- estimate_vram_usage propaga base_size_gb/max_layers a través de draft_aux,
  y sin esos parámetros el resultado es idéntico al de U3;
- /vram-estimate: el companion auto-detectado queda cortado por la declaración
  de la entry; un path manual NO se corta por declaración; el guard físico
  dispara solo ante un base grande con sidecar de header copiado;
- visión según la selección real: legacy sin use_vision intacto,
  use_vision=false apaga el mmproj, use_vision=true+path presupuesta ESE
  archivo (sidecar o path manual fuera del inventario), embebida (sin path)
  y self-path no suman, y la visión es ortogonal al presupuesto del draft.
"""

import pytest

import models as models_module
import vram_estimate as vram

from helpers import scan_and_wait, write_gguf

CTX = 8192

# Base y "draft con header copiado": dims y capas IDÉNTICAS al base (el caso
# real medido 2026-11-10: mtp-Qwen3.8-27B-Q4_0 declara los 65 layers del base).
BASE_KV = {
    "block_count": 16, "attention.head_count": 16, "attention.head_count_kv": 4,
    "attention.head_size": 128, "embedding_length": 2048,
}
# Draft legítimo de 2 capas con dims PROPIAS: el guard no debe tocarlo nunca.
DRAFT_KV = {
    "block_count": 2, "attention.head_count": 8, "attention.head_count_kv": 4,
    "attention.head_size": 128, "embedding_length": 1024,
}

BASE_NAME = "Base-4B-Q4_K_M.gguf"
MTP_NAME = "Base-4B-mtp-tx.gguf"
MMPROJ_NAME = "mmproj-Base-4B-F16.gguf"

BASE_META = {
    "n_layer": 16, "n_head": 16, "n_embd": 2048, "n_head_kv": 4, "head_dim": 128,
}
CLONE_META = dict(BASE_META)  # header copiado: declara 16 capas con dims completas
DRAFT_META = {
    "n_layer": 2, "n_head": 8, "n_embd": 1024, "n_head_kv": 4, "head_dim": 128,
}

_PAD = 100 * 1024 * 1024  # 100 MiB de pesos simulados


def _pad(path, pad=_PAD):
    path.write_bytes(path.read_bytes() + b"\0" * pad)
    return path


def _file_weights_gb(path) -> float:
    return path.stat().st_size / (1024 ** 3)


async def _scan_dir(client, model_dir):
    await client.post("/api/config", json={"model_dirs": [str(model_dir)]})
    await scan_and_wait(client)
    return (await client.get("/api/models")).json()


def _id_for(models, filename):
    return next(m["id"] for m in models if m["filename"] == filename)


def _draft_truth(meta: dict, weights_gb: float, ctx: int = CTX) -> float:
    """Verdad INDEPENDIENTE del clamp: el coste del draft es exactamente el
    estimador estándar aplicado a sus capas REALES (KV q8_0 + buffer FA), como
    lo documenta estimate_draft_aux_gb. Si el código no recorta las capas
    declaradas, los totales divergen de estos números."""
    est = vram.estimate_vram_usage(
        dict(meta), weights_gb, ctx,
        cache_type_k="q8_0", cache_type_v="q8_0",
        n_parallel=1, flash_attn=True, gpu_vram_gb=0.0,
    )
    return est["total_gb"]


def _base_dir(tmp_path, *, mtp_cloned=False, mtp_honest=False, mmproj=False,
              base_pad=_PAD):
    d = tmp_path / "models"
    d.mkdir()
    base = _pad(write_gguf(d / BASE_NAME, "llama", None, dict(BASE_KV)), base_pad)
    if mtp_cloned:
        _pad(write_gguf(d / MTP_NAME, "llama", None, dict(BASE_KV)))
    if mtp_honest:
        _pad(write_gguf(d / MTP_NAME, "llama", None, dict(DRAFT_KV)))
    if mmproj:
        _pad(write_gguf(d / MMPROJ_NAME, "llama", None, None), 50 * 1024 * 1024)
    return d, base


# ---------------------------------------------------------------------------
# Unit: clamp del draft (declarado y guard físico)
# ---------------------------------------------------------------------------


def test_clamp_declared_recorta_a_las_capas_que_la_cabeza_declara():
    # max_layers=1 (entry.mtp_embedded_layers del base): el sidecar con header
    # copiado queda en 1 capa, que es lo que la familia MTP realmente tiene.
    weights = 0.1
    # base_size elegido para que el GUARD esté silencioso (0.5 ≤ 8×0.1): lo que
    # recorta aquí es solo la declaración, no el guard.
    expected = _draft_truth({**CLONE_META, "n_layer": 1}, weights)
    got = vram.estimate_draft_aux_gb(
        BASE_META, CTX, weights, CLONE_META, base_size_gb=0.5, max_layers=1,
    )
    assert got == expected
    # Sin max_layers el resultado es el de U3 (header completo presupuestado):
    # si el clamp no existiera, este assert NO distinguirla.
    assert vram.estimate_draft_aux_gb(
        BASE_META, CTX, weights, CLONE_META, base_size_gb=0.5,
    ) == _draft_truth(CLONE_META, weights)
    # La metadata del llamador no se reescribe: la copia protege el dict ajeno.
    assert CLONE_META["n_layer"] == 16


def test_guard_fisico_recorta_un_header_copiado_del_base():
    # Sin declaración (max_layers=None) el guard físico hace su trabajo: 16
    # capas con dims del base implicarían 4 GB al quant del base, y el archivo
    # real pesa 0.1 GB (>8×) → se corta a lo que el archivo cabe físicamente.
    weights = 0.1
    expected = _draft_truth({**CLONE_META, "n_layer": 1}, weights)
    assert vram.estimate_draft_aux_gb(
        BASE_META, CTX, weights, CLONE_META, base_size_gb=4.0,
    ) == expected
    # Sin base_size_gb no hay guard posible: comportamiento U3 intacto.
    assert vram.estimate_draft_aux_gb(BASE_META, CTX, weights, CLONE_META) == (
        _draft_truth(CLONE_META, weights)
    )


def test_guard_no_toca_un_draft_honesto_de_pocas_capas():
    # Draft real de 2 capas con dims del base y peso coherente con ellas
    # (implied 0.5 ≤ 8×0.1): el guard NO debe recortar capas que sí existen.
    weights = 0.1
    honest = {**CLONE_META, "n_layer": 2}
    assert vram.estimate_draft_aux_gb(
        BASE_META, CTX, weights, honest, base_size_gb=4.0,
    ) == _draft_truth(honest, weights)


def test_guard_tolera_un_draft_de_quant_mas_alto_que_el_base():
    # Un sidecar Q8 junto a un base Q4 puede pesar casi tanto como el implicado
    # por su header: la tolerancia 8× lo deja pasar (4.0 ≤ 8×0.6).
    weights = 0.6
    assert vram.estimate_draft_aux_gb(
        BASE_META, CTX, weights, CLONE_META, base_size_gb=4.0,
    ) == _draft_truth(CLONE_META, weights)


def test_guard_no_dispara_si_las_dims_del_draft_no_coinciden():
    # El guard solo compara headers COPIADOS (mismas dims). Un draft con dims
    # propias (los fixtures legítimos de U3) mantiene sus capas declaradas.
    weights = 0.05  # con dims iguales el guard dispararía (16/16 de 4 GB > 8×0.05)
    claiming = {**DRAFT_META, "n_layer": 16}
    assert vram.estimate_draft_aux_gb(
        BASE_META, CTX, weights, claiming, base_size_gb=4.0,
    ) == _draft_truth(claiming, weights)


def test_clamp_hibrido_las_capas_del_draft_son_atencion_no_ssm():
    # Base híbrido (interval=4): el header clonado trae el mapa SSM. Al cortar
    # a 1 capa ese mapa la clasificaría como SSM (sin KV propio): el clamp
    # elimina el mapa y la capa del draft se presupuesta como atención completa.
    weights = 0.1
    ctx = 131072
    hybrid = {**CLONE_META, "full_attention_interval": 4}
    as_attention = _draft_truth({**CLONE_META, "n_layer": 1}, weights, ctx)
    as_ssm = _draft_truth(
        {**CLONE_META, "n_layer": 1, "full_attention_interval": 4}, weights, ctx,
    )
    assert as_attention > as_ssm  # mapa híbrido = esa capa no reserva KV
    got = vram.estimate_draft_aux_gb(
        {**BASE_META, "full_attention_interval": 4}, ctx, weights, hybrid,
        base_size_gb=0.5, max_layers=1,
    )
    assert got == as_attention


def test_aproximacion_sin_metadata_se_dimensiona_con_max_layers():
    # Cabeza embebida SIN header propio: la aproximación usa las capas que el
    # base declara para su cabeza (max_layers); sin declaración, una capa (U3).
    approx_n = 3
    approx_meta = {
        "n_head": BASE_META["n_head"], "n_embd": BASE_META["n_embd"],
        "n_head_kv": BASE_META["n_head_kv"], "n_layer": approx_n,
    }
    assert vram.estimate_draft_aux_gb(
        BASE_META, CTX, 0.0, None, max_layers=approx_n,
    ) == _draft_truth(approx_meta, 0.0)
    # Sin declaración sigue siendo exactamente la aproximación de una capa.
    one_layer = {**approx_meta, "n_layer": 1}
    assert vram.estimate_draft_aux_gb(BASE_META, CTX, 0.0, None) == (
        _draft_truth(one_layer, 0.0)
    )


def test_declaracion_inutil_o_mayor_no_recorta_nada():
    # max_layers 0/None o mayor que las capas declaradas por el propio header:
    # no hay fuente fiable, se presupuesta el header tal cual (guard silenciado).
    weights = 0.1
    full = _draft_truth(CLONE_META, weights)
    for max_layers in (None, 0, 30):
        assert vram.estimate_draft_aux_gb(
            BASE_META, CTX, weights, CLONE_META,
            base_size_gb=0.5, max_layers=max_layers,
        ) == full


# ---------------------------------------------------------------------------
# Unit: wiring del clamp a través de estimate_vram_usage (la card entera)
# ---------------------------------------------------------------------------


def test_estimate_vram_usage_propaga_max_layers_y_guard():
    meta = dict(BASE_META)
    weights = 0.1
    clamped = _draft_truth({**CLONE_META, "n_layer": 1}, weights)

    # max_layers viaja en el dict draft_aux → la card presupuesta 1 capa.
    with_declared = vram.estimate_vram_usage(
        meta, 0.5, CTX, gpu_vram_gb=24.0,
        draft_aux={"label": "d", "weights_gb": weights, "metadata": CLONE_META,
                   "max_layers": 1},
    )
    assert with_declared["aux_gb"] == round(clamped, 2)

    # Sin max_layers pero con un base REAL grande: el guard físico de la card
    # (model_size_gb) recorta igual.
    with_guard = vram.estimate_vram_usage(
        meta, 4.0, CTX, gpu_vram_gb=24.0,
        draft_aux={"label": "d", "weights_gb": weights, "metadata": CLONE_META},
    )
    assert with_guard["aux_gb"] == round(clamped, 2)


def test_estimate_vram_usage_sin_parametros_nuevos_es_identico_a_u3():
    # Sin max_layers y con un base de tamaño tal que el guard está silenciado,
    # la card calcula lo mismo que estimate_draft_aux_gb firma antigua: las
    # llamadas existentes (U3, fit) no cambian de número.
    meta = dict(BASE_META)
    weights = 0.1
    with_card = vram.estimate_vram_usage(
        meta, 0.5, CTX, gpu_vram_gb=24.0,
        draft_aux={"label": "d", "weights_gb": weights, "metadata": CLONE_META},
    )
    direct = vram.estimate_draft_aux_gb(BASE_META, CTX, weights, CLONE_META)
    assert with_card["aux_gb"] == round(direct, 2)
    assert with_card["aux_gb"] == round(_draft_truth(CLONE_META, weights), 2)


# ---------------------------------------------------------------------------
# HTTP: /vram-estimate con el clamp aplicado al inventario real
# ---------------------------------------------------------------------------


async def test_endpoint_con_declaracion_corta_el_companion_copiado(client, tmp_path):
    # Mismo par de archivos, misma petición: la única diferencia es la
    # declaración de la entry (mtp_embedded_layers). Con ella el sidecar
    # auto-detectado se presupuesta con 1 capa; sin ella, con las 16 del header
    # copiado. Ni el path ni el resto de la respuesta cambian.
    d, base = _base_dir(tmp_path, mtp_cloned=True)
    models = await _scan_dir(client, d)
    model_id = _id_for(models, BASE_NAME)
    mtp = d / MTP_NAME
    weights = _file_weights_gb(mtp)

    before = (await client.get(
        f"/api/models/{model_id}/vram-estimate",
        params={"n_ctx": CTX, "spec_type": "draft-mtp"},
    )).json()
    assert before["aux_gb"] == round(_draft_truth(CLONE_META, weights), 2)

    models_module.inventory.get(model_id).mtp_embedded_layers = 1
    after = (await client.get(
        f"/api/models/{model_id}/vram-estimate",
        params={"n_ctx": CTX, "spec_type": "draft-mtp"},
    )).json()
    clamped = _draft_truth({**CLONE_META, "n_layer": 1}, weights)
    assert after["aux_gb"] == round(clamped, 2)
    assert after["aux_components"] == [
        {"kind": "draft_model", "label": MTP_NAME, "size_gb": round(clamped, 2)}
    ]
    assert after["spec_budget"]["draft_path"] == str(mtp)
    assert after["total_gb"] < before["total_gb"]
    # La leyenda lleva el mismo presupuesto recortado en cada fila.
    assert after["legend"][0]["total_gb"] <= after["total_gb"]


async def test_endpoint_path_manual_no_se_corta_por_declaracion(client, tmp_path):
    # La declaración de la entry describe SU cabeza MTP; un draft elegido a
    # mano es otro archivo: se presupuesta con sus propias capas (2), no con la
    # max_layers del companion.
    d, _ = _base_dir(tmp_path, mtp_cloned=True)
    manual_dir = tmp_path / "descarga-manual"
    manual_dir.mkdir()
    manual = _pad(write_gguf(manual_dir / "eagle3-por-hf.gguf", "llama", None,
                            dict(DRAFT_KV)))
    models = await _scan_dir(client, d)
    model_id = _id_for(models, BASE_NAME)
    models_module.inventory.get(model_id).mtp_embedded_layers = 1

    res = await client.get(
        f"/api/models/{model_id}/vram-estimate",
        params={"n_ctx": CTX, "spec_type": "draft-mtp", "draft_model": str(manual)},
    )
    data = res.json()
    expected = round(_draft_truth(DRAFT_META, _file_weights_gb(manual)), 2)
    assert data["aux_gb"] == expected
    assert data["spec_budget"]["draft_path"] == str(manual)


async def test_endpoint_guard_automatico_ante_sidecar_de_header_copiado(client, tmp_path):
    # Caso real sin ninguna declaración: base de 1 GiB + sidecar de 100 MiB
    # que declara el header completo del base. El guard físico dispara solo
    # (1 GiB implicado > 8×100 MiB) y la card deja de inflar el draft.
    d, base = _base_dir(tmp_path, mtp_cloned=True, base_pad=1024 * 1024 * 1024)
    models = await _scan_dir(client, d)
    model_id = _id_for(models, BASE_NAME)
    mtp = d / MTP_NAME
    weights = _file_weights_gb(mtp)

    res = await client.get(
        f"/api/models/{model_id}/vram-estimate",
        params={"n_ctx": CTX, "spec_type": "draft-mtp"},
    )
    data = res.json()
    clamped = _draft_truth({**CLONE_META, "n_layer": 1}, weights)
    full = _draft_truth(CLONE_META, weights)
    assert data["aux_gb"] == round(clamped, 2)
    # Sin el clamp la card presupuestaría las 16 capas del header copiado: otro
    # número (y la brecha crece con el ctx, como pide el caso real de 128K).
    assert full > clamped and data["aux_gb"] != round(full, 2)
    big_ctx = 131072
    big = (await client.get(
        f"/api/models/{model_id}/vram-estimate",
        params={"n_ctx": big_ctx, "spec_type": "draft-mtp"},
    )).json()
    big_clamped = _draft_truth({**CLONE_META, "n_layer": 1}, weights, big_ctx)
    big_full = _draft_truth(CLONE_META, weights, big_ctx)
    assert big["aux_gb"] == round(big_clamped, 2)
    assert big_full - big_clamped > 1.0  # la inflación evitada es del orden de GB

    # Contraprueba en la MISMA request-less escena: un companion honesto (dims
    # propias) no se recorta aunque el base sea enorme — el guard exige que el
    # header sea una copia del base.
    honest_dir = tmp_path / "honestos"
    honest_dir.mkdir()
    honest_mtp = _pad(write_gguf(honest_dir / MTP_NAME, "llama", None, dict(DRAFT_KV)))
    _pad(write_gguf(honest_dir / BASE_NAME, "llama", None, dict(BASE_KV)),
         1024 * 1024 * 1024)
    await client.post("/api/config", json={"model_dirs": [str(honest_dir)]})
    await scan_and_wait(client)
    models = (await client.get("/api/models")).json()
    honest_base = next(
        m for m in models
        if m["filename"] == BASE_NAME and m["path"].startswith(str(honest_dir))
    )
    res2 = await client.get(
        f"/api/models/{honest_base['id']}/vram-estimate",
        params={"n_ctx": CTX, "spec_type": "draft-mtp"},
    )
    got = res2.json()
    expected_honest = _draft_truth(DRAFT_META, _file_weights_gb(honest_mtp))
    assert got["aux_gb"] == round(expected_honest, 2)

# ---------------------------------------------------------------------------
# HTTP: visión según la selección real de la UI (use_vision / vision_path)
# ---------------------------------------------------------------------------


async def _vision_dir(client, tmp_path):
    d, base = _base_dir(tmp_path, mmproj=True)
    models = await _scan_dir(client, d)
    entry = next(m for m in models if m["filename"] == BASE_NAME)
    mmproj = d / MMPROJ_NAME
    return d, entry, mmproj


async def test_vision_sin_use_vision_es_legacy_y_false_la_apaga(client, tmp_path):
    # Sin use_vision la card sigue siendo el legacy conservador (presupuesta
    # el mmproj de la entry, para llamantes sin selector). Con use_vision=false
    # — lo que manda la UI cuando el toggle está apagado — NO suma nada.
    d, entry, mmproj = await _vision_dir(client, tmp_path)
    legacy = (await client.get(
        f"/api/models/{entry['id']}/vram-estimate", params={"n_ctx": CTX},
    )).json()
    assert legacy["aux_gb"] == round(_file_weights_gb(mmproj), 2)
    assert [c["kind"] for c in legacy["aux_components"]] == ["mmproj"]

    off = (await client.get(
        f"/api/models/{entry['id']}/vram-estimate",
        params={"n_ctx": CTX, "use_vision": False},
    )).json()
    assert off["available"] is True
    assert off["aux_gb"] == 0.0
    assert off["aux_components"] == []
    assert off["total_gb"] < legacy["total_gb"]


async def test_vision_true_con_path_presupuesta_el_archivo_elegido(client, tmp_path):
    # Sidecar elegido en la UI: su tamaño REAL de archivo (igual que legacy,
    # pero explícito). Y un path manual FUERA del inventario se mide en disco:
    # entra ese archivo, no el mmproj de la entry.
    d, entry, mmproj = await _vision_dir(client, tmp_path)

    sidecar = (await client.get(
        f"/api/models/{entry['id']}/vram-estimate",
        params={"n_ctx": CTX, "use_vision": True, "vision_path": str(mmproj)},
    )).json()
    size = round(_file_weights_gb(mmproj), 2)
    assert sidecar["aux_components"] == [
        {"kind": "mmproj", "label": MMPROJ_NAME, "size_gb": size}
    ]
    assert sidecar["aux_gb"] == size

    other_dir = tmp_path / "descarga-manual"
    other_dir.mkdir()
    manual = _pad(write_gguf(other_dir / "vision-alterna.gguf", "llama", None, None),
                  80 * 1024 * 1024)
    manual_res = (await client.get(
        f"/api/models/{entry['id']}/vram-estimate",
        params={"n_ctx": CTX, "use_vision": True, "vision_path": str(manual)},
    )).json()
    manual_size = round(_file_weights_gb(manual), 2)
    assert manual_res["aux_components"] == [
        {"kind": "mmproj", "label": "vision-alterna.gguf", "size_gb": manual_size}
    ]
    assert manual_res["aux_gb"] == manual_size > size


async def test_vision_embebida_y_self_path_no_suman(client, tmp_path):
    # use_vision=true SIN path = visión embebida: sus tensores ya están dentro
    # del .gguf principal (contados en weights_gb); agregar el mmproj del
    # sidecar sería el doble conteo que inflaba la card de Qwen3.8-27B.
    d, entry, mmproj = await _vision_dir(client, tmp_path)
    embedded = (await client.get(
        f"/api/models/{entry['id']}/vram-estimate",
        params={"n_ctx": CTX, "use_vision": True},
    )).json()
    assert embedded["aux_gb"] == 0.0
    assert embedded["aux_components"] == []

    # Guard self-path: mandar el propio .gguf como vision_path no lo cuenta dos
    # veces (el mismo guard que ya cubría el mmproj autoasociado).
    self_path = (await client.get(
        f"/api/models/{entry['id']}/vram-estimate",
        params={"n_ctx": CTX, "use_vision": True, "vision_path": entry["path"]},
    )).json()
    assert self_path["aux_gb"] == 0.0
    assert self_path["aux_components"] == []


async def test_vision_es_ortogonal_al_presupuesto_del_draft(client, tmp_path):
    # Apagar la visión no apaga el draft, y encenderla no duplica el draft:
    # los dos componentes son independientes en la misma respuesta.
    d, base = _base_dir(tmp_path, mtp_honest=True, mmproj=True)
    models = await _scan_dir(client, d)
    entry = next(m for m in models if m["filename"] == BASE_NAME)
    mtp = d / MTP_NAME
    mmproj = d / MMPROJ_NAME

    only_draft = (await client.get(
        f"/api/models/{entry['id']}/vram-estimate",
        params={"n_ctx": CTX, "spec_type": "draft-mtp", "use_vision": False},
    )).json()
    draft_gb = round(_draft_truth(DRAFT_META, _file_weights_gb(mtp)), 2)
    assert only_draft["aux_gb"] == draft_gb
    assert [c["kind"] for c in only_draft["aux_components"]] == ["draft_model"]

    both = (await client.get(
        f"/api/models/{entry['id']}/vram-estimate",
        params={"n_ctx": CTX, "spec_type": "draft-mtp", "use_vision": True,
                "vision_path": str(mmproj)},
    )).json()
    mmproj_gb = round(_file_weights_gb(mmproj), 2)
    assert [c["kind"] for c in both["aux_components"]] == ["mmproj", "draft_model"]
    assert both["aux_components"][0]["size_gb"] == mmproj_gb
    # El presupuesto del draft no cambió por añadir visión.
    assert both["aux_components"][1]["size_gb"] == draft_gb
    # Suma total con tolerancia fina: el backend suma los valores crudos y
    # redondea al final.
    assert both["aux_gb"] == pytest.approx(mmproj_gb + draft_gb, abs=0.02)
