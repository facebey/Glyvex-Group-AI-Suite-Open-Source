import { useEffect, useMemo, useState } from "react";
import { createBrowserRouter, RouterProvider, NavLink, Outlet, useNavigate } from "react-router-dom";
import {
  MessageSquare, Rocket, Gauge, Activity, FileBarChart, Settings,
  CheckCircle2, Circle, Sun, Moon,
} from "lucide-react";
import { ToastProvider } from "./components/ToastNotification.jsx";
import { useLocalStorage } from "./hooks/useLocalStorage.js";
import StatusWidget from "./components/StatusWidget.jsx";
import Chat from "./pages/Chat.jsx";
import Launcher from "./pages/Launcher.jsx";
import Benchmark from "./pages/Benchmark.jsx";
import Monitor from "./pages/Monitor.jsx";
import Reports from "./pages/Reports.jsx";
import Config from "./pages/Config.jsx";

const NAV_ITEMS = [
  { to: "/", label: "Chat", icon: MessageSquare, end: true },
  { to: "/launcher", label: "Launcher", icon: Rocket },
  { to: "/benchmark", label: "Benchmark", icon: Gauge },
  { to: "/monitor", label: "Monitor", icon: Activity },
  { to: "/reports", label: "Reports", icon: FileBarChart },
  { to: "/config", label: "Config", icon: Settings },
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
        ? theme === "light"
          ? "bg-glyvex-accent/10 text-glyvex-accent"
          : "bg-glyvex-accent/15 text-glyvex-accent"
        : "text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-card",
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
  const navigate = useNavigate();
  const [cfg, setCfg] = useState(null);
  const [modelsCount, setModelsCount] = useState(0);
  const [hasActiveProcess, setHasActiveProcess] = useState(false);

  useEffect(() => {
    let cancelled = false;
    function poll() {
      Promise.all([
        fetch("/api/config").then((r) => r.json()),
        fetch("/api/models").then((r) => r.json()).catch(() => []),
        fetch("/api/launcher/status").then((r) => r.json()).catch(() => []),
      ])
        .then(([config, models, procs]) => {
          if (cancelled) return;
          setCfg(config);
          setModelsCount(Array.isArray(models) ? models.length : 0);
          setHasActiveProcess(Array.isArray(procs) && procs.some((p) => p.state === "running"));
        })
        .catch(() => {});
    }
    poll();
    const intervalId = setInterval(poll, 4000);
    return () => { cancelled = true; clearInterval(intervalId); };
  }, []);

  if (!cfg) return null;

  const step1Done = !allBackendPathsEmpty(cfg);
  const step2Done = (cfg.model_dirs || []).length > 0 && modelsCount > 0;
  const step3Done = hasActiveProcess;

  const steps = [
    { n: 1, label: "Configurar paths de backends", done: step1Done, to: "/config" },
    { n: 2, label: "Agregar directorio de modelos y escanear", done: step2Done, to: "/config" },
    { n: 3, label: "Lanzar el primer modelo", done: step3Done, to: "/launcher" },
  ];

  function goTo(to) { navigate(to); onDismiss(); }

  return (
    <div className="fixed inset-0 z-40 bg-glyvex-bg/95 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="max-w-md w-full bg-glyvex-card border border-glyvex-border rounded-lg p-6 space-y-5">
        <div>
          <h2 className="text-lg font-semibold">Bienvenido a Glyvex AI Suite</h2>
          <p className="text-sm text-glyvex-muted mt-1">
            Seguí estos 3 pasos para tener tu primer modelo corriendo.
          </p>
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
          Saltar por ahora
        </button>
      </div>
    </div>
  );
}

function Layout() {
  const [shouldShowOnboarding, setShouldShowOnboarding] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [theme, setTheme] = useLocalStorage("glyvex-theme", "dark");

  useEffect(() => {
    const root = document.documentElement;
    root.classList.remove("dark", "light");
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
    <div className="min-h-screen flex flex-col bg-glyvex-bg text-glyvex-text">
      <header className="border-b border-glyvex-border bg-glyvex-bg/95 backdrop-blur-md sticky top-0 z-50">
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
            {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
              <NavLink key={to} to={to} end={end} className={navLinkClasses}>
                <Icon size={16} />
                {label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <StatusWidget />
            <button
              type="button"
              onClick={() => setTheme((prev) => (prev === "dark" ? "light" : "dark"))}
              title={theme === "dark" ? "Cambiar a modo claro" : "Cambiar a modo oscuro"}
              className="p-1.5 rounded-md text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-card transition-colors"
            >
              {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
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
        <main className="relative z-10 flex-1 max-w-6xl w-full mx-auto px-4 py-6">
          <Outlet />
        </main>
      </div>

      <footer className="border-t border-glyvex-border bg-glyvex-bg-2 py-3 px-4">
        <div className="max-w-6xl mx-auto flex items-center justify-between text-xs text-glyvex-muted-2">
          <span>
            Desarrollado por{" "}
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
              className="hover:text-glyvex-muted transition-colors"
            >
              Glyvex Group
            </a>
          </span>
          <span>© {new Date().getFullYear()} Glyvex Group · Todos los derechos reservados</span>
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
