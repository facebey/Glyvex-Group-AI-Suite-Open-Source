// frontend/src/pages/Launcher.jsx
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Eye,
  Search,
  ArrowUpDown,
  Play,
  Square,
  RotateCw,
  Save,
  Cpu,
  Download,
  Gauge as GaugeIcon,
  Sliders,
  SlidersHorizontal,
  Settings,
  Puzzle,
  BrainCircuit,
  Server,
  LayoutGrid,
  List as ListIcon,
  ChevronDown,
  ChevronRight,
  Trash2,
  Terminal,
  Copy,
  Check,
} from "lucide-react";
import { useLlmStream } from "../hooks/useLlmStream.js";
import { useDisplay } from "../lib/metricsDisplay.js";

// Espejo de SAMPLING_PRESETS de backend/launcher.py. Vive acá para poder
// previsualizar los valores sin round-trip; si cambian en el backend, hay
// que actualizarlos también acá (o pedirlos a /api/launcher/sampling-presets).
// "instruct" usa penalties moderados: repeat/presence 1.5 degeneran la
// salida en la familia Qwen (especialmente código).
const SAMPLING_PRESETS = {
  thinking: {
    temperature: 1.0,
    top_p: 0.95,
    top_k: 20,
    min_p: 0.0,
    presence_penalty: 0.0,
    repeat_penalty: 1.0,
  },
  instruct: {
    temperature: 0.7,
    top_p: 0.80,
    top_k: 20,
    min_p: 0.0,
    presence_penalty: 0.3,
    repeat_penalty: 1.1,
  },
};

const SAMPLING_PRESET_LABELS = {
  thinking: "🧠 Thinking",
  instruct: "💬 Instruct",
  custom: "⚙️ Custom",
};

const DEFAULT_LAUNCH_CONFIG = {
  backend: "llama_server",
  n_ctx: 65536,
  // 2048 (default de llama.cpp) rinde bastante más en prompt processing
  // que 512 en GPUs anchas (medido: pp2048 ~1400 t/s con batch 2048).
  n_batch: 2048,
  n_ubatch: 512,
  n_gpu_layers: -1,
  gpu_mode: "gpu_only",
  cache_type_k: "q4_0",
  cache_type_v: "q4_0",
  flash_attn: true,
  load_mode: "auto",
  mtp_draft_model: "",
  mtp_embedded: false,
  n_draft: 5,
  // q8_0 ahorra ~50% de VRAM del draft vs el default f16 de llama-server,
  // con aceptación prácticamente idéntica. "" = no pasar el flag.
  cache_type_k_draft: "q8_0",
  cache_type_v_draft: "q8_0",
  mmproj_path: "",
  lora_path: "",
  lora_scale: 1.0,
  thinking_enabled: false,
  budget_tokens: 8192,
  sampling_preset: "instruct",
  temperature: 0.7,
  top_p: 0.80,
  top_k: 20,
  min_p: 0.0,
  presence_penalty: 0.3,
  repeat_penalty: 1.1,
  rope_freq_base: 0,
  rope_scaling_type: "none",
  yarn_ext_factor: -1,
  numa: false,
  no_kv_offload: false,
  cache_reuse: 0,
  defrag_thold: -1,
  grp_attn_n: 1,
  grp_attn_w: 512,
  jinja: true,
  reasoning_effort: "none",
  // -- Checkpoints de contexto y memoria (build 11003+) ----------------
  // Cada checkpoint cuesta ~150 MiB de VRAM (estado recurrente SSM).
  // Defaults de la build: 32 / 8192 -> hasta 16 en un ctx de 128k (~2.4 GiB).
  ctx_checkpoints: 8,
  checkpoint_min_step: 16384,
  // Prompt cache en RAM del sistema (no VRAM). 0 = desactivado.
  cache_ram_mib: 8192,
  // Margen de VRAM a reservar por dispositivo (0 = off).
  fit_target_mib: 0,
  no_reasoning_preserve: false,
  kv_unified: false,
  // -- VRAM / multi-modelo (build 11009) --------------------------------
  kv_unified_per_slot: 0,
  sleep_idle_seconds: 0,
  warmup: true,
  lazy_mode: "auto",
  // Token budget nativo del server para el reasoning (-1 = sin límite).
  reasoning_budget: -1,
  // -- P1.5: toggles por flag -------------------------------------------
  // fit = "ajusta los args sin fijar a la VRAM" (default on en la build).
  fit: true,
  // Modo automático (F4): comando estricto, solo modelo + puerto.
  auto_mode: false,
  host: "127.0.0.1",
  port: 8080,
  n_threads: -1,
  n_parallel: 1,
  api_key: "",
  log_file: "data/logs/llama-server.log",
};

// P1.5: un toggle por grupo de flags. OFF = el payload envía null = el
// builder no emite el flag = llama.cpp usa su default de build (permite
// benchmarks default-vs-fijado). `flag` es el que se chequea contra la
// disponibilidad del probe (/backend-info): ausente -> griseado.
const TOGGLE_GROUPS = {
  n_ctx: { flag: "--ctx-size", fields: ["n_ctx"] },
  n_batch: { flag: "--batch-size", fields: ["n_batch"] },
  // Extensión del alcance P1.5 (2026-09-20).
  n_ubatch: { flag: "--ubatch-size", fields: ["n_ubatch"] },
  cache_type_k: { flag: "--cache-type-k", fields: ["cache_type_k"] },
  cache_type_v: { flag: "--cache-type-v", fields: ["cache_type_v"] },
  mmproj: { flag: "--mmproj", fields: ["mmproj_path"] },
  rope: { flag: "--rope-scaling", fields: ["rope_scaling_type", "rope_freq_base", "yarn_ext_factor"] },
  cache_reuse: { flag: "--cache-reuse", fields: ["cache_reuse"] },
  defrag_thold: { flag: "--defrag-thold", fields: ["defrag_thold"] },
  grp_attn: { flag: "--grp-attn-n", fields: ["grp_attn_n", "grp_attn_w"] },
  cache_ram: { flag: "--cache-ram", fields: ["cache_ram_mib"] },
  fit: { flag: "--fit", fields: ["fit"] },
  fit_target: { flag: "--fit-target", fields: ["fit_target_mib"] },
  checkpoint_min_step: { flag: "--checkpoint-min-step", fields: ["checkpoint_min_step"] },
  ctx_checkpoints: { flag: "--ctx-checkpoints", fields: ["ctx_checkpoints"] },
  n_parallel: { flag: "--parallel", fields: ["n_parallel"] },
  n_threads: { flag: "--threads", fields: ["n_threads"] },
};

const DEFAULT_TOGGLES = Object.fromEntries(Object.keys(TOGGLE_GROUPS).map((k) => [k, true]));

// La config serializada de un proceso vivo trae null justo donde el toggle
// estaba OFF: se deduce el estado para restaurarlo al re-montar.
function togglesFromConfig(cfg) {
  const out = {};
  for (const [id, group] of Object.entries(TOGGLE_GROUPS)) {
    const known = group.fields.filter((f) => f in cfg);
    if (known.length === 0) continue;
    out[id] = !known.some((f) => cfg[f] === null);
  }
  return out;
}

const TEMPLATE_FIELDS = [
  "auto_mode",
  "n_ctx", "n_batch", "n_ubatch", "n_gpu_layers", "gpu_mode",
  "cache_type_k", "cache_type_v", "flash_attn", "load_mode",
  "n_draft", "n_parallel", "cache_type_k_draft", "cache_type_v_draft",
  // Razonamiento
  "thinking_enabled", "budget_tokens", "no_reasoning_preserve", "reasoning_budget",
  // Sampling
  "sampling_preset", "temperature", "top_p", "top_k",
  "min_p", "presence_penalty", "repeat_penalty",
  // Checkpoints / memoria (build 11003+)
  "ctx_checkpoints", "checkpoint_min_step", "cache_ram_mib",
  "fit_target_mib", "kv_unified",
  // VRAM / multi-modelo (build 11009)
  "kv_unified_per_slot", "sleep_idle_seconds", "warmup", "lazy_mode",
  // Parámetros avanzados
  "rope_freq_base", "rope_scaling_type", "yarn_ext_factor",
  "numa", "no_kv_offload", "cache_reuse", "defrag_thold",
  "grp_attn_n", "grp_attn_w", "jinja", "reasoning_effort",
];

const N_CTX_PRESETS = [4096, 8192, 16384, 32768, 65536, 131072, 262144];
const N_CTX_LABELS = {
  4096: "4K",
  8192: "8K",
  16384: "16K",
  32768: "32K",
  65536: "64K",
  131072: "128K",
  262144: "256K",
};
const BUDGET_PRESETS = [1024, 4096, 8192, 16384];
// bf16 entra (está en FA_QUANTS de la build); q4_1 sale: con Flash
// Attention activado el kernel solo acepta q4_0/q4_0, q8_0/q8_0,
// f16/f16 y bf16/bf16.
const CACHE_TYPES = ["f16", "bf16", "q8_0", "q4_0"];
// Reemplaza a los viejos toggles use_mlock/use_mmap: el binario los unificó
// en --load-mode (--mlock/--no-mmap ya no existen en builds recientes).
const LOAD_MODE_OPTIONS = ["auto", "none", "mmap", "mlock", "mmap+mlock", "dio"];
const ROPE_SCALING_TYPES = ["none", "linear", "yarn"];
// Semáforo del estimador de VRAM (backend/vram_estimate.py).
const VRAM_STATE_DOT = {
  comodo: "bg-emerald-400",
  justo: "bg-amber-400",
  no_cabe: "bg-red-400",
  unknown: "bg-white/30",
};

const STATE_BADGE = {
  starting: { label: "STARTING", classes: "bg-amber-500/15 text-amber-400 border-amber-500/30" },
  running: { label: "RUNNING", classes: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30" },
  stopped: { label: "STOPPED", classes: "bg-white/5 text-glyvex-muted border-white/10" },
  error: { label: "ERROR", classes: "bg-red-500/15 text-red-400 border-red-500/30" },
};

function wsUrlFor(path) {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}${path}`;
}

function Panel({ icon: Icon, title, children }) {
  return (
    <div className="bg-glyvex-card rounded-lg border border-white/10 p-5">
      <h3 className="flex items-center gap-2 text-sm font-medium text-glyvex-muted uppercase tracking-wide mb-4">
        <Icon size={14} />
        {title}
      </h3>
      <div className="space-y-4">{children}</div>
    </div>
  );
}

function Field({ label, children, hint }) {
  return (
    <label className="block">
      <span className="block text-sm text-glyvex-muted mb-1">{label}</span>
      {children}
      {hint && <span className="block text-xs text-glyvex-muted/70 mt-1">{hint}</span>}
    </label>
  );
}

const inputClasses =
  "w-full bg-black/30 border border-white/10 rounded-md px-3 py-2 text-sm " +
  "text-glyvex-text placeholder:text-glyvex-muted/60 focus:outline-none " +
  "focus:ring-2 focus:ring-glyvex-accent/60";

// El wrapper es un <span class="flex"> (no un <label>) para poder anidarlo
// dentro de otros labels/rows sin generar HTML inválido.
function Toggle({ label, checked, onChange, disabled = false, title }) {
  function handleToggle() {
    if (disabled) return;
    onChange(!checked);
  }

  return (
    <span
      role="switch"
      aria-checked={checked}
      aria-disabled={disabled}
      title={title}
      tabIndex={disabled ? -1 : 0}
      onClick={handleToggle}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handleToggle(); } }}
      className={
        "flex items-center gap-3 select-none " +
        (disabled ? "opacity-50 cursor-not-allowed " : "cursor-pointer ") +
        (label ? "justify-between" : "")
      }
    >
      {label && <span className="text-sm text-glyvex-text">{label}</span>}
      <span
        className={
          "relative inline-flex h-5 w-9 items-center rounded-full transition-colors shrink-0 " +
          (checked ? "bg-glyvex-accent" : "bg-white/10")
        }
      >
        <span
          className={
            "inline-block h-4 w-4 transform rounded-full bg-white transition-transform " +
            (checked ? "translate-x-4" : "translate-x-0.5")
          }
        />
      </span>
    </span>
  );
}

function SamplingSlider({ label, value, min, max, step, digits = 2, onChange }) {
  return (
    <Field label={`${label}: ${Number(value).toFixed(digits)}`}>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-glyvex-accent"
      />
    </Field>
  );
}

const SORT_OPTIONS = [
  { value: "name", label: "Nombre" },
  { value: "size_gb", label: "Tamaño" },
  { value: "family", label: "Familia" },
];

// ---------------------------------------------------------------------------
// Comando de lanzamiento (preview antes de LAUNCH + argv real del proceso)
// ---------------------------------------------------------------------------

/** Cita un argumento para poder pegarlo tal cual en una shell POSIX. */
function shellQuote(arg) {
  if (arg === "") return "''";
  // Sin metacaracteres: va crudo. Con ellos: comillas simples, escapando las
  // simples internas (así el JSON de --chat-template-kwargs sobrevive).
  if (/^[A-Za-z0-9_@%+=:,./-]+$/.test(arg)) return arg;
  return "'" + arg.replace(/'/g, `'\\''`) + "'";
}

/**
 * Parte el argv en líneas legibles: el binario solo, y después cada flag con
 * su valor. Es solo presentación; el texto que se copia sale de shellQuote.
 */
function argvToLines(argv) {
  if (!argv?.length) return [];
  const lines = [{ flag: argv[0], value: null }];
  for (let i = 1; i < argv.length; i++) {
    const arg = argv[i];
    if (arg.startsWith("-")) {
      const next = argv[i + 1];
      if (next != null && !next.startsWith("--")) {
        lines.push({ flag: arg, value: next });
        i++;
      } else {
        lines.push({ flag: arg, value: null });
      }
    } else {
      lines.push({ flag: arg, value: null });
    }
  }
  return lines;
}

/**
 * Bloque de comando con copiar al portapapeles. `argv` es la lista cruda;
 * la api-key ya viene enmascarada por el backend (mask_command).
 */
function CommandBlock({ argv, emptyHint }) {
  const [copied, setCopied] = useState(false);
  const lines = useMemo(() => argvToLines(argv), [argv]);
  const oneLiner = useMemo(() => (argv || []).map(shellQuote).join(" "), [argv]);

  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(oneLiner);
    } catch {
      // Sin permiso de portapapeles (o contexto no seguro): fallback manual.
      const ta = document.createElement("textarea");
      ta.value = oneLiner;
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); } catch { /* el usuario copia a mano */ }
      document.body.removeChild(ta);
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }, [oneLiner]);

  if (!argv?.length) {
    return <p className="text-xs text-glyvex-muted">{emptyHint || "Sin comando disponible."}</p>;
  }

  return (
    <div className="relative rounded-md border border-white/10 bg-black/40">
      <button
        type="button"
        onClick={copy}
        title="Copiar el comando completo en una línea"
        className="absolute top-2 right-2 z-10 flex items-center gap-1 px-2 py-1 rounded text-xs border border-white/10 bg-glyvex-card text-glyvex-muted hover:text-glyvex-text"
      >
        {copied ? <Check size={12} /> : <Copy size={12} />}
        {copied ? "Copiado" : "Copiar"}
      </button>
      {/* overflow-x-auto: en ventanas angostas el comando scrollea dentro de
          su caja en vez de estirar el panel entero. */}
      <pre className="overflow-x-auto p-3 pr-24 text-xs leading-relaxed font-mono text-glyvex-text">
        {lines.map((l, i) => (
          <div key={i} className="whitespace-pre">
            {i === 0 ? (
              <span className="text-glyvex-accent">{l.flag}</span>
            ) : (
              <>
                {"  "}
                <span className="text-sky-400">{l.flag}</span>
                {l.value != null && <span className="text-glyvex-muted"> {l.value}</span>}
              </>
            )}
          </div>
        ))}
      </pre>
    </div>
  );
}

function BackendBadge({ backend }) {
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs bg-black/30 border border-white/10 text-glyvex-muted">
      {backend}
    </span>
  );
}

function ModelTable({ modelList, selectedId, onSelect }) {
  const [search, setSearch] = useState("");
  const [familyFilter, setFamilyFilter] = useState("all");
  const [quantFilter, setQuantFilter] = useState("all");
  const [backendFilter, setBackendFilter] = useState("all");
  const [sortBy, setSortBy] = useState("name");
  const [sortDir, setSortDir] = useState("asc");

  const families = useMemo(
    () => [...new Set(modelList.map((m) => m.family).filter(Boolean))].sort(),
    [modelList]
  );
  const quantizations = useMemo(
    () => [...new Set(modelList.map((m) => m.quantization).filter(Boolean))].sort(),
    [modelList]
  );
  const backends = useMemo(
    () => [...new Set(modelList.flatMap((m) => m.compatible_backends))].sort(),
    [modelList]
  );

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    let result = modelList.filter((m) => {
      if (query && !m.name.toLowerCase().includes(query)) return false;
      if (familyFilter !== "all" && m.family !== familyFilter) return false;
      if (quantFilter !== "all" && m.quantization !== quantFilter) return false;
      if (backendFilter !== "all" && !m.compatible_backends.includes(backendFilter)) return false;
      return true;
    });
    result = [...result].sort((a, b) => {
      let cmp = 0;
      if (sortBy === "size_gb") { cmp = a.size_gb - b.size_gb; }
      else {
        const av = (a[sortBy] || "").toString().toLowerCase();
        const bv = (b[sortBy] || "").toString().toLowerCase();
        cmp = av.localeCompare(bv);
      }
      return sortDir === "asc" ? cmp : -cmp;
    });
    return result;
  }, [modelList, search, familyFilter, quantFilter, backendFilter, sortBy, sortDir]);

  function toggleSort(field) {
    if (sortBy === field) setSortDir((prev) => (prev === "asc" ? "desc" : "asc"));
    else { setSortBy(field); setSortDir("asc"); }
  }

  const selectClasses =
    "bg-black/30 border border-white/10 rounded-md px-2 py-1.5 text-sm " +
    "text-glyvex-text focus:outline-none focus:ring-2 focus:ring-glyvex-accent/60";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[200px]">
          <Search size={16} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-glyvex-muted" />
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Buscar por nombre…"
            className="w-full bg-black/30 border border-white/10 rounded-md pl-8 pr-3 py-1.5 text-sm text-glyvex-text placeholder:text-glyvex-muted/60 focus:outline-none focus:ring-2 focus:ring-glyvex-accent/60" />
        </div>
        <select className={selectClasses} value={familyFilter} onChange={(e) => setFamilyFilter(e.target.value)}>
          <option value="all">Todas las familias</option>
          {families.map((f) => <option key={f} value={f}>{f}</option>)}
        </select>
        <select className={selectClasses} value={quantFilter} onChange={(e) => setQuantFilter(e.target.value)}>
          <option value="all">Todas las cuantizaciones</option>
          {quantizations.map((q) => <option key={q} value={q}>{q}</option>)}
        </select>
        <select className={selectClasses} value={backendFilter} onChange={(e) => setBackendFilter(e.target.value)}>
          <option value="all">Todos los backends</option>
          {backends.map((b) => <option key={b} value={b}>{b}</option>)}
        </select>
        <select className={selectClasses} value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
          {SORT_OPTIONS.map((opt) => <option key={opt.value} value={opt.value}>Ordenar por {opt.label}</option>)}
        </select>
      </div>
      {modelList.length === 0 ? (
        <p className="text-sm text-glyvex-muted">No hay modelos en el inventario todavía.</p>
      ) : filtered.length === 0 ? (
        <p className="text-sm text-glyvex-muted">Ningún modelo coincide con los filtros.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-white/10">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-glyvex-card text-left text-glyvex-muted">
                <th className="px-3 py-2 font-medium"><button type="button" onClick={() => toggleSort("name")} className="flex items-center gap-1 hover:text-glyvex-text">Nombre <ArrowUpDown size={12} /></button></th>
                <th className="px-3 py-2 font-medium"><button type="button" onClick={() => toggleSort("family")} className="flex items-center gap-1 hover:text-glyvex-text">Familia <ArrowUpDown size={12} /></button></th>
                <th className="px-3 py-2 font-medium">Parámetros</th>
                <th className="px-3 py-2 font-medium">Cuantización</th>
                <th className="px-3 py-2 font-medium"><button type="button" onClick={() => toggleSort("size_gb")} className="flex items-center gap-1 hover:text-glyvex-text">Tamaño <ArrowUpDown size={12} /></button></th>
                <th className="px-3 py-2 font-medium">Formato</th>
                <th className="px-3 py-2 font-medium">Backends</th>
                <th className="px-3 py-2 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((m) => (
                <tr key={m.id} className={"border-t border-white/10 " + (m.id === selectedId ? "bg-glyvex-accent/10" : "hover:bg-white/5")}>
                  <td className="px-3 py-2"><div className="flex items-center gap-2">{m.has_mmproj && <Eye size={14} className="text-glyvex-accent shrink-0" />}<span className="truncate">{m.name}</span></div></td>
                  <td className="px-3 py-2 text-glyvex-muted">{m.family || "—"}</td>
                  <td className="px-3 py-2 text-glyvex-muted">{m.parameters || "—"}</td>
                  <td className="px-3 py-2 text-glyvex-muted">{m.quantization || "—"}</td>
                  <td className="px-3 py-2 text-glyvex-muted">{m.size_gb} GB</td>
                  <td className="px-3 py-2 text-glyvex-muted">{m.format}</td>
                  <td className="px-3 py-2"><div className="flex flex-wrap gap-1">{m.compatible_backends.map((b) => <BackendBadge key={b} backend={b} />)}</div></td>
                  <td className="px-3 py-2">
                    <button type="button" onClick={() => onSelect(m.id)}
                      className={"px-3 py-1 rounded-md text-xs shrink-0 " + (m.id === selectedId ? "bg-glyvex-accent text-white" : "border border-white/10 text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-card")}>
                      {m.id === selectedId ? "Seleccionado" : "Seleccionar"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// Etiqueta que aparece al lado de un checkbox cuando la capacidad la detectó
// el scanner leyendo el header del GGUF, no el usuario a mano.
function DetectedBadge({ children }) {
  return (
    <span className="px-1.5 py-0.5 rounded text-[10px] bg-glyvex-accent/15 text-glyvex-accent border border-glyvex-accent/30 shrink-0">
      {children}
    </span>
  );
}

// Selector de origen para una capacidad que puede venir de más de un lado:
// horneada en el propio modelo, un archivo aparte detectado en la carpeta,
// o un path manual que el usuario tipea. `options` es una lista de
// [key, label] — armada por el caller según lo que haya disponible para
// ESTE modelo puntual (siempre incluye "manual" como salida de emergencia).
function SourcePicker({ value, options, onChange }) {
  return (
    <div className="flex gap-2 mb-1.5 text-xs flex-wrap">
      {options.map(([key, label]) => (
        <button key={key} type="button" onClick={() => onChange(key)}
          className={
            "px-2 py-1 rounded border " +
            (value === key
              ? "border-glyvex-accent text-glyvex-accent bg-glyvex-accent/10"
              : "border-white/10 text-glyvex-muted hover:text-glyvex-text")
          }>
          {label}
        </button>
      ))}
    </div>
  );
}

function GroupCard({ group, onSelect }) {
  const [expanded, setExpanded] = useState(false);
  const [selectedBaseId, setSelectedBaseId] = useState(group.base_models[0]?.id ?? null);
  const [useMtp, setUseMtp] = useState(false);
  const [mtpSource, setMtpSource] = useState("none");       // "embedded" | "sidecar" | "manual" | "none"
  const [selectedMtpId, setSelectedMtpId] = useState(group.mtp_models[0]?.id ?? null);
  const [manualMtpPath, setManualMtpPath] = useState("");
  const [useMmproj, setUseMmproj] = useState(false);
  const [mmprojSource, setMmprojSource] = useState("none"); // "embedded" | "sidecar" | "manual" | "none"
  const [selectedMmprojId, setSelectedMmprojId] = useState(group.mmproj_models[0]?.id ?? null);
  const [manualMmprojPath, setManualMmprojPath] = useState("");

  // Apenas el usuario toca un control a mano, el autodetect deja de pisarlo
  // (incluso si después cambia de cuantización base).
  const mtpTouchedRef = useRef(false);
  const mmprojTouchedRef = useRef(false);

  const selectedBase = group.base_models.find((m) => m.id === selectedBaseId) ?? null;

  const hasEmbeddedMtp = Boolean(selectedBase?.mtp_embedded);
  const hasSidecarMtp = group.mtp_models.length > 0;
  const hasEmbeddedVision = Boolean(selectedBase?.has_vision_embedded);
  const hasSidecarVision = group.mmproj_models.length > 0;

  // Opciones del picker: "manual" está SIEMPRE disponible como salida de
  // emergencia, aunque el scanner haya detectado algo — el detector puede
  // equivocarse, o el usuario simplemente puede preferir otro archivo.
  const mtpOptions = [
    ...(hasEmbeddedMtp ? [["embedded", "Incluido en el modelo"]] : []),
    ...(hasSidecarMtp ? [["sidecar", "Archivo detectado en la carpeta"]] : []),
    ["manual", "Path manual"],
  ];
  const visionOptions = [
    ...(hasEmbeddedVision ? [["embedded", "Incluido en el modelo"]] : []),
    ...(hasSidecarVision ? [["sidecar", "Archivo detectado en la carpeta"]] : []),
    ["manual", "Path manual"],
  ];

  // Default sugerido: si el modelo lo trae adentro, esa es la opción; si no,
  // el archivo aparte; si no hay ninguno, apagado. Se recalcula al cambiar de
  // cuantización base porque cada .gguf de la carpeta puede diferir.
  useEffect(() => {
    if (mtpTouchedRef.current) return;
    if (hasEmbeddedMtp) { setUseMtp(true); setMtpSource("embedded"); }
    else if (hasSidecarMtp) { setUseMtp(true); setMtpSource("sidecar"); }
    else { setUseMtp(false); setMtpSource("none"); }
  }, [selectedBaseId, hasEmbeddedMtp, hasSidecarMtp]);

  useEffect(() => {
    if (mmprojTouchedRef.current) return;
    if (hasEmbeddedVision) { setUseMmproj(true); setMmprojSource("embedded"); }
    else if (hasSidecarVision) { setUseMmproj(false); setMmprojSource("sidecar"); }
    else { setUseMmproj(false); setMmprojSource("none"); }
  }, [selectedBaseId, hasEmbeddedVision, hasSidecarVision]);

  if (group.base_models.length === 0) return null;

  // Propagación en vivo: cada cambio en los controles de módulos re-emite la
  // decisión actual para que los toggles del panel no esperen a "Configurar".
  // `next` pasa el valor que el estado VA A tener (las updates de useState
  // son asíncronas).
  function emitSelection(next = {}, announce = false) {
    if (!selectedBaseId) return;
    const m = next.mtp ?? useMtp;
    const ms = next.mtpSource ?? mtpSource;
    const mtpId = next.mtpId ?? selectedMtpId;
    const mtpManual = next.mtpManual ?? manualMtpPath;
    const v = next.vision ?? useMmproj;
    const vs = next.visionSource ?? mmprojSource;
    const mmprojId = next.mmprojId ?? selectedMmprojId;
    const mmprojManual = next.mmprojManual ?? manualMmprojPath;

    const mtpPath = m
      ? (ms === "sidecar"
          ? (group.mtp_models.find((x) => x.id === mtpId)?.path ?? "")
          : ms === "manual" ? mtpManual.trim()
          : "")
      : "";
    const mmprojPath = v
      ? (vs === "sidecar"
          ? (group.mmproj_models.find((x) => x.id === mmprojId)?.path ?? "")
          : vs === "manual" ? mmprojManual.trim()
          : "")
      : "";

    onSelect(selectedBaseId, {
      mtp_draft_model: mtpPath,
      mtp_embedded: m && ms === "embedded",
      mmproj_path: mmprojPath,
    }, announce);
  }

  function handleConfigureClick() {
    emitSelection({}, true);
  }

  return (
    <div className="bg-glyvex-card rounded-lg border border-white/10 overflow-hidden">
      <button type="button" onClick={() => setExpanded((v) => !v)} className="w-full flex items-center justify-between p-4 text-left">
        <div className="min-w-0">
          <p className="text-sm font-medium truncate">{group.name}</p>
          <p className="text-xs text-glyvex-muted truncate">
            {group.base_models.length} cuantización{group.base_models.length !== 1 ? "es" : ""}
            {group.mtp_models.length > 0 ? ` · ${group.mtp_models.length} MTP` : ""}
            {group.mmproj_models.length > 0 ? ` · ${group.mmproj_models.length} visión` : ""}
          </p>
        </div>
        {expanded ? <ChevronDown size={16} className="shrink-0 text-glyvex-muted" /> : <ChevronRight size={16} className="shrink-0 text-glyvex-muted" />}
      </button>
      {expanded && (
        <div className="border-t border-white/10 p-4 space-y-4">
          <div>
            <p className="text-xs text-glyvex-muted uppercase tracking-wide mb-2">Cuantización base</p>
            <div className="space-y-1.5">
              {group.base_models.map((m) => (
                <label key={m.id} className="flex items-center gap-2 text-sm cursor-pointer">
                  <input type="radio" name={`base-${group.group_id}`} checked={selectedBaseId === m.id} onChange={() => setSelectedBaseId(m.id)} className="accent-glyvex-accent" />
                  <span className="truncate">{m.name}</span>
                  <span className="text-glyvex-muted text-xs shrink-0">{m.size_gb} GB</span>
                  {m.has_mmproj && <Eye size={12} className="text-glyvex-accent shrink-0" />}
                </label>
              ))}
            </div>
          </div>
          {(hasEmbeddedMtp || hasSidecarMtp) && (
            <div>
              <label className="flex items-center gap-2 text-sm cursor-pointer mb-1.5">
                <input type="checkbox" checked={useMtp}
                  onChange={(e) => {
                    mtpTouchedRef.current = true;
                    const checked = e.target.checked;
                    const src = checked && mtpSource === "none"
                      ? (hasEmbeddedMtp ? "embedded" : "sidecar")
                      : mtpSource;
                    setUseMtp(checked);
                    if (src !== mtpSource) setMtpSource(src);
                    emitSelection({ mtp: checked, mtpSource: src });
                  }}
                  className="accent-glyvex-accent" />
                MTP draft model
                {useMtp && mtpSource === "embedded" && <DetectedBadge>detectado · incluido en el modelo</DetectedBadge>}
              </label>
              {useMtp && (
                <SourcePicker value={mtpSource} options={mtpOptions}
                  onChange={(v) => { mtpTouchedRef.current = true; setMtpSource(v); emitSelection({ mtpSource: v }); }} />
              )}
              {useMtp && mtpSource === "sidecar" && group.mtp_models.length > 1 && (
                <select className="w-full bg-black/30 border border-white/10 rounded-md px-2 py-1.5 text-xs text-glyvex-text"
                  value={selectedMtpId ?? ""}
                  onChange={(e) => { mtpTouchedRef.current = true; setSelectedMtpId(e.target.value); emitSelection({ mtpId: e.target.value }); }}>
                  {group.mtp_models.map((m) => <option key={m.id} value={m.id}>{m.name} ({m.size_gb} GB)</option>)}
                </select>
              )}
              {useMtp && mtpSource === "manual" && (
                <input className="w-full bg-black/30 border border-white/10 rounded-md px-2 py-1.5 text-xs text-glyvex-text placeholder:text-glyvex-muted/60"
                  value={manualMtpPath}
                  onChange={(e) => { mtpTouchedRef.current = true; setManualMtpPath(e.target.value); emitSelection({ mtpManual: e.target.value }); }}
                  placeholder="C:\ruta\a\tu-draft.gguf" />
              )}
            </div>
          )}
          {(hasEmbeddedVision || hasSidecarVision) && (
            <div>
              <label className="flex items-center gap-2 text-sm cursor-pointer mb-1.5">
                <input type="checkbox" checked={useMmproj}
                  onChange={(e) => {
                    mmprojTouchedRef.current = true;
                    const checked = e.target.checked;
                    const src = checked && mmprojSource === "none"
                      ? (hasEmbeddedVision ? "embedded" : "sidecar")
                      : mmprojSource;
                    setUseMmproj(checked);
                    if (src !== mmprojSource) setMmprojSource(src);
                    emitSelection({ vision: checked, visionSource: src });
                  }}
                  className="accent-glyvex-accent" />
                Módulo de visión (mmproj)
                {useMmproj && mmprojSource === "embedded" && <DetectedBadge>detectado · incluido en el modelo</DetectedBadge>}
              </label>
              {useMmproj && (
                <SourcePicker value={mmprojSource} options={visionOptions}
                  onChange={(v) => { mmprojTouchedRef.current = true; setMmprojSource(v); emitSelection({ visionSource: v }); }} />
              )}
              {useMmproj && mmprojSource === "sidecar" && group.mmproj_models.length > 1 && (
                <select className="w-full bg-black/30 border border-white/10 rounded-md px-2 py-1.5 text-xs text-glyvex-text"
                  value={selectedMmprojId ?? ""}
                  onChange={(e) => { mmprojTouchedRef.current = true; setSelectedMmprojId(e.target.value); emitSelection({ mmprojId: e.target.value }); }}>
                  {group.mmproj_models.map((m) => <option key={m.id} value={m.id}>{m.name} ({m.size_gb} GB)</option>)}
                </select>
              )}
              {useMmproj && mmprojSource === "manual" && (
                <input className="w-full bg-black/30 border border-white/10 rounded-md px-2 py-1.5 text-xs text-glyvex-text placeholder:text-glyvex-muted/60"
                  value={manualMmprojPath}
                  onChange={(e) => { mmprojTouchedRef.current = true; setManualMmprojPath(e.target.value); emitSelection({ mmprojManual: e.target.value }); }}
                  placeholder="C:\ruta\a\tu-mmproj.gguf" />
              )}
            </div>
          )}
          <button type="button" onClick={handleConfigureClick} disabled={!selectedBaseId}
            className="flex items-center gap-2 px-3 py-2 rounded-md text-sm font-medium border border-glyvex-accent/40 bg-glyvex-accent/15 text-glyvex-accent hover:bg-glyvex-accent/25 disabled:opacity-50">
            Configurar
            <ChevronRight size={14} />
          </button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Vitales del servidor llama-server (WS /api/llm-metrics/{id}/stream)
// ---------------------------------------------------------------------------

const VITALS_POINTS = 60;
const STRIP_KEYS = ["launcher.tg", "launcher.ctx", "launcher.queue", "launcher.cache", "launcher.mtp"];

/**
 * Velocidad a mostrar: la actual si está generando; si no, la última que
 * midió el backend (tg_tps_last, sin importar cuánto hace). Devuelve también
 * el tooltip con la antigüedad.
 */
function tgDisplay(snaps) {
  const last = snaps[snaps.length - 1] || null;
  if (last?.tg_tps != null) return { value: last.tg_tps, live: true, title: "Velocidad de generación actual" };
  const fromBackend = last?.tg_tps_last ?? null;
  const fromBuffer = [...snaps].reverse().find((s) => s.tg_tps != null)?.tg_tps ?? null;
  const value = fromBackend ?? fromBuffer;
  if (value == null) return { value: null, live: false, title: "Todavía no hubo generación en este proceso" };
  const at = last?.tg_tps_last_at ? Date.parse(String(last.tg_tps_last_at).replace(/(\.\d{3})\d+/, "$1")) : NaN;
  const seconds = Number.isNaN(at) ? null : Math.max(0, Math.round((Date.now() - at) / 1000));
  const ago = seconds == null ? "" : seconds < 60 ? ` hace ${seconds} s` : seconds < 3600 ? ` hace ${Math.round(seconds / 60)} min` : ` hace ${Math.round(seconds / 3600)} h`;
  return { value, live: false, title: `Sin generación ahora: última velocidad medida${ago}` };
}

function contextTitle(snap) {
  if (!snap || snap.ctx_used == null) {
    return "Este build de llama-server no expone el contexto actual: se muestra el pico observado";
  }
  if (snap.ctx_source === "kv_cache") return "Tokens en el KV cache ahora";
  if (snap.slots_busy === 0) {
    return "Secuencia de la última conversación, que sigue en el KV cache para reutilizarse (dato de /slots)";
  }
  return "Tokens en la secuencia actual: prompt + generados hasta ahora (dato de /slots)";
}

function vitalsCtxColor(ratio) {
  if (ratio == null) return "#9ca3af";
  if (ratio < 0.5) return "#22c55e";
  if (ratio <= 0.8) return "#f59e0b";
  return "#ef4444";
}

/** Sparkline mínima en SVG: sin recharts, para una tira que se actualiza cada segundo. */
function MiniSpark({ values, color = "#06b6d4", width = 72, height = 20 }) {
  const points = values.filter((v) => v != null);
  if (points.length < 2) return <svg width={width} height={height} aria-hidden="true" />;
  const max = Math.max(...points);
  const min = Math.min(...points);
  const span = max - min || 1;
  const step = width / (values.length - 1 || 1);
  // Los null cortan la línea: un rato sin generar se ve como hueco, no como caída a 0.
  const segments = [];
  let current = [];
  values.forEach((v, i) => {
    if (v == null) {
      if (current.length) segments.push(current);
      current = [];
      return;
    }
    const y = height - 2 - ((v - min) / span) * (height - 4);
    current.push(`${(i * step).toFixed(1)},${y.toFixed(1)}`);
  });
  if (current.length) segments.push(current);
  return (
    <svg width={width} height={height} aria-hidden="true">
      {segments.map((seg, i) =>
        seg.length === 1 ? (
          <circle key={i} cx={seg[0].split(",")[0]} cy={seg[0].split(",")[1]} r="1.5" fill={color} />
        ) : (
          <polyline key={i} points={seg.join(" ")} fill="none" stroke={color} strokeWidth="1.5" />
        )
      )}
    </svg>
  );
}

/**
 * Tira de vitales de un proceso llama-server: velocidad de generación,
 * contexto y cola. Usa useLlmStream (buffer inicial por /history + WS con
 * reconexión) para no empezar la sparkline vacía ni quedar con datos
 * obsoletos si cae la conexión.
 */
function ServerVitalsStrip({ processId, state }) {
  const [snaps, setSnaps] = useState([]);
  const { isVisible: show, anyVisible, ready: displayReady } = useDisplay();
  const stripVisible = anyVisible(STRIP_KEYS);
  const { connected } = useLlmStream({
    processId,
    active: displayReady && stripVisible,
    onHistory: (data) => setSnaps(data.slice(-VITALS_POINTS)),
    onSnap: (snap) =>
      setSnaps((prev) => {
        const next = [...prev, snap];
        return next.length > VITALS_POINTS ? next.slice(next.length - VITALS_POINTS) : next;
      }),
  });

  useEffect(() => {
    setSnaps([]);
  }, [processId]);

  const last = snaps[snaps.length - 1] || null;
  const tgValues = snaps.map((s) => s.tg_tps ?? null);
  const tg = tgDisplay(snaps);

  // Todo oculto en Config: ni tira ni WebSocket (ver active de useLlmStream).
  if (!stripVisible) return null;

  if (state !== "running" && !last) {
    return (
      <div className="px-3 py-2 rounded-md border border-white/10 bg-black/30 text-xs text-glyvex-muted">
        Vitales del servidor: disponibles cuando el proceso esté listo.
      </div>
    );
  }

  const ctxTotal = last?.ctx_total ?? null;
  const ctxUsed = last?.ctx_used ?? null;
  const ctxRatio = last?.ctx_usage_ratio ?? null;
  const ctxPeak = last?.ctx_peak ?? null;
  const processing = last?.requests_processing ?? null;
  const deferred = last?.requests_deferred ?? null;
  const cacheTotal = last?.cache_hit_pct_total ?? null;
  const cacheInterval = last?.cache_hit_pct ?? null;
  const specTotal = last?.spec_accept_pct_total ?? null;
  const specInterval = last?.spec_accept_pct ?? null;

  return (
    <div className="px-3 py-2 rounded-md border border-white/10 bg-black/30 text-xs space-y-1.5" aria-live="off">
      {state === "running" && !connected && last && (
        <p className="text-amber-400" title="Los valores mostrados son del último snap recibido">
          Stream de métricas caído: reconectando… (valores obsoletos)
        </p>
      )}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        {show("launcher.tg") && (
        <div
          className="flex items-center gap-2"
          title={tg.title}
        >
          <span className="text-glyvex-muted">tg</span>
          <span className={`font-medium tabular-nums ${tg.live ? "text-glyvex-text" : "text-glyvex-muted"}`}>
            {tg.value != null ? `${tg.value.toFixed(1)} t/s` : "—"}
          </span>
          <MiniSpark values={tgValues} />
        </div>
        )}

        {show("launcher.ctx") && (
        <div
          className="flex items-center gap-2"
          title={contextTitle(last)}
        >
          <span className="text-glyvex-muted">ctx</span>
          {ctxUsed != null ? (
            <>
              <span className="font-medium tabular-nums text-glyvex-text">
                {ctxUsed.toLocaleString()}{ctxTotal ? ` / ${ctxTotal.toLocaleString()}` : ""}
              </span>
              {ctxRatio != null && (
                <span className="w-16 h-1.5 rounded-full bg-black/40 overflow-hidden">
                  <span
                    className="block h-full rounded-full"
                    style={{ width: `${Math.min(100, ctxRatio * 100)}%`, backgroundColor: vitalsCtxColor(ctxRatio) }}
                  />
                </span>
              )}
            </>
          ) : (
            <span className="font-medium tabular-nums text-glyvex-text">
              {ctxPeak != null ? `pico ${ctxPeak.toLocaleString()}` : "—"}
              {ctxTotal ? <span className="text-glyvex-muted font-normal"> / {ctxTotal.toLocaleString()}</span> : null}
            </span>
          )}
        </div>
        )}

        {show("launcher.queue") && (
        <div className="flex items-center gap-2" title="Requests procesándose y esperando un slot libre">
          <span className="text-glyvex-muted">cola</span>
          <span className="font-medium tabular-nums text-glyvex-text">
            {processing ?? "—"}
            <span className="text-glyvex-muted font-normal"> en curso</span>
            {deferred ? (
              <span className="text-amber-400"> · {deferred} en espera</span>
            ) : null}
          </span>
        </div>
        )}

        {show("launcher.cache") && cacheTotal != null && (
          <div
            className="flex items-center gap-2"
            title={
              "Tokens de prompt reutilizados del caché desde que arrancó el servidor" +
              (cacheInterval != null ? `. Último request: ${cacheInterval.toFixed(1)} %` : "")
            }
          >
            <span className="text-glyvex-muted">caché</span>
            <span className="font-medium tabular-nums text-glyvex-text">{cacheTotal.toFixed(1)} %</span>
          </div>
        )}

        {show("launcher.mtp") && specTotal != null && (
          <div
            className="flex items-center gap-2"
            title={
              "Tokens propuestos por el draft (MTP) que el modelo aceptó, desde que arrancó" +
              (specInterval != null ? `. Último intervalo: ${specInterval.toFixed(1)} %` : "")
            }
          >
            <span className="text-glyvex-muted">MTP</span>
            <span className="font-medium tabular-nums text-glyvex-text">{specTotal.toFixed(1)} % aceptado</span>
          </div>
        )}
      </div>

      {last && !last.scrape_ok && last.scrape_error && (
        <p className="text-amber-400">{last.scrape_error}</p>
      )}
    </div>
  );
}

function LogTerminal({ processId }) {
  const [lines, setLines] = useState([]);
  const containerRef = useRef(null);

  useEffect(() => {
    setLines([]);
    if (!processId) return undefined;
    const ws = new WebSocket(wsUrlFor(`/api/launcher/logs/${processId}/stream`));
    ws.onmessage = (event) => {
      setLines((prev) => {
        const next = [...prev, event.data];
        return next.length > 500 ? next.slice(next.length - 500) : next;
      });
    };
    ws.onerror = () => {};
    // cleanup: cerrar el socket al desmontar o al cambiar de proceso
    return () => { try { ws.close(); } catch { /* ya cerrado */ } };
  }, [processId]);

  useEffect(() => {
    const el = containerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines]);

  function handleDownload() {
    // Solo baja lo que está en el buffer del terminal (últimas 500 líneas).
    // El log completo de la corrida vive en el log_file del backend.
    const content = lines.join("\n");
    const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `glyvex-llama-server-${processId}-${Date.now()}.log`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-xs text-glyvex-muted">Terminal de logs</span>
        <button
          type="button"
          onClick={handleDownload}
          disabled={lines.length === 0}
          className="flex items-center gap-1 px-2 py-1 rounded text-xs border border-glyvex-border text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-card disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <Download size={12} />
          Descargar log
        </button>
      </div>
      <div ref={containerRef} className="bg-black/50 border border-white/10 rounded-md p-3 h-64 overflow-y-auto font-mono text-xs text-glyvex-muted space-y-0.5">
        {lines.length === 0 ? <p className="text-glyvex-muted/60">Sin logs todavía.</p> : lines.map((line, i) => <div key={i}>{line}</div>)}
      </div>
    </div>
  );
}

export default function Launcher() {
  const [modelList, setModelList] = useState([]);
  const [groupList, setGroupList] = useState([]);
  const [viewMode, setViewMode] = useState("group");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [modelMeta, setModelMeta] = useState(null);
  const [vramEstimate, setVramEstimate] = useState(null);
  const [launchConfig, setLaunchConfig] = useState(DEFAULT_LAUNCH_CONFIG);
  const [templateList, setTemplateList] = useState([]);
  const [selectedTemplate, setSelectedTemplate] = useState("");
  const [templateOpen, setTemplateOpen] = useState(false);
  const [newTemplateName, setNewTemplateName] = useState("");
  const [processInfo, setProcessInfo] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [launching, setLaunching] = useState(false);
  const [advancedMode, setAdvancedMode] = useState(false);
  const [toggles, setToggles] = useState({ ...DEFAULT_TOGGLES });
  const [customCtx, setCustomCtx] = useState(false);
  const [scrollTick, setScrollTick] = useState(0);
  const pollRef = useRef(null);
  const configPanelRef = useRef(null);
  const templateBoxRef = useRef(null);
  // Build de llama-server detectada por el probe del backend. Se muestra en
  // la cabecera para saber contra qué binario se está lanzando sin tener que
  // ir a comparar releases a mano después de cada actualización.
  const [backendInfo, setBackendInfo] = useState(null);
  // Preview del comando: se pide al backend (mismo probe y mismo filtrado que
  // usa start()) solo cuando el usuario abre el bloque, no en cada tecleo.
  const [showCommand, setShowCommand] = useState(false);
  const [commandPreview, setCommandPreview] = useState(null);
  const [commandError, setCommandError] = useState(null);

  // Una vez que el usuario (o un template, o una config restaurada) definió
  // el sampling, la metadata del GGUF ya no lo pisa.
  const samplingTouchedRef = useRef(false);

  const selectedModel = useMemo(() => modelList.find((m) => m.id === selectedId) || null, [modelList, selectedId]);

  // Modo automático (F4): comando estricto -m + --port. Grisea los 16
  // toggles y sus controles (el payload hace bypass total de la config).
  const autoMode = Boolean(launchConfig.auto_mode);

  // P1.5: disponibilidad por build — el probe (flags de /backend-info) dice
  // qué soporta esta build. probe fallido (probed=false) -> null = todo
  // habilitado (filter_command sigue protegiendo en el backend).
  const supportedFlags = useMemo(
    () => (backendInfo?.probed ? new Set(backendInfo.flags || []) : null),
    [backendInfo]
  );
  const isBuildUnavailable = (id) => {
    const flag = TOGGLE_GROUPS[id].flag;
    return Boolean(supportedFlags && !supportedFlags.has(flag));
  };
  // disabled de toggles Y controles: modo auto ON o build sin el flag.
  const isToggleUnavailable = (id) => autoMode || isBuildUnavailable(id);
  const toggleTitle = (id) => {
    const flag = TOGGLE_GROUPS[id].flag;
    if (isBuildUnavailable(id)) {
      return `No disponible en esta build (${backendInfo?.build || "?"}): ${flag} no está en el --help del binario`;
    }
    return backendInfo?.flag_help?.[flag] || undefined;
  };
  // OFF -> null en los campos del grupo (el builder salta lo que es None).
  const applyToggles = useCallback((cfg) => {
    const out = { ...cfg };
    for (const [id, group] of Object.entries(TOGGLE_GROUPS)) {
      if (toggles[id]) continue;
      for (const f of group.fields) out[f] = null;
    }
    return out;
  }, [toggles]);
  const setToggle = useCallback((id, value) => {
    setToggles((prev) => ({ ...prev, [id]: value }));
  }, []);

  // Preview del comando. Se re-pide con debounce cuando cambia la config, y
  // solo mientras el bloque está abierto: así mover un slider no dispara una
  // request por frame.
  useEffect(() => {
    if (!showCommand || !selectedId) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const res = await fetch("/api/launcher/preview-command", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(autoMode
            ? { model_id: selectedId, host: launchConfig.host, port: launchConfig.port, auto_mode: true }
            : { model_id: selectedId, ...applyToggles(launchConfig) }),
        });
        const data = await res.json();
        if (cancelled) return;
        if (!res.ok) {
          setCommandPreview(null);
          setCommandError(data?.detail ? String(data.detail) : "No se pudo armar el comando.");
          return;
        }
        setCommandError(null);
        setCommandPreview(data);
      } catch {
        if (!cancelled) {
          setCommandPreview(null);
          setCommandError("No se pudo consultar el comando al backend.");
        }
      }
    }, 300);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [showCommand, selectedId, launchConfig, applyToggles]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [models, groups, tpls] = await Promise.all([
          fetch("/api/models").then((r) => r.json()),
          fetch("/api/models/groups").then((r) => r.json()),
          fetch("/api/launcher/templates").then((r) => r.json()),
        ]);
        if (cancelled) return;
        setModelList(models);
        setGroupList(groups);
        setTemplateList(Array.isArray(tpls) ? tpls : []);
      } catch {
        if (!cancelled) setError("No se pudo cargar el inventario de modelos o los templates.");
      } finally {
        if (!cancelled) setLoading(false);
      }

      // Restaurar el proceso activo si el backend sigue corriendo un modelo
      try {
        const statusRes = await fetch("/api/launcher/status");
        const allProcesses = await statusRes.json();
        if (cancelled || !Array.isArray(allProcesses)) return;
        const activeProcess = allProcesses.find((p) => p.state !== "stopped");
        if (activeProcess) {
          setProcessInfo(activeProcess);
          if (activeProcess.model_id) setSelectedId(activeProcess.model_id);
          if (activeProcess.launch_config) {
            // La config del proceso vivo manda sobre el preset sugerido por
            // la metadata: es lo que realmente está corriendo.
            samplingTouchedRef.current = true;
            setLaunchConfig((prev) => ({ ...prev, ...activeProcess.launch_config }));
            // P1.5: los toggles OFF quedaron como null en la config serializada.
            setToggles((prev) => ({ ...prev, ...togglesFromConfig(activeProcess.launch_config) }));
          }
        }
      } catch { /* sin proceso activo que restaurar */ }

      // Build del binario: informativo, nunca bloquea la carga del Launcher.
      try {
        const infoRes = await fetch("/api/launcher/backend-info");
        if (!cancelled && infoRes.ok) setBackendInfo(await infoRes.json());
      } catch { /* backend sin binario configurado todavía */ }
    })();
    return () => { cancelled = true; };
  }, []);

  // Metadata real del GGUF del modelo seleccionado
  useEffect(() => {
    if (!selectedId) { setModelMeta(null); return undefined; }
    let cancelled = false;
    setModelMeta(null);
    fetch(`/api/models/${selectedId}/metadata`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((meta) => {
        if (cancelled || !meta) return;
        setModelMeta(meta);
        if (meta.error) return;
        if (samplingTouchedRef.current) return;
        const preset = meta.suggested_preset;
        if (preset && SAMPLING_PRESETS[preset]) {
          setLaunchConfig((prev) => ({
            ...prev,
            sampling_preset: preset,
            ...SAMPLING_PRESETS[preset],
          }));
        }
      })
      .catch(() => {
        if (!cancelled) setModelMeta({ error: "No se pudo leer la metadata del modelo." });
      });
    return () => { cancelled = true; };
  }, [selectedId]);

  // VRAM estimada del modelo seleccionado; depende de los parámetros que la
  // mueven (n_ctx, tipos de KV, slots). Con debounce corto para no disparar
  // una fetch por cada dígito al editar n_parallel.
  useEffect(() => {
    if (!selectedId) { setVramEstimate(null); return undefined; }
    let cancelled = false;
    setVramEstimate(null);
    const params = new URLSearchParams({
      n_ctx: String(launchConfig.n_ctx),
      cache_type_k: launchConfig.cache_type_k,
      cache_type_v: launchConfig.cache_type_v,
      n_parallel: String(launchConfig.n_parallel),
      flash_attn: String(launchConfig.flash_attn),
    });
    const timer = setTimeout(() => {
      fetch(`/api/models/${selectedId}/vram-estimate?${params}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => { if (!cancelled && data) setVramEstimate(data); })
        .catch(() => { /* sin estimación: la tarjeta simplemente no se muestra */ });
    }, 250);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [selectedId, launchConfig.n_ctx, launchConfig.cache_type_k, launchConfig.cache_type_v, launchConfig.n_parallel, launchConfig.flash_attn]);

  useEffect(() => {
    if (!selectedId) { setProcessInfo(null); return undefined; }
    let cancelled = false;
    fetch("/api/launcher/status")
      .then((r) => r.json())
      .then((all) => {
        if (cancelled || !Array.isArray(all)) return;
        const existing = all.find((p) => p.model_id === selectedId && p.state !== "stopped");
        setProcessInfo(existing || null);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [selectedId]);

  useEffect(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    if (!processInfo || processInfo.state !== "starting") return undefined;
    pollRef.current = setInterval(() => {
      fetch(`/api/launcher/status/${processInfo.process_id}`)
        .then((r) => r.json())
        .then((info) => setProcessInfo(info))
        .catch(() => {});
    }, 2000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); pollRef.current = null; };
  }, [processInfo]);

  // Scroll al panel de configuración cuando se entra desde "Configurar"
  useEffect(() => {
    if (scrollTick === 0) return;
    configPanelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [scrollTick]);

  // Cerrar el dropdown de templates al clickear afuera
  useEffect(() => {
    if (!templateOpen) return undefined;
    function handleDocClick(event) {
      if (templateBoxRef.current && !templateBoxRef.current.contains(event.target)) {
        setTemplateOpen(false);
      }
    }
    document.addEventListener("mousedown", handleDocClick);
    return () => document.removeEventListener("mousedown", handleDocClick);
  }, [templateOpen]);

  function updateConfig(patch) { setLaunchConfig((prev) => ({ ...prev, ...patch })); }

  function applySamplingPreset(preset) {
    samplingTouchedRef.current = true;
    updateConfig({
      sampling_preset: preset,
      ...(preset !== "custom" ? SAMPLING_PRESETS[preset] : {}),
    });
  }

  // Mover cualquier slider de sampling pasa el preset a "custom".
  function updateSampling(patch) {
    samplingTouchedRef.current = true;
    updateConfig({ ...patch, sampling_preset: "custom" });
  }

  const reloadTemplates = useCallback(async () => {
    try {
      const tpls = await fetch("/api/launcher/templates").then((r) => r.json());
      setTemplateList(Array.isArray(tpls) ? tpls : []);
    } catch { setActionError("No se pudo recargar la lista de templates."); }
  }, []);

  function applyTemplate(name) {
    setSelectedTemplate(name);
    const tpl = templateList.find((t) => t.name === name);
    if (tpl && tpl.params) {
      // P1.5 F5: el snapshot trae el estado de toggles y el modo automático.
      // Compat: un template viejo sin `toggles` restaura todo ON (los grupos
      // que no estén en el snapshot guardado usan su default ON).
      setToggles(tpl.params.toggles
        ? { ...DEFAULT_TOGGLES, ...tpl.params.toggles }
        : { ...DEFAULT_TOGGLES });
      // `toggles` no es un campo de launchConfig: se saca antes de merguear.
      const { toggles: _toggles, ...configParams } = tpl.params;
      // Un template guardado trae su propio sampling: no dejamos que la
      // metadata del modelo lo sobrescriba después.
      if (Object.prototype.hasOwnProperty.call(tpl.params, "sampling_preset")) {
        samplingTouchedRef.current = true;
      }
      updateConfig(configParams);
      setCustomCtx(false);
    }
  }

  async function saveTemplate() {
    const name = newTemplateName.trim();
    if (!name) return;
    const params = Object.fromEntries(TEMPLATE_FIELDS.map((k) => [k, launchConfig[k]]));
    // P1.5 F5: snapshot de los toggles (estado por grupo, no solo valores).
    params.toggles = { ...toggles };
    setActionError(null);
    try {
      const res = await fetch("/api/launcher/templates", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, builtin: false, params }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "save_template_failed");
      }
      const saved = await res.json();
      await reloadTemplates();
      setNewTemplateName("");
      setSelectedTemplate(saved.name);
    } catch (err) {
      setActionError(typeof err.message === "string" && err.message !== "save_template_failed" ? err.message : "No se pudo guardar el template.");
    }
  }

  async function deleteTemplate(name) {
    setActionError(null);
    try {
      const res = await fetch(`/api/launcher/templates/${encodeURIComponent(name)}`, { method: "DELETE" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "No se pudo eliminar el template.");
      }
      await reloadTemplates();
      if (selectedTemplate === name) setSelectedTemplate("");
    } catch (err) {
      setActionError(typeof err.message === "string" ? err.message : "No se pudo eliminar el template.");
    }
  }

  // Seleccionar un modelo desde una GroupCard: NO lanza, solo preselecciona
  // el modelo + los overrides y lleva al panel de configuración.
  const handleSelectModel = useCallback((modelId, configOverrides = null, announce = true) => {
    setSelectedId(modelId);
    if (configOverrides && Object.keys(configOverrides).length > 0) {
      // Viene de una GroupCard: trae la decisión explícita del usuario sobre
      // MTP y visión.
      setLaunchConfig((prev) => ({ ...prev, ...configOverrides }));
      // Link: el toggle de visión sigue al checkbox de la GroupCard
      // (sin path, OFF).
      setToggles((prev) => ({ ...prev, mmproj: Boolean(configOverrides.mmproj_path) }));
    } else {
      // Viene de la vista List (sin overrides): se derivan los defaults del
      // propio modelo, para que no quede pegada la config del modelo anterior.
      const model = modelList.find((m) => m.id === modelId);
      setLaunchConfig((prev) => ({
        ...prev,
        mtp_draft_model: "",
        mtp_embedded: Boolean(model?.mtp_embedded),
        // El mmproj del modelo anterior no viaja: se re-liga al nuevo modelo.
        mmproj_path: "",
      }));
      setToggles((prev) => ({ ...prev, mmproj: false }));
    }
    setActionError(null);
    // Las emiciones en vivo de la card no arrastran la página al panel.
    if (announce) setScrollTick((tick) => tick + 1);
  }, [modelList]);

  const handleLaunch = useCallback(async () => {
    if (!selectedModel) return;
    setLaunching(true);
    setActionError(null);
    try {
      // Modo automático (F4): bypass total — solo modelo + host + puerto;
      // el backend arma el comando estricto con los defaults de la build.
      if (autoMode) {
        const res = await fetch("/api/launcher/launch", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ model_id: selectedModel.id, host: launchConfig.host, port: launchConfig.port, auto_mode: true }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "launch_failed");
        setProcessInfo(data);
        return;
      }
      // Los selects opcionales usan "" como "automático"; el backend espera
      // null (no pasar el flag) — normalizar antes de enviar. P1.5: los
      // toggles OFF ya dejaron sus campos en null vía applyToggles.
      const toggled = applyToggles(launchConfig);
      const payload = {
        ...toggled,
        model_id: selectedModel.id,
        mtp_draft_model: toggled.mtp_draft_model || null,
        cache_type_k_draft: toggled.cache_type_k_draft || null,
        cache_type_v_draft: toggled.cache_type_v_draft || null,
        mmproj_path: toggled.mmproj_path || null,
        lora_path: toggled.lora_path || null,
      };
      const res = await fetch("/api/launcher/launch", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "launch_failed");
      setProcessInfo(data);
    } catch (err) {
      setActionError(typeof err.message === "string" ? err.message : "Error al lanzar el modelo.");
    } finally { setLaunching(false); }
  }, [launchConfig, selectedModel, applyToggles]);

  async function handleStop() {
    if (!processInfo) return;
    setActionError(null);
    try {
      const res = await fetch(`/api/launcher/stop/${processInfo.process_id}`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "stop_failed");
      setProcessInfo(data && data.state ? data : null);
    } catch { setActionError("Error al detener el proceso."); }
  }

  async function handleRestart() {
    if (!processInfo) return;
    setActionError(null);
    try {
      const res = await fetch(`/api/launcher/restart/${processInfo.process_id}`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "restart_failed");
      setProcessInfo(data);
    } catch { setActionError("Error al reiniciar el proceso."); }
  }

  const isProcessActive = processInfo && processInfo.state !== "stopped";
  const badge = processInfo ? STATE_BADGE[processInfo.state] : null;
  const selectClasses = "w-full bg-black/30 border border-white/10 rounded-md px-3 py-2 text-sm text-glyvex-text focus:outline-none focus:ring-2 focus:ring-glyvex-accent/60";
  const showCustomCtx = customCtx || !N_CTX_PRESETS.includes(launchConfig.n_ctx);

  const metaOk = modelMeta && !modelMeta.error;
  const nativeCtx = metaOk ? modelMeta.context_length : null;
  const ctxOverflow = Boolean(nativeCtx && launchConfig.n_ctx > nativeCtx);
  const thinkingUnsupported = Boolean(metaOk && modelMeta.enable_thinking_kwarg === false);
  const showSamplingSliders = advancedMode || launchConfig.sampling_preset === "custom";

  const samplingPanel = (
    <Panel icon={SlidersHorizontal} title="Sampling">
      <div className="flex gap-2">
        {["thinking", "instruct", "custom"].map((preset) => (
          <button
            key={preset}
            type="button"
            onClick={() => applySamplingPreset(preset)}
            className={
              "flex-1 px-3 py-2 rounded-md text-sm border " +
              (launchConfig.sampling_preset === preset
                ? "bg-glyvex-accent/15 border-glyvex-accent text-glyvex-accent"
                : "border-glyvex-border text-glyvex-muted hover:text-glyvex-text")
            }
          >
            {SAMPLING_PRESET_LABELS[preset]}
          </button>
        ))}
      </div>
      {!showSamplingSliders && (
        <div className="flex flex-wrap gap-2 text-xs font-mono text-glyvex-muted">
          {[
            ["temp", Number(launchConfig.temperature).toFixed(2)],
            ["top_p", Number(launchConfig.top_p).toFixed(2)],
            ["top_k", launchConfig.top_k],
            ["min_p", Number(launchConfig.min_p).toFixed(2)],
            ["presence", Number(launchConfig.presence_penalty).toFixed(2)],
            ["repeat", Number(launchConfig.repeat_penalty).toFixed(2)],
          ].map(([label, value]) => (
            <span key={label} className="px-2 py-1 rounded border border-glyvex-border bg-black/30">
              {label}: {value}
            </span>
          ))}
        </div>
      )}
      {showSamplingSliders && (
        <div className="space-y-3">
          <SamplingSlider label="Temperature" value={launchConfig.temperature} min={0} max={2} step={0.05}
            onChange={(v) => updateSampling({ temperature: v })} />
          <SamplingSlider label="Top P" value={launchConfig.top_p} min={0} max={1} step={0.01}
            onChange={(v) => updateSampling({ top_p: v })} />
          <SamplingSlider label="Top K" value={launchConfig.top_k} min={0} max={100} step={1} digits={0}
            onChange={(v) => updateSampling({ top_k: v })} />
          <SamplingSlider label="Min P" value={launchConfig.min_p} min={0} max={1} step={0.01}
            onChange={(v) => updateSampling({ min_p: v })} />
          <SamplingSlider label="Presence penalty" value={launchConfig.presence_penalty} min={0} max={2} step={0.05}
            onChange={(v) => updateSampling({ presence_penalty: v })} />
          <SamplingSlider label="Repeat penalty" value={launchConfig.repeat_penalty} min={1} max={2} step={0.05}
            onChange={(v) => updateSampling({ repeat_penalty: v })} />
        </div>
      )}
      {modelMeta && (
        <div className="space-y-1 pt-1 border-t border-white/10">
          {modelMeta.error ? (
            <p className="text-xs text-glyvex-muted-2">
              No se pudo leer la metadata del GGUF: {modelMeta.error}
            </p>
          ) : (
            <>
              {nativeCtx && (
                <p className="text-xs text-glyvex-muted">
                  Contexto nativo del modelo: {nativeCtx.toLocaleString()} tokens
                </p>
              )}
              {ctxOverflow && (
                <p className="text-xs text-amber-400">
                  ⚠ n_ctx ({Number(launchConfig.n_ctx).toLocaleString()}) supera el contexto nativo
                  del modelo ({nativeCtx.toLocaleString()})
                </p>
              )}
              {modelMeta.architecture && (
                <p className="text-xs text-glyvex-muted-2">
                  Arquitectura: {modelMeta.architecture}
                  {modelMeta.thinking_support ? " · Soporta thinking ✓" : ""}
                </p>
              )}
            </>
          )}
        </div>
      )}
      {vramEstimate && vramEstimate.available && (
        <div className="mt-2 rounded-md bg-black/20 border border-white/10 p-3 space-y-1">
          <div className="flex items-center gap-2 text-sm">
            <span className={`inline-block w-2.5 h-2.5 rounded-full ${VRAM_STATE_DOT[vramEstimate.state] || VRAM_STATE_DOT.unknown}`} />
            <span className="font-medium">VRAM estimada: {vramEstimate.total_gb} GB</span>
            {vramEstimate.pct !== null && (
              <span className="text-glyvex-muted">/ {vramEstimate.gpu_vram_gb} GB ({vramEstimate.pct}%)</span>
            )}
          </div>
          <p className="text-xs text-glyvex-muted">
            Pesos {vramEstimate.weights_gb} GB · KV cache {vramEstimate.kv_gb} GB
            {vramEstimate.ssm_gb ? ` · SSM ${vramEstimate.ssm_gb} GB` : ""} · Cómputo {vramEstimate.compute_gb} GB
            {vramEstimate.n_parallel > 1 ? ` · ${vramEstimate.n_parallel} slots` : ""}
          </p>
          {Array.isArray(vramEstimate.legend) && vramEstimate.legend.length > 0 && (
            <div className="pt-1.5 border-t border-white/10">
              <p className="text-[11px] text-glyvex-muted-2 mb-1">
                VRAM según contexto (misma cuantización):
              </p>
              <div className="flex flex-wrap gap-x-3 gap-y-0.5">
                {vramEstimate.legend.map((row) => (
                  <span key={row.n_ctx} className="text-[11px] text-glyvex-muted">
                    <span className="font-medium">{N_CTX_LABELS[row.n_ctx] ?? `${Math.round(row.n_ctx / 1024)}K`}</span>{" "}
                    {row.total_gb} GB
                    {row.pct !== null && (
                      <span className={row.state === "no_cabe" ? "text-red-400" : row.state === "justo" ? "text-amber-400" : ""}>
                        {" "}({row.pct}%)
                      </span>
                    )}
                  </span>
                ))}
              </div>
            </div>
          )}
          {vramEstimate.state === "no_cabe" && (
            <p className="text-xs text-red-400">
              No cabe en la VRAM de la GPU: probá una cuantización más baja, un n_ctx menor u offload de MoE a CPU.
            </p>
          )}
          {vramEstimate.state === "justo" && (
            <p className="text-xs text-amber-400">
              Cabe, pero justo al límite: considerá reducir n_ctx o usar fit_target.
            </p>
          )}
          {vramEstimate.state === "unknown" && (
            <p className="text-xs text-glyvex-muted-2">
              Declará la VRAM de tu GPU en Configuración para saber si cabe.
            </p>
          )}
          {vramEstimate.is_moe && (
            <p className="text-xs text-glyvex-muted-2">
              MoE: los pesos son los parámetros totales (por token solo corren los activos).
            </p>
          )}
        </div>
      )}
    </Panel>
  );

  if (loading) return <p className="text-glyvex-muted text-sm">Cargando modelos…</p>;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-baseline gap-3">
          <h1 className="text-xl font-semibold">Launcher</h1>
          {backendInfo && (
            <span
              className="text-xs font-mono px-2 py-0.5 rounded border border-white/10 text-glyvex-muted"
              title={
                backendInfo.probed
                  ? `${backendInfo.version_line || "llama-server"}\n${backendInfo.flags?.length ?? 0} flags soportados\n${backendInfo.path}`
                  : "No se pudo leer --help/--version del binario: no se filtran flags no soportados"
              }
            >
              {backendInfo.probed
                ? `llama.cpp ${backendInfo.build || "build desconocida"}`
                : "⚠ binario sin detectar"}
            </span>
          )}
        </div>
        <div className="flex gap-1 bg-glyvex-card border border-white/10 rounded-md p-1">
          <button type="button" onClick={() => setViewMode("group")} className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-xs ${viewMode === "group" ? "bg-glyvex-accent text-white" : "text-glyvex-muted hover:text-glyvex-text"}`}><LayoutGrid size={13} /> Group</button>
          <button type="button" onClick={() => setViewMode("list")} className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-xs ${viewMode === "list" ? "bg-glyvex-accent text-white" : "text-glyvex-muted hover:text-glyvex-text"}`}><ListIcon size={13} /> List</button>
        </div>
      </div>

      {error && <p className="text-sm text-red-400">{error}</p>}

      {viewMode === "group" ? (
        groupList.length === 0 ? (
          <p className="text-sm text-glyvex-muted">No hay modelos en el inventario todavía.</p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {groupList.map((g) => <GroupCard key={g.group_id} group={g} onSelect={handleSelectModel} />)}
          </div>
        )
      ) : (
        <ModelTable modelList={modelList} selectedId={selectedId} onSelect={(id) => handleSelectModel(id)} />
      )}

      {selectedModel && (
        <div ref={configPanelRef} className="space-y-6 scroll-mt-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-glyvex-muted">Configurando lanzamiento para <span className="text-glyvex-text">{selectedModel.name}</span></p>
            <div className="flex items-center gap-3">
              <span className="flex items-center gap-2 text-sm">
                <span className="text-glyvex-muted">Auto</span>
                <Toggle checked={autoMode} onChange={(v) => updateConfig({ auto_mode: v })}
                  disabled={advancedMode}
                  title="Comando estricto: solo modelo + puerto (defaults de la build). Conserva la infra: --verbosity y --metrics." />
              </span>
              <span className="flex items-center gap-2 text-sm">
                <span className="text-glyvex-muted">Avanzado</span>
                <Toggle checked={advancedMode}
                  onChange={(v) => { setAdvancedMode(v); if (v) updateConfig({ auto_mode: false }); }} />
              </span>
              <button
                type="button"
                onClick={() => setShowCommand((v) => !v)}
                title="Ver el comando de llama-server que se va a ejecutar"
                className={"flex items-center gap-2 px-3 py-2 rounded-md text-sm border " + (showCommand ? "border-glyvex-accent/60 text-glyvex-text bg-glyvex-card" : "border-white/10 text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-card")}
              >
                <Terminal size={16} />
                Comando
                {showCommand ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              </button>
              {isProcessActive && (
                <button type="button" onClick={handleRestart} className="flex items-center gap-2 px-3 py-2 rounded-md text-sm border border-white/10 text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-card"><RotateCw size={16} />Restart</button>
              )}
              {isProcessActive ? (
                <button type="button" onClick={handleStop} className="flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium bg-red-600 text-white hover:bg-red-500"><Square size={16} />STOP</button>
              ) : (
                <button type="button" onClick={handleLaunch} disabled={launching} className="flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium bg-emerald-600 text-white hover:bg-emerald-500 disabled:opacity-50"><Play size={16} />{launching ? "Lanzando…" : "LAUNCH"}</button>
              )}
            </div>
          </div>

          {actionError && <p className="text-sm text-red-400">{actionError}</p>}

          {showCommand && (
            <Panel icon={Terminal} title="Comando de lanzamiento">
              {commandError ? (
                <p className="text-xs text-red-400">{commandError}</p>
              ) : (
                <>
                  <p className="text-xs text-glyvex-muted">
                    Lo que se va a ejecutar con la config actual
                    {commandPreview?.build ? ` (build detectada: ${commandPreview.build})` : ""}.
                    La api-key se muestra enmascarada.
                  </p>
                  <CommandBlock
                    argv={commandPreview?.command}
                    emptyHint="LM Studio corre su propio servidor: no hay comando que lanzar desde acá."
                  />
                  {commandPreview?.dropped?.length > 0 && (
                    <p className="text-xs text-amber-400">
                      ⚠ Esta build no soporta {commandPreview.dropped.length} flag(s), se omiten:{" "}
                      <span className="font-mono">{commandPreview.dropped.join(", ")}</span>
                    </p>
                  )}
                </>
              )}
            </Panel>
          )}

          <div className="grid grid-cols-1 lg:grid-cols-2 2xl:grid-cols-3 gap-4">
            <Panel icon={Sliders} title="Template">
              <Field label="Template predefinido o guardado">
                <div className="relative" ref={templateBoxRef}>
                  <button type="button" onClick={() => setTemplateOpen((v) => !v)}
                    className={selectClasses + " flex items-center justify-between text-left"}>
                    <span className={selectedTemplate ? "truncate" : "truncate text-glyvex-muted"}>
                      {selectedTemplate || "— Elegir template —"}
                    </span>
                    <ChevronDown size={14} className="shrink-0 text-glyvex-muted" />
                  </button>
                  {templateOpen && (
                    <div className="absolute z-20 mt-1 w-full max-h-64 overflow-y-auto rounded-md border border-white/10 bg-glyvex-card shadow-lg">
                      <button type="button"
                        onClick={() => { setSelectedTemplate(""); setTemplateOpen(false); }}
                        className="w-full px-3 py-2 text-left text-sm text-glyvex-muted hover:bg-white/5">
                        — Elegir template —
                      </button>
                      {templateList.length === 0 && (
                        <p className="px-3 py-2 text-sm text-glyvex-muted">Todavía no hay templates guardados.</p>
                      )}
                      {templateList.map((t) => (
                        <div key={t.name} className={"flex items-center gap-1 pr-1 hover:bg-white/5 " + (t.name === selectedTemplate ? "bg-glyvex-accent/10" : "")}>
                          <button type="button"
                            onClick={() => { applyTemplate(t.name); setNewTemplateName(t.name); setTemplateOpen(false); }}
                            className="flex-1 min-w-0 px-3 py-2 text-left text-sm text-glyvex-text">
                            <span className="truncate block">{t.name}</span>
                            {t.builtin && <span className="text-xs text-glyvex-muted">predefinido</span>}
                          </button>
                          {!t.builtin && (
                            <button type="button" title={`Eliminar ${t.name}`}
                              onClick={(e) => { e.stopPropagation(); deleteTemplate(t.name); }}
                              className="p-2 rounded text-glyvex-muted hover:text-red-400 hover:bg-red-500/10 shrink-0">
                              <Trash2 size={14} />
                            </button>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </Field>
              <div className="flex gap-2">
                <input className={inputClasses} value={newTemplateName} onChange={(e) => setNewTemplateName(e.target.value)} placeholder="Nombre para guardar como template…" />
                <button type="button" onClick={saveTemplate} className="flex items-center gap-1 px-3 py-2 rounded-md text-sm border border-white/10 text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-card shrink-0"><Save size={14} />Guardar</button>
              </div>
            </Panel>

            <Panel icon={Cpu} title="Contexto">
              <Toggle label="n_ctx (--ctx-size)" checked={toggles.n_ctx}
                onChange={(v) => setToggle("n_ctx", v)}
                disabled={isToggleUnavailable("n_ctx")} title={toggleTitle("n_ctx")} />
              <Field label="n_ctx (tamaño de contexto)">
                <select className={selectClasses}
                  disabled={!toggles.n_ctx || isToggleUnavailable("n_ctx")}
                  value={showCustomCtx ? "custom" : launchConfig.n_ctx}
                  onChange={(e) => {
                    if (e.target.value === "custom") {
                      setCustomCtx(true);
                      updateConfig({ n_ctx: 0 });
                    } else {
                      setCustomCtx(false);
                      updateConfig({ n_ctx: Number(e.target.value) });
                    }
                  }}>
                  {N_CTX_PRESETS.map((v) => <option key={v} value={v}>{N_CTX_LABELS[v]}</option>)}
                  <option value="custom">Custom…</option>
                </select>
              </Field>
              {showCustomCtx && (
                <Field label="n_ctx custom" hint="Cualquier valor mayor a 0 (en tokens).">
                  <input type="number" min={1} className={inputClasses}
                    disabled={!toggles.n_ctx || isToggleUnavailable("n_ctx")}
                    value={launchConfig.n_ctx || ""}
                    onChange={(e) => updateConfig({ n_ctx: Number(e.target.value) })} />
                </Field>
              )}
              {nativeCtx && (
                <p className={"text-xs " + (ctxOverflow ? "text-amber-400" : "text-glyvex-muted")}>
                  Contexto nativo: {nativeCtx.toLocaleString()} tokens
                  {` (n_ctx actual: ${Number(launchConfig.n_ctx).toLocaleString()})`}
                </p>
              )}
              <Toggle label="n_batch (--batch-size)" checked={toggles.n_batch}
                onChange={(v) => setToggle("n_batch", v)}
                disabled={isToggleUnavailable("n_batch")} title={toggleTitle("n_batch")} />
              <Field label="n_batch" hint="2048 (default de llama.cpp) rinde más en prompt processing que 512 en GPUs anchas.">
                <input type="number" className={inputClasses}
                  disabled={!toggles.n_batch || isToggleUnavailable("n_batch")}
                  value={launchConfig.n_batch} onChange={(e) => updateConfig({ n_batch: Number(e.target.value) })} />
              </Field>
              <Toggle label="n_ubatch (--ubatch-size)" checked={toggles.n_ubatch}
                onChange={(v) => setToggle("n_ubatch", v)}
                disabled={isToggleUnavailable("n_ubatch")} title={toggleTitle("n_ubatch")} />
              <Field label="n_ubatch" hint="Tamaño del compute buffer por paso. 512 no lo infla; más rinde en CPUs.">
                <input type="number" min={1} className={inputClasses}
                  disabled={!toggles.n_ubatch || isToggleUnavailable("n_ubatch")}
                  value={launchConfig.n_ubatch} onChange={(e) => updateConfig({ n_ubatch: Number(e.target.value) })} />
              </Field>
            </Panel>

            <Panel icon={GaugeIcon} title="Aceleración">
              <Field label="Modo">
                <div className="flex gap-2">
                  {["gpu_only", "cpu_only", "hybrid"].map((mode) => (
                    <button key={mode} type="button"
                      onClick={() => updateConfig({ gpu_mode: mode, n_gpu_layers: mode === "cpu_only" ? 0 : launchConfig.n_gpu_layers === 0 ? -1 : launchConfig.n_gpu_layers })}
                      className={"flex-1 px-3 py-2 rounded-md text-sm border " + (launchConfig.gpu_mode === mode ? "bg-glyvex-accent/15 border-glyvex-accent text-glyvex-accent" : "border-white/10 text-glyvex-muted hover:text-glyvex-text")}>
                      {mode}
                    </button>
                  ))}
                </div>
              </Field>
              <Field label={`n_gpu_layers: ${launchConfig.n_gpu_layers === -1 ? "todas" : launchConfig.n_gpu_layers}`}>
                <input type="range" min={-1} max={100} value={launchConfig.n_gpu_layers} disabled={launchConfig.gpu_mode === "cpu_only"} onChange={(e) => updateConfig({ n_gpu_layers: Number(e.target.value) })} className="w-full accent-glyvex-accent" />
              </Field>
            </Panel>

            {advancedMode && (
              <>
                <Panel icon={Sliders} title="Memoria / Precisión">
                  <div className="grid grid-cols-2 gap-3">
                    <Toggle label="cache_type_k" checked={toggles.cache_type_k}
                      onChange={(v) => setToggle("cache_type_k", v)}
                      disabled={isToggleUnavailable("cache_type_k")} title={toggleTitle("cache_type_k")} />
                    <Toggle label="cache_type_v" checked={toggles.cache_type_v}
                      onChange={(v) => setToggle("cache_type_v", v)}
                      disabled={isToggleUnavailable("cache_type_v")} title={toggleTitle("cache_type_v")} />
                  </div>
                  <div className="grid grid-cols-2 gap-4">
                    <Field label="cache_type_k"><select className={selectClasses}
                      disabled={!toggles.cache_type_k || isToggleUnavailable("cache_type_k")}
                      value={launchConfig.cache_type_k} onChange={(e) => updateConfig({ cache_type_k: e.target.value })}>{CACHE_TYPES.map((c) => <option key={c} value={c}>{c}</option>)}</select></Field>
                    <Field label="cache_type_v"><select className={selectClasses}
                      disabled={!toggles.cache_type_v || isToggleUnavailable("cache_type_v")}
                      value={launchConfig.cache_type_v} onChange={(e) => updateConfig({ cache_type_v: e.target.value })}>{CACHE_TYPES.map((c) => <option key={c} value={c}>{c}</option>)}</select></Field>
                  </div>
                  {launchConfig.flash_attn && launchConfig.cache_type_k !== launchConfig.cache_type_v && (
                    <p className="text-xs text-red-400">
                      ⚠ Con flash_attn=on el KV cache debe ser simétrico y estar en
                      q4_0/q4_0, q8_0/q8_0, f16/f16 o bf16/bf16 (FA_QUANTS de la build).
                    </p>
                  )}
                  <Toggle label="flash_attn" checked={launchConfig.flash_attn} onChange={(v) => updateConfig({ flash_attn: v })} />
                  <Field label="load_mode" hint="Reemplaza a mlock/mmap: cómo carga el binario el archivo del modelo (--load-mode).">
                    <select className={selectClasses} value={launchConfig.load_mode} onChange={(e) => updateConfig({ load_mode: e.target.value })}>
                      {LOAD_MODE_OPTIONS.map((m) => <option key={m} value={m}>{m}</option>)}
                    </select>
                  </Field>
                </Panel>

                <Panel icon={Puzzle} title="Módulos opcionales">
                  {selectedModel.mtp_embedded && (
                    <Toggle
                      label={`MTP incluido en el modelo${selectedModel.mtp_embedded_layers ? ` (${selectedModel.mtp_embedded_layers} capa${selectedModel.mtp_embedded_layers !== 1 ? "s" : ""} nextn)` : ""}`}
                      checked={launchConfig.mtp_embedded}
                      onChange={(v) => updateConfig({ mtp_embedded: v })}
                    />
                  )}
                  <div className="grid grid-cols-2 gap-3">
                    <Field
                      label="MTP draft model (path)"
                      hint={launchConfig.mtp_embedded ? "El modelo ya trae la cabeza MTP: no necesita un archivo aparte." : undefined}>
                      <input className={inputClasses}
                        value={launchConfig.mtp_draft_model || ""}
                        onChange={(e) => updateConfig({ mtp_draft_model: e.target.value })}
                        placeholder={launchConfig.mtp_embedded ? "No requiere path" : "/models/draft.gguf"}
                        disabled={launchConfig.mtp_embedded} />
                    </Field>
                    <Field label="n_draft" hint="Tokens a especular por paso (--spec-draft-n-max).">
                      <input type="number" className={inputClasses} value={launchConfig.n_draft} onChange={(e) => updateConfig({ n_draft: Number(e.target.value) })} />
                    </Field>
                  </div>
                  {(launchConfig.mtp_embedded || launchConfig.mtp_draft_model) && (
                    <div className="grid grid-cols-2 gap-3">
                      <Field label="cache_type_k (draft)" hint="q8_0 ahorra ~50% de VRAM del draft vs el default f16 de llama-server.">
                        <select className={selectClasses} value={launchConfig.cache_type_k_draft}
                          onChange={(e) => updateConfig({ cache_type_k_draft: e.target.value })}>
                          <option value="">Automático (f16)</option>
                          {CACHE_TYPES.map((c) => <option key={c} value={c}>{c}</option>)}
                        </select>
                      </Field>
                      <Field label="cache_type_v (draft)">
                        <select className={selectClasses} value={launchConfig.cache_type_v_draft}
                          onChange={(e) => updateConfig({ cache_type_v_draft: e.target.value })}>
                          <option value="">Automático (f16)</option>
                          {CACHE_TYPES.map((c) => <option key={c} value={c}>{c}</option>)}
                        </select>
                      </Field>
                    </div>
                  )}
                  {(launchConfig.mtp_embedded || launchConfig.mtp_draft_model) &&
                    launchConfig.flash_attn &&
                    (launchConfig.cache_type_k_draft || "f16") !== (launchConfig.cache_type_v_draft || "f16") && (
                      <p className="text-xs text-red-400 -mt-2">
                        ⚠ El KV del draft también cae bajo FA_QUANTS: con flash_attn=on
                        k y v deben ser iguales (o ambos en Automático). El backend
                        rechaza el lanzamiento si difieren.
                      </p>
                    )}
                  <Toggle label="mmproj (--mmproj)" checked={toggles.mmproj}
                    onChange={(v) => {
                      setToggle("mmproj", v);
                      // Al activarlo se liga el archivo detectado si no hay
                      // path: lo que se ve en el campo es lo que se envía.
                      if (v && !launchConfig.mmproj_path && selectedModel?.mmproj_path) {
                        updateConfig({ mmproj_path: selectedModel.mmproj_path });
                      }
                    }}
                    disabled={isToggleUnavailable("mmproj")} title={toggleTitle("mmproj")} />
                  <Field
                    label="Módulo de visión — mmproj (path)"
                    hint={
                      selectedModel.has_vision_embedded
                        ? "Este modelo trae el encoder de visión adentro: no necesita un mmproj aparte."
                        : selectedModel.has_mmproj
                          ? "Este modelo tiene un mmproj detectado en el scan."
                          : undefined
                    }>
                    <input className={inputClasses}
                      disabled={!toggles.mmproj || isToggleUnavailable("mmproj")}
                      value={launchConfig.mmproj_path || (selectedModel.has_vision_embedded ? "" : (selectedModel.mmproj_path ?? ""))}
                      onChange={(e) => updateConfig({ mmproj_path: e.target.value })}
                      placeholder={selectedModel.has_vision_embedded ? "No requiere path" : "/models/mmproj.gguf"} />
                  </Field>
                  <Field label="LoRA (path)"><input className={inputClasses} value={launchConfig.lora_path || ""} onChange={(e) => updateConfig({ lora_path: e.target.value })} placeholder="/models/lora.gguf" /></Field>
                  <Field label={`lora_scale: ${Number(launchConfig.lora_scale).toFixed(2)}`}><input type="range" min={0} max={2} step={0.05} value={launchConfig.lora_scale} onChange={(e) => updateConfig({ lora_scale: Number(e.target.value) })} className="w-full accent-glyvex-accent" /></Field>
                </Panel>

                <Panel icon={BrainCircuit} title="Razonamiento">
                  <Toggle
                    label="thinking_enabled"
                    checked={launchConfig.thinking_enabled}
                    onChange={(v) => updateConfig({ thinking_enabled: v })}
                    disabled={thinkingUnsupported}
                    title={thinkingUnsupported ? (modelMeta.thinking_support
                      ? "El template de este modelo no usa enable_thinking"
                      : "Este modelo no soporta thinking") : undefined}
                  />
                  {thinkingUnsupported && (
                    <p className="text-xs text-glyvex-muted-2">
                      {modelMeta.thinking_support
                        ? "El template de este modelo no usa enable_thinking: el thinking no se puede controlar desde aquí."
                        : "Este modelo no soporta thinking."}
                    </p>
                  )}
                  {launchConfig.backend === "ollama" && (
                    <p className="text-xs text-amber-400">
                      ⚠ Ollama no soporta reasoning_effort ni chat templates nativos.
                    </p>
                  )}
                  <Field label={`budget_tokens: ${launchConfig.budget_tokens === -1 ? "∞" : launchConfig.budget_tokens}`}>
                    <select className={selectClasses} disabled={!launchConfig.thinking_enabled || thinkingUnsupported} value={launchConfig.budget_tokens} onChange={(e) => updateConfig({ budget_tokens: Number(e.target.value) })}>
                      {BUDGET_PRESETS.map((v) => <option key={v} value={v}>{v.toLocaleString()}</option>)}
                      <option value={-1}>∞ (sin límite)</option>
                    </select>
                  </Field>
                  <Toggle
                    label="--jinja (requerido para reasoning_effort)"
                    checked={launchConfig.jinja}
                    onChange={(v) => updateConfig({ jinja: v })}
                  />
                  <Field label="reasoning_effort">
                    <div className="flex gap-2 flex-wrap">
                      {["none", "low", "medium", "high", "xhigh"].map((level) => (
                        <button key={level} type="button"
                            disabled={!launchConfig.jinja || thinkingUnsupported}
                          onClick={() => updateConfig({ reasoning_effort: level })}
                          className={
                            "px-3 py-1.5 rounded-md text-xs border capitalize " +
                            (launchConfig.reasoning_effort === level
                              ? "bg-glyvex-accent/15 border-glyvex-accent text-glyvex-accent "
                              : "border-glyvex-border text-glyvex-muted hover:text-glyvex-text ") +
                            "disabled:opacity-40 disabled:cursor-not-allowed"
                          }>
                          {level}
                        </button>
                      ))}
                    </div>
                    <p className="text-xs text-glyvex-muted mt-1">
                      Solo llama-server con --jinja. No compatible con Ollama.
                    </p>
                  </Field>
                  <Toggle
                    label="no_reasoning_preserve (ahorra tokens)"
                    checked={launchConfig.no_reasoning_preserve}
                    disabled={thinkingUnsupported}
                    onChange={(v) => updateConfig({ no_reasoning_preserve: v })}
                  />
                  <p className="text-xs text-glyvex-muted -mt-2">
                    La build 11003 preserva el razonamiento en cada turno por defecto
                    (gasta tokens extra); activarlo agrega --no-reasoning-preserve.
                  </p>
                  <Field label={`reasoning_budget: ${launchConfig.reasoning_budget === -1 ? "∞" : launchConfig.reasoning_budget}`}>
                    <select className={selectClasses} disabled={thinkingUnsupported} value={launchConfig.reasoning_budget} onChange={(e) => updateConfig({ reasoning_budget: Number(e.target.value) })}>
                      <option value={-1}>∞ (sin límite)</option>
                      <option value={0}>0 (fin inmediato)</option>
                      {BUDGET_PRESETS.map((v) => <option key={v} value={v}>{v.toLocaleString()}</option>)}
                    </select>
                  </Field>
                  <p className="text-xs text-glyvex-muted -mt-2">
                    Token budget nativo del server (--reasoning-budget). Controla el
                    razonamiento en todos los requests, no solo con thinking enabled.
                  </p>
                </Panel>
              </>
            )}

            {samplingPanel}

            {advancedMode && (
              <>
                <Panel icon={Server} title="Servidor">
                  <div className="grid grid-cols-2 gap-3">
                    <Toggle label="n_parallel (--parallel)" checked={toggles.n_parallel}
                      onChange={(v) => setToggle("n_parallel", v)}
                      disabled={isToggleUnavailable("n_parallel")} title={toggleTitle("n_parallel")} />
                    <Toggle label="n_threads (--threads)" checked={toggles.n_threads}
                      onChange={(v) => setToggle("n_threads", v)}
                      disabled={isToggleUnavailable("n_threads")} title={toggleTitle("n_threads")} />
                  </div>
                  <div className="grid grid-cols-2 gap-4">
                    <Field label="Host"><input className={inputClasses} value={launchConfig.host} onChange={(e) => updateConfig({ host: e.target.value })} /></Field>
                    <Field label="Port"><input type="number" className={inputClasses} value={launchConfig.port} onChange={(e) => updateConfig({ port: Number(e.target.value) })} /></Field>
                    <Field label="n_parallel (1–8)"><input type="number" min={1} max={8} className={inputClasses}
                      disabled={!toggles.n_parallel || isToggleUnavailable("n_parallel")}
                      value={launchConfig.n_parallel} onChange={(e) => updateConfig({ n_parallel: Number(e.target.value) })} /></Field>
                    <Field label="n_threads (-1 = auto)"><input type="number" className={inputClasses}
                      disabled={!toggles.n_threads || isToggleUnavailable("n_threads")}
                      value={launchConfig.n_threads} onChange={(e) => updateConfig({ n_threads: Number(e.target.value) })} /></Field>
                  </div>
                  {launchConfig.n_parallel > 1 && !launchConfig.kv_unified && (
                    <p className="text-xs text-amber-400">
                      ⚠ Con n_parallel &gt; 1 y kv_unified apagado, cada slot reserva su
                      propio KV cache completo (x{launchConfig.n_parallel} la VRAM de contexto).
                    </p>
                  )}
                  <Field label="API key (opcional)"><input className={inputClasses} value={launchConfig.api_key} onChange={(e) => updateConfig({ api_key: e.target.value })} placeholder="Dejar vacío para no requerir auth" /></Field>
                </Panel>

                <Panel icon={Settings} title="Parámetros avanzados">
                  <Toggle label="RoPE (--rope-scaling, grupo)" checked={toggles.rope}
                    onChange={(v) => setToggle("rope", v)}
                    disabled={isToggleUnavailable("rope")} title={toggleTitle("rope")} />
                  <div className="grid grid-cols-2 gap-4">
                    <Field label="rope_freq_base" hint="0 = auto">
                      <input type="number" min={0} step={1000} className={inputClasses}
                        disabled={!toggles.rope || isToggleUnavailable("rope")}
                        value={launchConfig.rope_freq_base}
                        onChange={(e) => updateConfig({ rope_freq_base: Number(e.target.value) })} />
                    </Field>
                    <Field label="rope_scaling_type">
                      <select className={selectClasses}
                        disabled={!toggles.rope || isToggleUnavailable("rope")}
                        value={launchConfig.rope_scaling_type}
                        onChange={(e) => updateConfig({ rope_scaling_type: e.target.value })}>
                        {ROPE_SCALING_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                      </select>
                    </Field>
                  </div>
                  {launchConfig.rope_scaling_type === "yarn" && (
                    <Field label="yarn_ext_factor" hint="-1 = auto">
                      <input type="number" step={0.1} className={inputClasses}
                        disabled={!toggles.rope || isToggleUnavailable("rope")}
                        value={launchConfig.yarn_ext_factor}
                        onChange={(e) => updateConfig({ yarn_ext_factor: Number(e.target.value) })} />
                    </Field>
                  )}
                  <Toggle label="numa" checked={launchConfig.numa} onChange={(v) => updateConfig({ numa: v })} />
                  <Toggle label="no_kv_offload" checked={launchConfig.no_kv_offload} onChange={(v) => updateConfig({ no_kv_offload: v })} />

                  <Toggle label="cache_reuse (--cache-reuse)" checked={toggles.cache_reuse}
                    onChange={(v) => setToggle("cache_reuse", v)}
                    disabled={isToggleUnavailable("cache_reuse")} title={toggleTitle("cache_reuse")} />
                  <Field label={`cache_reuse: ${launchConfig.cache_reuse === 0 ? "0 (desactivado)" : launchConfig.cache_reuse}`}>
                    <input type="range" min={0} max={256} step={1} value={launchConfig.cache_reuse}
                      disabled={!toggles.cache_reuse || isToggleUnavailable("cache_reuse")}
                      onChange={(e) => updateConfig({ cache_reuse: Number(e.target.value) })}
                      className="w-full accent-glyvex-accent" />
                  </Field>
                  <Toggle label="defrag_thold (--defrag-thold)" checked={toggles.defrag_thold}
                    onChange={(v) => setToggle("defrag_thold", v)}
                    disabled={isToggleUnavailable("defrag_thold")} title={toggleTitle("defrag_thold")} />
                  <Field label={`defrag_thold: ${launchConfig.defrag_thold < 0 ? "-1 (desactivado)" : Number(launchConfig.defrag_thold).toFixed(2)}`}
                    hint="Marcado DEPRECATED en el --help del binario; el backend lo sigue enviando por ahora.">
                    <input type="range" min={-1} max={1} step={0.01} value={launchConfig.defrag_thold}
                      disabled={!toggles.defrag_thold || isToggleUnavailable("defrag_thold")}
                      onChange={(e) => updateConfig({ defrag_thold: Number(e.target.value) })}
                      className="w-full accent-glyvex-accent" />
                  </Field>

                  <Toggle label="grp_attn (--grp-attn-n/w, grupo)" checked={toggles.grp_attn}
                    onChange={(v) => setToggle("grp_attn", v)}
                    disabled={isToggleUnavailable("grp_attn")} title={toggleTitle("grp_attn")} />
                  <div className="grid grid-cols-2 gap-4">
                    <Field label="grp_attn_n" hint="1 = desactivado">
                      <input type="number" min={1} className={inputClasses} value={launchConfig.grp_attn_n}
                        disabled={!toggles.grp_attn || isToggleUnavailable("grp_attn")}
                        onChange={(e) => updateConfig({ grp_attn_n: Number(e.target.value) })} />
                    </Field>
                    {launchConfig.grp_attn_n > 1 && (
                      <Field label="grp_attn_w">
                        <input type="number" min={1} className={inputClasses} value={launchConfig.grp_attn_w}
                          disabled={!toggles.grp_attn || isToggleUnavailable("grp_attn")}
                          onChange={(e) => updateConfig({ grp_attn_w: Number(e.target.value) })} />
                      </Field>
                    )}
                  </div>
                  <div className="border-t border-white/10 pt-4 space-y-4">
                    <p className="text-xs text-glyvex-muted uppercase tracking-wide">
                      Checkpoints y memoria (build 11003+)
                    </p>
                    <div className="grid grid-cols-2 gap-3">
                      <Toggle label="ctx_checkpoints" checked={toggles.ctx_checkpoints}
                        onChange={(v) => setToggle("ctx_checkpoints", v)}
                        disabled={isToggleUnavailable("ctx_checkpoints")} title={toggleTitle("ctx_checkpoints")} />
                      <Toggle label="checkpoint_min_step" checked={toggles.checkpoint_min_step}
                        onChange={(v) => setToggle("checkpoint_min_step", v)}
                        disabled={isToggleUnavailable("checkpoint_min_step")} title={toggleTitle("checkpoint_min_step")} />
                      <Toggle label="cache_ram (--cache-ram)" checked={toggles.cache_ram}
                        onChange={(v) => setToggle("cache_ram", v)}
                        disabled={isToggleUnavailable("cache_ram")} title={toggleTitle("cache_ram")} />
                      <Toggle label="fit_target (--fit-target)" checked={toggles.fit_target}
                        onChange={(v) => setToggle("fit_target", v)}
                        disabled={isToggleUnavailable("fit_target")} title={toggleTitle("fit_target")} />
                      <Toggle label="fit (--fit on)" checked={toggles.fit}
                        onChange={(v) => setToggle("fit", v)}
                        disabled={isToggleUnavailable("fit")} title={toggleTitle("fit")} />
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                      <Field label="ctx_checkpoints" hint="Máx. checkpoints de contexto por slot (~150 MiB de VRAM c/u). Default de la build: 32.">
                        <input type="number" min={0} className={inputClasses} value={launchConfig.ctx_checkpoints}
                          disabled={!toggles.ctx_checkpoints || isToggleUnavailable("ctx_checkpoints")}
                          onChange={(e) => updateConfig({ ctx_checkpoints: Number(e.target.value) })} />
                      </Field>
                      <Field label="checkpoint_min_step" hint="Espaciado mínimo entre checkpoints (tokens). Default de la build: 8192.">
                        <input type="number" min={512} step={512} className={inputClasses} value={launchConfig.checkpoint_min_step}
                          disabled={!toggles.checkpoint_min_step || isToggleUnavailable("checkpoint_min_step")}
                          onChange={(e) => updateConfig({ checkpoint_min_step: Number(e.target.value) })} />
                      </Field>
                      <Field label="cache_ram_mib" hint="Límite del prompt cache en RAM del sistema (0 = desactivado).">
                        <input type="number" min={0} step={1024} className={inputClasses} value={launchConfig.cache_ram_mib}
                          disabled={!toggles.cache_ram || isToggleUnavailable("cache_ram")}
                          onChange={(e) => updateConfig({ cache_ram_mib: Number(e.target.value) })} />
                      </Field>
                      <Field label="fit_target_mib" hint="Margen de VRAM a reservar por dispositivo (0 = off).">
                        <input type="number" min={0} step={512} className={inputClasses} value={launchConfig.fit_target_mib}
                          disabled={!toggles.fit_target || isToggleUnavailable("fit_target")}
                          onChange={(e) => updateConfig({ fit_target_mib: Number(e.target.value) })} />
                      </Field>
                    </div>
                    <Toggle label="kv_unified (KV compartido entre slots)" checked={launchConfig.kv_unified}
                      onChange={(v) => updateConfig({ kv_unified: v })} />
                    <Field label="kv_unified_per_slot" hint="Contexto por slot con kv_unified activo (0 = sin límite, usa n_ctx).">
                      <input type="number" min={0} step={4096} className={inputClasses} value={launchConfig.kv_unified_per_slot}
                        onChange={(e) => updateConfig({ kv_unified_per_slot: Number(e.target.value) })} />
                    </Field>
                    <Field label="sleep_idle_seconds" hint="Segundos de inactividad hasta que el server libera VRAM (0 = off).">
                      <input type="number" min={0} step={60} className={inputClasses} value={launchConfig.sleep_idle_seconds}
                        onChange={(e) => updateConfig({ sleep_idle_seconds: Number(e.target.value) })} />
                    </Field>
                    <Toggle label="warmup (corrida vacía al arrancar)" checked={launchConfig.warmup}
                      onChange={(v) => updateConfig({ warmup: v })} />
                    <Field label="lazy_mode" hint="Lectura bajo demanda de tensores grandes (auto = solo >4GiB).">
                      <select className={selectClasses} value={launchConfig.lazy_mode} onChange={(e) => updateConfig({ lazy_mode: e.target.value })}>
                        {["auto", "on", "off"].map((m) => <option key={m} value={m}>{m}</option>)}
                      </select>
                    </Field>
                  </div>
                </Panel>
              </>
            )}
          </div>

          <Panel icon={GaugeIcon} title="Estado del proceso">
            {!processInfo ? (
              <p className="text-sm text-glyvex-muted">Sin proceso activo para este modelo.</p>
            ) : (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-3">
                  {badge && <span className={`px-2 py-1 rounded border text-xs font-medium ${badge.classes}`}>{badge.label}</span>}
                  {processInfo.pid && <span className="text-sm text-glyvex-muted">PID {processInfo.pid}</span>}
                  <span className="text-sm text-glyvex-muted">{processInfo.host}:{processInfo.port}</span>
                  {processInfo.state === "running" && <span className="text-sm text-emerald-400">✓ Servidor listo</span>}
                </div>
                {processInfo.warning && <p className="text-sm text-amber-400">{processInfo.warning}</p>}
                {processInfo.error_message && <p className="text-sm text-red-400">{processInfo.error_message}</p>}
                {processInfo.backend === "llama_server" && (
                  <ServerVitalsStrip processId={processInfo.process_id} state={processInfo.state} />
                )}
                {processInfo.command?.length > 0 && (
                  <details className="group">
                    <summary className="flex items-center gap-2 cursor-pointer text-xs text-glyvex-muted hover:text-glyvex-text select-none">
                      <Terminal size={13} />
                      Comando ejecutado
                      <ChevronRight size={12} className="group-open:rotate-90 transition-transform" />
                    </summary>
                    <div className="mt-2">
                      <CommandBlock argv={processInfo.command} />
                    </div>
                  </details>
                )}
                <LogTerminal processId={processInfo.process_id} />
              </div>
            )}
          </Panel>
        </div>
      )}
    </div>
  );
}
