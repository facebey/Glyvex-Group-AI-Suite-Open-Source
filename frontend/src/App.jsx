import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { createBrowserRouter, RouterProvider, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  MessageSquare, Rocket, Gauge, Activity, FileBarChart, Settings,
  CheckCircle2, Circle, Palette, Cpu, Download, Loader2, AlertTriangle, Github,
} from "lucide-react";
import { ToastProvider } from "./components/ToastNotification.jsx";
import { getStoredTheme, cycleTheme, nextTheme, THEMES, THEME_META, THEME_CHANGED_EVENT } from "./lib/theme.js";
import MatrixRain from "./components/MatrixRain.jsx";
import { consumeSSE } from "./lib/sse.js";
import StatusWidget from "./components/StatusWidget.jsx";
import LanguageSelector from "./components/LanguageSelector.jsx";
import Chat from "./pages/Chat.jsx";
import Launcher from "./pages/Launcher.jsx";
import Benchmark from "./pages/Benchmark.jsx";
import Monitor from "./pages/Monitor.jsx";
import Reports from "./pages/Reports.jsx";
import Config from "./pages/Config.jsx";

const NAV_ITEMS = [
  { to: "/", labelKey: "nav.chat", icon: MessageSquare, end: true },
  { to: "/launcher", labelKey: "nav.launcher", icon: Rocket },
  { to: "/benchmark", labelKey: "nav.benchmark", icon: Gauge },
  { to: "/monitor", labelKey: "nav.monitor", icon: Activity },
  { to: "/reports", labelKey: "nav.reports", icon: FileBarChart },
  { to: "/config", labelKey: "nav.config", icon: Settings },
];

/**
 * El fondo del nav-link activo necesita algo más de opacidad en light para
 * mantener contraste legible sobre --color-glyvex-accent (teal más oscuro
 * en ese tema): 10% en light, 15% en dark.
 */
function makeNavLinkClasses(theme) {
  return function navLinkClasses({ isActive }) {
    return [
      "flex items-center gap-2 px-3 py-2 rounded-md text-sm transition-colors",
      isActive
        ? "gx-nav-on " + (theme === "light"
          ? "bg-glyvex-accent/10 text-glyvex-accent"
          : "bg-glyvex-accent/15 text-glyvex-accent")
        : "text-glyvex-bg-muted hover:text-glyvex-text hover:bg-glyvex-card",
    ].join(" ");
  };
}

function allBackendPathsEmpty(cfg) {
  const backends = cfg?.backends || {};
  return Object.values(backends).every((b) => !b.binary_path && !b.python_env);
}

/** Ícono de red neuronal Glyvex AI — logo del navbar (gradiente violet→sky). */
function GlyvexAiIcon({ size = 28 }) {
  return (
    <svg viewBox="0 0 80 80" width={size} height={size} xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="navAiGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#7c3aed" />
          <stop offset="100%" stopColor="#0ea5e9" />
        </linearGradient>
      </defs>
      <circle cx="40" cy="40" r="28" fill="none" stroke="url(#navAiGrad)" strokeWidth="2" />
      <circle cx="40" cy="40" r="8" fill="#7c3aed" />
      <circle cx="40" cy="12" r="4.5" fill="#0ea5e9" />
      <circle cx="64" cy="26" r="4.5" fill="#06b6d4" />
      <circle cx="64" cy="54" r="4.5" fill="#7c3aed" opacity="0.75" />
      <circle cx="40" cy="68" r="4.5" fill="#0ea5e9" opacity="0.75" />
      <circle cx="16" cy="54" r="4.5" fill="#06b6d4" />
      <circle cx="16" cy="26" r="4.5" fill="#7c3aed" opacity="0.55" />
      <line x1="40" y1="32" x2="40" y2="16" stroke="#0ea5e9" strokeWidth="1.5" />
      <line x1="47" y1="35" x2="60" y2="29" stroke="#06b6d4" strokeWidth="1.5" />
      <line x1="47" y1="45" x2="60" y2="51" stroke="#7c3aed" strokeWidth="1.5" />
      <line x1="40" y1="48" x2="40" y2="64" stroke="#0ea5e9" strokeWidth="1.5" />
      <line x1="33" y1="45" x2="20" y2="51" stroke="#06b6d4" strokeWidth="1.5" />
      <line x1="33" y1="35" x2="20" y2="29" stroke="#7c3aed" strokeWidth="1.5" />
    </svg>
  );
}

/** Misma silueta que GlyvexAiIcon pero en currentColor plano, para la marca de agua. */
function GlyvexAiWatermark({ size = 400 }) {
  return (
    <svg viewBox="0 0 80 80" width={size} height={size} xmlns="http://www.w3.org/2000/svg">
      <circle cx="40" cy="40" r="28" fill="none" stroke="currentColor" strokeWidth="2" />
      <circle cx="40" cy="40" r="8" fill="currentColor" />
      <circle cx="40" cy="12" r="4.5" fill="currentColor" />
      <circle cx="64" cy="26" r="4.5" fill="currentColor" />
      <circle cx="64" cy="54" r="4.5" fill="currentColor" opacity="0.75" />
      <circle cx="40" cy="68" r="4.5" fill="currentColor" opacity="0.75" />
      <circle cx="16" cy="54" r="4.5" fill="currentColor" />
      <circle cx="16" cy="26" r="4.5" fill="currentColor" opacity="0.55" />
      <line x1="40" y1="32" x2="40" y2="16" stroke="currentColor" strokeWidth="1.5" />
      <line x1="47" y1="35" x2="60" y2="29" stroke="currentColor" strokeWidth="1.5" />
      <line x1="47" y1="45" x2="60" y2="51" stroke="currentColor" strokeWidth="1.5" />
      <line x1="40" y1="48" x2="40" y2="64" stroke="currentColor" strokeWidth="1.5" />
      <line x1="33" y1="45" x2="20" y2="51" stroke="currentColor" strokeWidth="1.5" />
      <line x1="33" y1="35" x2="20" y2="29" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}

function OnboardingScreen({ onDismiss }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [cfg, setCfg] = useState(null);
  const [modelsCount, setModelsCount] = useState(0);
  const [hasActiveProcess, setHasActiveProcess] = useState(false);
  const [runtime, setRuntime] = useState(null);
  const [dl, setDl] = useState(null);
  const abortRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    function poll() {
      Promise.all([
        fetch("/api/config").then((r) => r.json()),
        fetch("/api/models").then((r) => r.json()).catch(() => []),
        fetch("/api/launcher/status").then((r) => r.json()).catch(() => []),
        fetch("/api/runtime/status").then((r) => r.json()).catch(() => null),
      ])
        .then(([config, models, procs, rt]) => {
          if (cancelled) return;
          setCfg(config);
          setModelsCount(Array.isArray(models) ? models.length : 0);
          setHasActiveProcess(Array.isArray(procs) && procs.some((p) => p.state === "running"));
          setRuntime(rt);
        })
        .catch(() => {});
    }
    poll();
    const intervalId = setInterval(poll, 4000);
    return () => {
      cancelled = true;
      clearInterval(intervalId);
      abortRef.current?.abort();
    };
  }, []);

  async function handleRuntimeDownload() {
    const controller = new AbortController();
    abortRef.current = controller;
    setDl({ phase: "downloading", pct: 0, detail: "" });
    try {
      const res = await fetch("/api/runtime/download", { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || t("onboarding.runtimeError"));
      }
      await consumeSSE(res, controller.signal, (event) => {
        if (event.type === "progress") {
          setDl({ phase: "downloading", pct: event.pct, detail: event.detail });
        } else if (event.type === "done") {
          setDl(null);
          setRuntime(event.status);
        } else if (event.type === "error") {
          setDl({ phase: "error", error: event.message });
        }
      });
    } catch (err) {
      setDl({ phase: "error", error: err.message });
    }
  }

  if (!cfg) return null;

  const step1Done = !allBackendPathsEmpty(cfg);
  const step2Done = (cfg.model_dirs || []).length > 0 && modelsCount > 0;
  const step3Done = hasActiveProcess;

  // Estado del paso "Descargar runtime" (RT-10): experto gana (usa su
  // binario), luego descarga en curso, error, ready. En Windows el motor
  // base se ofrece a TODA la GPU (la aceleración depende de la familia);
  // fuera de Windows va al banner "no soportado".
  const expertBinary = cfg?.backends?.llama_server?.binary_path;
  const isWindows = runtime?.platform === "Windows";
  const gpuLabel = runtime?.gpu === "cpu" ? "CPU" : (runtime?.gpu || "CPU");
  const isNvidia = runtime?.gpu_family === "nvidia";
  const sizeMb = isNvidia ? 549 : 19;

  let runtimeView;
  if (expertBinary) runtimeView = "expert";
  else if (dl?.phase === "downloading" || runtime?.state === "downloading") runtimeView = "downloading";
  else if (dl?.phase === "error" || runtime?.state === "error") runtimeView = "error";
  else if (runtime?.state === "ready") runtimeView = "ready";
  else if (!isWindows) runtimeView = "skipped";
  else runtimeView = "action";

  const steps = [
    { n: 1, label: t("onboarding.step1"), done: step1Done, to: "/config" },
    { n: 2, label: t("onboarding.step2"), done: step2Done, to: "/config" },
    { n: 3, label: t("onboarding.step3"), done: step3Done, to: "/launcher" },
  ];

  function goTo(to) { navigate(to); onDismiss(); }

  return (
    <div className="fixed inset-0 z-40 bg-glyvex-bg/95 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="max-w-md w-full bg-glyvex-card border border-glyvex-border rounded-lg p-6 space-y-5">
        <div>
          <h2 className="text-lg font-semibold">{t("onboarding.title")}</h2>
          <p className="text-sm text-glyvex-muted mt-1">
            {t("onboarding.subtitle")}
          </p>
        </div>
        <div className="rounded-md border border-glyvex-border p-4 space-y-3">
          {runtimeView === "ready" && (
            <>
              <div className="flex items-center gap-2 text-sm font-medium">
                <CheckCircle2 size={18} className="text-emerald-400 shrink-0" />
                {t("onboarding.runtimeReady", { pin: runtime?.pin })}
              </div>
              <p className="text-xs text-glyvex-muted">
                {t("onboarding.runtimeReadySub", { gpu: gpuLabel })}
              </p>
              {isNvidia && !runtime?.accel && (
                <p className="text-xs text-amber-400">
                  {t("onboarding.runtimeDegraded")}
                </p>
              )}
            </>
          )}
          {runtimeView === "expert" && (
            <>
              <div className="flex items-center gap-2 text-sm font-medium">
                <CheckCircle2 size={18} className="text-emerald-400 shrink-0" />
                {t("onboarding.runtimeExpert")}
              </div>
              <p className="text-xs text-glyvex-muted">{t("onboarding.runtimeExpertSub")}</p>
            </>
          )}
          {runtimeView === "downloading" && (
            <>
              <div className="flex items-center gap-2 text-sm font-medium">
                <Loader2 size={18} className="text-glyvex-accent animate-spin shrink-0" />
                {t("onboarding.runtimeDownloading")}
              </div>
              <div className="h-2 rounded-full bg-glyvex-border overflow-hidden">
                <div className="h-full bg-glyvex-accent transition-all" style={{ width: `${dl?.pct ?? 0}%` }} />
              </div>
              {dl?.detail && (
                <p className="text-xs text-glyvex-muted">{Math.round(dl.pct)}% · {dl.detail}</p>
              )}
            </>
          )}
          {runtimeView === "action" && (
            <>
              <div className="flex items-center gap-2 text-sm font-medium">
                <Cpu size={18} className="text-glyvex-accent shrink-0" />
                {t("onboarding.runtimeGpuDetected", { gpu: gpuLabel })}
              </div>
              <p className="text-xs text-glyvex-muted">
                {t("onboarding.runtimeSize", { size: sizeMb })}
                {!isNvidia && <> · {t("onboarding.runtimeCpuNote")}</>}
              </p>
              <button type="button" onClick={handleRuntimeDownload}
                className="flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium bg-emerald-600 text-white hover:bg-emerald-500">
                <Download size={16} />{t("onboarding.runtimeDownload")}
              </button>
            </>
          )}
          {runtimeView === "error" && (
            <>
              <div className="flex items-center gap-2 text-sm font-medium text-red-400">
                <AlertTriangle size={18} className="shrink-0" />
                {t("onboarding.runtimeError")}
              </div>
              <p className="text-xs text-red-400/80 break-all">{dl?.error || runtime?.error}</p>
              <button type="button" onClick={handleRuntimeDownload}
                className="flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium border border-glyvex-accent/40 bg-glyvex-accent/15 text-glyvex-accent hover:bg-glyvex-accent/25">
                {t("onboarding.runtimeRetry")}
              </button>
            </>
          )}
          {runtimeView === "skipped" && (
            <div className="flex items-start gap-2">
              <AlertTriangle size={18} className="text-amber-400 shrink-0 mt-0.5" />
              <div className="space-y-1">
                <p className="text-sm text-glyvex-text">
                  {t("onboarding.runtimeUnsupported")}
                </p>
                <button type="button" onClick={() => goTo("/config")}
                  className="text-xs text-glyvex-accent hover:underline">
                  {t("onboarding.runtimeGoConfig")}
                </button>
              </div>
            </div>
          )}
        </div>
        <div className="space-y-2">
          {steps.map((step) => (
            <button key={step.n} type="button" onClick={() => goTo(step.to)}
              className="w-full flex items-center gap-3 px-3 py-2.5 rounded-md border border-glyvex-border hover:bg-glyvex-card-hi text-left">
              {step.done
                ? <CheckCircle2 size={18} className="text-emerald-400 shrink-0" />
                : <Circle size={18} className="text-glyvex-muted shrink-0" />}
              <span className={step.done ? "text-glyvex-muted line-through" : "text-glyvex-text"}>
                {step.n}. {step.label}
              </span>
            </button>
          ))}
        </div>
        <button type="button" onClick={onDismiss}
          className="w-full text-center text-sm text-glyvex-muted hover:text-glyvex-text">
          {t("onboarding.skip")}
        </button>
      </div>
    </div>
  );
}

// Páginas que son un espacio de trabajo y aprovechan todo el ancho de la
// pantalla. En 6xl (1152 px) el chat quedaba en ~800 px al restarle el panel
// de configuración, y las respuestas con código o tablas se veían angostas.
const WIDE_ROUTES = new Set(["/"]);

function Layout() {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const mainWidth = WIDE_ROUTES.has(pathname) ? "max-w-[1800px]" : "max-w-6xl";
  const [shouldShowOnboarding, setShouldShowOnboarding] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [theme, setTheme] = useState(getStoredTheme());

  useEffect(() => {
    const onThemeChanged = (e) => setTheme(e.detail);
    window.addEventListener(THEME_CHANGED_EVENT, onThemeChanged);
    return () => window.removeEventListener(THEME_CHANGED_EVENT, onThemeChanged);
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    THEMES.forEach((t) => root.classList.remove(t));
    root.classList.add(theme);
  }, [theme]);

  const navLinkClasses = useMemo(() => makeNavLinkClasses(theme), [theme]);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/config").then((r) => r.json()).then((cfg) => {
      if (cancelled) return;
      const empty = (cfg.model_dirs || []).length === 0 && allBackendPathsEmpty(cfg);
      setShouldShowOnboarding(empty);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="gx-app min-h-screen flex flex-col bg-glyvex-bg text-glyvex-bg-text">
      {theme === "matrix" && <MatrixRain />}
      <header className="gx-shell border-b border-glyvex-border bg-glyvex-bg/95 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-6xl mx-auto flex items-center gap-6 px-4 py-3">
          <a
            href="https://ai.glyvexgroup.com"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2.5 hover:opacity-90 transition-opacity"
            title="Glyvex AI — glyvexgroup.com"
          >
            <GlyvexAiIcon size={28} />
            <span className="font-bold text-base tracking-tight">
              <span style={{ color: "#0d9488" }}>GLYVEX</span>
              <span style={{ color: "#7c3aed" }}> AI</span>
            </span>
          </a>
          <nav className="flex items-center gap-1">
            {NAV_ITEMS.map(({ to, labelKey, icon: Icon, end }) => (
              <NavLink key={to} to={to} end={end} className={navLinkClasses}>
                <Icon size={16} />
                {t(labelKey)}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <StatusWidget />
            <LanguageSelector />
            <button
              type="button"
              onClick={() => cycleTheme(theme)}
              title={t("common.themeCycle", {
                current: t(THEME_META[theme].labelKey),
                next: t(THEME_META[nextTheme(theme)].labelKey),
              })}
              aria-label={t("common.themeCycle", {
                current: t(THEME_META[theme].labelKey),
                next: t(THEME_META[nextTheme(theme)].labelKey),
              })}
              className="relative p-1.5 rounded-md text-glyvex-bg-muted hover:text-glyvex-text hover:bg-glyvex-card transition-colors"
            >
              <Palette size={17} />
              {/* muestra del tema actual */}
              <span
                aria-hidden="true"
                className="absolute bottom-0.5 right-0.5 w-2 h-2 rounded-full ring-1 ring-black/40"
                style={{ backgroundColor: THEME_META[theme].swatch }}
              />
            </button>
          </div>
        </div>
      </header>

      <div className="relative flex-1">
        {/* Marca de agua */}
        <div
          className="pointer-events-none fixed inset-0 flex items-center justify-center opacity-[0.02] z-0"
          aria-hidden="true"
        >
          <GlyvexAiWatermark size={400} />
        </div>
        <main className={`relative z-10 flex-1 ${mainWidth} w-full mx-auto px-4 py-6`}>
          <Outlet />
        </main>
      </div>

      <footer className="gx-shell relative z-10 border-t border-glyvex-border bg-glyvex-bg-2 py-3 px-4">
        <div className="max-w-6xl mx-auto flex items-center justify-between text-xs text-glyvex-bg-muted">
          <span>
            {t("footer.developedBy")}{" "}
            <a
              href="https://ai.glyvexgroup.com"
              target="_blank"
              rel="noopener noreferrer"
              className="text-glyvex-accent hover:text-glyvex-cyan transition-colors"
            >
              Glyvex AI
            </a>
            {" · "}
            <a
              href="https://glyvexgroup.com"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-glyvex-bg-muted transition-colors"
            >
              Glyvex Group
            </a>
            {" · "}
            <a
              href="https://github.com/facebey/Glyvex-Group-AI-Suite-Open-Source"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-glyvex-bg-muted transition-colors"
              title="Código abierto — Apache 2.0"
            >
              <span className="inline-flex items-center gap-1">
                <Github size={12} /> GitHub
              </span>
            </a>
          </span>
          <span>{t("footer.rights", { year: new Date().getFullYear() })}</span>
        </div>
      </footer>

      {shouldShowOnboarding && !dismissed && (
        <OnboardingScreen onDismiss={() => setDismissed(true)} />
      )}
    </div>
  );
}

const router = createBrowserRouter([
  { path: "/", element: <Layout />,
    children: [
      { index: true, element: <Chat /> },
      { path: "launcher", element: <Launcher /> },
      { path: "benchmark", element: <Benchmark /> },
      { path: "monitor", element: <Monitor /> },
      { path: "reports", element: <Reports /> },
      { path: "config", element: <Config /> },
    ],
  },
]);

export default function App() {
  return (
    <ToastProvider>
      <RouterProvider router={router} />
    </ToastProvider>
  );
}
