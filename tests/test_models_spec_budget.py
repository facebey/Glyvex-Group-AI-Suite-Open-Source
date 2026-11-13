"""Tests de LL-4 U3 — presupuesto de VRAM por modo de especulación.

Cobertura:
- vram_estimate.estimate_draft_aux_gb: metadata real del draft, aproximación
  de una capa sin metadata, y None cuando no hay datos mínimos;
- estimate_vram_usage con draft_aux: componente {"kind":"draft_model"} al
  total y a la leyenda, re-estimado en cada fila de contexto;
- /vram-estimate con spec_type: modos draft presupuestan el draft elegido
  (sidecar por familia, path manual que pisa, o cabeza embebida), ngram/none
  no suman VRAM aunque exista companion, draft sin archivo se declara en
  spec_budget, spec_type inválido da 422, y el comportamiento legacy sin
  spec_type queda intacto.
"""

import pytest

import models as models_module
import vram_estimate as vram

from helpers import scan_and_wait, write_gguf

# Modelo base y draft con dims DISTINTAS a propósito: si el presupuesto usara
# las dims del base en lugar de las del draft, los números divergirían.
BASE_KV = {
    "block_count": 16, "attention.head_count": 16, "attention.head_count_kv": 4,
    "attention.head_size": 128, "embedding_length": 2048,
}
DRAFT_KV = {
    "block_count": 2, "attention.head_count": 8, "attention.head_count_kv": 4,
    "attention.head_size": 128, "embedding_length": 1024,
}

BASE_NAME = "Base-4B-Q4_K_M.gguf"
MTP_NAME = "Base-4B-mtp-tx.gguf"
DFLASH_NAME = "Base-4B-dflash-q8_0.gguf"

_PAD = 100 * 1024 * 1024  # 100 MiB de pesos simulados para el archivo draft


def _pad(path, pad=_PAD):
    path.write_bytes(path.read_bytes() + b"\0" * pad)
    return path


def _file_weights_gb(path) -> float:
    return path.stat().st_size / (1024 ** 3)


async def _scan_dir(client, model_dir):
    await client.post("/api/config", json={"model_dirs": [str(model_dir)]})
    await scan_and_wait(client)
    return (await client.get("/api/models")).json()


def _base_dir(tmp_path, *, with_mtp=False, with_dflash=False):
    d = tmp_path / "models"
    d.mkdir()
    base = write_gguf(d / BASE_NAME, "llama", None, dict(BASE_KV))
    if with_mtp:
        _pad(write_gguf(d / MTP_NAME, "llama", None, dict(DRAFT_KV)))
    if with_dflash:
        _pad(write_gguf(d / DFLASH_NAME, "llama", None, dict(DRAFT_KV)))
    return d, base


def _id_for(models, filename):
    return next(m["id"] for m in models if m["filename"] == filename)


def _approx_gb(ctx: int, kv_heads: int, head_dim: int, weights: float = 0.0) -> float:
    """Coste declarado del draft SIN metadata: 1 capa de atención del modelo
    principal al ctx (kv_heads/head_dim del base) + pesos + buffer FA (0.75).
    q8_0 = 34/32 bytes por elemento, K y V."""
    kv_bytes = 2 * (ctx * kv_heads * head_dim) * (34.0 / 32.0)
    return round(weights + kv_bytes / (1024 ** 3) + 0.75, 2)


# ---------------------------------------------------------------------------
# Helper compartido: estimate_draft_aux_gb
# ---------------------------------------------------------------------------


BASE_META = {
    "n_layer": 16, "n_head": 16, "n_embd": 2048, "n_head_kv": 4, "head_dim": 128,
}
DRAFT_META = {
    "n_layer": 2, "n_head": 8, "n_embd": 1024, "n_head_kv": 4, "head_dim": 128,
}


def test_draft_aux_usa_metadata_real_del_draft():
    # Con header legible: dims REALES del draft (2 capas, sus heads) y su
    # tamaño real. El KV del draft va en q8_0 (los flags propios del launch)
    # con el buffer FA, igual que el mecanismo A2F-1 pero para cualquier modo.
    expected = vram.estimate_vram_usage(
        DRAFT_META, 0.5, 8192,
        cache_type_k="q8_0", cache_type_v="q8_0",
        n_parallel=1, flash_attn=True, gpu_vram_gb=0.0,
    )["total_gb"]
    assert vram.estimate_draft_aux_gb(BASE_META, 8192, 0.5, DRAFT_META) == expected
    # Sin esa metadata el resultado sería OTRA cosa (1 capa con dims del base):
    # si el código ignorara el header del draft, este assert rompería.
    assert expected != vram.estimate_draft_aux_gb(BASE_META, 8192, 0.5, None)


def test_draft_aux_sin_metadata_aproxima_una_capa_del_base():
    # Cabeza embebida o .gguf sin header legible: aproximación conservadora =
    # una capa completa del MODELO principal al ctx pedido + pesos del archivo
    # si los hay + buffer FA del estimador.
    assert vram.estimate_draft_aux_gb(BASE_META, 8192, 0.5, None) == _approx_gb(
        8192, kv_heads=4, head_dim=128, weights=0.5
    )


def test_draft_aux_none_sin_datos_minimos():
    # Ni draft ni base traen metadata: no se inventa un número (None = el
    # componente no entra al fit ni al presupuesto).
    assert vram.estimate_draft_aux_gb({}, 8192) is None
    assert vram.estimate_draft_aux_gb({"n_layer": 8}, 8192, 0.3, {"n_layer": 1}) is None


def test_estimate_vram_usage_suma_draft_como_componente():
    meta = dict(BASE_META)
    plain = vram.estimate_vram_usage(meta, 4.0, 8192, gpu_vram_gb=24.0)
    with_draft = vram.estimate_vram_usage(
        meta, 4.0, 8192, gpu_vram_gb=24.0,
        draft_aux={"label": "draft.gguf", "weights_gb": 0.5, "metadata": DRAFT_META},
    )
    draft_gb = vram.estimate_draft_aux_gb(meta, 8192, 0.5, DRAFT_META)
    assert with_draft["aux_gb"] == round(draft_gb, 2)
    assert with_draft["aux_components"] == [
        {"kind": "draft_model", "label": "draft.gguf", "size_gb": round(draft_gb, 2)}
    ]
    # Los pesos del draft NO se mezclan con los del modelo: el desglose permite
    # a la UI decir cuánto es especulación y cuánto es el modelo.
    assert with_draft["weights_gb"] == plain["weights_gb"]
    delta = with_draft["total_gb"] - plain["total_gb"]
    assert delta == pytest.approx(draft_gb, abs=0.02)  # redondeos de componente aparte
    assert with_draft["kv_gb"] == plain["kv_gb"]  # el KV del base no se toca


def test_legend_reestima_el_draft_en_cada_ctx():
    # El KV del draft crece con el contexto: tratarlo como peso fijo haría que
    # las filas cortas HEREDEN el coste del ctx pedido. Cada fila debe llevar
    # su propia estimación (coincide con estimate_vram_usage a ese ctx).
    rows = vram.estimate_vram_legend(
        dict(BASE_META), 4.0,
        ctx_values=(4096, 8192),
        aux_weights_gb=0.0, aux_components=[],
        draft_aux={"label": "d", "weights_gb": 0.5, "metadata": DRAFT_META},
    )
    for row in rows:
        direct = vram.estimate_vram_usage(
            dict(BASE_META), 4.0, row["n_ctx"], gpu_vram_gb=24.0,
            draft_aux={"label": "d", "weights_gb": 0.5, "metadata": DRAFT_META},
        )
        assert row["total_gb"] == direct["total_gb"]
    # Crecimiento real: el delta entre filas incluye el KV adicional del draft
    # (más que el KV solo del modelo base).
    plain_rows = vram.estimate_vram_legend(
        dict(BASE_META), 4.0, ctx_values=(4096, 8192), gpu_vram_gb=24.0,
    )
    delta_draft = rows[1]["total_gb"] - rows[0]["total_gb"]
    delta_base = plain_rows[1]["total_gb"] - plain_rows[0]["total_gb"]
    assert delta_draft > delta_base


# ---------------------------------------------------------------------------
# /vram-estimate con spec_type: matriz de presupuesto
# ---------------------------------------------------------------------------


async def test_spec_none_no_cuenta_aux_aunque_haya_companion(client, tmp_path):
    # `none` explícito manda aunque el modelo tenga sidecar MTP: sin
    # especulación no hay draft que cargar. El aviso legacy mtp_available NO
    # se emite aquí: la estimación ya es la del modo elegido.
    d, _ = _base_dir(tmp_path, with_mtp=True)
    models = await _scan_dir(client, d)
    res = await client.get(
        f"/api/models/{_id_for(models, BASE_NAME)}/vram-estimate",
        params={"n_ctx": 8192, "spec_type": "none"},
    )
    data = res.json()
    assert data["available"] is True
    assert data["aux_gb"] == 0.0
    assert data["aux_components"] == []
    assert "mtp_available" not in data
    assert data["spec_budget"] == {
        "spec_type": "none", "family": "no-aux",
        "draft_path": None, "draft_embedded": False,
        "note": "Este modo no suma VRAM: su lookup vive en RAM-CPU.",
    }


async def test_spec_ngram_no_desaconseja_por_vram_aunque_haya_companion(client, tmp_path):
    # El lookup ngram vive en RAM-CPU: VRAM 0, aunque el sidecar esté al lado.
    d, _ = _base_dir(tmp_path, with_mtp=True, with_dflash=True)
    models = await _scan_dir(client, d)
    for modo in ("ngram-simple", "ngram-mod"):
        res = await client.get(
            f"/api/models/{_id_for(models, BASE_NAME)}/vram-estimate",
            params={"n_ctx": 8192, "spec_type": modo},
        )
        data = res.json()
        assert data["aux_gb"] == 0.0
        assert [c["kind"] for c in data["aux_components"]] == []
        assert data["spec_budget"]["family"] == "no-aux"


async def test_spec_draft_mtp_presupuesta_kv_y_computo_del_sidecar(client, tmp_path):
    # Con spec_type=draft-mtp el sidecar deja de ser un peso crudo: entra su
    # coste completo con METADATA REAL del archivo (pesos + KV propio q8_0 +
    # buffer FA). El path sin spec_type (legacy) sigue con los pesos crudos.
    d, base = _base_dir(tmp_path, with_mtp=True)
    models = await _scan_dir(client, d)
    model_id = _id_for(models, BASE_NAME)
    mtp = d / MTP_NAME

    legacy = (await client.get(
        f"/api/models/{model_id}/vram-estimate", params={"n_ctx": 8192},
    )).json()
    assert legacy["aux_gb"] == round(_file_weights_gb(mtp), 2)
    assert [c["kind"] for c in legacy["aux_components"]] == ["mtp_draft"]
    assert "spec_budget" not in legacy  # sin spec_type: respuesta legacy intacta

    draft_meta = await models_module.read_gguf_metadata_cached(mtp)
    expected = vram.estimate_draft_aux_gb(
        {}, 8192, _file_weights_gb(mtp), draft_meta
    )
    res = await client.get(
        f"/api/models/{model_id}/vram-estimate",
        params={"n_ctx": 8192, "spec_type": "draft-mtp"},
    )
    data = res.json()
    assert data["aux_gb"] == round(expected, 2)
    assert data["aux_components"] == [
        {"kind": "draft_model", "label": MTP_NAME, "size_gb": round(expected, 2)}
    ]
    assert data["total_gb"] - (
        data["weights_gb"] + data["kv_gb"] + data["compute_gb"]
    ) == pytest.approx(data["aux_gb"], abs=0.02)
    assert data["spec_budget"] == {
        "spec_type": "draft-mtp", "family": "draft",
        "draft_path": str(mtp), "draft_embedded": False,
    }
    # El presupuesto honesto es mayor que los pesos crudos del legacy: ahí
    # estaba el undercount que el diseño de LL-4 U3 quiso cerrar.
    assert data["aux_gb"] > legacy["aux_gb"]


async def test_spec_draft_dflash_presupuesta_el_sidecar_de_su_familia(client, tmp_path):
    # draft-dflash → el archivo dflash (pairing U2), no el mtp; y su coste usa
    # la metadata del dflash. El legacy sin spec_type sigue MTP-only.
    d, _ = _base_dir(tmp_path, with_mtp=True, with_dflash=True)
    models = await _scan_dir(client, d)
    model_id = _id_for(models, BASE_NAME)
    dflash = d / DFLASH_NAME

    res = await client.get(
        f"/api/models/{model_id}/vram-estimate",
        params={"n_ctx": 16384, "spec_type": "draft-dflash"},
    )
    data = res.json()
    draft_meta = await models_module.read_gguf_metadata_cached(dflash)
    expected = vram.estimate_draft_aux_gb({}, 16384, _file_weights_gb(dflash), draft_meta)
    assert data["aux_gb"] == round(expected, 2)
    assert data["aux_components"] == [
        {"kind": "draft_model", "label": DFLASH_NAME, "size_gb": round(expected, 2)}
    ]
    assert data["spec_budget"]["draft_path"] == str(dflash)

    # KV propio: al subir el ctx sube el presupuesto del draft (no es un peso
    # fijo heredado de otra fila).
    bigger = (await client.get(
        f"/api/models/{model_id}/vram-estimate",
        params={"n_ctx": 32768, "spec_type": "draft-dflash"},
    )).json()
    expected_big = vram.estimate_draft_aux_gb({}, 32768, _file_weights_gb(dflash), draft_meta)
    assert bigger["aux_gb"] == round(expected_big, 2) > data["aux_gb"]
    # Cada fila de la leyenda lleva SU ctx (la 16384 coincide con la principal).
    row = next(r for r in data["legend"] if r["n_ctx"] == 16384)
    assert row["total_gb"] == data["total_gb"]


async def test_spec_draft_model_manual_pisa_a_la_autodeteccion(client, tmp_path):
    # El path que la UI va a lanzar manda sobre el companion (misma prioridad
    # que siempre tuvo mtp_draft_model), aunque el archivo esté fuera del
    # inventario y con dims distintas.
    d, _ = _base_dir(tmp_path, with_mtp=True)
    manual_dir = tmp_path / "descarga-manual"
    manual_dir.mkdir()
    manual = _pad(write_gguf(
        manual_dir / "eagle3-por-hf.gguf", "llama", None,
        {"block_count": 3, "attention.head_count": 8, "attention.head_count_kv": 8,
         "attention.head_size": 128, "embedding_length": 1024},
    ))
    models = await _scan_dir(client, d)
    res = await client.get(
        f"/api/models/{_id_for(models, BASE_NAME)}/vram-estimate",
        params={"n_ctx": 8192, "spec_type": "draft-eagle3", "draft_model": str(manual)},
    )
    data = res.json()
    draft_meta = await models_module.read_gguf_metadata_cached(manual)
    expected = vram.estimate_draft_aux_gb({}, 8192, _file_weights_gb(manual), draft_meta)
    assert data["aux_gb"] == round(expected, 2)
    assert data["aux_components"][0]["label"] == "eagle3-por-hf.gguf"
    assert data["spec_budget"]["draft_path"] == str(manual)


async def test_spec_draft_mtp_embebido_presupuesta_la_cabeza(client, tmp_path):
    # Cabeza embebida (sin sidecar): 0 pesos nuevos — sus tensores ya están
    # en el .gguf — pero SUELO propio: su KV al ctx y el buffer FA entran al
    # presupuesto con la aproximación de una capa del modelo principal.
    d, base = _base_dir(tmp_path)
    models = await _scan_dir(client, d)
    model_id = _id_for(models, BASE_NAME)

    data = (await client.get(
        f"/api/models/{model_id}/vram-estimate",
        params={"n_ctx": 8192, "spec_type": "draft-mtp", "mtp_embedded": "true"},
    )).json()
    expected = _approx_gb(8192, kv_heads=4, head_dim=128)
    assert data["aux_gb"] == expected
    assert data["aux_components"] == [
        {"kind": "draft_model", "label": "cabeza MTP embebida", "size_gb": expected},
    ]
    assert data["spec_budget"]["draft_embedded"] is True
    assert data["spec_budget"]["draft_path"] is None

    # mtp_embedded=false explícito: sin archivo y sin embebido no hay draft
    # que presupuestar — se declara, no se inventa.
    data = (await client.get(
        f"/api/models/{model_id}/vram-estimate",
        params={"n_ctx": 8192, "spec_type": "draft-mtp", "mtp_embedded": "false"},
    )).json()
    assert data["aux_gb"] == 0.0
    assert data["spec_budget"]["draft_path"] is None
    assert "note" in data["spec_budget"]


async def test_spec_draft_simple_sin_draft_se_declara_no_se_inventa(client, tmp_path):
    # draft-simple/eagle3 no tienen familia auto-detectable: sin draft_model
    # el presupuesto no suma nada y la respuesta lo dice explícitamente.
    d, _ = _base_dir(tmp_path, with_mtp=True)  # el mtp NO vale para simple
    models = await _scan_dir(client, d)
    data = (await client.get(
        f"/api/models/{_id_for(models, BASE_NAME)}/vram-estimate",
        params={"n_ctx": 8192, "spec_type": "draft-simple"},
    )).json()
    assert data["aux_gb"] == 0.0
    assert [c["kind"] for c in data["aux_components"]] == []
    assert "Sin draft especificado" in data["spec_budget"]["note"]


async def test_spec_type_invalido_422(client, tmp_path):
    d, _ = _base_dir(tmp_path)
    models = await _scan_dir(client, d)
    res = await client.get(
        f"/api/models/{_id_for(models, BASE_NAME)}/vram-estimate",
        params={"spec_type": "draft-dspark"},
    )
    assert res.status_code == 422
    assert "spec_type desconocido" in res.json()["detail"]


async def test_draft_ilegible_presupuesta_con_aproximacion(client, tmp_path):
    # Archivo con pesos reales pero metadata ilegible: entra su tamaño real +
    # la aproximación de una capa del modelo principal (el estimador no
    # inventa dims del draft que no puede leer).
    d, _ = _base_dir(tmp_path, with_mtp=True)
    draft = d / DFLASH_NAME
    draft.write_bytes(b"\0" * _PAD)  # pesos reales SIN header legible
    models = await _scan_dir(client, d)
    res = await client.get(
        f"/api/models/{_id_for(models, BASE_NAME)}/vram-estimate",
        params={"n_ctx": 8192, "spec_type": "draft-dflash"},
    )
    data = res.json()
    expected = _approx_gb(8192, kv_heads=4, head_dim=128, weights=_file_weights_gb(draft))
    assert data["aux_gb"] == expected
    assert data["aux_components"][0]["label"] == DFLASH_NAME
