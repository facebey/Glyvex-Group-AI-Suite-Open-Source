// frontend/src/components/JudgePanel.jsx
// Panel de Judge compartido: Benchmark lo usa sobre el run recién terminado y
// Reports sobre runs históricos. El backend (POST /run/{run_id}/judge) juzga
// solo los results sin score_judge, sin importar si el run está vivo o persistido.
import { useEffect, useRef, useState } from "react";
import { Square } from "lucide-react";
import { useTranslation } from "react-i18next";

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

export default function JudgePanel({ runId, totalResults = 0, runEndpoint = null, runApiKey = null, initialAvgScore = null, onRunRefreshed }) {
  const { t } = useTranslation();
  const [judgeEndpoint, setJudgeEndpoint] = useState(runEndpoint || "http://127.0.0.1:8080");
  const [judgeModel, setJudgeModel] = useState("");
  // Si el run corrió contra un endpoint externo con key (K2.a), el judge puede
  // usar la misma sin reescribirla; el panel se monta con key={run_id}, así
  // que seedear el estado inicial es seguro.
  const [judgeApiKey, setJudgeApiKey] = useState(runApiKey || "");
  const [judging, setJudging] = useState(false);
  const [judgeProgress, setJudgeProgress] = useState({ evaluated: 0, total: 0, currentPrompt: null, currentScore: null });
  const [judgeAvgScore, setJudgeAvgScore] = useState(initialAvgScore);
  const [judgeError, setJudgeError] = useState(null);
  const judgeAbortRef = useRef(null);
  const judgeEndpointTouchedRef = useRef(false);

  // El endpoint del judge arranca igual al del run, salvo que el usuario lo haya editado.
  useEffect(() => {
    if (judgeEndpointTouchedRef.current) return;
    if (runEndpoint) setJudgeEndpoint(runEndpoint);
  }, [runEndpoint]);

  useEffect(() => () => { judgeAbortRef.current?.abort(); }, []);

  async function handleJudge() {
    if (!judgeModel.trim()) return;
    setJudging(true);
    setJudgeError(null);
    setJudgeAvgScore(null);
    setJudgeProgress({ evaluated: 0, total: totalResults, currentPrompt: null, currentScore: null });
    const controller = new AbortController();
    judgeAbortRef.current = controller;
    try {
      const response = await fetch(`/api/benchmark/run/${runId}/judge`, {
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
            // Refrescar scores/badges desde la fuente persistida (history ya
            // incluye score_judge y el summary recalculado).
            const refreshed = await fetch(`/api/benchmark/history/${runId}`);
            if (refreshed.ok && onRunRefreshed) {
              onRunRefreshed(await refreshed.json());
            }
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

  return (
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
  );
}
