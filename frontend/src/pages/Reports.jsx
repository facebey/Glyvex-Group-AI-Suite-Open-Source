// frontend/src/pages/Reports.jsx
import { useEffect, useState } from "react";
import { Eye, Trash2, GitCompare, ArrowLeft, Info } from "lucide-react";
import { useTranslation } from "react-i18next";

function fmtDate(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

function fmtJudgeAvg(score) {
  return score != null ? `${Number(score).toFixed(1)}/10` : "—";
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
    <span title={reasoning} className="inline-flex text-glyvex-muted hover:text-glyvex-text cursor-help">
      <Info size={14} />
    </span>
  );
}

function RunDetail({ run, onBack }) {
  const { t } = useTranslation();
  const results = run.results || [];
  const avgJudge = run.summary?.avg_judge_score;
  return (
    <div className="space-y-4">
      <button type="button" onClick={onBack} className="flex items-center gap-1 text-sm text-glyvex-muted hover:text-glyvex-text"><ArrowLeft size={14} /> {t("reports.backToHistory")}</button>
      <div className="bg-glyvex-card rounded-lg border border-white/10 p-5 space-y-2">
        <p className="text-sm"><span className="text-glyvex-muted">{t("reports.detail.model")}:</span> {run.config.model_name || "—"}</p>
        <p className="text-sm"><span className="text-glyvex-muted">Endpoint:</span> {run.config.endpoint}</p>
        <p className="text-sm"><span className="text-glyvex-muted">Sets:</span> {run.config.sets.join(", ")}</p>
        <p className="text-sm"><span className="text-glyvex-muted">{t("reports.detail.started")}:</span> {fmtDate(run.started_at)}</p>
        <p className="text-sm"><span className="text-glyvex-muted">{t("reports.detail.finished")}:</span> {fmtDate(run.finished_at)}</p>
        <p className="text-sm"><span className="text-glyvex-muted">{t("reports.detail.status")}:</span> {run.status}</p>
      </div>
      {run.summary && (
        <div className="flex flex-wrap gap-3 text-sm">
          <span className="bg-glyvex-card border border-white/10 rounded-md px-3 py-1.5">{t("benchmark.summary.avgTps")}: <b>{run.summary.avg_tps}</b></span>
          <span className="bg-glyvex-card border border-white/10 rounded-md px-3 py-1.5">{t("benchmark.summary.avgTtft")}: <b>{run.summary.avg_ttft_ms}ms</b></span>
          <span className="bg-glyvex-card border border-white/10 rounded-md px-3 py-1.5">{t("benchmark.summary.keywordHitRate")}: <b>{(run.summary.keyword_hit_rate * 100).toFixed(0)}%</b></span>
          <span className="bg-glyvex-card border border-white/10 rounded-md px-3 py-1.5">{t("benchmark.summary.errors")}: <b>{run.summary.errors}</b></span>
          {avgJudge != null && (
            <span className="bg-glyvex-card border border-white/10 rounded-md px-3 py-1.5">{t("reports.detail.avgJudgeScore")}: <b>{fmtJudgeAvg(avgJudge)}</b></span>
          )}
        </div>
      )}
      <div className="overflow-x-auto rounded-lg border border-white/10">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-glyvex-card text-left text-glyvex-muted">
              <th className="px-3 py-2 font-medium">Set</th><th className="px-3 py-2 font-medium">Prompt</th>
              <th className="px-3 py-2 font-medium">t/s</th><th className="px-3 py-2 font-medium">TTFT</th>
              <th className="px-3 py-2 font-medium">Tokens</th><th className="px-3 py-2 font-medium">Keywords</th>
              <th className="px-3 py-2 font-medium">Score Judge</th><th className="px-3 py-2 font-medium">Reasoning</th>
            </tr>
          </thead>
          <tbody>
            {results.map((r, i) => {
              const found = r.keywords_found || [];
              const missing = r.keywords_missing || [];
              return (
                <tr key={`${r.set_id}-${r.prompt_id}-${i}`} className="border-t border-white/10">
                  <td className="px-3 py-2 text-glyvex-muted">{r.set_id}</td>
                  <td className="px-3 py-2">{r.prompt_title}</td>
                  <td className="px-3 py-2 text-glyvex-muted">{r.metrics?.tps?.toFixed(1) ?? "—"}</td>
                  <td className="px-3 py-2 text-glyvex-muted">{r.metrics?.ttft_ms ?? "—"}</td>
                  <td className="px-3 py-2 text-glyvex-muted">{r.metrics?.tokens_generated ?? "—"}</td>
                  <td className="px-3 py-2 text-glyvex-muted">{found.length}/{found.length + missing.length}</td>
                  <td className="px-3 py-2"><JudgeScoreBadge score={r.score_judge} /></td>
                  <td className="px-3 py-2"><ReasoningCell reasoning={r.judge_reasoning} /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {run.summary && avgJudge == null && (
        <div className="bg-glyvex-card rounded-lg border border-white/10 p-5">
          <p className="text-sm text-glyvex-muted">
            {t("reports.detail.notEvaluated")}
          </p>
        </div>
      )}
    </div>
  );
}

function CompareView({ runA, runB, onBack }) {
  const { t } = useTranslation();
  const metrics = [
    [t("reports.compare.model"), (r) => r.config.model_name || "—"],
    [t("reports.compare.sets"), (r) => r.config.sets.join(", ")],
    [t("reports.compare.totalPrompts"), (r) => r.summary?.total_prompts ?? "—"],
    [t("reports.compare.completed"), (r) => r.summary?.completed ?? "—"],
    [t("reports.compare.errors"), (r) => r.summary?.errors ?? "—"],
    [t("reports.compare.avgTps"), (r) => r.summary?.avg_tps ?? "—"],
    [t("reports.compare.avgTtft"), (r) => r.summary?.avg_ttft_ms ?? "—"],
    [t("reports.compare.avgTokens"), (r) => r.summary?.avg_tokens ?? "—"],
    [t("reports.compare.keywordHitRate"), (r) => r.summary ? `${(r.summary.keyword_hit_rate * 100).toFixed(0)}%` : "—"],
    [t("reports.compare.avgJudgeScore"), (r) => r.summary?.avg_judge_score != null ? `${r.summary.avg_judge_score.toFixed(1)}/10` : "—"],
    [t("reports.compare.totalDuration"), (r) => r.summary?.total_duration_s ?? "—"],
  ];
  return (
    <div className="space-y-4">
      <button type="button" onClick={onBack} className="flex items-center gap-1 text-sm text-glyvex-muted hover:text-glyvex-text"><ArrowLeft size={14} /> {t("reports.backToHistory")}</button>
      <div className="overflow-x-auto rounded-lg border border-white/10">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-glyvex-card text-left text-glyvex-muted">
              <th className="px-3 py-2 font-medium">{t("reports.compare.metric")}</th>
              <th className="px-3 py-2 font-medium">{t("reports.compare.runA", { date: fmtDate(runA.started_at) })}</th>
              <th className="px-3 py-2 font-medium">{t("reports.compare.runB", { date: fmtDate(runB.started_at) })}</th>
            </tr>
          </thead>
          <tbody>
            {metrics.map(([label, getValue]) => (
              <tr key={label} className="border-t border-white/10">
                <td className="px-3 py-2 text-glyvex-muted">{label}</td>
                <td className="px-3 py-2">{getValue(runA)}</td>
                <td className="px-3 py-2">{getValue(runB)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function Reports() {
  const { t } = useTranslation();
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [compareIds, setCompareIds] = useState([]);
  const [viewRun, setViewRun] = useState(null);
  const [compareRuns, setCompareRuns] = useState(null);

  function loadHistory() {
    setLoading(true);
    fetch("/api/benchmark/history").then((r) => r.json()).then(setHistory).catch(() => {}).finally(() => setLoading(false));
  }

  useEffect(() => { loadHistory(); }, []);

  function toggleCompare(runId) {
    setCompareIds((prev) => {
      if (prev.includes(runId)) return prev.filter((id) => id !== runId);
      if (prev.length >= 2) return [prev[1], runId];
      return [...prev, runId];
    });
  }

  async function viewRunDetail(runId) {
    const res = await fetch(`/api/benchmark/history/${runId}`);
    const run = await res.json();
    setViewRun(run);
  }

  async function doCompare() {
    if (compareIds.length !== 2) return;
    const [a, b] = await Promise.all(compareIds.map((id) => fetch(`/api/benchmark/history/${id}`).then((r) => r.json())));
    setCompareRuns([a, b]);
  }

  async function deleteRun(runId) {
    await fetch(`/api/benchmark/run/${runId}`, { method: "DELETE" }).catch(() => {});
    setCompareIds((prev) => prev.filter((id) => id !== runId));
    loadHistory();
  }

  if (viewRun) return <RunDetail run={viewRun} onBack={() => setViewRun(null)} />;
  if (compareRuns) return <CompareView runA={compareRuns[0]} runB={compareRuns[1]} onBack={() => setCompareRuns(null)} />;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Reports</h1>
        <button type="button" onClick={doCompare} disabled={compareIds.length !== 2}
          className="flex items-center gap-2 px-3 py-2 rounded-md text-sm border border-white/10 text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-card disabled:opacity-40">
          <GitCompare size={14} />{t("reports.compareSelected", { n: compareIds.length })}
        </button>
      </div>
      {loading ? (
        <p className="text-sm text-glyvex-muted">{t("reports.loading")}</p>
      ) : history.length === 0 ? (
        <p className="text-sm text-glyvex-muted">{t("reports.empty")}</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-white/10">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-glyvex-card text-left text-glyvex-muted">
                <th className="px-3 py-2 font-medium"></th>
                <th className="px-3 py-2 font-medium">{t("reports.table.date")}</th><th className="px-3 py-2 font-medium">{t("reports.table.model")}</th>
                <th className="px-3 py-2 font-medium">Sets</th><th className="px-3 py-2 font-medium">{t("reports.table.prompts")}</th>
                <th className="px-3 py-2 font-medium">{t("benchmark.summary.avgTps")}</th><th className="px-3 py-2 font-medium">Avg Judge</th>
                <th className="px-3 py-2 font-medium">{t("reports.table.duration")}</th>
                <th className="px-3 py-2 font-medium">{t("reports.table.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {history.map((run) => (
                <tr key={run.run_id} className="border-t border-white/10 hover:bg-white/5">
                  <td className="px-3 py-2"><input type="checkbox" checked={compareIds.includes(run.run_id)} onChange={() => toggleCompare(run.run_id)} className="accent-glyvex-accent w-4 h-4" /></td>
                  <td className="px-3 py-2 text-glyvex-muted">{fmtDate(run.started_at)}</td>
                  <td className="px-3 py-2">{run.model_name || "—"}</td>
                  <td className="px-3 py-2 text-glyvex-muted">{(run.sets || []).join(", ")}</td>
                  <td className="px-3 py-2 text-glyvex-muted">{run.summary?.total_prompts ?? "—"}</td>
                  <td className="px-3 py-2 text-glyvex-muted">{run.summary?.avg_tps ?? "—"}</td>
                  <td className="px-3 py-2 text-glyvex-muted">{run.summary?.avg_judge_score ? `${run.summary.avg_judge_score.toFixed(1)}/10` : "—"}</td>
                  <td className="px-3 py-2 text-glyvex-muted">{run.summary?.total_duration_s ? `${run.summary.total_duration_s}s` : "—"}</td>
                  <td className="px-3 py-2">
                    <div className="flex gap-2">
                      <button type="button" onClick={() => viewRunDetail(run.run_id)} className="text-glyvex-muted hover:text-glyvex-text" aria-label={t("reports.table.view")}><Eye size={14} /></button>
                      <button type="button" onClick={() => deleteRun(run.run_id)} className="text-glyvex-muted hover:text-red-400" aria-label={t("reports.table.delete")}><Trash2 size={14} /></button>
                    </div>
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
