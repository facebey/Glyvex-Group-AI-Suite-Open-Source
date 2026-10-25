// frontend/src/pages/Benchmark.jsx
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, ChevronDown, ChevronRight, Play, Square, Download, Check, X, Info } from "lucide-react";
import { useLocalStorage } from "../hooks/useLocalStorage.js";
import { useTranslation } from "react-i18next";
import { groupAllOn, toggleGroup, turnGroupOn, turnGroupOff } from "../lib/benchmarkSelection.js";
import JudgePanel from "../components/JudgePanel.jsx";
import {
  BarChart, Bar, ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts";

const ACTIVE_RUN_KEY = "glyvex_active_run_id";
// Valor centinela del <select>: permite probar un endpoint externo al que no
// levantamos nosotros (K2.a). Al elegirlo aparecen inputs de URL y api_key.
const CUSTOM_ENDPOINT = "__custom__";

// K2.b: campos de sampling seleccionables en «modo avanzado». Vacío = no se
// manda al body (el endpoint conserva sus defaults). top_k/min_p/repeat_penalty/
// seed los entiende llama-server; un endpoint ajeno puede ignorarlos.
const SAMPLING_FIELDS = [
  { key: "top_p", step: "0.01", min: 0, max: 1 },
  { key: "top_k", step: "1", min: 0 },
  { key: "min_p", step: "0.01", min: 0, max: 1 },
  { key: "repeat_penalty", step: "0.01", min: 0 },
  { key: "seed", step: "1", min: 0 },
];
const SAMPLING_KEYS = SAMPLING_FIELDS.map((f) => f.key);
const EMPTY_SAMPLING = Object.fromEntries(SAMPLING_KEYS.map((k) => [k, ""]));

const inputClasses =
  "w-full bg-glyvex-veil-disabled border border-glyvex-border-soft rounded-md px-3 py-2 text-sm " +
  "text-glyvex-text placeholder:text-glyvex-muted/60 focus:outline-none " +
  "focus:ring-2 focus:ring-glyvex-accent/60";

function wsUrlFor(path) {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}${path}`;
}

function downloadBlob(content, filename, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

function resultsToCsv(results) {
  const header = ["set_id","prompt_id","prompt_title","ok","tps","ttft_ms","tokens_generated","keywords_found","keywords_missing","score_auto","score_judge","judge_model","error"];
  const rows = results.map((r) => [
    r.set_id, r.prompt_id, (r.prompt_title || "").replace(/,/g, ";"),
    r.error ? "false" : "true",
    r.metrics?.tps ?? "", r.metrics?.ttft_ms ?? "", r.metrics?.tokens_generated ?? "",
    (r.keywords_found || []).join("|"), (r.keywords_missing || []).join("|"),
    r.score_auto ?? "", r.score_judge ?? "", r.judge_model ?? "",
    (r.error || "").replace(/,/g, ";"),
  ]);
  return [header, ...rows].map((row) => row.join(",")).join("\n");
}

// score_auto puede venir como fracción (0–1) o como porcentaje (0–100).
function fmtScoreKw(score) {
  if (score == null) return "—";
  const pct = score <= 1 ? score * 100 : score;
  return `${Math.round(pct)}%`;
}

function judgeBadgeClasses(score) {
  if (score >= 7) return "bg-emerald-500/15 text-emerald-400 border-emerald-500/30";
  if (score >= 4) return "bg-amber-500/15 text-amber-400 border-amber-500/30";
  return "bg-red-500/15 text-red-400 border-red-500/30";
}

function JudgeScoreBadge({ score }) {
  if (score == null) return <span className="text-glyvex-muted">—</span>;
  const shown = Number.isInteger(score) ? score : Math.round(score * 10) / 10;
  return (
    <span className={"inline-flex items-center px-2 py-0.5 rounded border text-xs font-medium " + judgeBadgeClasses(score)}>
      {shown}/10
    </span>
  );
}

function ReasoningCell({ reasoning }) {
  if (!reasoning) return <span className="text-glyvex-muted">—</span>;
  return (
    <span
      onClick={(e) => e.stopPropagation()}
      title={reasoning}
      className="inline-flex text-glyvex-muted hover:text-glyvex-text cursor-help"
    >
      <Info size={14} />
    </span>
  );
}

function liveRowFromResult(r) {
  return {
    set_id: r.set_id,
    prompt_id: r.prompt_id,
    ok: !r.error,
    tps: r.metrics?.tps ?? null,
  };
}

function SetCard({ set, checked, onToggle, expanded, onToggleExpand, detail, offPrompts = [], onTogglePrompt, onToggleAll, allOn = false }) {
  const { t } = useTranslation();
  const setName = t(`benchmark.sets.items.${set.id}.name`, { defaultValue: set.name });
  const setDesc = t(`benchmark.sets.items.${set.id}.description`, { defaultValue: set.description });
  return (
    <div className="bg-glyvex-card rounded-lg border border-glyvex-border-soft overflow-hidden">
      <div className="flex items-center gap-3 p-4">
        <input type="checkbox" checked={checked} onChange={onToggle} className="w-4 h-4 accent-glyvex-accent shrink-0" />
        <span className="text-xl">{set.icon}</span>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium">{setName}</p>
          <p className="text-xs text-glyvex-muted">{setDesc} · {set.prompt_count} prompts</p>
        </div>
        <button type="button" onClick={onToggleExpand} className="text-glyvex-muted hover:text-glyvex-text shrink-0">
          {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </button>
      </div>
      {expanded && (
        <div className="border-t border-glyvex-border-soft px-4 py-3 space-y-2 max-h-56 overflow-y-auto">
          {!detail ? <p className="text-xs text-glyvex-muted">{t("benchmark.sets.loading")}</p> : (
            <>
              <label className="flex cursor-pointer items-center gap-2 text-xs font-medium">
                <input type="checkbox" checked={allOn} onChange={onToggleAll}
                  className="h-3.5 w-3.5 shrink-0 accent-glyvex-accent" />
                <span>{t("benchmark.prompts.all")}</span>
              </label>
              {detail.prompts.map((p) => (
                <label key={p.id} className="flex cursor-pointer items-center gap-2 text-xs">
                  <input type="checkbox" checked={!offPrompts.includes(p.id)} onChange={() => onTogglePrompt?.(p.id)}
                    className="h-3.5 w-3.5 shrink-0 accent-glyvex-accent" />
                  <span className="text-glyvex-text font-medium">{p.title}</span>
                  <span className="text-glyvex-muted"> — {t(`benchmark.difficulty.${p.difficulty}`, { defaultValue: p.difficulty })}</span>
                </label>
              ))}
              <p className="text-[11px] text-glyvex-muted">{t("benchmark.prompts.hint")}</p>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function ResultRow({ result, expanded, onToggle }) {
  const { t } = useTranslation();
  const found = result.keywords_found || [];
  const missing = result.keywords_missing || [];
  return (
    <>
      <tr className="border-t border-glyvex-border-soft hover:bg-glyvex-veil cursor-pointer" onClick={onToggle}>
        <td className="px-3 py-2">{result.set_id}</td>
        <td className="px-3 py-2 truncate max-w-xs">{result.prompt_title}</td>
        <td className="px-3 py-2">{result.error ? <X size={14} className="text-red-400" /> : <Check size={14} className="text-emerald-400" />}</td>
        <td className="px-3 py-2 text-glyvex-muted">{result.metrics?.tps?.toFixed(1) ?? "—"}</td>
        <td className="px-3 py-2 text-glyvex-muted">{result.metrics?.ttft_ms ?? "—"}</td>
        <td className="px-3 py-2 text-glyvex-muted">{result.metrics?.tokens_generated ?? "—"}</td>
        <td className="px-3 py-2 text-glyvex-muted">{found.length}/{found.length + missing.length}</td>
        <td className="px-3 py-2 text-glyvex-muted">{fmtScoreKw(result.score_auto)}</td>
        <td className="px-3 py-2"><JudgeScoreBadge score={result.score_judge} /></td>
        <td className="px-3 py-2"><ReasoningCell reasoning={result.judge_reasoning} /></td>
      </tr>
      {expanded && (
        <tr className="border-t border-glyvex-border-soft bg-glyvex-veil-box">
          <td colSpan={10} className="px-3 py-3 text-xs space-y-2">
            {result.error ? <p className="text-red-400">{t("benchmark.row.error", { msg: result.error })}</p> : (
              <>
                {result.thinking && (<div><p className="text-glyvex-muted mb-1">{t("benchmark.row.thinking")}</p><pre className="whitespace-pre-wrap font-mono text-glyvex-muted bg-glyvex-surface-code p-2 rounded">{result.thinking}</pre></div>)}
                <div><p className="text-glyvex-muted mb-1">{t("benchmark.row.response")}</p><pre className="whitespace-pre-wrap font-mono text-glyvex-text bg-glyvex-surface-code p-2 rounded">{result.response}</pre></div>
              </>
            )}
            {result.judge_reasoning && (
              <div>
                <p className="text-glyvex-muted mb-1 flex items-center gap-2">
                  <span>{t("benchmark.row.judge", { model: result.judge_model ? ` (${result.judge_model})` : "" })}</span>
                  <JudgeScoreBadge score={result.score_judge} />
                </p>
                <pre className="whitespace-pre-wrap font-mono text-glyvex-muted bg-glyvex-surface-code p-2 rounded">{result.judge_reasoning}</pre>
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

export default function Benchmark() {
  const { t } = useTranslation();
  const [endpoints, setEndpoints] = useState([]);
  const [endpointUrl, setEndpointUrl] = useState("http://127.0.0.1:8080");
  const [customEndpoint, setCustomEndpoint] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [modelName, setModelName] = useState("");
  const [endpointModels, setEndpointModels] = useState([]);
  const [sets, setSets] = useState([]);
  const [setsError, setSetsError] = useState(false);
  const [selectedSetIds, setSelectedSetIds] = useLocalStorage("glyvex_benchmark_sets", []);
  const selectedSets = useMemo(() => new Set(selectedSetIds), [selectedSetIds]);
  // Categorías de los sets seleccionados (chips de la sección Categorías).
  // El estado de cada chip se DERIVA de offPrompts (fuente única de verdad):
  // un chip está marcado cuando todos sus prompts están marcados.
  const availableCats = useMemo(() => {
    const sel = sets.filter((s) => selectedSets.has(s.id));
    return [...new Set(sel.flatMap((s) => s.categories || []))].sort();
  }, [sets, selectedSets]);
  // K3: prompts individuales DESMARCADOS (persistidos); activos = disponibles − desmarcados.
  const [offPrompts, setOffPrompts] = useLocalStorage("glyvex_benchmark_prompts_off", []);
  const [expandedSetId, setExpandedSetId] = useState(null);
  const [setDetails, setSetDetails] = useState({});
  // K3: la cola de prompts individuales necesita el detalle de TODOS los sets
  // seleccionados, no solo el expandido (se carga al expandir).
  useEffect(() => {
    sets.forEach((s) => {
      if (selectedSets.has(s.id) && !setDetails[s.id]) {
        fetch(`/api/benchmark/sets/${s.id}`).then((r) => r.json()).then((detail) => setSetDetails((prev) => ({ ...prev, [s.id]: detail }))).catch(() => {});
      }
    });
  }, [sets, selectedSets, setDetails]);
  // Objetos de prompt de los sets seleccionados (los interruptores de grupo
  // por categoría y "Todos" necesitan id + category, no solo ids).
  const availablePromptObjs = useMemo(
    () => sets.filter((s) => selectedSets.has(s.id)).flatMap((s) => setDetails[s.id]?.prompts || []),
    [sets, selectedSets, setDetails],
  );
  const availablePrompts = useMemo(() => availablePromptObjs.map((p) => p.id), [availablePromptObjs]);
  const activePromptIds = useMemo(() => availablePrompts.filter((id) => !offPrompts.includes(id)), [availablePrompts, offPrompts]);
  const [temperature, setTemperature] = useState(0.0);
  const [maxTokens, setMaxTokens] = useState(2048);
  const [repetitions, setRepetitions] = useState(1);
  const [timeoutS, setTimeoutS] = useState(120);
  const [thinkingEnabled, setThinkingEnabled] = useState(false);
  // K2.b: «modo avanzado» persistido. En modo simple los parámetros quedan
  // ocultos y se usan los defaults (temperature 0, max tokens 2048, 1 repetición,
  // sampling = default del endpoint).
  const [advancedMode, setAdvancedMode] = useLocalStorage("glyvex_benchmark_advanced", false);
  const [sampling, setSampling] = useState(EMPTY_SAMPLING);
  const [running, setRunning] = useState(false);
  const [runId, setRunId] = useState(null);
  const [progress, setProgress] = useState({ completed: 0, total: 0 });
  const [liveResults, setLiveResults] = useState([]);
  const [finishedRun, setFinishedRun] = useState(null);
  const [runError, setRunError] = useState(null);
  const [expandedRowId, setExpandedRowId] = useState(null);
  const wsRef = useRef(null);
  // Reconexión del WS de progreso (D4): mismo backoff que useLlmStream
  // (1 s → 15 s). Un corte de conexión ya no deja la barra congelada.
  const wsReconnectAttemptRef = useRef(0);
  const wsRetryRef = useRef(null);
  const wsClosedByUsRef = useRef(false);

  const loadSets = useCallback(() => {
    setSetsError(false);
    fetch("/api/benchmark/sets")
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        // Vacío también es error: los 6 sets versionados siempre deben llegar;
        // un [] significa que la instalación no trae backend/prompts.
        if (!Array.isArray(data) || data.length === 0) throw new Error("sets vacíos");
        setSets(data);
      })
      .catch(() => setSetsError(true));
  }, []);

  useEffect(() => {
    fetch("/api/chat/endpoints").then((r) => r.json()).then(setEndpoints).catch(() => {});
    loadSets();
  }, [loadSets]);

  // K2.a: picker de modelos del endpoint elegido, mismo patrón que Chat (E4):
  // el navegador no puede llamar /v1/models a un endpoint ajeno (CORS), así que
  // proxy del backend con debounce de 300 ms (la api_key se teclea tecla a
  // tecla). Si el endpoint no expone modelos, la UI mantiene el texto libre.
  useEffect(() => {
    if (!endpointUrl) return undefined;
    let cancelled = false;
    const timer = setTimeout(() => {
      const qs = new URLSearchParams({ url: endpointUrl });
      if (apiKey) qs.set("api_key", apiKey);
      fetch(`/api/chat/endpoint-models?${qs.toString()}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (cancelled) return;
          const list = data?.models || [];
          setEndpointModels(list);
          if (list.length > 0 && !list.includes(modelName)) setModelName(list[0]);
        })
        .catch(() => {
          if (!cancelled) setEndpointModels([]);
        });
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [endpointUrl, apiKey]);

  useEffect(() => {
    return () => {
      wsClosedByUsRef.current = true;
      clearTimeout(wsRetryRef.current);
      wsRef.current?.close();
    };
  }, []);

  function connectProgress(id) {
    wsRef.current?.close();
    clearTimeout(wsRetryRef.current);
    wsClosedByUsRef.current = false;
    const ws = new WebSocket(wsUrlFor(`/api/benchmark/run/${id}/progress`));
    wsRef.current = ws;
    ws.onopen = () => { wsReconnectAttemptRef.current = 0; };
    ws.onmessage = (event) => {
      let data;
      try { data = JSON.parse(event.data); } catch { return; }
      if (data.type === "start") {
        setProgress((prev) => ({ completed: prev.completed, total: data.total }));
      } else if (data.type === "result") {
        setProgress({ completed: data.completed, total: data.total });
        setLiveResults((prev) => [...prev, data]);
      } else if (data.type === "complete" || data.type === "cancelled" || data.type === "error") {
        setRunning(false);
        if (data.type === "error") setRunError(data.message);
        localStorage.removeItem(ACTIVE_RUN_KEY);
        fetch(`/api/benchmark/run/${id}`).then((r) => r.json()).then(setFinishedRun).catch(() => {});
        wsClosedByUsRef.current = true;
        ws.close();
      } else if (data.error) {
        // Frame sin `type` (p. ej. {"error": "Run no encontrado"} tras un
        // reinicio del backend, y el socket se cierra): terminal. Sin esto el
        // onclose reintentaría el WS en loop y la UI quedaría congelada en
        // "running".
        setRunning(false);
        setRunError(data.error);
        localStorage.removeItem(ACTIVE_RUN_KEY);
        wsClosedByUsRef.current = true;
        ws.close();
      }
    };
    ws.onclose = () => {
      // Cerrado por nosotros (run terminado o unmount) o reemplazado por una
      // conexión nueva: no reintentar. Corte inesperado: backoff 1→15 s.
      if (wsClosedByUsRef.current || wsRef.current !== ws) return;
      const delay = Math.min(1000 * 2 ** wsReconnectAttemptRef.current, 15000);
      wsReconnectAttemptRef.current += 1;
      wsRetryRef.current = setTimeout(() => connectProgress(id), delay);
    };
    return ws;
  }

  // Restaurar el run activo al montar (sobrevive a navegar entre páginas).
  useEffect(() => {
    const saved = localStorage.getItem(ACTIVE_RUN_KEY);
    if (!saved) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`/api/benchmark/run/${saved}`);
        if (!res.ok) { localStorage.removeItem(ACTIVE_RUN_KEY); return; }
        const run = await res.json();
        if (cancelled) return;
        if (run.status === "running") {
          const done = run.results || [];
          setRunId(saved);
          setRunning(true);
          setLiveResults(done.map(liveRowFromResult));
          setProgress({
            completed: run.progress?.completed ?? done.length,
            total: run.progress?.total ?? run.summary?.total_prompts ?? done.length,
          });
          if (run.config?.endpoint) setEndpointUrl(run.config.endpoint);
          if (run.config?.model_name) setModelName(run.config.model_name);
          // K2.a: un run con api_key era contra un endpoint externo; restaurar
          // la key permite re-juzgar/re-runear sin reescribirla.
          if (run.config?.api_key) { setApiKey(run.config.api_key); setCustomEndpoint(true); }
          // K2.b: un run con sampling configurado vuelve a abrir el modo avanzado
          // con sus valores, para no re-runear pierdeando parámetros.
          const configuredSampling = SAMPLING_KEYS.filter((k) => run.config?.[k] != null);
          if (configuredSampling.length > 0) {
            setAdvancedMode(true);
            setSampling((prev) => ({ ...prev, ...Object.fromEntries(configuredSampling.map((k) => [k, String(run.config[k])])) }));
          }
          connectProgress(saved);
        } else if (run.status === "completed") {
          const hist = await fetch(`/api/benchmark/history/${saved}`);
          if (hist.ok) {
            const full = await hist.json();
            if (!cancelled) {
              setRunId(saved);
              setFinishedRun(full);
            }
          }
          localStorage.removeItem(ACTIVE_RUN_KEY);
        } else {
          localStorage.removeItem(ACTIVE_RUN_KEY);
        }
      } catch {
        localStorage.removeItem(ACTIVE_RUN_KEY);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  function toggleSet(id) {
    setSelectedSetIds((prev) => prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]);
  }

  // Una categoría es un interruptor de grupo de sus prompts: marcarla activa
  // todos los de esa categoría y desmarcarla los desactiva todos (el estado
  // visible de los checkboxes individuales es el mismo offPrompts).
  function toggleCat(cat) {
    const catIds = availablePromptObjs.filter((p) => p.category === cat).map((p) => p.id);
    setOffPrompts((prev) => toggleGroup(catIds, prev));
  }

  function togglePrompt(id) {
    setOffPrompts((prev) => prev.includes(id) ? prev.filter((p) => p !== id) : [...prev, id]);
  }

  function toggleExpandSet(id) {
    if (expandedSetId === id) { setExpandedSetId(null); return; }
    setExpandedSetId(id);
    if (!setDetails[id]) {
      fetch(`/api/benchmark/sets/${id}`).then((r) => r.json()).then((detail) => setSetDetails((prev) => ({ ...prev, [id]: detail }))).catch(() => {});
    }
  }

  async function handleStart() {
    if (selectedSets.size === 0) return;
    // K3: la cola queda vacía si desmarcaron todos los prompts disponibles.
    if (availablePrompts.length > 0 && activePromptIds.length === 0) return;
    setRunError(null); setFinishedRun(null); setLiveResults([]); setProgress({ completed: 0, total: 0 });
    try {
      // K2.b: el sampling solo viaja en modo avanzado y solo con valores escritos.
      const samplingBody = advancedMode
        ? Object.fromEntries(SAMPLING_KEYS.filter((k) => sampling[k] !== "").map((k) => [k, Number(sampling[k])]))
        : {};
      const res = await fetch("/api/benchmark/run", {
        method: "POST", headers: { "Content-Type": "application/json" },
        // La selección por prompts define la cola completa: las categorías ya
        // son interruptores de grupo de esos prompts (sin filtro AND aparte).
        body: JSON.stringify({ endpoint: endpointUrl, api_key: apiKey, model_name: modelName, sets: [...selectedSets], prompts: activePromptIds.length === availablePrompts.length ? null : activePromptIds, thinking_enabled: thinkingEnabled, temperature, max_tokens: maxTokens, repetitions, timeout_s: timeoutS, ...samplingBody }),
      });
      const run = await res.json();
      if (!res.ok) throw new Error(run.detail || t("benchmark.errors.startFailed"));
      localStorage.setItem(ACTIVE_RUN_KEY, run.run_id);
      setRunId(run.run_id);
      setRunning(true);
      connectProgress(run.run_id);
    } catch (err) { setRunError(err.message || t("benchmark.errors.startCatch")); }
  }

  async function handleCancel() {
    if (!runId) return;
    // El endpoint es dual (cancelar o eliminar): si el run ya no está, el
    // DELETE borra datos. Chequear la respuesta y reportar el error evita
    // que un fallo (o la rama de eliminación) pase en silencio.
    try {
      const res = await fetch(`/api/benchmark/run/${runId}`, { method: "DELETE" });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        setRunError(detail?.detail || t("benchmark.errors.cancelFailed", { status: res.status }));
      }
    } catch {
      setRunError(t("benchmark.errors.cancelNoResponse"));
    }
    localStorage.removeItem(ACTIVE_RUN_KEY);
  }

  function exportJson() { if (!finishedRun) return; downloadBlob(JSON.stringify(finishedRun, null, 2), `benchmark-${finishedRun.run_id}.json`, "application/json"); }
  function exportCsv() { if (!finishedRun) return; downloadBlob(resultsToCsv(finishedRun.results), `benchmark-${finishedRun.run_id}.csv`, "text/csv"); }
  async function exportHtml() {
    if (!finishedRun) return;
    const res = await fetch(`/api/benchmark/history/${finishedRun.run_id}/report`);
    const html = await res.text();
    downloadBlob(html, `benchmark-${finishedRun.run_id}.html`, "text/html");
  }

  const barData = useMemo(() => (finishedRun?.results || []).map((r) => ({ name: r.prompt_id, tps: r.metrics?.tps ?? 0 })), [finishedRun]);
  const scatterData = useMemo(() => (finishedRun?.results || []).filter((r) => r.metrics).map((r) => ({ ttft: r.metrics.ttft_ms, tokens: r.metrics.tokens_generated, name: r.prompt_id })), [finishedRun]);

  return (
    <div className="space-y-6 max-w-5xl">
      <h1 className="text-xl font-semibold">Benchmark</h1>

      <div className="bg-glyvex-card rounded-lg border border-glyvex-border-soft p-5 space-y-3">
        <h2 className="text-sm font-medium text-glyvex-muted uppercase tracking-wide">{t("benchmark.step1")}</h2>
        <div className="grid grid-cols-2 gap-4">
          <label className="block">
            <span className="block text-xs text-glyvex-muted mb-1">Endpoint</span>
            <select
              className={inputClasses}
              value={customEndpoint ? CUSTOM_ENDPOINT : endpointUrl}
              onChange={(e) => {
                const v = e.target.value;
                if (v === CUSTOM_ENDPOINT) { setCustomEndpoint(true); return; }
                setCustomEndpoint(false);
                setEndpointUrl(v);
              }}
            >
              {endpoints.map((e) => <option key={e.url} value={e.url}>{e.name}</option>)}
              {!customEndpoint && !endpoints.some((e) => e.url === endpointUrl) && <option value={endpointUrl}>{endpointUrl}</option>}
              <option value={CUSTOM_ENDPOINT}>{t("benchmark.customEndpointOption")}</option>
            </select>
            {customEndpoint && (
              <div className="mt-2 space-y-2">
                <input
                  className={inputClasses}
                  value={endpointUrl}
                  onChange={(e) => setEndpointUrl(e.target.value)}
                  placeholder="http://127.0.0.1:8080"
                  spellCheck={false}
                />
                <input
                  className={inputClasses}
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={t("benchmark.apiKeyPh")}
                  spellCheck={false}
                />
              </div>
            )}
          </label>
          <label className="block">
            <span className="block text-xs text-glyvex-muted mb-1">{t("benchmark.modelLabel")}</span>
            {endpointModels.length > 0 ? (
              <select className={inputClasses} value={modelName} onChange={(e) => setModelName(e.target.value)}>
                {endpointModels.map((m) => <option key={m} value={m}>{m}</option>)}
                {!endpointModels.includes(modelName) && <option value={modelName}>{modelName}</option>}
              </select>
            ) : (
              <input className={inputClasses} value={modelName} onChange={(e) => setModelName(e.target.value)} placeholder={t("benchmark.modelNamePh")} />
            )}
          </label>
        </div>
      </div>

      <div className="space-y-3">
        <h2 className="text-sm font-medium text-glyvex-muted uppercase tracking-wide">{t("benchmark.step2")}</h2>
        {setsError ? (
          <div className="border border-amber-500/40 bg-amber-500/10 rounded-md p-4 text-sm text-amber-300 space-y-2">
            <p className="flex items-center gap-2">
              <AlertTriangle size={16} className="shrink-0" />
              {t("benchmark.errors.setsLoad")}
            </p>
            <button type="button" onClick={loadSets}
              className="flex items-center gap-2 px-3 py-1.5 rounded-md text-xs font-medium border border-amber-500/40 bg-amber-500/15 text-amber-200 hover:bg-amber-500/25">
              {t("benchmark.errors.setsRetry")}
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {sets.map((s) => {
              const setPromptIds = (setDetails[s.id]?.prompts || []).map((p) => p.id);
              return (
                <SetCard key={s.id} set={s} checked={selectedSets.has(s.id)} onToggle={() => toggleSet(s.id)}
                  expanded={expandedSetId === s.id} onToggleExpand={() => toggleExpandSet(s.id)} detail={setDetails[s.id]}
                  offPrompts={offPrompts} onTogglePrompt={togglePrompt}
                  allOn={groupAllOn(setPromptIds, offPrompts)}
                  onToggleAll={() => setOffPrompts((prev) => toggleGroup(setPromptIds, prev))} />
              );
            })}
          </div>
        )}
        {!setsError && availableCats.length > 0 && (
          <div className="bg-glyvex-card rounded-lg border border-glyvex-border-soft p-4 space-y-2">
            <div className="flex items-center justify-between">
              <h3 className="text-xs font-medium text-glyvex-muted uppercase tracking-wide">{t("benchmark.categories.title")}</h3>
              <div className="flex gap-3 text-xs">
                <button type="button" onClick={() => setOffPrompts((prev) => turnGroupOn(availablePrompts, prev))} className="text-glyvex-accent hover:underline">{t("benchmark.categories.all")}</button>
                <button type="button" onClick={() => setOffPrompts((prev) => turnGroupOff(availablePrompts, prev))} className="text-glyvex-accent hover:underline">{t("benchmark.categories.none")}</button>
              </div>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {availableCats.map((cat) => {
                const catIds = availablePromptObjs.filter((p) => p.category === cat).map((p) => p.id);
                return (
                  <label key={cat} className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md border border-glyvex-border-soft bg-glyvex-veil text-xs cursor-pointer hover:border-glyvex-accent/50">
                    <input type="checkbox" checked={groupAllOn(catIds, offPrompts)} onChange={() => toggleCat(cat)} className="w-3.5 h-3.5 accent-glyvex-accent" />
                    {t(`benchmark.categories.items.${cat}`, { defaultValue: cat })}
                  </label>
                );
              })}
            </div>
          </div>
        )}
      </div>

      <div className="bg-glyvex-card rounded-lg border border-glyvex-border-soft p-5 space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-medium text-glyvex-muted uppercase tracking-wide">{t("benchmark.step3")}</h2>
          <label className="flex items-center gap-2 text-xs text-glyvex-muted cursor-pointer select-none">
            <input type="checkbox" checked={advancedMode} onChange={(e) => setAdvancedMode(e.target.checked)} className="accent-glyvex-accent w-3.5 h-3.5" />
            {t("benchmark.advancedMode")}
          </label>
        </div>
        {advancedMode ? (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <label className="block"><span className="block text-xs text-glyvex-muted mb-1">Temperature</span><input type="number" step="0.1" className={inputClasses} value={temperature} onChange={(e) => setTemperature(Number(e.target.value))} /></label>
              <label className="block"><span className="block text-xs text-glyvex-muted mb-1">Max tokens</span><input type="number" className={inputClasses} value={maxTokens} onChange={(e) => setMaxTokens(Number(e.target.value))} /></label>
              <label className="block"><span className="block text-xs text-glyvex-muted mb-1">{t("benchmark.repetitions")}</span><input type="number" min={1} className={inputClasses} value={repetitions} onChange={(e) => setRepetitions(Number(e.target.value))} /></label>
              <label className="block"><span className="block text-xs text-glyvex-muted mb-1">Timeout (s)</span><input type="number" className={inputClasses} value={timeoutS} onChange={(e) => setTimeoutS(Number(e.target.value))} /></label>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-4">
              {SAMPLING_FIELDS.map((f) => (
                <label key={f.key} className="block">
                  <span className="block text-xs text-glyvex-muted mb-1">{f.key}</span>
                  <input
                    type="number" step={f.step} min={f.min} max={f.max} className={inputClasses}
                    value={sampling[f.key]}
                    onChange={(e) => setSampling((prev) => ({ ...prev, [f.key]: e.target.value }))}
                    placeholder="—"
                  />
                </label>
              ))}
            </div>
            <p className="text-xs text-glyvex-muted">{t("benchmark.samplingHint")}</p>
          </>
        ) : (
          <p className="text-xs text-glyvex-muted">{t("benchmark.simpleDefaults")}</p>
        )}
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={thinkingEnabled} onChange={(e) => setThinkingEnabled(e.target.checked)} className="accent-glyvex-accent w-4 h-4" />
          thinking_enabled
        </label>
      </div>

      {runError && <p className="text-sm text-red-400">{runError}</p>}

      {!running ? (
        <button type="button" onClick={handleStart} disabled={selectedSets.size === 0 || (availablePrompts.length > 0 && activePromptIds.length === 0)}
          className="flex items-center gap-2 px-5 py-2.5 rounded-md text-sm font-medium bg-emerald-600 text-white hover:bg-emerald-500 disabled:opacity-50">
          <Play size={16} />{t("benchmark.start")}
        </button>
      ) : (
        <div className="bg-glyvex-card rounded-lg border border-glyvex-border-soft p-5 space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-sm text-glyvex-muted">{t("benchmark.progress", { done: progress.completed, total: progress.total })}</p>
            <button type="button" onClick={handleCancel} className="flex items-center gap-2 px-3 py-1.5 rounded-md text-xs bg-red-600 text-white hover:bg-red-500"><Square size={14} />{t("benchmark.cancel")}</button>
          </div>
          <div className="gx-bar w-full bg-glyvex-veil-disabled rounded-full h-2 overflow-hidden">
            <div className="gx-bar-fill bg-glyvex-accent text-glyvex-accent h-2 rounded-full transition-all" style={{ width: progress.total ? `${(progress.completed / progress.total) * 100}%` : "0%" }} />
          </div>
          <div className="max-h-48 overflow-y-auto space-y-1">
            {liveResults.map((r, i) => (
              <div key={`${r.set_id}-${r.prompt_id}-${i}`} className="flex items-center gap-2 text-xs">
                {r.ok ? <Check size={12} className="text-emerald-400" /> : <X size={12} className="text-red-400" />}
                <span className="text-glyvex-muted">{r.set_id}/{r.prompt_id}</span>
                <span className="text-glyvex-text">{r.tps != null ? `${r.tps.toFixed(1)} t/s` : "—"}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {finishedRun && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium text-glyvex-bg-muted uppercase tracking-wide">{t("benchmark.resultsTitle")}</h2>
            <div className="flex gap-2">
              <button type="button" onClick={exportJson} className="flex items-center gap-1 px-3 py-1.5 rounded-md text-xs border border-glyvex-border-soft text-glyvex-bg-muted hover:text-glyvex-text hover:bg-glyvex-veil-disabled"><Download size={12} /> JSON</button>
              <button type="button" onClick={exportCsv} className="flex items-center gap-1 px-3 py-1.5 rounded-md text-xs border border-glyvex-border-soft text-glyvex-bg-muted hover:text-glyvex-text hover:bg-glyvex-veil-disabled"><Download size={12} /> CSV</button>
              <button type="button" onClick={exportHtml} className="flex items-center gap-1 px-3 py-1.5 rounded-md text-xs border border-glyvex-border-soft text-glyvex-bg-muted hover:text-glyvex-text hover:bg-glyvex-veil-disabled"><Download size={12} /> HTML</button>
            </div>
          </div>

          {finishedRun.summary && (
            <div className="flex flex-wrap gap-3 text-sm">
              <span className="bg-glyvex-card border border-glyvex-border-soft rounded-md px-3 py-1.5">{t("benchmark.summary.avgTps")}: <b>{finishedRun.summary.avg_tps}</b></span>
              <span className="bg-glyvex-card border border-glyvex-border-soft rounded-md px-3 py-1.5">{t("benchmark.summary.avgTtft")}: <b>{finishedRun.summary.avg_ttft_ms}ms</b></span>
              <span className="bg-glyvex-card border border-glyvex-border-soft rounded-md px-3 py-1.5">{t("benchmark.summary.keywordHitRate")}: <b>{(finishedRun.summary.keyword_hit_rate * 100).toFixed(0)}%</b></span>
              <span className="bg-glyvex-card border border-glyvex-border-soft rounded-md px-3 py-1.5">{t("benchmark.summary.errors")}: <b>{finishedRun.summary.errors}</b></span>
              {finishedRun.summary.avg_judge_score != null && (
                <span className="bg-glyvex-card border border-glyvex-border-soft rounded-md px-3 py-1.5">{t("benchmark.summary.avgJudge")}: <b>{Number(finishedRun.summary.avg_judge_score).toFixed(1)}/10</b></span>
              )}
            </div>
          )}

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="bg-glyvex-card rounded-lg border border-glyvex-border-soft p-4 h-64">
              <p className="text-xs text-glyvex-muted mb-2">{t("benchmark.chartTps")}</p>
              <ResponsiveContainer width="100%" height="90%">
                <BarChart data={barData}><CartesianGrid strokeDasharray="3 3" stroke="var(--color-glyvex-chart-grid)" /><XAxis dataKey="name" tick={{ fontSize: 10, fill: "var(--color-glyvex-chart-tick)" }} /><YAxis tick={{ fontSize: 10, fill: "var(--color-glyvex-chart-tick)" }} /><Tooltip contentStyle={{ background: "var(--color-glyvex-chart-tip-bg)", border: "1px solid var(--color-glyvex-chart-tip-bd)" }} /><Bar dataKey="tps" fill="var(--color-glyvex-chart-1)" radius={[4, 4, 0, 0]} /></BarChart>
              </ResponsiveContainer>
            </div>
            <div className="bg-glyvex-card rounded-lg border border-glyvex-border-soft p-4 h-64">
              <p className="text-xs text-glyvex-muted mb-2">{t("benchmark.chartTtft")}</p>
              <ResponsiveContainer width="100%" height="90%">
                <ScatterChart><CartesianGrid strokeDasharray="3 3" stroke="var(--color-glyvex-chart-grid)" /><XAxis dataKey="ttft" name="TTFT (ms)" tick={{ fontSize: 10, fill: "var(--color-glyvex-chart-tick)" }} /><YAxis dataKey="tokens" name="Tokens" tick={{ fontSize: 10, fill: "var(--color-glyvex-chart-tick)" }} /><Tooltip contentStyle={{ background: "var(--color-glyvex-chart-tip-bg)", border: "1px solid var(--color-glyvex-chart-tip-bd)" }} cursor={{ strokeDasharray: "3 3" }} /><Scatter data={scatterData} fill="var(--color-glyvex-chart-2)" /></ScatterChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="overflow-x-auto rounded-lg border border-glyvex-border-soft">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-glyvex-card text-left text-glyvex-muted">
                  <th className="px-3 py-2 font-medium">Set</th><th className="px-3 py-2 font-medium">Prompt</th><th className="px-3 py-2 font-medium">OK</th>
                  <th className="px-3 py-2 font-medium">t/s</th><th className="px-3 py-2 font-medium">TTFT</th><th className="px-3 py-2 font-medium">Tokens</th>
                  <th className="px-3 py-2 font-medium">Keywords</th><th className="px-3 py-2 font-medium">Score KW</th>
                  <th className="px-3 py-2 font-medium">Score Judge</th><th className="px-3 py-2 font-medium">{t("benchmark.table.reasoning")}</th>
                </tr>
              </thead>
              <tbody>
                {(finishedRun.results || []).map((r, i) => {
                  const rowKey = `${r.set_id}-${r.prompt_id}-${i}`;
                  return (
                    <ResultRow key={rowKey} result={r} expanded={expandedRowId === rowKey}
                      onToggle={() => setExpandedRowId((prev) => (prev === rowKey ? null : rowKey))} />
                  );
                })}
              </tbody>
            </table>
          </div>

          <JudgePanel
            key={finishedRun.run_id}
            runId={finishedRun.run_id}
            totalResults={(finishedRun.results || []).length}
            runEndpoint={finishedRun.config?.endpoint}
            runApiKey={finishedRun.config?.api_key || null}
            initialAvgScore={finishedRun.summary?.avg_judge_score ?? null}
            onRunRefreshed={setFinishedRun}
          />
        </div>
      )}
    </div>
  );
}
