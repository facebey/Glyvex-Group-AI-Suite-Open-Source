import { createContext, useCallback, useContext, useEffect, useReducer, useRef } from "react";
import { useTranslation } from "react-i18next";
import { CheckCircle2, XCircle, AlertTriangle, Info, X } from "lucide-react";

const MAX_TOASTS = 5;
const DEFAULT_DURATION_MS = 5000;

const LEVEL_STYLES = {
  success: { icon: CheckCircle2, classes: "border-emerald-500/30 bg-emerald-500/10 text-emerald-400" },
  error: { icon: XCircle, classes: "border-red-500/30 bg-red-500/10 text-red-400" },
  warning: { icon: AlertTriangle, classes: "border-amber-500/30 bg-amber-500/10 text-amber-400" },
  info: { icon: Info, classes: "border-glyvex-accent/30 bg-glyvex-accent/10 text-glyvex-accent" },
};

const ToastContext = createContext(null);

function toastReducer(state, action) {
  switch (action.type) {
    case "ADD": {
      const next = [...state, action.payload];
      // Stack de hasta 5 simultáneos: si se pasa, se descarta el más viejo.
      return next.length > MAX_TOASTS ? next.slice(next.length - MAX_TOASTS) : next;
    }
    case "DISMISS":
      return state.filter((t) => t.id !== action.id);
    default:
      return state;
  }
}

/**
 * Envolvé la app con <ToastProvider> una sola vez (en App.jsx). Cualquier
 * componente hijo puede llamar useToast() para disparar notificaciones.
 */
export function ToastProvider({ children }) {
  const [toasts, dispatch] = useReducer(toastReducer, []);
  const timeoutsRef = useRef(new Map());

  const dismiss = useCallback((id) => {
    dispatch({ type: "DISMISS", id });
    const timeoutId = timeoutsRef.current.get(id);
    if (timeoutId) {
      clearTimeout(timeoutId);
      timeoutsRef.current.delete(id);
    }
  }, []);

  const addToast = useCallback((message, level = "info", duration = DEFAULT_DURATION_MS) => {
    const id = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    dispatch({ type: "ADD", payload: { id, message, level, duration } });
    if (duration > 0) {
      const timeoutId = setTimeout(() => dismiss(id), duration);
      timeoutsRef.current.set(id, timeoutId);
    }
    return id;
  }, [dismiss]);

  // Cleanup: limpiar todos los timers pendientes al desmontar el provider.
  useEffect(() => {
    const timeouts = timeoutsRef.current;
    return () => {
      timeouts.forEach((timeoutId) => clearTimeout(timeoutId));
      timeouts.clear();
    };
  }, []);

  return (
    <ToastContext.Provider value={{ toasts, addToast, dismiss }}>
      {children}
      <ToastStack toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

/** Hook de conveniencia: const { addToast } = useToast(); addToast("Listo", "success"); */
export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error("useToast debe usarse dentro de un <ToastProvider>");
  }
  return ctx;
}

function ToastStack({ toasts, onDismiss }) {
  const { t } = useTranslation();
  if (toasts.length === 0) return null;

  return (
    <div className="fixed top-4 right-4 z-50 flex flex-col gap-2 w-80 max-w-[calc(100vw-2rem)]">
      {toasts.map((toast) => {
        const { icon: Icon, classes } = LEVEL_STYLES[toast.level] || LEVEL_STYLES.info;
        return (
          <div
            key={toast.id}
            role="alert"
            className={`flex items-start gap-2 px-3 py-2.5 rounded-lg border shadow-lg backdrop-blur-sm bg-glyvex-card ${classes}`}
          >
            <Icon size={16} className="shrink-0 mt-0.5" />
            <p className="flex-1 text-sm text-glyvex-text">{toast.message}</p>
            <button
              type="button"
              onClick={() => onDismiss(toast.id)}
              className="shrink-0 text-glyvex-muted hover:text-glyvex-text"
              aria-label={t("toast.close")}
            >
              <X size={14} />
            </button>
          </div>
        );
      })}
    </div>
  );
}

export default ToastProvider;
