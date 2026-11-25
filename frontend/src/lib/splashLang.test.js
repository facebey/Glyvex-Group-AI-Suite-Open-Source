// Tests puros del idioma del splash de arranque (I18N-SPLASH). La lógica
// vive en lib/ (como specModes.test.js / visionRelink.test.js) para testearla
// sin React ni Tauri.
import { describe, expect, it } from "vitest";
import es from "../locales/es.json";
import en from "../locales/en.json";
import { SPLASH_TEXTS, SUPPORTED_LANGS, resolveSplashLang } from "./splashLang.js";

describe("resolveSplashLang", () => {
  it("acepta los idiomas soportados tal cual", () => {
    expect(resolveSplashLang("es")).toBe("es");
    expect(resolveSplashLang("en")).toBe("en");
  });

  it("tolera mayúsculas y espacios del valor espejado por la shell", () => {
    expect(resolveSplashLang(" EN ")).toBe("en");
    expect(resolveSplashLang("Es")).toBe("es");
  });

  it("cualquier otra cosa cae al default 'es' (igual que i18n.js)", () => {
    expect(resolveSplashLang("fr")).toBe("es");
    expect(resolveSplashLang("es-AR")).toBe("es");
    expect(resolveSplashLang("")).toBe("es");
    expect(resolveSplashLang(null)).toBe("es");
    expect(resolveSplashLang(undefined)).toBe("es");
  });
});

describe("SPLASH_TEXTS", () => {
  it("cubre exactamente los idiomas soportados", () => {
    expect(Object.keys(SPLASH_TEXTS).sort()).toEqual([...SUPPORTED_LANGS].sort());
  });

  it("el tagline del splash espeja about.tagline de cada idioma", () => {
    // El diálogo About ya estaba traducido: splash y About no pueden decir
    // cosas distintas (era exactamente la queja del traspapelado I18N-SPLASH).
    expect(SPLASH_TEXTS.es.tagline).toBe(es.about.tagline);
    expect(SPLASH_TEXTS.en.tagline).toBe(en.about.tagline);
  });

  it("todos los textos de un idioma están en ese idioma (sin mezclas)", () => {
    expect(SPLASH_TEXTS.en.tagline).not.toContain("sin nube");
    expect(SPLASH_TEXTS.en.starting).not.toContain("Iniciando");
    expect(SPLASH_TEXTS.es.starting).toContain("Iniciando");
  });
});
