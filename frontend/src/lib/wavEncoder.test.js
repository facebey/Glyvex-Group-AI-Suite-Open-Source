import { describe, expect, it } from "vitest";
import { toWavBlob } from "./wavEncoder.js";

async function readHeader(blob) {
  const buffer = await blob.arrayBuffer();
  return new DataView(buffer);
}

function readTag(view, offset) {
  return String.fromCharCode(
    view.getUint8(offset),
    view.getUint8(offset + 1),
    view.getUint8(offset + 2),
    view.getUint8(offset + 3)
  );
}

describe("toWavBlob", () => {
  it("devuelve un Blob vacío de audio/wav sin muestras", async () => {
    const blob = toWavBlob([], 16000);
    expect(blob.type).toBe("audio/wav");
    expect(blob.size).toBe(0);
  });

  it("escribe el header RIFF/WAVE/fmt/data correcto para 16 kHz mono", async () => {
    const samples = 100;
    const view = await readHeader(toWavBlob([new Float32Array(samples)], 16000));

    expect(readTag(view, 0)).toBe("RIFF");
    expect(view.getUint32(4, true)).toBe(36 + samples * 2);
    expect(readTag(view, 8)).toBe("WAVE");
    expect(readTag(view, 12)).toBe("fmt ");
    expect(view.getUint32(16, true)).toBe(16);
    expect(view.getUint16(20, true)).toBe(1); // PCM
    expect(view.getUint16(22, true)).toBe(1); // mono
    expect(view.getUint32(24, true)).toBe(16000);
    expect(view.getUint32(28, true)).toBe(32000); // byte rate
    expect(view.getUint16(32, true)).toBe(2); // block align
    expect(view.getUint16(34, true)).toBe(16); // bits por muestra
    expect(readTag(view, 36)).toBe("data");
    expect(view.getUint32(40, true)).toBe(samples * 2);
  });

  it("lleva la sample rate real que le pasa el capturador", async () => {
    const view = await readHeader(toWavBlob([new Float32Array(10)], 48000));
    expect(view.getUint32(24, true)).toBe(48000);
    expect(view.getUint32(28, true)).toBe(96000);
  });

  it("convierte Float32 [-1, 1] a PCM 16 bits con los extremos exactos", async () => {
    const blob = toWavBlob([new Float32Array([-1, 1, 0])], 16000);
    const view = await readHeader(blob);
    expect(view.getInt16(44, true)).toBe(-32768);
    expect(view.getInt16(46, true)).toBe(32767);
    expect(view.getInt16(48, true)).toBe(0);
  });

  it("recorta las muestras fuera de rango en vez de desbordar", async () => {
    const blob = toWavBlob([new Float32Array([-2, 2, 3.5])], 16000);
    const view = await readHeader(blob);
    expect(view.getInt16(44, true)).toBe(-32768);
    expect(view.getInt16(46, true)).toBe(32767);
    expect(view.getInt16(48, true)).toBe(32767);
  });

  it("concatena los chunks en orden y el tamaño total es 44 + 2 por muestra", async () => {
    const blob = toWavBlob([new Float32Array([0.25, -0.25]), new Float32Array([1])], 16000);
    expect(blob.size).toBe(44 + 3 * 2);
    const view = await readHeader(blob);
    expect(view.getInt16(44, true)).toBe(8191); // 0.25 * 0x7fff truncado
    expect(view.getInt16(46, true)).toBe(-8192); // -0.25 * 0x8000
    expect(view.getInt16(48, true)).toBe(32767);
  });
});
