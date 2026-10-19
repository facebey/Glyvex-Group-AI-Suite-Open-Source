import { describe, expect, it } from "vitest";

import {
  EXTERNAL_THEME_NAME_RE,
  isExternalThemeName,
  nextInList,
  nextTheme,
  themeHref,
  THEMES,
} from "./theme.js";

describe("isExternalThemeName", () => {
  it("acepta nombres de carpeta válidos", () => {
    for (const name of ["Mi Tema", "my-theme.v2", "a", "Tema 2.0"]) {
      expect(isExternalThemeName(name)).toBe(true);
    }
  });

  it("rechaza nombres inválidos", () => {
    for (const name of [
      "",
      " leading",
      ".hidden",
      "con/slash",
      "con\\slash",
      "con paréntesis (x)",
      "x".repeat(65),
      null,
      42,
    ]) {
      expect(isExternalThemeName(name)).toBe(false);
    }
  });

  it("los nombres de los internos nunca cuentan como externos", () => {
    for (const name of THEMES) {
      expect(isExternalThemeName(name)).toBe(false);
    }
  });

  it("el regex exportado coincide con el contrato del backend", () => {
    expect(EXTERNAL_THEME_NAME_RE.source).toBe("^[A-Za-z0-9][A-Za-z0-9 _\\-.]{0,63}$");
  });
});

describe("nextInList", () => {
  it("avanza y cicla", () => {
    expect(nextInList(["a", "b", "c"], "a")).toBe("b");
    expect(nextInList(["a", "b", "c"], "c")).toBe("a");
  });

  it("elemento desconocido → el primero", () => {
    expect(nextInList(["a", "b"], "zzz")).toBe("a");
  });

  it("lista vacía → devuelve lo que venía", () => {
    expect(nextInList([], "a")).toBe("a");
    expect(nextInList(null, "a")).toBe("a");
  });
});

describe("nextTheme (ciclo de internos)", () => {
  it("sigue el orden declarado", () => {
    expect(nextTheme("dark")).toBe("light");
    expect(nextTheme("matrix")).toBe(THEMES[0]);
  });
});

describe("themeHref", () => {
  it("codifica nombre y archivo, y agrega el cache-buster", () => {
    expect(themeHref("Mi Tema", "theme.css", 1700000000)).toBe(
      "/api/themes/Mi%20Tema/theme.css?v=1700000000"
    );
  });

  it("sin mtime no lleva query", () => {
    expect(themeHref("tex", "sub/fondo.png")).toBe("/api/themes/tex/sub/fondo.png");
  });

  it("mtime no numérico se ignora", () => {
    expect(themeHref("tex", "a.css", undefined)).toBe("/api/themes/tex/a.css");
    expect(themeHref("tex", "a.css", NaN)).toBe("/api/themes/tex/a.css");
  });
});
