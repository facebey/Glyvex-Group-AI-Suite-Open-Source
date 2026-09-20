import { useEffect, useRef, useState } from "react";
import { Plus, Trash2, Save, RotateCcw, ScanSearch } from "lucide-react";
import Section from "../components/ui/Section.jsx";
import Field from "../components/ui/Field.jsx";
import ChatSettings from "../components/ChatSettings.jsx";
import MetricsDisplaySettings from "../components/MetricsDisplaySettings.jsx";
import MetricsExportSettings from "../components/MetricsExportSettings.jsx";
import { CONFIG_SAVED_EVENT, PRESETS } from "../lib/metricsDisplay.js";
import { formInputClasses as inputClasses } from "../lib/styles.js";

const EMPTY_CONFIG = {
  app: { port: 7981, theme: "dark", language: "es" },
  hardware: { gpu_model: "", vram_gb: 0, ram_gb: 0, cpu_model: "", cpu_cores: 0 },
  backends: {
    llama_server: { binary_path: "", default_port: 8080, enabled: true },
    ollama: { binary_path: "/usr/bin/ollama", default_port: 11434, enabled: false },
    lm_studio: { binary_path: "", default_port: 1234, enabled: false },
    unsloth: { python_env: "", enabled: false },
  },
  model_dirs: [],
  last_used_model: null,
  auto_start_last: false,
  attachments: {
    max_file_mb: 16,
    max_files_per_message: 10,
    max_text_chars: 50000,
    image_tokens_estimate: 1024,
  },
  tools: {
    search_provider: "ddgs",
    region: "wt-wt",
    searxng_url: "http://127.0.0.1:8888",
    max_results: 5,
    fetch_max_chars: 8000,
    max_rounds: 5,
    allow_private_hosts: false,
  },
  stt: {
    engine: "auto",
    language: "es-AR",
    whisper_model: "base",
    whisper_device: "auto",
    whisper_compute_type: "int8",
    whisper_language: "",
  },
  display: { preset: "default", hidden: PRESETS.default },
  exports: {
    prometheus_enabled: false,
    prometheus_token: "",
    influx: { enabled: false, version: "v2", url: "http://127.0.0.1:8086" },
  },
};

/** Avisa a las páginas abiertas (Monitor, Chat, Launcher) que la config cambió. */
function notifyConfigSaved(data) {
  window.dispatchEvent(new CustomEvent(CONFIG_SAVED_EVENT, { detail: data }));
}

/**
 * Lee un stream SSE emitido por POST /api/models/scan.
 *
 * No usamos `new EventSource(...)` porque EventSource solo soporta GET
 * nativo en el browser, y este endpoint es POST (dispara el scan). En su
 * lugar leemos el body del fetch como stream y parseamos manualmente los
 * bloques "data: {...}\n\n", que es el mismo formato que EventSource
 * consumiría. `onEvent` se llama por cada evento parseado.
 */
async function consumeScanStream(signal, onEvent) {
  const res = await fetch("/api/models/scan", { method: "POST", signal });
  if (!res.ok || !res.body) {
    throw new Error("scan_failed");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const rawEvent = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const line = rawEvent.split("\n").find((l) => l.startsWith("data:"));
      if (line) {
        try {
          onEvent(JSON.parse(line.slice(5).trim()));
        } catch {
          // Evento malformado: se ignora, no debe cortar el stream.
        }
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
}

export default function Config() {
  const [config, setConfig] = useState(EMPTY_CONFIG);
  const [newDir, setNewDir] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState(null);

  const [scanning, setScanning] = useState(false);
  const [scanProgress, setScanProgress] = useState(null); // último evento "scanning"
  const [scanSummary, setScanSummary] = useState(null); // evento "complete"
  const scanAbortRef = useRef(null);

  // Cleanup: si el componente se desmonta mientras hay un scan en curso,
  // se aborta el fetch en progreso para no dejar el stream colgado.
  useEffect(() => {
    return () => {
      scanAbortRef.current?.abort();
    };
  }, []);

  async function handleScan() {
    setScanning(true);
    setScanProgress(null);
    setScanSummary(null);
    setMessage(null);

    const controller = new AbortController();
    scanAbortRef.current = controller;

    try {
      await consumeScanStream(controller.signal, (event) => {
        if (event.status === "complete") {
          setScanSummary(event);
        } else {
          setScanProgress(event);
        }
      });
    } catch (err) {
      if (err.name !== "AbortError") {
        setMessage({ type: "error", text: "Error durante el escaneo de modelos." });
      }
    } finally {
      setScanning(false);
      scanAbortRef.current = null;
    }
  }

  useEffect(() => {
    let cancelled = false;
    fetch("/api/config")
      .then((res) => res.json())
      .then((data) => {
        if (!cancelled) setConfig((prev) => ({ ...prev, ...data }));
      })
      .catch(() => {
        if (!cancelled) setMessage({ type: "error", text: "No se pudo cargar la configuración." });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function updateBackend(name, patch) {
    setConfig((prev) => ({
      ...prev,
      backends: {
        ...prev.backends,
        [name]: { ...prev.backends[name], ...patch },
      },
    }));
  }

  function addModelDir() {
    const dir = newDir.trim();
    if (!dir) return;
    if (config.model_dirs.includes(dir)) {
      setNewDir("");
      return;
    }
    setConfig((prev) => ({ ...prev, model_dirs: [...prev.model_dirs, dir] }));
    setNewDir("");
  }

  function removeModelDir(dir) {
    setConfig((prev) => ({
      ...prev,
      model_dirs: prev.model_dirs.filter((d) => d !== dir),
    }));
  }

  async function handleSave() {
    setSaving(true);
    setMessage(null);
    try {
      const res = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
      });
      if (!res.ok) throw new Error("save_failed");
      const data = await res.json();
      setConfig((prev) => ({ ...prev, ...data }));
      notifyConfigSaved(data);
      setMessage({ type: "success", text: "Configuración guardada." });
    } catch {
      setMessage({ type: "error", text: "Error al guardar la configuración." });
    } finally {
      setSaving(false);
    }
  }

  async function handleReset() {
    setSaving(true);
    setMessage(null);
    try {
      const res = await fetch("/api/config/reset", { method: "POST" });
      if (!res.ok) throw new Error("reset_failed");
      const data = await res.json();
      setConfig(data);
      notifyConfigSaved(data);
      setMessage({ type: "success", text: "Configuración restaurada a defaults." });
    } catch {
      setMessage({ type: "error", text: "Error al restaurar defaults." });
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <p className="text-glyvex-muted text-sm">Cargando configuración…</p>;
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Config</h1>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={handleReset}
            disabled={saving}
            className="flex items-center gap-2 px-3 py-2 rounded-md text-sm border border-white/10 text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-card disabled:opacity-50"
          >
            <RotateCcw size={16} />
            Restaurar defaults
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-2 px-3 py-2 rounded-md text-sm bg-glyvex-accent text-white hover:bg-glyvex-accent/90 disabled:opacity-50"
          >
            <Save size={16} />
            Guardar
          </button>
        </div>
      </div>

      {message && (
        <p
          className={
            message.type === "success"
              ? "text-sm text-emerald-400"
              : "text-sm text-red-400"
          }
        >
          {message.text}
        </p>
      )}

      <Section title="Backends">
        <Field label="llama-server — binary path">
          <input
            className={inputClasses}
            value={config.backends.llama_server.binary_path}
            onChange={(e) => updateBackend("llama_server", { binary_path: e.target.value })}
            placeholder="/opt/llama.cpp/build/bin/llama-server"
          />
        </Field>
        <Field label="Ollama — binary path">
          <input
            className={inputClasses}
            value={config.backends.ollama.binary_path}
            onChange={(e) => updateBackend("ollama", { binary_path: e.target.value })}
            placeholder="/usr/bin/ollama"
          />
        </Field>
        <Field label="LM Studio — binary path">
          <input
            className={inputClasses}
            value={config.backends.lm_studio.binary_path}
            onChange={(e) => updateBackend("lm_studio", { binary_path: e.target.value })}
            placeholder="/opt/lmstudio/lm-studio"
          />
        </Field>
      </Section>

      <Section title="Directorios de modelos">
        <div className="flex gap-2">
          <input
            className={inputClasses}
            value={newDir}
            onChange={(e) => setNewDir(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && addModelDir()}
            placeholder="/home/fabian/models"
          />
          <button
            type="button"
            onClick={addModelDir}
            className="flex items-center gap-1 px-3 py-2 rounded-md text-sm bg-glyvex-accent text-white hover:bg-glyvex-accent/90 shrink-0"
          >
            <Plus size={16} />
            Agregar
          </button>
        </div>
        {config.model_dirs.length === 0 ? (
          <p className="text-sm text-glyvex-muted">No hay directorios configurados.</p>
        ) : (
          <ul className="space-y-2">
            {config.model_dirs.map((dir) => (
              <li
                key={dir}
                className="flex items-center justify-between bg-black/30 border border-white/10 rounded-md px-3 py-2 text-sm"
              >
                <span className="truncate">{dir}</span>
                <button
                  type="button"
                  onClick={() => removeModelDir(dir)}
                  className="text-glyvex-muted hover:text-red-400 shrink-0 ml-3"
                  aria-label={`Quitar ${dir}`}
                >
                  <Trash2 size={16} />
                </button>
              </li>
            ))}
          </ul>
        )}

        <div className="border-t border-white/10 pt-4 space-y-3">
          <button
            type="button"
            onClick={handleScan}
            disabled={scanning || config.model_dirs.length === 0}
            className="flex items-center gap-2 px-3 py-2 rounded-md text-sm bg-glyvex-accent text-white hover:bg-glyvex-accent/90 disabled:opacity-50"
          >
            <ScanSearch size={16} className={scanning ? "animate-pulse" : ""} />
            {scanning ? "Escaneando…" : "Escanear ahora"}
          </button>

          {scanning && scanProgress && (
            <p className="text-sm text-glyvex-muted">
              Escaneando <span className="text-glyvex-text">{scanProgress.dir}</span> — {scanProgress.found} encontrados
              en este directorio, {scanProgress.total_so_far} en total hasta ahora.
            </p>
          )}

          {!scanning && scanSummary && (
            <p className="text-sm text-emerald-400">
              {scanSummary.total} modelos encontrados, {scanSummary.new} nuevos, {scanSummary.removed} eliminados
              ({scanSummary.duration_s}s).
            </p>
          )}
        </div>
      </Section>

      <ChatSettings config={config} onChange={setConfig} />

      <Section title="Perfil de hardware">
        <div className="grid grid-cols-2 gap-4">
          <Field label="GPU">
            <input
              className={inputClasses}
              value={config.hardware.gpu_model}
              onChange={(e) =>
                setConfig((prev) => ({
                  ...prev,
                  hardware: { ...prev.hardware, gpu_model: e.target.value },
                }))
              }
              placeholder="RTX 3090"
            />
          </Field>
          <Field label="VRAM (GB)">
            <input
              type="number"
              className={inputClasses}
              value={config.hardware.vram_gb}
              onChange={(e) =>
                setConfig((prev) => ({
                  ...prev,
                  hardware: { ...prev.hardware, vram_gb: Number(e.target.value) },
                }))
              }
            />
          </Field>
          <Field label="CPU">
            <input
              className={inputClasses}
              value={config.hardware.cpu_model}
              onChange={(e) =>
                setConfig((prev) => ({
                  ...prev,
                  hardware: { ...prev.hardware, cpu_model: e.target.value },
                }))
              }
              placeholder="i9-12900K"
            />
          </Field>
          <Field label="RAM (GB)">
            <input
              type="number"
              className={inputClasses}
              value={config.hardware.ram_gb}
              onChange={(e) =>
                setConfig((prev) => ({
                  ...prev,
                  hardware: { ...prev.hardware, ram_gb: Number(e.target.value) },
                }))
              }
            />
          </Field>
          <Field label="Núcleos CPU">
            <input
              type="number"
              className={inputClasses}
              value={config.hardware.cpu_cores}
              onChange={(e) =>
                setConfig((prev) => ({
                  ...prev,
                  hardware: { ...prev.hardware, cpu_cores: Number(e.target.value) },
                }))
              }
            />
          </Field>
        </div>
      </Section>

      <Section title="Preferencias de la app">
        <div className="grid grid-cols-2 gap-4">
          <Field label="Puerto">
            <input
              type="number"
              className={inputClasses}
              value={config.app.port}
              onChange={(e) =>
                setConfig((prev) => ({
                  ...prev,
                  app: { ...prev.app, port: Number(e.target.value) },
                }))
              }
            />
          </Field>
          <Field label="Tema">
            <select
              className={inputClasses}
              value={config.app.theme}
              onChange={(e) =>
                setConfig((prev) => ({
                  ...prev,
                  app: { ...prev.app, theme: e.target.value },
                }))
              }
            >
              <option value="dark">Oscuro</option>
              <option value="light">Claro</option>
            </select>
          </Field>
        </div>
      </Section>

      <MetricsDisplaySettings
        display={config.display}
        onChange={(display) => setConfig((prev) => ({ ...prev, display }))}
      />

      <MetricsExportSettings
        exportsConfig={config.exports}
        port={config.app?.port}
        onChange={(exportsConfig) => setConfig((prev) => ({ ...prev, exports: exportsConfig }))}
      />
    </div>
  );
}
