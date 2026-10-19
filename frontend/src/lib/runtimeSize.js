// Delta de tamaño del runtime (pulido RT-12): cuánto cambia el espacio en
// disco al actualizar. Reemplazando la build anterior (default): delta =
// nueva − anterior. Conservándola (checkbox rollback): la vieja no se borra,
// así que el costo completo es el de la nueva.

export function sizeDeltaMb(newSizeMb, prevSizeMb, keepPrevious = false) {
  if (keepPrevious) return Math.round(newSizeMb);
  if (typeof prevSizeMb !== "number" || !Number.isFinite(prevSizeMb)) return null;
  return Math.round(newSizeMb - prevSizeMb);
}

// Etiqueta firmada del delta en MB: "+549", "-162", "0". Null → null
// (tamaño previo desconocido: la UI oculta el delta).
export function formatDeltaMb(deltaMb) {
  if (deltaMb === null || deltaMb === undefined) return null;
  return deltaMb > 0 ? `+${deltaMb}` : `${deltaMb}`;
}
