import { describe, expect, it } from "vitest";

import { fallbackName, safeFilename } from "../components/CodeBlock.jsx";

// El nombre del archivo descargado se decide acá, sin DOM: es la parte del
// fix CHAT-CODE-DL que puede romperse (el botón ahora siempre existe, así que
// siempre hay un nombre que generar aunque el modelo no haya nombrado el archivo).

describe("safeFilename", () => {
  it("mantiene un nombre simple", () => {
    expect(safeFilename("index.html")).toBe("index.html");
  });
  it("deja solo el basename: el nombre viene del modelo y puede traer rutas", () => {
    expect(safeFilename("src/utils/parsedor.py")).toBe("srcutilsparsedor.py");
    expect(safeFilename("C:\\tmp\\notas.md")).toBe("Ctmpnotas.md");
  });
  it("cae en un default si no hay nombre usable", () => {
    expect(safeFilename("")).toBe("archivo.txt");
    expect(safeFilename(null)).toBe("archivo.txt");
    expect(safeFilename("///")).toBe("archivo.txt");
  });
});

describe("fallbackName", () => {
  it("usa la etiqueta del lenguaje, en minúsculas y con .txt", () => {
    expect(fallbackName("HTML")).toBe("html.txt");
    expect(fallbackName("Python")).toBe("python.txt");
    expect(fallbackName("PowerShell")).toBe("powershell.txt");
  });
  it("limpia los caracteres que no son de archivo (los lenguajes con símbolos)", () => {
    expect(fallbackName("C++")).toBe("c.txt");
    expect(fallbackName("Objective-C")).toBe("objective_c.txt");
  });
  it("traduce: la etiqueta plana en español da un nombre en español", () => {
    expect(fallbackName("texto")).toBe("texto.txt");
  });
  it("nunca devuelve un nombre vacío", () => {
    expect(fallbackName("")).toBe("bloque.txt");
    expect(fallbackName("###")).toBe("bloque.txt");
    expect(fallbackName(undefined)).toBe("bloque.txt");
  });
});
