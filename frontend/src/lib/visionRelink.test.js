// Tests puros del re-ligue de visión de plantillas (F-4 TPL-VISION-RELINK).
// Igual que specModes.test.js: la lógica vive en lib/ para testearla sin React.
import { describe, expect, it } from "vitest";
import {
  resolveVisionTemplate,
  templateVisionIntent,
  visionIntentFromState,
} from "./visionRelink.js";

describe("templateVisionIntent", () => {
  it("lee la intención nueva de los params de la plantilla", () => {
    const intent = templateVisionIntent({ vision_intent: { enabled: true, manual_path: "D:\\mm\\proj.gguf" } });
    expect(intent).toEqual({ enabled: true, manual_path: "D:\\mm\\proj.gguf" });
  });

  it("un vision_intent desactivado manda: no se re-liga visión", () => {
    const intent = templateVisionIntent({ vision_intent: { enabled: false, manual_path: "" }, toggles: { mmproj: true } });
    expect(intent).toEqual({ enabled: false, manual_path: "" });
  });

  it("templates previos a F-4: el snapshot toggles.mmproj=true se lee como intención legacy, sin path", () => {
    // Es exactamente lo que guardan hoy las plantillas "-Vision" reales
    // (medido en hw_templates: toggles.mmproj=true, ningún mmproj_path).
    const intent = templateVisionIntent({ n_ctx: 8192, toggles: { mmproj: true, draft: false } });
    expect(intent).toEqual({ enabled: true, manual_path: "" });
  });

  it("template viejo sin toggles (anterior a P1.5 F5) queda apagado: no se inventa una preferencia", () => {
    expect(templateVisionIntent({ n_ctx: 8192 })).toEqual({ enabled: false, manual_path: "" });
    expect(templateVisionIntent(undefined)).toEqual({ enabled: false, manual_path: "" });
    expect(templateVisionIntent(null)).toEqual({ enabled: false, manual_path: "" });
  });
});

describe("resolveVisionTemplate", () => {
  it("intención apagada -> modo none, sin path", () => {
    expect(resolveVisionTemplate({ enabled: false, manual_path: "" }, { embeddedVision: true, mmprojPaths: ["a"] }))
      .toEqual({ enabled: false, path: "", mode: "none" });
  });

  it("sin path guardado, engancha el sidecar detectado para ESTE modelo (el path del inventario, no uno propio)", () => {
    const r = resolveVisionTemplate({ enabled: true, manual_path: "" }, { mmprojPaths: ["D:\\modelos\\m-mmproj.gguf"] });
    expect(r).toEqual({ enabled: true, path: "D:\\modelos\\m-mmproj.gguf", mode: "sidecar" });
  });

  it("un path elegido a mano que sigue siendo archivo del grupo manda sobre el primer sidecar", () => {
    const paths = ["D:\\m\\primero-mmproj.gguf", "D:\\m\\elegido-mmproj.gguf"];
    const r = resolveVisionTemplate({ enabled: true, manual_path: "D:\\m\\elegido-mmproj.gguf" }, { mmprojPaths: paths });
    expect(r).toEqual({ enabled: true, path: "D:\\m\\elegido-mmproj.gguf", mode: "sidecar" });
  });

  it("visión embebida sin archivo: toggle ON y modo embedded (no se emite --mmproj)", () => {
    const r = resolveVisionTemplate({ enabled: true, manual_path: "" }, { embeddedVision: true });
    expect(r).toEqual({ enabled: true, path: "", mode: "embedded" });
  });

  // Precedencia documentada en el lib: elección explícita > embebido > primer
  // sidecar detectado. Es la misma lógica de preferencia que usa la card.
  it("la elección explícita gana aunque el modelo también tenga visión embebida", () => {
    const r = resolveVisionTemplate(
      { enabled: true, manual_path: "D:\\m\\elegido-mmproj.gguf" },
      { embeddedVision: true, mmprojPaths: ["D:\\m\\elegido-mmproj.gguf"] },
    );
    expect(r).toEqual({ enabled: true, path: "D:\\m\\elegido-mmproj.gguf", mode: "sidecar" });
  });

  it("la visión embebida gana sobre un sidecar genérico cuando no hay elección explícita", () => {
    const r = resolveVisionTemplate({ enabled: true, manual_path: "" }, { embeddedVision: true, mmprojPaths: ["D:\\m\\x-mmproj.gguf"] });
    expect(r).toEqual({ enabled: true, path: "", mode: "embedded" });
  });

  it("un path manual que ya no existe se conserva tal cual (mode manual): no se inventa ni se reemplaza", () => {
    const r = resolveVisionTemplate({ enabled: true, manual_path: "D:\\borrado\\mmproj.gguf" }, { mmprojPaths: [] });
    expect(r).toEqual({ enabled: true, path: "D:\\borrado\\mmproj.gguf", mode: "manual" });
  });

  it("la plantilla pide visión pero el modelo no ofrece ninguna vía: OFF, sin path inventado (y sin aviso)", () => {
    const r = resolveVisionTemplate({ enabled: true, manual_path: "" }, { embeddedVision: false, mmprojPaths: [] });
    expect(r).toEqual({ enabled: false, path: "", mode: "none" });
  });
});

describe("visionIntentFromState", () => {
  it("toggle OFF: se guarda enabled=false y nada más", () => {
    expect(visionIntentFromState({ enabled: false, mmprojPath: "X:\\olvidado.gguf" }))
      .toEqual({ enabled: false, manual_path: "" });
  });

  it("el path detectado por el escaneo NO se guarda: al aplicar se re-liga (la receta no queda atada a la ruta)", () => {
    const intent = visionIntentFromState({
      enabled: true,
      mmprojPath: "D:\\modelos\\m-mmproj.gguf",
      detectedPaths: ["D:\\modelos\\m-mmproj.gguf"],
    });
    expect(intent).toEqual({ enabled: true, manual_path: "" });
  });

  it("un path distinto del detectado (elección manual o entre varios sidecars) SÍ se guarda", () => {
    const intent = visionIntentFromState({
      enabled: true,
      mmprojPath: "D:\\modelos\\otro-mmproj.gguf",
      detectedPaths: ["D:\\modelos\\m-mmproj.gguf"],
    });
    expect(intent).toEqual({ enabled: true, manual_path: "D:\\modelos\\otro-mmproj.gguf" });
  });

  it("visión encendida sin path (embebida): enabled=true con manual_path vacío, distinguible de OFF", () => {
    expect(visionIntentFromState({ enabled: true, mmprojPath: "" }))
      .toEqual({ enabled: true, manual_path: "" });
  });

  it("round-trip: una receta guardada con el sidecar detectado se re-liga a ese mismo archivo en otro equipo", () => {
    const saved = visionIntentFromState({
      enabled: true,
      mmprojPath: "D:\\modelos\\m-mmproj.gguf",
      detectedPaths: ["D:\\modelos\\m-mmproj.gguf"],
    });
    // Al aplicar, el escaneo de ESTE modelo ofrece la ruta (puede ser otra carpeta).
    const r = resolveVisionTemplate(templateVisionIntent({ vision_intent: saved }), { mmprojPaths: ["D:\\otra-carpeta\\m-mmproj.gguf"] });
    expect(r.mode).toBe("sidecar");
    expect(r.path).toBe("D:\\otra-carpeta\\m-mmproj.gguf");
  });
});
