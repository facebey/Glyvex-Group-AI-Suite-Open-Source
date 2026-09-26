// frontend/src/pages/Benchmark.jsx
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, ChevronDown, ChevronRight, Play, Square, Download, Check, X, Info } from "lucide-react";
import { useLocalStorage } from "../hooks/useLocalStorage.js";
import { useTranslation } from "react-i18next";
import {
  BarChart, Bar, ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts";

const ACTIVE_RUN_KEY = "glyvex_active_run_id";

// Ajustar los `value` al nombre exacto con el que cada modelo se expone en el endpoint del judge.
// El label visible se resuelve por idioma vía benchmark.judgeChips.<id>.
const JUDGE_MODEL_CHIPS = [
  { id: "recommended", value: "Qwen3-8B-Q4_K_M" },
  { id: "light", value: "Agents-A1-4B" },
  { id: "precise", value: "Qwen3-Coder-30B" },
];

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

function SetCard({ set, checked, onToggle, expanded, onToggleExpand, detail }) {
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
          {!detail ? <p className="text-xs text-glyvex-muted">{t("benchmark.sets.loading")}</p> : detail.prompts.map((p) => (
            <div key={p.id} className="text-xs">
              <span className="text-glyvex-text font-medium">{p.title}</span>
              <span className="text-glyvex-muted"> — {p.difficulty}</span>
            </div>
          ))}
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
  const [modelName, setModelName] = useState("");
  const [sets, setSets] = useState([]);
  const [setsError, setSetsError] = useState(false);
  const [selectedSetIds, setSelectedSetIds] = useLocalStorage("glyvex_benchmark_sets", []);
  const selectedSets = useMemo(() => new Set(selectedSetIds), [selectedSetIds]);
  const [expandedSetId, setExpandedSetId] = useState(null);
  const [setDetails, setSetDetails] = useState({});
  const [temperature, setTemperature] = useState(0.0);
  const [maxTokens, setMaxTokens] = useState(2048);
  const [repetitions, setRepetitions] = useState(1);
  const [timeoutS, setTimeoutS] = useState(120);
  const [thinkingEnabled, setThinkingEnabled] = useState(false);
  const [running, setRunning] = useState(false);
  const [runId, setRunId] = useState(null);
  const [progress, setProgress] = useState({ completed: 0, total: 0 });
  const [liveResults, setLiveResults] = useState([]);
  const [finishedRun, setFinishedRun] = useState(null);
  const [runError, setRunError] = useState(null);
  const [expandedRowId, setExpandedRowId] = useState(null);
  const wsRef = useRef(null);

  // --- Judge ---
  const [judgeEndpoint, setJudgeEndpoint] = useState("http://127.0.0.1:8080");
  const [judgeModel, setJudgeModel] = useState("");
  const [judgeApiKey, setJudgeApiKey] = useState("");
  const [judging, setJudging] = useState(false);
  const [judgeProgress, setJudgeProgress] = useState({ evaluated: 0, total: 0, currentPrompt: null, currentScore: null });
  const [judgeAvgScore, setJudgeAvgScore] = useState(null);
  const [judgeError, setJudgeError] = useState(null);
  const judgeAbortRef = useRef(null);
  const judgeEndpointTouchedRef = useRef(false);

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

  useEffect(() => {
    return () => { wsRef.current?.close(); };
  }, []);

  useEffect(() => {
    return () => { judgeAbortRef.current?.abort(); };
  }, []);

  // El endpoint del judge arranca igual al del run, salvo que el usuario lo haya editado.
  useEffect(() => {
    if (judgeEndpointTouchedRef.current) return;
    const ep = finishedRun?.config?.endpoint;
    if (ep) setJudgeEndpoint(ep);
  }, [finishedRun]);

  function connectProgress(id) {
    wsRef.current?.close();
    const ws = new WebSocket(wsUrlFor(`/api/benchmark/run/${id}/progress`));
    wsRef.current = ws;
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
        ws.close();
      }
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
          connectProgress(saved);
        } else if (run.status === "completed") {
          const hist = await fetch(`/api/benchmark/history/${saved}`);
          if (hist.ok) {
            const full = await hist.json();
            if (!cancelled) {
              setRunId(saved);
              setFinishedRun(full);
              if (full.summary?.avg_judge_score != null) setJudgeAvgScore(full.summary.avg_judge_score);
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

  function toggleExpandSet(id) {
    if (expandedSetId === id) { setExpandedSetId(null); return; }
    setExpandedSetId(id);
    if (!setDetails[id]) {
      fetch(`/api/benchmark/sets/${id}`).then((r) => r.json()).then((detail) => setSetDetails((prev) => ({ ...prev, [id]: detail }))).catch(() => {});
    }
  }

  async function handleStart() {
    if (selectedSets.size === 0) return;
    setRunError(null); setFinishedRun(null); setLiveResults([]); setProgress({ completed: 0, total: 0 });
    setJudgeAvgScore(null); setJudgeError(null);
    setJudgeProgress({ evaluated: 0, total: 0, currentPrompt: null, currentScore: null });
    try {
      const res = await fetch("/api/benchmark/run", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ endpoint: endpointUrl, model_name: modelName, sets: [...selectedSets], thinking_enabled: thinkingEnabled, temperature, max_tokens: maxTokens, repetitions, timeout_s: timeoutS }),
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
    await fetch(`/api/benchmark/run/${runId}`, { method: "DELETE" }).catch(() => {});
    localStorage.removeItem(ACTIVE_RUN_KEY);
  }

  async function handleJudge() {
    if (!finishedRun || !judgeModel.trim()) return;
    setJudging(true);
    setJudgeError(null);
    setJudgeAvgScore(null);
    setJudgeProgress({
      evaluated: 0,
      total: (finishedRun.results || []).length,
      currentPrompt: null,
      currentScore: null,
    });
    const controller = new AbortController();
    judgeAbortRef.current = controller;
    try {
      const response = await fetch(`/api/benchmark/run/${finishedRun.run_id}/judge`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          endpoint: judgeEndpoint,
          model: judgeModel,
          api_key: judgeApiKey,
        }),
        signal: controller.signal,
      });
      if (!response.ok) {
        const detail = await response.text().catch(() => "");
        throw new Error(detail || t("benchmark.errors.judgeStatus", { status: response.status }));
      }
      if (!response.body) throw new Error(t("benchmark.errors.noStream"));
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();
        for (const line of lines) {
          if (!line.startsWith("data:")) continue;
          let data;
          try { data = JSON.parse(line.slice(5).trim()); } catch { continue; }
          if (data.status === "complete") {
            setJudgeAvgScore(data.avg_score);
            setJudgeProgress((prev) => ({
              ...prev,
              evaluated: data.evaluated ?? prev.evaluated,
              total: data.total ?? prev.total,
              currentPrompt: null,
              currentScore: null,
            }));
            const updated = await fetch(`/api/benchmark/history/${finishedRun.run_id}`).then((r) => r.json());
            setFinishedRun(updated);
          } else if (data.error) {
            setJudgeError(data.error);
          } else {
            setJudgeProgress((prev) => ({
              evaluated: data.evaluated ?? prev.evaluated,
              total: data.total ?? prev.total,
              currentPrompt: data.current_prompt ?? null,
              currentScore: data.current_score ?? null,
            }));
          }
        }
      }
    } catch (err) {
      if (err.name !== "AbortError") setJudgeError(err.message || t("benchmark.errors.judgeCatch"));
    } finally {
      setJudging(false);
      judgeAbortRef.current = null;
    }
  }

  function handleStopJudge() {
    judgeAbortRef.current?.abort();
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
            <select className={inputClasses} value={endpointUrl} onChange={(e) => setEndpointUrl(e.target.value)}>
              {endpoints.map((e) => <option key={e.url} value={e.url}>{e.name}</option>)}
              {!endpoints.some((e) => e.url === endpointUrl) && <option value={endpointUrl}>{endpointUrl}</option>}
            </select>
          </label>
          <label className="block">
            <span className="block text-xs text-glyvex-muted mb-1">{t("benchmark.modelLabel")}</span>
            <input className={inputClasses} value={modelName} onChange={(e) => setModelName(e.target.value)} placeholder={t("benchmark.modelNamePh")} />
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
            {sets.map((s) => (
              <SetCard key={s.id} set={s} checked={selectedSets.has(s.id)} onToggle={() => toggleSet(s.id)}
                expanded={expandedSetId === s.id} onToggleExpand={() => toggleExpandSet(s.id)} detail={setDetails[s.id]} />
            ))}
          </div>
        )}
      </div>

      <div className="bg-glyvex-card rounded-lg border border-glyvex-border-soft p-5 space-y-3">
        <h2 className="text-sm font-medium text-glyvex-muted uppercase tracking-wide">{t("benchmark.step3")}</h2>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <label className="block"><span className="block text-xs text-glyvex-muted mb-1">Temperature</span><input type="number" step="0.1" className={inputClasses} value={temperature} onChange={(e) => setTemperature(Number(e.target.value))} /></label>
          <label className="block"><span className="block text-xs text-glyvex-muted mb-1">Max tokens</span><input type="number" className={inputClasses} value={maxTokens} onChange={(e) => setMaxTokens(Number(e.target.value))} /></label>
          <label className="block"><span className="block text-xs text-glyvex-muted mb-1">{t("benchmark.repetitions")}</span><input type="number" min={1} className={inputClasses} value={repetitions} onChange={(e) => setRepetitions(Number(e.target.value))} /></label>
          <label className="block"><span className="block text-xs text-glyvex-muted mb-1">Timeout (s)</span><input type="number" className={inputClasses} value={timeoutS} onChange={(e) => setTimeoutS(Number(e.target.value))} /></label>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={thinkingEnabled} onChange={(e) => setThinkingEnabled(e.target.checked)} className="accent-glyvex-accent w-4 h-4" />
          thinking_enabled
        </label>
      </div>

      {runError && <p className="text-sm text-red-400">{runError}</p>}

      {!running ? (
        <button type="button" onClick={handleStart} disabled={selectedSets.size === 0}
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

          <div className="bg-glyvex-card rounded-lg border border-glyvex-border-soft p-5 space-y-4">
            <div>
              <h3 className="text-sm font-medium">{t("benchmark.judge.title")}</h3>
              <p className="text-xs text-glyvex-muted mt-1">{t("benchmark.judge.hint")}</p>
            </div>

            <div className="flex flex-wrap gap-2">
              {JUDGE_MODEL_CHIPS.map((chip) => (
                <button key={chip.value} type="button" disabled={judging} onClick={() => setJudgeModel(chip.value)}
                  className={"px-3 py-1.5 rounded-full text-xs border transition-colors disabled:opacity-50 " +
                    (judgeModel === chip.value
                      ? "bg-glyvex-accent text-white border-glyvex-accent"
                      : "bg-glyvex-veil-disabled border-glyvex-border-soft text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-veil")}>
                  {t(`benchmark.judgeChips.${chip.id}`)}
                </button>
              ))}
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <label className="block">
                <span className="block text-xs text-glyvex-muted mb-1">{t("benchmark.judge.endpointLabel")}</span>
                <input className={inputClasses} value={judgeEndpoint} disabled={judging}
                  onChange={(e) => { judgeEndpointTouchedRef.current = true; setJudgeEndpoint(e.target.value); }}
                  placeholder="http://127.0.0.1:8080" />
              </label>
              <label className="block">
                <span className="block text-xs text-glyvex-muted mb-1">{t("benchmark.judge.modelLabel")}</span>
                <input className={inputClasses} value={judgeModel} disabled={judging}
                  onChange={(e) => setJudgeModel(e.target.value)} placeholder={t("benchmark.judge.modelPh")} />
              </label>
              <label className="block">
                <span className="block text-xs text-glyvex-muted mb-1">{t("benchmark.judge.apiKeyLabel")}</span>
                <input className={inputClasses} value={judgeApiKey} disabled={judging}
                  onChange={(e) => setJudgeApiKey(e.target.value)} placeholder={t("benchmark.judge.apiKeyPh")} />
              </label>
            </div>

            <div className="flex flex-wrap gap-2">
              <button type="button" onClick={handleJudge} disabled={judging || !judgeModel.trim()}
                className="flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium bg-glyvex-accent text-white hover:bg-glyvex-accent/85 disabled:opacity-50">
                {judging ? t("benchmark.judge.evaluating") : t("benchmark.judge.evaluate")}
              </button>
              {judging && (
                <button type="button" onClick={handleStopJudge}
                  className="flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium bg-red-600 text-white hover:bg-red-500">
                  <Square size={14} />{t("benchmark.judge.stop")}
                </button>
              )}
            </div>

            {judging && (
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-3 text-xs text-glyvex-muted">
                  <span>{t("benchmark.judge.evaluated", { done: judgeProgress.evaluated, total: judgeProgress.total || "?" })}</span>
                  {judgeProgress.currentScore != null && <JudgeScoreBadge score={judgeProgress.currentScore} />}
                </div>
                <div className="gx-bar w-full bg-glyvex-veil-disabled rounded-full h-2 overflow-hidden">
                  <div className="gx-bar-fill bg-glyvex-accent text-glyvex-accent h-2 rounded-full transition-all"
                    style={{ width: judgeProgress.total ? `${(judgeProgress.evaluated / judgeProgress.total) * 100}%` : "0%" }} />
                </div>
                {judgeProgress.currentPrompt && (
                  <p className="text-xs text-glyvex-muted truncate">{t("benchmark.judge.evaluatingPrompt", { prompt: judgeProgress.currentPrompt })}</p>
                )}
              </div>
            )}

            {!judging && judgeAvgScore != null && (
              <p className="text-sm text-emerald-400">{t("benchmark.judge.done", { score: Number(judgeAvgScore).toFixed(1) })}</p>
            )}
            {judgeError && <p className="text-sm text-red-400">{judgeError}</p>}
          </div>
        </div>
      )}
    </div>
  );
}
