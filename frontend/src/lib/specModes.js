// Taxonomía de modos de especulación (LL-4 U4). Espejo frontend de
// SPEC_DRAFT_MODES/SPEC_NO_AUX_MODES de backend/vram_estimate.py y de los
// flags --spec-type / --spec-* de la build b11827. Vive separado del page
// para poder testearse sin React y porque el mismo conocimiento lo van a
// necesitar Monitor y Benchmark.

// Orden = orden del selector de la UI. `family` determina qué campos aplica
// cada modo (los mismos grupos que emite build_llama_server_command).
export const SPEC_MODES = [
  { value: "none", family: "none" },
  { value: "draft-mtp", family: "draft" },
  { value: "draft-dflash", family: "draft" },
  { value: "draft-simple", family: "draft" },
  { value: "draft-eagle3", family: "draft" },
  { value: "ngram-simple", family: "ngram-simple" },
  { value: "ngram-mod", family: "ngram-mod" },
];

export const SPEC_TYPE_OPTIONS = SPEC_MODES.map((m) => m.value);
export const SPEC_DRAFT_MODES = SPEC_MODES.filter((m) => m.family === "draft").map((m) => m.value);
export const SPEC_NGRAM_MODES = SPEC_MODES.filter((m) => m.family.startsWith("ngram")).map((m) => m.value);

export function specFamily(mode) {
  const found = SPEC_MODES.find((m) => m.value === mode);
  return found ? found.family : "unknown";
}

export const isDraftMode = (mode) => SPEC_DRAFT_MODES.includes(mode);
export const isNgramMode = (mode) => SPEC_NGRAM_MODES.includes(mode);

// draft-mtp puede funcionar con la cabeza embebida en el propio .gguf (sin
// archivo). El resto de los modos draft exige un .gguf de draft: es el mismo
// criterio que el validator de LaunchConfig en el backend.
export function specRequiresDraftFile(mode) {
  return isDraftMode(mode) && mode !== "draft-mtp";
}

// Campos ngram por modo (los mismos flags que emite el builder). Vacío =
// no emitir = default del binario.
export const NGRAM_SIMPLE_FIELDS = ["ngram_size_n", "ngram_size_m", "ngram_min_hits"];
export const NGRAM_MOD_FIELDS = ["ngram_n_min", "ngram_n_max", "ngram_n_match"];
export const NGRAM_FIELD_FLAGS = {
  ngram_size_n: "--spec-ngram-simple-size-n",
  ngram_size_m: "--spec-ngram-simple-size-m",
  ngram_min_hits: "--spec-ngram-simple-min-hits",
  ngram_n_min: "--spec-ngram-mod-n-min",
  ngram_n_max: "--spec-ngram-mod-n-max",
  ngram_n_match: "--spec-ngram-mod-n-match",
};

// Defaults declarados por la build b11827. La UI los muestra como placeholder:
// dejar el campo vacío NO es "cero", es dejar el default del binario.
export const NGRAM_DEFAULTS = {
  "ngram-simple": { ngram_size_n: 12, ngram_size_m: 48, ngram_min_hits: 1 },
  "ngram-mod": { ngram_n_min: 48, ngram_n_max: 64, ngram_n_match: 24 },
};

export function ngramFieldsForMode(mode) {
  if (mode === "ngram-simple") return NGRAM_SIMPLE_FIELDS;
  if (mode === "ngram-mod") return NGRAM_MOD_FIELDS;
  return [];
}

// Familia del draft por nombre de archivo: la MISMA regla de token que
// SPEC_DRAFT_NAME_TOKENS y build_groups en el backend (dflash-* vs mtp-*).
// Un path manual se clasifica igual: si el archivo se llama dflash-*, el
// modo que tiene sentido es draft-dflash.
export function specTypeForDraftFilename(nameOrPath) {
  const lowered = String(nameOrPath || "").toLowerCase();
  if (lowered.includes("dflash")) return "draft-dflash";
  return "draft-mtp";
}

// Preselección honesta al elegir un modelo: solo modos con soporte REAL
// detectado (cabeza embebida o sidecar en la carpeta). Si no hay nada,
// "none": la UI no inventa especulación que el modelo no tiene.
export function defaultSpecMode({ embeddedMtp = false, mtpSidecars = 0, dflashSidecars = 0 }) {
  if (embeddedMtp || mtpSidecars > 0) return "draft-mtp";
  if (dflashSidecars > 0) return "draft-dflash";
  return "none";
}

// Migración local de configs/templates guardados antes de LL-4: el MISMO
// mapeo que _migrate_legacy_spec_fields en backend/launcher.py. Hace falta
// acá porque el frontend ya no tiene el campo mtp_draft_model: un template
// viejo llegaría con él y el backend no lo migra cuando spec_type ya viene
// explícito ("none" es un valor explícito válido).
export function migrateLegacySpecFields(params) {
  if (!params || typeof params !== "object") return params;
  // Marcadores reales de una config pre-LL-4: el campo removido
  // mtp_draft_model, o mtp_embedded SIN spec_type (una config nueva siempre
  // trae spec_type y su mtp_embedded sigue siendo un campo válido: no es
  // legacy y no se toca).
  const hasLegacyPath = "mtp_draft_model" in params;
  const legacyEmbedded = Boolean(params.mtp_embedded) && !("spec_type" in params);
  if (!hasLegacyPath && !legacyEmbedded) return params;
  const out = { ...params };
  const legacyPath = out.mtp_draft_model || null;
  const legacyOn = Boolean(legacyPath) || legacyEmbedded;
  delete out.mtp_draft_model;
  if (legacyOn && !out.spec_type) {
    out.spec_type = "draft-mtp";
    if (legacyPath && !out.draft_model) out.draft_model = legacyPath;
  }
  return out;
}

// Avisos de coherencia para el modo actual (espejo de los ValueError del
// validator: draft-mtp exige path O cabeza embebida; el resto de modos draft
// exige path). Devuelve la clave i18n del aviso o null. Es un AVISO de UI:
// el ground-truth del rechazo sigue siendo el log de llama-server (U5).
export function specModeWarningKey(mode, draftModel, mtpEmbedded) {
  if (!isDraftMode(mode)) return null;
  const hasPath = Boolean(String(draftModel || "").trim());
  if (mode === "draft-mtp") {
    return hasPath || mtpEmbedded ? null : "specWarnMtpEither";
  }
  return hasPath ? null : "specWarnNeedDraft";
}
