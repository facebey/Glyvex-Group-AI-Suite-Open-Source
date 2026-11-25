// Preferencia de visión de una plantilla (TPL-VISION-RELINK, F-4).
//
// La receta guarda la INTENCIÓN ("quiero visión en este modelo") y, solo si el
// usuario eligió un archivo a mano o un sidecar distinto del detectado, ese
// path. No guarda el ORIGEN como verdad absoluta: el origen es propiedad del
// modelo actual y cada .gguf de un grupo puede tener capacidades distintas (por
// eso la auto-detección de la card se recalcula por cuantización). Al aplicar,
// la intención se re-liga a lo que el modelo realmente ofrece, igual que hace
// el MCP con sus overrides (backend/mcp_server.py: `_require(entry.has_mmproj
// ...)` usa la ruta medida del inventario, nunca una guardada).
//
// Shape persistido en HWTemplate.params["vision_intent"] (clave de frontend,
// inofensiva para el backend: pydantic ignora campos no declarados, mismo
// trato que `toggles`):
//   { enabled: boolean, manual_path: string }

// Intención desde los params de una plantilla. Los templates guardados antes
// de F-4 no traen `vision_intent`: la única señal que dejaron es el snapshot
// de toggles (`toggles.mmproj`), que se lee como intención legacy. Un path
// legacy no existía: `mmproj_path` nunca fue campo de TEMPLATE_FIELDS, y un
// template viejo SIN bloque toggles (los anteriores a P1.5 F5) queda apagado —
// no inventamos una preferencia que nadie guardó.
export function templateVisionIntent(params) {
  const vi = params && typeof params === "object" ? params.vision_intent : null;
  if (vi && typeof vi === "object") {
    return { enabled: Boolean(vi.enabled), manual_path: String(vi.manual_path || "").trim() };
  }
  return {
    enabled: Boolean(params && params.toggles && params.toggles.mmproj),
    manual_path: "",
  };
}

// Traducción de la intención al modelo realmente seleccionado.
//   embeddedVision: el .gguf base trae la visión embebida (no requiere archivo)
//   mmprojPaths:    sidecars detectados para ESTE modelo (orden del inventario)
// Devuelve { enabled, path, mode } con mode "none" | "sidecar" | "embedded" |
// "manual". Precedencia (misma lógica de preferencia que la auto-detección de
// la GroupCard): primero la elección explícita que sigue existiendo, después lo
// embebido, después el primer sidecar detectado. Si la plantilla pide visión
// pero el modelo no ofrece ninguna vía, el resultado es OFF (mode "none"): la
// UI no lo señala con ningún aviso — la receta simplemente no puede aplicarse
// en ESTE modelo, y el usuario decide.
export function resolveVisionTemplate(intent, { embeddedVision = false, mmprojPaths = [] } = {}) {
  if (!intent || !intent.enabled) return { enabled: false, path: "", mode: "none" };
  const manual = String(intent.manual_path || "").trim();
  const paths = (mmprojPaths || []).map((p) => String(p || "")).filter(Boolean);

  // 1. Una elección explícita que sigue siendo un archivo de ESTE grupo manda:
  //    preserva la selección entre varios sidecars de la carpeta.
  if (manual && paths.includes(manual)) {
    return { enabled: true, path: manual, mode: "sidecar" };
  }
  // 2. Visión embebida: no se emite --mmproj (el binario la trae dentro); el
  //    toggle ON solo mantiene coherente el panel.
  if (embeddedVision) {
    return { enabled: true, path: "", mode: "embedded" };
  }
  // 3. El sidecar que el escaneo midió para este modelo.
  if (paths.length > 0) {
    return { enabled: true, path: paths[0], mode: "sidecar" };
  }
  // 4. La receta apuntaba a un archivo que este modelo no tiene: se conserva
  //    tal cual. No hay validación de existencia en el backend para --mmproj
  //    (los 422 por archivo son del draft), así que el fallo real lo daría el
  //    binario al subir — no lo inventamos ni lo silenciamos cambiándolo.
  if (manual) {
    return { enabled: true, path: manual, mode: "manual" };
  }
  return { enabled: false, path: "", mode: "none" };
}

// Intención a persistir al GUARDAR una plantilla. Se guarda como manual_path
// solo lo que no se puede volver a encontrar por re-ligue: un path distinto de
// lo detectado para este modelo. Si el path coincide con lo detectado (o viene
// de /api/hardware/recommend, que usa la ruta medida del inventario), se
// omite: al aplicar se re-liga igual y así la receta no queda atada a una
// ruta fija. enabled=true con manual_path "" cubre ambos casos sin path fijo
// (embebido y sidecar detectado): al aplicar se resuelven con lo que el
// modelo ofrezca entonces.
export function visionIntentFromState({ enabled, mmprojPath, detectedPaths = [] }) {
  if (!enabled) return { enabled: false, manual_path: "" };
  const path = String(mmprojPath || "").trim();
  const detected = (detectedPaths || []).map((p) => String(p || "")).filter(Boolean);
  const manual = path && !detected.includes(path) ? path : "";
  return { enabled: true, manual_path: manual };
}
