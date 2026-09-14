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
} from "lucide-react";

// Espejo de SAMPLING_PRESETS de backend/launcher.py. Vive acá para poder
// previsualizar los valores sin round-trip; si cambian en el backend, hay
// que actualizarlos también acá (o pedirlos a /api/launcher/sampling-presets).
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
    presence_penalty: 1.5,
    repeat_penalty: 1.5,
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
  n_batch: 512,
  n_ubatch: 512,
  n_gpu_layers: -1,
  gpu_mode: "gpu_only",
  cache_type_k: "q4_0",
  cache_type_v: "q4_0",
  flash_attn: true,
  use_mlock: false,
  use_mmap: true,
  mtp_draft_model: "",
  n_draft: 5,
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
  presence_penalty: 0.0,
  repeat_penalty: 1.5,
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
  host: "127.0.0.1",
  port: 8080,
  n_threads: -1,
  n_parallel: 1,
  api_key: "",
  log_file: "data/logs/llama-server.log",
};

const TEMPLATE_FIELDS = [
  "n_ctx","n_batch","n_ubatch","n_gpu_layers","gpu_mode",
  "cache_type_k","cache_type_v","flash_attn","use_mlock","use_mmap",
  "n_draft","n_parallel",
  // Razonamiento
  "thinking_enabled","budget_tokens",
  // Sampling
  "sampling_preset","temperature","top_p","top_k",
  "min_p","presence_penalty","repeat_penalty",
  // Parámetros avanzados
  "rope_freq_base","rope_scaling_type","yarn_ext_factor",
  "numa","no_kv_offload","cache_reuse","defrag_thold",
  "grp_attn_n","grp_attn_w","jinja", "reasoning_effort",
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
const CACHE_TYPES = ["f16", "q8_0", "q4_0", "q4_1"];
const ROPE_SCALING_TYPES = ["none", "linear", "yarn"];

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

function GroupCard({ group, onSelect }) {
  const [expanded, setExpanded] = useState(false);
  const [selectedBaseId, setSelectedBaseId] = useState(group.base_models[0]?.id ?? null);
  const [useMtp, setUseMtp] = useState(false);
  const [selectedMtpId, setSelectedMtpId] = useState(group.mtp_models[0]?.id ?? null);
  const [useMmproj, setUseMmproj] = useState(false);
  const [selectedMmprojId, setSelectedMmprojId] = useState(group.mmproj_models[0]?.id ?? null);

  if (group.base_models.length === 0) return null;

  function handleConfigureClick() {
    if (!selectedBaseId) return;
    const mtpModel = useMtp ? group.mtp_models.find((m) => m.id === selectedMtpId) : null;
    const mmprojModel = useMmproj ? group.mmproj_models.find((m) => m.id === selectedMmprojId) : null;
    onSelect(selectedBaseId, {
      mtp_draft_model: mtpModel ? mtpModel.path : "",
      mmproj_path: mmprojModel ? mmprojModel.path : "",
    });
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
          {group.mtp_models.length > 0 && (
            <div>
              <label className="flex items-center gap-2 text-sm cursor-pointer mb-1.5">
                <input type="checkbox" checked={useMtp} onChange={(e) => setUseMtp(e.target.checked)} className="accent-glyvex-accent" />
                MTP draft model
              </label>
              {useMtp && group.mtp_models.length > 1 && (
                <select className="w-full bg-black/30 border border-white/10 rounded-md px-2 py-1.5 text-xs text-glyvex-text" value={selectedMtpId ?? ""} onChange={(e) => setSelectedMtpId(e.target.value)}>
                  {group.mtp_models.map((m) => <option key={m.id} value={m.id}>{m.name} ({m.size_gb} GB)</option>)}
                </select>
              )}
            </div>
          )}
          {group.mmproj_models.length > 0 && (
            <div>
              <label className="flex items-center gap-2 text-sm cursor-pointer mb-1.5">
                <input type="checkbox" checked={useMmproj} onChange={(e) => setUseMmproj(e.target.checked)} className="accent-glyvex-accent" />
                Módulo de visión (mmproj)
              </label>
              {useMmproj && group.mmproj_models.length > 1 && (
                <select className="w-full bg-black/30 border border-white/10 rounded-md px-2 py-1.5 text-xs text-glyvex-text" value={selectedMmprojId ?? ""} onChange={(e) => setSelectedMmprojId(e.target.value)}>
                  {group.mmproj_models.map((m) => <option key={m.id} value={m.id}>{m.name} ({m.size_gb} GB)</option>)}
                </select>
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
  const [launchConfig, setLaunchConfig] = useState(DEFAULT_LAUNCH_CONFIG);
  const [templateList, setTemplateList] = useState([]);
  const [selectedTemplate, setSelectedTemplate] = useState("");
  const [templateOpen, setTemplateOpen] = useState(false);
  const [newTemplateName, setNewTemplateName] = useState("");
  const [processInfo, setProcessInfo] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [launching, setLaunching] = useState(false);
  const [advancedMode, setAdvancedMode] = useState(false);
  const [customCtx, setCustomCtx] = useState(false);
  const [scrollTick, setScrollTick] = useState(0);
  const pollRef = useRef(null);
  const configPanelRef = useRef(null);
  const templateBoxRef = useRef(null);
  // Una vez que el usuario (o un template, o una config restaurada) definió
  // el sampling, la metadata del GGUF ya no lo pisa.
  const samplingTouchedRef = useRef(false);

  const selectedModel = useMemo(() => modelList.find((m) => m.id === selectedId) || null, [modelList, selectedId]);

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
          }
        }
      } catch { /* sin proceso activo que restaurar */ }
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
      // Un template guardado trae su propio sampling: no dejamos que la
      // metadata del modelo lo sobrescriba después.
      if (Object.prototype.hasOwnProperty.call(tpl.params, "sampling_preset")) {
        samplingTouchedRef.current = true;
      }
      updateConfig(tpl.params);
      setCustomCtx(false);
    }
  }

  async function saveTemplate() {
    const name = newTemplateName.trim();
    if (!name) return;
    const params = Object.fromEntries(TEMPLATE_FIELDS.map((k) => [k, launchConfig[k]]));
    setActionError(null);
    try {
      const res = await fetch("/api/launcher/templates", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, builtin: false, params }),
      });
      if (!res.ok) throw new Error("save_template_failed");
      const saved = await res.json();
      await reloadTemplates();
      setNewTemplateName("");
      setSelectedTemplate(saved.name);
    } catch { setActionError("No se pudo guardar el template."); }
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
  const handleSelectModel = useCallback((modelId, configOverrides = {}) => {
    setSelectedId(modelId);
    if (Object.keys(configOverrides).length > 0) {
      setLaunchConfig((prev) => ({ ...prev, ...configOverrides }));
    }
    setActionError(null);
    setScrollTick((tick) => tick + 1);
  }, []);

  const handleLaunch = useCallback(async () => {
    if (!selectedModel) return;
    setLaunching(true);
    setActionError(null);
    try {
      const payload = {
        ...launchConfig,
        model_id: selectedModel.id,
        mtp_draft_model: launchConfig.mtp_draft_model || null,
        mmproj_path: launchConfig.mmproj_path || null,
        lora_path: launchConfig.lora_path || null,
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
  }, [launchConfig, selectedModel]);

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
  const thinkingUnsupported = Boolean(metaOk && modelMeta.thinking_support === false);
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
    </Panel>
  );

  if (loading) return <p className="text-glyvex-muted text-sm">Cargando modelos…</p>;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Launcher</h1>
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
                <span className="text-glyvex-muted">Avanzado</span>
                <Toggle checked={advancedMode} onChange={setAdvancedMode} />
              </span>
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

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
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
                            onClick={() => { applyTemplate(t.name); setTemplateOpen(false); }}
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
              <Field label="n_ctx (tamaño de contexto)">
                <select className={selectClasses}
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
              <Field label="n_batch">
                <input type="number" className={inputClasses} value={launchConfig.n_batch} onChange={(e) => updateConfig({ n_batch: Number(e.target.value) })} />
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
                  <div className="grid grid-cols-2 gap-4">
                    <Field label="cache_type_k"><select className={selectClasses} value={launchConfig.cache_type_k} onChange={(e) => updateConfig({ cache_type_k: e.target.value })}>{CACHE_TYPES.map((c) => <option key={c} value={c}>{c}</option>)}</select></Field>
                    <Field label="cache_type_v"><select className={selectClasses} value={launchConfig.cache_type_v} onChange={(e) => updateConfig({ cache_type_v: e.target.value })}>{CACHE_TYPES.map((c) => <option key={c} value={c}>{c}</option>)}</select></Field>
                  </div>
                  <Toggle label="flash_attn" checked={launchConfig.flash_attn} onChange={(v) => updateConfig({ flash_attn: v })} />
                  <Toggle label="use_mlock" checked={launchConfig.use_mlock} onChange={(v) => updateConfig({ use_mlock: v })} />
                  <Toggle label="use_mmap" checked={launchConfig.use_mmap} onChange={(v) => updateConfig({ use_mmap: v })} />
                </Panel>

                <Panel icon={Puzzle} title="Módulos opcionales">
                  <div className="grid grid-cols-2 gap-3">
                    <Field label="MTP draft model (path)"><input className={inputClasses} value={launchConfig.mtp_draft_model || ""} onChange={(e) => updateConfig({ mtp_draft_model: e.target.value })} placeholder="/models/draft.gguf" /></Field>
                    <Field label="n_draft"><input type="number" className={inputClasses} value={launchConfig.n_draft} onChange={(e) => updateConfig({ n_draft: Number(e.target.value) })} /></Field>
                  </div>
                  <Field label="Módulo de visión — mmproj (path)" hint={selectedModel.has_mmproj ? "Este modelo tiene un mmproj detectado en el scan." : undefined}>
                    <input className={inputClasses} value={launchConfig.mmproj_path || (selectedModel.mmproj_path ?? "")} onChange={(e) => updateConfig({ mmproj_path: e.target.value })} placeholder="/models/mmproj.gguf" />
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
                    title={thinkingUnsupported ? "Este modelo no soporta thinking" : undefined}
                  />
                  {thinkingUnsupported && (
                    <p className="text-xs text-glyvex-muted-2">Este modelo no soporta thinking.</p>
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
                          disabled={!launchConfig.jinja}
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
                </Panel>
              </>
            )}

            {samplingPanel}

            {advancedMode && (
              <>
                <Panel icon={Server} title="Servidor">
                  <div className="grid grid-cols-2 gap-4">
                    <Field label="Host"><input className={inputClasses} value={launchConfig.host} onChange={(e) => updateConfig({ host: e.target.value })} /></Field>
                    <Field label="Port"><input type="number" className={inputClasses} value={launchConfig.port} onChange={(e) => updateConfig({ port: Number(e.target.value) })} /></Field>
                    <Field label="n_parallel (1–8)"><input type="number" min={1} max={8} className={inputClasses} value={launchConfig.n_parallel} onChange={(e) => updateConfig({ n_parallel: Number(e.target.value) })} /></Field>
                    <Field label="n_threads (-1 = auto)"><input type="number" className={inputClasses} value={launchConfig.n_threads} onChange={(e) => updateConfig({ n_threads: Number(e.target.value) })} /></Field>
                  </div>
                  <Field label="API key (opcional)"><input className={inputClasses} value={launchConfig.api_key} onChange={(e) => updateConfig({ api_key: e.target.value })} placeholder="Dejar vacío para no requerir auth" /></Field>
                </Panel>

                <Panel icon={Settings} title="Parámetros avanzados">
                  <div className="grid grid-cols-2 gap-4">
                    <Field label="rope_freq_base" hint="0 = auto">
                      <input type="number" min={0} step={1000} className={inputClasses}
                        value={launchConfig.rope_freq_base}
                        onChange={(e) => updateConfig({ rope_freq_base: Number(e.target.value) })} />
                    </Field>
                    <Field label="rope_scaling_type">
                      <select className={selectClasses} value={launchConfig.rope_scaling_type}
                        onChange={(e) => updateConfig({ rope_scaling_type: e.target.value })}>
                        {ROPE_SCALING_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                      </select>
                    </Field>
                  </div>
                  {launchConfig.rope_scaling_type === "yarn" && (
                    <Field label="yarn_ext_factor" hint="-1 = auto">
                      <input type="number" step={0.1} className={inputClasses}
                        value={launchConfig.yarn_ext_factor}
                        onChange={(e) => updateConfig({ yarn_ext_factor: Number(e.target.value) })} />
                    </Field>
                  )}

                  <Toggle label="numa" checked={launchConfig.numa} onChange={(v) => updateConfig({ numa: v })} />
                  <Toggle label="no_kv_offload" checked={launchConfig.no_kv_offload} onChange={(v) => updateConfig({ no_kv_offload: v })} />

                  <Field label={`cache_reuse: ${launchConfig.cache_reuse === 0 ? "0 (desactivado)" : launchConfig.cache_reuse}`}>
                    <input type="range" min={0} max={256} step={1} value={launchConfig.cache_reuse}
                      onChange={(e) => updateConfig({ cache_reuse: Number(e.target.value) })}
                      className="w-full accent-glyvex-accent" />
                  </Field>
                  <Field label={`defrag_thold: ${launchConfig.defrag_thold < 0 ? "-1 (desactivado)" : Number(launchConfig.defrag_thold).toFixed(2)}`}>
                    <input type="range" min={-1} max={1} step={0.01} value={launchConfig.defrag_thold}
                      onChange={(e) => updateConfig({ defrag_thold: Number(e.target.value) })}
                      className="w-full accent-glyvex-accent" />
                  </Field>

                  <div className="grid grid-cols-2 gap-4">
                    <Field label="grp_attn_n" hint="1 = desactivado">
                      <input type="number" min={1} className={inputClasses} value={launchConfig.grp_attn_n}
                        onChange={(e) => updateConfig({ grp_attn_n: Number(e.target.value) })} />
                    </Field>
                    {launchConfig.grp_attn_n > 1 && (
                      <Field label="grp_attn_w">
                        <input type="number" min={1} className={inputClasses} value={launchConfig.grp_attn_w}
                          onChange={(e) => updateConfig({ grp_attn_w: Number(e.target.value) })} />
                      </Field>
                    )}
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
                {processInfo.error_message && <p className="text-sm text-red-400">{processInfo.error_message}</p>}
                <LogTerminal processId={processInfo.process_id} />
              </div>
            )}
          </Panel>
        </div>
      )}
    </div>
  );
}
