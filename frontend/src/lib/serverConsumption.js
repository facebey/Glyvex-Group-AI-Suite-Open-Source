/**
 * MON-4: consumo total de una instancia de llama-server (todos los clientes) y
 * MON-5: total de por vida de todas las instancias.
 *
 * El backend (/api/llm-metrics/{id}/consumption y /consumption/all) ya mergea
 * reinicios del mismo process_id; acá vive solo el mapeo al formato de
 * cloudCost() y el total de visualización.
 *
 * Semántica llama.cpp: prompt_tokens_total EXCLUYE los tokens cacheados (van en
 * un contador aparte), así que para cloudCost — que factura lo fresco como
 * prompt - cached — hay que pasarle prompt = fresco + cacheados.
 */

import { cloudCost } from "./sessionCost.js";

/** Uso estilo OpenAI para cloudCost() a partir de los totales absolutos. null → 0. */
export function usageForPricing(consumption) {
  const fresh = Number(consumption?.prompt_tokens) || 0;
  const cached = Math.max(0, Number(consumption?.cached_tokens) || 0);
  const predicted = Number(consumption?.predicted_tokens) || 0;
  return { prompt_tokens: fresh + cached, completion_tokens: predicted, cached_tokens: cached };
}

/** Tokens procesados en total (entrada fresca + caché reutilizado + generado). */
export function totalTokens(consumption) {
  const fresh = Number(consumption?.prompt_tokens) || 0;
  const cached = Number(consumption?.cached_tokens) || 0;
  const predicted = Number(consumption?.predicted_tokens) || 0;
  return fresh + cached + predicted;
}

/**
 * MON-5: filas del desglose por modelo del total de por vida
 * (/consumption/all). Cada modelo trae los mismos campos que un consumo por
 * proceso, así que usageForPricing/totalTokens aplican tal cual.
 */
export function lifetimeModelRows(life, provider) {
  const models = Array.isArray(life?.models) ? life.models : [];
  return models.map((m) => ({
    model_name: m.model_name || "",
    tokens: totalTokens(m),
    cost: provider ? cloudCost(usageForPricing(m), provider) : 0,
  }));
}

export { cloudCost };
