import { describe, expect, it } from "vitest";
import { toSpokenText } from "./tts.js";

describe("toSpokenText", () => {
  it("devuelve string vacío para contenido vacío", () => {
    expect(toSpokenText("")).toBe("");
    expect(toSpokenText(null)).toBe("");
    expect(toSpokenText(undefined)).toBe("");
  });

  it("saca los bloques de código del todo", () => {
    const text = toSpokenText("Antes del código\n```python\nprint(1)\n```\nDespués.");
    expect(text).not.toContain("python");
    expect(text).toContain("Antes del código");
    expect(text).toContain("Después.");
  });

  it("deja el código inline como texto plano", () => {
    expect(toSpokenText("Usá `npm install` para instalar")).toBe("Usá npm install para instalar");
  });

  it("deja el texto de los enlaces y tira la URL", () => {
    expect(toSpokenText("Ver la [documentación](https://docs.example.com/x) acá")).toBe(
      "Ver la documentación acá"
    );
  });

  it("quita los markers de títulos y listas", () => {
    const text = toSpokenText("# Título\n- uno\n- dos");
    expect(text).not.toContain("#");
    expect(text).not.toMatch(/^\s*-/m);
    expect(text).toContain("Título");
    expect(text).toContain("uno");
    expect(text).toContain("dos");
  });

  it("quita bold/italic y las barras de tabla, pero conserva los guiones de las palabras", () => {
    expect(toSpokenText("**bold** y *italic* y well-known")).toBe("bold y italic y well-known");
  });

  it("acota al máximo de caracteres", () => {
    const long = "a".repeat(5000);
    expect(toSpokenText(long)).toHaveLength(3000);
    expect(toSpokenText(long, 100)).toHaveLength(100);
  });

  it("preserva acentos y signos para SAPI", () => {
    expect(toSpokenText("¿Cómo andás, Fabián? ¡Todo bien!")).toBe("¿Cómo andás, Fabián? ¡Todo bien!");
  });
});
