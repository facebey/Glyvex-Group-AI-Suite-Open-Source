"""
hardware_api.py — API de hardware detectado y recomendaciones (M6 / A2).

- GET  /api/hardware            -> HardwareSnapshot (cacheada TTL; ?refresh=1
                                  fuerza una nueva tanda de sondas).
- POST /api/hardware/recommend  -> draft de LaunchConfig a partir de la foto
                                  (body opcional {model_id} para fit real).

Las sondas son blocking (registro, NVML, HTTP local a LHM): corren en el
threadpool para no bloquear el event loop.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

import hardware_detection
import hardware_recommend
import models as models_module

logger = logging.getLogger("glyvex.hardware.api")

router = APIRouter()


class RecommendRequest(BaseModel):
    model_id: str | None = None


@router.get("")
async def get_hardware(refresh: bool = Query(default=False)) -> dict:
    snapshot = await run_in_threadpool(hardware_detection.detect_hardware, refresh)
    return snapshot.model_dump()


async def _model_inputs(model_id: str) -> dict[str, Any]:
    """Ficha del modelo para el fit real (mismo material que /vram-estimate:
    metadata persistida en models.json; si falta, se lee el header). A2F-1
    añade los componentes auxiliares reales: mmproj con su tamaño de archivo y
    el draft MTP sidecar (path + tamaño + header) vía la regla compartida
    mtp_companion_file. Con tamaño 0 o metadata None el motor trata el
    componente como no-medido (el fit honesto prefiere no inventar datos).

    Limitación honesta v1 (LL-4 U3): el fit automático sigue resolviendo solo
    el sidecar MTP — hoy es el único draft auto-descubrible con confianza.
    Los demás modos draft (dflash/simple/eagle3) se presupuestan en
    /vram-estimate y se eligen a mano; extender esta propuesta a dflash
    requeriría oferta HF de sidecars."""
    entry = models_module.inventory.get(model_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Modelo no encontrado: {model_id}")
    metadata = entry.metadata
    if metadata is None:
        try:
            metadata = await models_module.read_gguf_metadata_cached(Path(entry.path))
        except Exception as exc:  # noqa: BLE001 — sin header: el motor cae a la clase genérica
            logger.warning("no se pudo leer metadata de %s: %s", entry.path, exc)
            metadata = None

    mmproj_size = 0.0
    if entry.mmproj_path:
        try:
            mmproj_size = Path(entry.mmproj_path).stat().st_size / (1024 ** 3)
        except OSError:
            pass

    mtp_path = models_module.mtp_companion_file(entry)
    mtp_size = 0.0
    mtp_metadata: dict[str, Any] | None = None
    if mtp_path:
        try:
            mtp_size = Path(mtp_path).stat().st_size / (1024 ** 3)
            mtp_metadata = await models_module.read_gguf_metadata_cached(Path(mtp_path))
        except Exception as exc:  # noqa: BLE001 — draft ilegible: se cuenta solo su peso
            logger.warning("no se pudo leer metadata del draft %s: %s", mtp_path, exc)
            mtp_metadata = None

    return {
        "size_gb": entry.size_gb,
        "metadata": metadata or {},
        "mtp_embedded": entry.mtp_embedded,
        # Capas MTP que la arquitectura del base declara (U6): la ficha las
        # pasa al fit para que un sidecar con header copiado no se presupuesta
        # con las capas completas del base.
        "mtp_embedded_layers": entry.mtp_embedded_layers,
        "mmproj_path": entry.mmproj_path,
        "mmproj_size_gb": mmproj_size,
        "mtp_draft_model": mtp_path,
        "mtp_draft_size_gb": mtp_size,
        "mtp_draft_metadata": mtp_metadata,
    }


@router.post("/recommend")
async def recommend(body: RecommendRequest | None = None) -> dict:
    snapshot = await run_in_threadpool(hardware_detection.detect_hardware, False)
    model_inputs: dict[str, Any] | None = None
    if body and body.model_id:
        model_inputs = await _model_inputs(body.model_id)
    return await run_in_threadpool(hardware_recommend.recommend, snapshot, model_inputs)
