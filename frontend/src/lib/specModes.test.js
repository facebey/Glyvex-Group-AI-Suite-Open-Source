import { describe, expect, it } from "vitest";

import {
  NGRAM_DEFAULTS,
  SPEC_DRAFT_MODES,
  SPEC_MODES,
  SPEC_NGRAM_MODES,
  defaultSpecMode,
  isDraftMode,
  isNgramMode,
  migrateLegacySpecFields,
  ngramFieldsForMode,
  specFamily,
  specModeWarningKey,
  specRequiresDraftFile,
  specTypeForDraftFilename,
} from "./specModes.js";

// El contrato con el backend: estos valores son los que acepta SpecType en
// launcher.py. Si el backend agrega un modo, este test es el que avisa.

describe("lista de modos (contrato con SpecType del backend)", () => {
  it("cubre exactamente los modos verificados en --spec-type de b11827", () => {
    expect(SPEC_MODES.map((m) => m.value)).toEqual([
      "none",
      "draft-mtp",
      "draft-dflash",
      "draft-simple",
      "draft-eagle3",
      "ngram-simple",
      "ngram-mod",
    ]);
  });

  it("las familias derivan de la lista (draft / ngram / none)", () => {
    expect(SPEC_DRAFT_MODES).toEqual(["draft-mtp", "draft-dflash", "draft-simple", "draft-eagle3"]);
    expect(SPEC_NGRAM_MODES).toEqual(["ngram-simple", "ngram-mod"]);
    expect(specFamily("draft-mtp")).toBe("draft");
    expect(specFamily("ngram-mod")).toBe("ngram-mod");
    expect(specFamily("none")).toBe("none");
    expect(specFamily("inventado")).toBe("unknown");
  });

  it("isDraftMode / isNgramMode coinciden con las listas", () => {
    expect(isDraftMode("draft-dflash")).toBe(true);
    expect(isDraftMode("ngram-simple")).toBe(false);
    expect(isNgramMode("ngram-mod")).toBe(true);
    expect(isNgramMode("none")).toBe(false);
  });

  it("solo draft-mtp puede funcionar sin archivo (cabeza embebida)", () => {
    expect(specRequiresDraftFile("draft-mtp")).toBe(false);
    expect(specRequiresDraftFile("draft-dflash")).toBe(true);
    expect(specRequiresDraftFile("draft-simple")).toBe(true);
    expect(specRequiresDraftFile("draft-eagle3")).toBe(true);
    expect(specRequiresDraftFile("ngram-simple")).toBe(false);
  });
});

describe("campos ngram", () => {
  it("cada modo ngram tiene sus propios campos y defaults", () => {
    expect(ngramFieldsForMode("ngram-simple")).toEqual(["ngram_size_n", "ngram_size_m", "ngram_min_hits"]);
    expect(ngramFieldsForMode("ngram-mod")).toEqual(["ngram_n_min", "ngram_n_max", "ngram_n_match"]);
    expect(ngramFieldsForMode("draft-mtp")).toEqual([]);
    expect(Object.keys(NGRAM_DEFAULTS["ngram-simple"])).toEqual(ngramFieldsForMode("ngram-simple"));
    expect(Object.keys(NGRAM_DEFAULTS["ngram-mod"])).toEqual(ngramFieldsForMode("ngram-mod"));
  });
});

describe("familia del draft por nombre de archivo", () => {
  it("dflash-* es draft-dflash; lo demás se asume la familia MTP", () => {
    expect(specTypeForDraftFilename("dflash-qwen3-4b.q5_k_m.gguf")).toBe("draft-dflash");
    expect(specTypeForDraftFilename("C:\\modelos\\Qwen3-DFlash-4B.gguf")).toBe("draft-dflash");
    expect(specTypeForDraftFilename("qwen3-8b-mtp.q8_0.gguf")).toBe("draft-mtp");
    expect(specTypeForDraftFilename("")).toBe("draft-mtp");
  });
});

describe("preselección honesta al elegir un modelo", () => {
  it("prioriza la cabeza embebida, luego el sidecar MTP, luego DFlash", () => {
    expect(defaultSpecMode({ embeddedMtp: true, mtpSidecars: 0, dflashSidecars: 0 })).toBe("draft-mtp");
    expect(defaultSpecMode({ embeddedMtp: false, mtpSidecars: 2, dflashSidecars: 1 })).toBe("draft-mtp");
    expect(defaultSpecMode({ embeddedMtp: false, mtpSidecars: 0, dflashSidecars: 1 })).toBe("draft-dflash");
  });

  it("sin soporte detectado no inventa especulación", () => {
    expect(defaultSpecMode({})).toBe("none");
    expect(defaultSpecMode({ embeddedMtp: false, mtpSidecars: 0, dflashSidecars: 0 })).toBe("none");
  });
});

describe("migración de configs/templates pre-LL-4", () => {
  it("mapea mtp_draft_model a draft_model + spec_type draft-mtp", () => {
    const out = migrateLegacySpecFields({ mtp_draft_model: "C:\\x\\mtp.gguf", mtp_embedded: false });
    expect(out).toEqual({ spec_type: "draft-mtp", draft_model: "C:\\x\\mtp.gguf", mtp_embedded: false });
  });

  it("mtp_embedded solo también activa draft-mtp (sin path)", () => {
    const out = migrateLegacySpecFields({ mtp_embedded: true });
    expect(out.spec_type).toBe("draft-mtp");
    expect(out.draft_model).toBeUndefined();
    expect("mtp_draft_model" in out).toBe(false);
  });

  it("un spec_type explícito gana sobre la migración (igual que el backend)", () => {
    const out = migrateLegacySpecFields({ mtp_draft_model: "C:\\x\\mtp.gguf", spec_type: "ngram-mod" });
    expect(out.spec_type).toBe("ngram-mod");
    expect(out.draft_model).toBeUndefined();
  });

  it("no toca configs que ya vienen con los campos nuevos", () => {
    const cfg = { spec_type: "draft-dflash", draft_model: "d.gguf", mtp_embedded: false };
    expect(migrateLegacySpecFields(cfg)).toBe(cfg);
  });
});

describe("avisos de coherencia del modo (espejo del validator)", () => {
  it("draft-mtp sin path ni embebido avisa; con cualquiera de los dos no", () => {
    expect(specModeWarningKey("draft-mtp", "", false)).toBe("specWarnMtpEither");
    expect(specModeWarningKey("draft-mtp", "mtp.gguf", false)).toBeNull();
    expect(specModeWarningKey("draft-mtp", "   ", true)).toBeNull();
  });

  it("el resto de modos draft exige path", () => {
    expect(specModeWarningKey("draft-dflash", "", true)).toBe("specWarnNeedDraft");
    expect(specModeWarningKey("draft-eagle3", "eagle3.gguf", false)).toBeNull();
  });

  it("none y ngram no tienen avisos de draft", () => {
    expect(specModeWarningKey("none", "", false)).toBeNull();
    expect(specModeWarningKey("ngram-mod", "", false)).toBeNull();
  });
});
