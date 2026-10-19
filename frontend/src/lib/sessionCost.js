/**
 * MON-2: costo de referencia en la nube del chat local.
 *
 * Precios por proveedor y por millón de tokens (config.cloud_pricing,
 * editables desde Config). Los tokens cacheados se facturan a la tarifa de
 * lectura de caché; si el proveedor no la diferencia, se facturan como
 * entrada normal.
 */

export function cloudCost(usage, provider) {
  const prompt = Number(usage?.prompt_tokens) || 0;
  const completion = Number(usage?.completion_tokens) || 0;
  // cached nunca excede al prompt (clamp defensivo contra datos corrompidos).
  const cached = Math.min(prompt, Math.max(0, Number(usage?.cached_tokens) || 0));
  const freshInput = prompt - cached;
  const inputRate = Number(provider?.input_per_mtok) || 0;
  const outputRate = Number(provider?.output_per_mtok) || 0;
  // cache_read_per_mtok puede ser null/ausente: el proveedor no diferencia
  // la lectura de caché y la parte cacheada va a la tarifa de entrada.
  const cacheRate =
    provider?.cache_read_per_mtok != null ? Number(provider.cache_read_per_mtok) : inputRate;
  return (freshInput * inputRate + cached * cacheRate + completion * outputRate) / 1_000_000;
}

/** 42 → "42", 1234 → "1.2 k", 1234567 → "1.23 M". */
export function formatTokens(value) {
  const n = Number(value) || 0;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)} M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)} k`;
  return String(n);
}

export function formatMoney(value, currency = "USD") {
  const n = Number(value) || 0;
  const symbol = currency === "USD" ? "$" : `${currency} `;
  // Abajo de un centavo, 4 decimales: un costo de $0.004 no se muestra $0.00.
  const text = n > 0 && n < 0.01 ? n.toFixed(4) : n.toFixed(2);
  return `${symbol}${text}`;
}
