/**
 * wavEncoder.js — Captura de PCM 16 kHz mono y armado de WAV en el navegador.
 *
 * whisper-cli solo acepta WAV/FLAC/MP3/OGG de entrada, y el audio que manda
 * MediaRecorder (webm/opus) no lo decodifica: por eso el motor whispercpp usa
 * esta captura en vez del MediaRecorder. ScriptProcessor está deprecado en
 * los navegadores pero sigue disponible en todos (incluido el WebView de
 * Tauri), y para grabar 10–30 s de dictado su overhead es irrelevante.
 *
 * Se pide 16 kHz (la nativa de Whisper). Si el WebView no respeta el
 * sampleRate y queda a 44.1/48 kHz, el WAV lleva la tasa real en el header y
 * whisper-cli resamplea solo (verificado en el POC T2.1 con un WAV a 22 kHz).
 */

const TARGET_SAMPLE_RATE = 16000;
export function createCapture16k(stream) {
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  const context = new AudioCtx({ sampleRate: TARGET_SAMPLE_RATE });
  const source = context.createMediaStreamSource(stream);
  const processor = context.createScriptProcessor(4096, 1, 1);
  const chunks = [];
  let actualRate = context.sampleRate;

  processor.onaudioprocess = (event) => {
    // copyOf: onaudioprocess reutiliza los buffers entre callbacks.
    chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
  };
  source.connect(processor);
  // En Chrome el processor debe conectar a destination para disparar el
  // callback; con gain 0 no se oye eco del micrófono.
  const silence = context.createGain();
  silence.gain.value = 0;
  processor.connect(silence);
  silence.connect(context.destination);

  return {
    get sampleRate() {
      return actualRate;
    },
    stop() {
      processor.onaudioprocess = null;
      source.disconnect();
      processor.disconnect();
      silence.disconnect();
      context.close().catch(() => {});
      return toWavBlob(chunks, actualRate);
    },
    cancel() {
      processor.onaudioprocess = null;
      source.disconnect();
      processor.disconnect();
      silence.disconnect();
      context.close().catch(() => {});
    },
  };
}

export function toWavBlob(chunks, sampleRate) {
  let total = 0;
  for (const chunk of chunks) total += chunk.length;
  if (total === 0) return new Blob([], { type: "audio/wav" });

  const data = new Int16Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    data.set(chunk.map(clampToPcm16), offset);
    offset += chunk.length;
  }

  const buffer = new ArrayBuffer(44 + data.byteLength);
  const view = new DataView(buffer);
  writeString(view, 0, "RIFF");
  view.setUint32(4, 36 + data.byteLength, true);
  writeString(view, 8, "WAVE");
  writeString(view, 12, "fmt ");
  view.setUint32(16, 16, true); // fmt chunk size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate (16 bits mono)
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  writeString(view, 36, "data");
  view.setUint32(40, data.byteLength, true);
  new Uint8Array(buffer, 44).set(new Uint8Array(data.buffer));

  return new Blob([buffer], { type: "audio/wav" });
}

function clampToPcm16(sample) {
  const s = Math.max(-1, Math.min(1, sample));
  return s < 0 ? s * 0x8000 : s * 0x7fff;
}

function writeString(view, offset, value) {
  for (let i = 0; i < value.length; i += 1) {
    view.setUint8(offset + i, value.charCodeAt(i));
  }
}
