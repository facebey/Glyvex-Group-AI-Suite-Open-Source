import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown, ChevronRight, Download, Upload, Trash2,
  PanelLeftClose, PanelLeftOpen, X, History, Plus,
  Search, Loader2, FilePlus2, ArrowDown,
} from "lucide-react";
import MetricsBar from "../components/MetricsBar.jsx";
import MessageBubble from "../components/MessageBubble.jsx";
import ChatComposer from "../components/ChatComposer.jsx";
import { ToolActivityLive } from "../components/ToolActivity.jsx";
import { DEFAULT_REASONING } from "../components/ReasoningControl.jsx";
import { useToast } from "../components/ToastNotification.jsx";
import { useLocalStorage } from "../hooks/useLocalStorage.js";
import { inputClasses } from "../lib/styles.js";
import {
  ROOT_ID, appendChain, emptyTree, patchNode, siblingInfo, switchBranch, toTree, visiblePath,
} from "../lib/conversationTree.js";
import { useAutoScroll } from "../hooks/useAutoScroll.js";

const SESSION_KEY = "glyvex_chat_messages";

const DEFAULT_PARAMS = {
  temperature: 0.7,
  top_p: 0.9,
  top_k: 40,
  min_p: 0.0,
  repeat_penalty: 1.1,
  max_tokens: 4096,
  seed: -1,
};

const HISTORY_PAGE_SIZE = 50;
const AUTOSAVE_DEBOUNCE_MS = 1000;
/** Refresco de métricas: ~12 fps. Suficiente para leerse "en vivo" sin
 *  disparar un re-render extra por cada token del stream. */
const METRICS_FLUSH_MS = 80;
/** La estimación de contexto pega contra /tokenize del upstream, así que se
 *  espera a que el usuario pare de escribir antes de pedirla. */
const ESTIMATE_DEBOUNCE_MS = 700;

function uuid() {
  return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function nowIso() {
  return new Date().toISOString();
}

function formatDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
}

/**
 * Content del mensaje tal como lo espera el upstream.
 *
 * Con imágenes va el array multimodal, donde las imágenes viajan como
 * {type: "image_ref"} y el backend las expande a base64 leyendo el archivo
 * de disco justo antes de mandarlas (así el base64 nunca toca el estado de
 * React, sessionStorage ni la DB).
 *
 * Sin imágenes se aplana todo a un string: el resultado que ve el modelo es
 * idéntico, pero evita mandarle un array de partes a builds de llama-server
 * o a backends que solo esperan texto plano.
 */
function toPayloadContent(message) {
  const usable = (message.attachments || []).filter((a) => a.status === "ready");
  if (usable.length === 0) return message.content;

  const textBlocks = usable
    .filter((a) => a.kind !== "image" && a.text)
    .map((a) => `--- archivo: ${a.filename} ---\n${a.text}`);

  const images = usable.filter((a) => a.kind === "image");

  if (images.length === 0) {
    return [message.content, ...textBlocks].filter(Boolean).join("\n\n");
  }

  return [
    { type: "text", text: message.content || "" },
    ...textBlocks.map((text) => ({ type: "text", text })),
    ...images.map((a) => ({ type: "image_ref", id: a.id, filename: a.filename })),
  ];
}

/**
 * Aplana un mensaje del historial a los mensajes que espera el upstream.
 *
 * Una respuesta que usó tools no es un solo mensaje: es el assistant que
 * pidió las tools, los resultados role=tool, y recién después el texto
 * final. Se guardan los tres para que el próximo turno conserve el contexto
 * de lo buscado en vez de repreguntarle al modelo sobre fuentes que ya no ve.
 */
function toPayloadMessages(message) {
  const exchange = (message.tool_exchange || []).map((m) => ({ ...m }));
  return [...exchange, { role: message.role, content: toPayloadContent(message) }];
}

/** Lee el body SSE de fetch como stream y llama onEvent por cada evento. */
async function consumeSSE(response, signal, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    if (signal.aborted) {
      await reader.cancel().catch(() => {});
      return;
    }
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const rawEvent = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const line = rawEvent.split("\n").find((l) => l.startsWith("data:"));
      if (line) {
        const data = line.slice(5).trim();
        if (data === "[DONE]") return;
        try {
          onEvent(JSON.parse(data));
        } catch {
          // línea malformada — se ignora
        }
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
}

function exportAsJson(messages, model) {
  const payload = { model, exported_at: nowIso(), messages };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `glyvex-chat-${Date.now()}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

function exportAsMarkdown(messages, model) {
  const date = new Date().toLocaleString();
  let md = `# Conversación — ${model || "modelo"} — ${date}\n\n`;
  for (const m of messages) {
    const time = m.timestamp ? new Date(m.timestamp).toLocaleTimeString() : "";
    if (m.role === "user") {
      md += `**Usuario** ${time}\n`;
      if (m.attachments?.length) {
        md += `_Adjuntos: ${m.attachments.map((a) => a.filename).join(", ")}_\n\n`;
      }
      md += `${m.content}\n\n`;
    } else if (m.role === "assistant") {
      const tps = m.metrics?.tps ? `${m.metrics.tps.toFixed(1)} t/s` : "";
      const ttft = m.metrics?.ttft_ms ? `${Math.round(m.metrics.ttft_ms)}ms TTFT` : "";
      const suffix = [tps, ttft].filter(Boolean).join(" | ");
      md += `**Asistente** ${time}${suffix ? " | " + suffix : ""}\n${m.content}\n`;
      if (m.thinking) {
        md += `\n<details><summary>🧠 Razonamiento</summary>\n\n${m.thinking}\n\n</details>\n`;
      }
      md += "\n";
    }
  }
  const blob = new Blob([md], { type: "text/markdown" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `glyvex-chat-${Date.now()}.md`;
  a.click();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Bloques del panel izquierdo
// ---------------------------------------------------------------------------

function CollapsibleSection({ title, open, onToggle, children }) {
  return (
    <div className="border-t border-white/10 pt-3">
      <button
        type="button"
        onClick={onToggle}
        className="w-full flex items-center justify-between text-sm font-medium text-glyvex-muted uppercase tracking-wide mb-2"
      >
        {title}
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </button>
      {open && <div className="space-y-3">{children}</div>}
    </div>
  );
}

function Slider({ label, value, min, max, step = 1, onChange, formatValue }) {
  return (
    <label className="block">
      <span className="flex justify-between text-xs text-glyvex-muted mb-1">
        <span>{label}</span>
        <span className="text-glyvex-text">{formatValue ? formatValue(value) : value}</span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-glyvex-accent"
      />
    </label>
  );
}

// ---------------------------------------------------------------------------
// Panel de historial
// ---------------------------------------------------------------------------

function HistoryPanel({
  conversations, loading, search, currentId,
  onSearch, onSelect, onDelete, onNew, onClose,
}) {
  return (
    <div className="absolute inset-y-0 left-0 w-[280px] z-20 flex flex-col bg-glyvex-card border-r border-white/10 shadow-xl shadow-black/40">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-white/10">
        <span className="text-sm font-medium text-glyvex-text flex-1">Historial</span>
        <button
          type="button"
          onClick={onNew}
          title="Nueva conversación"
          className="p-1 rounded-md text-glyvex-muted hover:text-glyvex-text hover:bg-white/5"
        >
          <Plus size={16} />
        </button>
        <button
          type="button"
          onClick={onClose}
          title="Cerrar historial"
          className="p-1 rounded-md text-glyvex-muted hover:text-glyvex-text hover:bg-white/5"
        >
          <X size={16} />
        </button>
      </div>

      <div className="p-2 border-b border-white/10">
        <div className="relative">
          <Search size={13} className="absolute left-2 top-1/2 -translate-y-1/2 text-glyvex-muted" />
          <input
            className={inputClasses + " pl-7"}
            value={search}
            onChange={(e) => onSearch(e.target.value)}
            placeholder="Buscar por título o contenido"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-2 space-y-1.5">
        {loading && (
          <div className="flex items-center justify-center gap-2 py-6 text-xs text-glyvex-muted">
            <Loader2 size={14} className="animate-spin" /> Cargando
          </div>
        )}

        {!loading && conversations.length === 0 && (
          <p className="text-xs text-glyvex-muted text-center py-6 px-2">
            Todavía no hay conversaciones guardadas. Escribí un mensaje y se guarda sola.
          </p>
        )}

        {!loading &&
          conversations.map((conv) => (
            <div
              key={conv.id}
              className={
                "group relative rounded-md border p-2 cursor-pointer " +
                (conv.id === currentId
                  ? "border-glyvex-accent/60 bg-glyvex-accent/10"
                  : "border-white/10 hover:bg-white/5")
              }
              onClick={() => onSelect(conv.id)}
            >
              <p className="text-sm text-glyvex-text truncate pr-6">
                {conv.title || "Conversación sin título"}
              </p>
              <p className="text-xs text-glyvex-muted truncate">{conv.model_name || "—"}</p>
              <p className="text-xs text-glyvex-muted tabular-nums">
                {formatDate(conv.updated_at)} · {conv.message_count} mensajes
              </p>
              <button
                type="button"
                title="Eliminar conversación"
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete(conv.id);
                }}
                className="absolute top-2 right-2 p-1 rounded-md text-glyvex-muted opacity-0 group-hover:opacity-100 hover:text-red-400 hover:bg-white/5"
              >
                <Trash2 size={13} />
              </button>
            </div>
          ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Página
// ---------------------------------------------------------------------------

export default function Chat() {
  const { addToast } = useToast();

  const [endpoints, setEndpoints] = useState([]);
  const [endpointUrl, setEndpointUrl] = useLocalStorage("glyvex_chat_endpoint", "http://127.0.0.1:8080");
  const [manualEndpoint, setManualEndpoint] = useState("");
  const [models, setModels] = useState([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [capabilities, setCapabilities] = useState(null);
  const [apiKey, setApiKey] = useState("");

  const [systemPrompt, setSystemPrompt] = useState("");
  const [systemPromptOpen, setSystemPromptOpen] = useState(false);

  const [reasoning, setReasoning] = useLocalStorage("glyvex_chat_reasoning", DEFAULT_REASONING);

  const [paramsOpen, setParamsOpen] = useState(false);
  const [params, setParams] = useLocalStorage("glyvex_chat_params", DEFAULT_PARAMS);

  const [compact, setCompact] = useLocalStorage("glyvex_compact_chat", false);
  const [exportPanelOpen, setExportPanelOpen] = useState(false);

  // La conversación es un árbol (ver lib/conversationTree.js): editar o
  // regenerar crean ramas hermanas en vez de pisar lo anterior. `nodes` es
  // el árbol completo que se persiste; `path` es la rama visible.
  const [nodes, setNodes] = useState(emptyTree);
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState([]);
  const [dragging, setDragging] = useState(false);
  const [toolsEnabled, setToolsEnabled] = useState(false);
  const [toolsStatus, setToolsStatus] = useState(null);
  const [liveToolActivity, setLiveToolActivity] = useState([]);
  const [sttStatus, setSttStatus] = useState(null);
  const [interimTranscript, setInterimTranscript] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [liveMetrics, setLiveMetrics] = useState(null);
  const [estimate, setEstimate] = useState(null);
  const [chatError, setChatError] = useState(null);

  const path = useMemo(() => visiblePath(nodes), [nodes]);

  const [historyOpen, setHistoryOpen] = useState(false);
  const [conversations, setConversations] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historySearch, setHistorySearch] = useState("");
  const [currentConvId, setCurrentConvId] = useLocalStorage("glyvex_current_conv_id", null);

  const abortRef = useRef(null);
  const importInputRef = useRef(null);
  const metricsRef = useRef(null);
  const metricsFlushRef = useRef(0);
  const saveTimerRef = useRef(null);
  const dragDepthRef = useRef(0);
  const inputRef = useRef("");
  inputRef.current = input;
  const toolsEnabledRef = useRef(false);
  toolsEnabledRef.current = toolsEnabled;

  // Snapshot del estado para los callbacks diferidos (autosave con debounce),
  // que de otro modo verían valores viejos por el closure.
  const stateRef = useRef({});
  stateRef.current = {
    nodes, path, currentConvId, selectedModel, endpointUrl, params, reasoning, systemPrompt,
  };

  const reasoningEnabled = reasoning.mode !== "off";
  const preserveThinking = reasoning.mode === "preserve";
  const visionReady = Boolean(capabilities?.vision);
  const modelContextSize = capabilities?.context_size ?? 0;
  const busyWithAttachments = attachments.some((a) => a.status === "pending");
  const toolsSupported = Boolean(capabilities?.tools);

  // -- endpoints conocidos ------------------------------------------------

  useEffect(() => {
    let cancelled = false;
    fetch("/api/chat/endpoints")
      .then((r) => r.json())
      .then((data) => {
        if (cancelled) return;
        setEndpoints(data);
        if (data.length > 0 && !data.some((e) => e.url === endpointUrl)) {
          setEndpointUrl(data[0].url);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const activeEndpointStatus = useMemo(
    () => endpoints.find((e) => e.url === endpointUrl)?.status ?? null,
    [endpoints, endpointUrl]
  );

  // -- modelos del endpoint activo ------------------------------------------

  useEffect(() => {
    if (!endpointUrl) return undefined;
    let cancelled = false;
    fetch(`${endpointUrl.replace(/\/$/, "")}/v1/models`)
      .then((r) => r.json())
      .then((data) => {
        if (cancelled) return;
        const list = (data.data || []).map((m) => m.id);
        setModels(list);
        if (list.length > 0 && !list.includes(selectedModel)) {
          setSelectedModel(list[0]);
        }
      })
      .catch(() => {
        if (!cancelled) setModels([]);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [endpointUrl]);

  // -- capacidades reales del endpoint --------------------------------------
  // Reemplaza la vieja heurística isVisionModel() sobre el nombre del modelo:
  // /props de llama-server dice si hay mmproj cargado, si el chat template
  // soporta tools y con cuánto contexto está corriendo de verdad.

  useEffect(() => {
    if (!endpointUrl) return undefined;
    let cancelled = false;
    const qs = new URLSearchParams({ endpoint: endpointUrl, model: selectedModel || "" });
    fetch(`/api/chat/capabilities?${qs.toString()}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!cancelled) setCapabilities(data);
      })
      .catch(() => {
        if (!cancelled) setCapabilities(null);
      });
    return () => {
      cancelled = true;
    };
  }, [endpointUrl, selectedModel]);

  // -- disponibilidad de las tools -------------------------------------------
  // Se consulta una vez: si SearXNG no está o no sirve JSON, el toggle queda
  // deshabilitado con el motivo en el tooltip en vez de fallar a mitad de
  // una respuesta.

  useEffect(() => {
    let cancelled = false;
    fetch("/api/chat/tools/status")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!cancelled) setToolsStatus(data);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  // -- motores de dictado ----------------------------------------------------

  useEffect(() => {
    let cancelled = false;
    fetch("/api/stt/status")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!cancelled) setSttStatus(data);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  // Si el modelo activo no sabe llamar tools, se apaga solo.
  useEffect(() => {
    if (capabilities && !capabilities.tools && toolsEnabled) {
      setToolsEnabled(false);
    }
  }, [capabilities, toolsEnabled]);

  // Si el modelo dejó de aceptar imágenes, las que ya estaban adjuntadas
  // quedan marcadas en vez de fallar recién al enviar.
  useEffect(() => {
    if (!capabilities) return;
    setAttachments((prev) =>
      prev.map((a) => {
        if (a.kind !== "image") return a;
        if (!capabilities.vision && a.status === "ready") {
          return { ...a, status: "vision_unsupported" };
        }
        return a;
      })
    );
  }, [capabilities]);

  // -- estimación de contexto ------------------------------------------------
  // El borrador se incluye en cubos de 500 caracteres: sin eso cada pausa al
  // escribir dispararía un /tokenize sobre todo el historial.

  const draftBucket = Math.floor(input.length / 500);

  useEffect(() => {
    if (!endpointUrl || streaming) return undefined;
    if (path.length === 0 && attachments.length === 0 && !input.trim()) {
      setEstimate(null);
      return undefined;
    }

    const controller = new AbortController();
    const timer = setTimeout(async () => {
      const pending = path.flatMap(toPayloadMessages);

      const draftAttachments = attachments.filter((a) => a.status === "ready");
      if (draftAttachments.length > 0 || inputRef.current.trim()) {
        pending.push({
          role: "user",
          content: toPayloadContent({ content: inputRef.current, attachments: draftAttachments }),
        });
      }

      try {
        const res = await fetch("/api/chat/estimate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          signal: controller.signal,
          body: JSON.stringify({
            endpoint: endpointUrl,
            api_key: apiKey,
            messages: pending,
            system_prompt: systemPrompt,
            max_tokens: params.max_tokens,
          }),
        });
        if (res.ok) setEstimate(await res.json());
      } catch {
        // El aviso de contexto es informativo: si falla, no molestamos.
      }
    }, ESTIMATE_DEBOUNCE_MS);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, attachments, draftBucket, systemPrompt, endpointUrl, apiKey, params.max_tokens, streaming]);

  // -- persistencia de la conversación entre rutas ---------------------------
  // sessionStorage (no localStorage): cambiar de página no pierde nada, pero
  // al cerrar el navegador se limpia.

  useEffect(() => {
    const saved = sessionStorage.getItem(SESSION_KEY);
    if (!saved) return;
    try {
      const parsed = JSON.parse(saved);
      if (Array.isArray(parsed) && parsed.length > 0) setNodes(toTree(parsed));
    } catch {
      sessionStorage.removeItem(SESSION_KEY);
    }
  }, []);

  useEffect(() => {
    if (streaming || path.length === 0) return;
    try {
      sessionStorage.setItem(SESSION_KEY, JSON.stringify(nodes));
    } catch {
      // cuota llena — la conversación sigue viva en memoria y en la DB.
    }
  }, [nodes, path.length, streaming]);

  // -- autoscroll -----------------------------------------------------------
  // Se apaga solo cuando el usuario scrollea hacia arriba durante el
  // streaming, y el botón flotante lo vuelve a enganchar.

  const { containerRef, bottomRef, showJump, scrollToBottom, handleScroll } = useAutoScroll({
    dependency: nodes,
    streaming,
  });

  // -- cleanup: abortar generación en curso al desmontar ---------------------

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    };
  }, []);

  function updateParam(patch) {
    setParams((prev) => ({ ...prev, ...patch }));
  }

  function useManualEndpoint() {
    const url = manualEndpoint.trim();
    if (!url) return;
    setEndpointUrl(url);
    setManualEndpoint("");
  }

  // -- historial: carga de la lista -----------------------------------------

  const loadConversations = useCallback(async (search = "") => {
    setHistoryLoading(true);
    try {
      const qs = new URLSearchParams({ limit: String(HISTORY_PAGE_SIZE), offset: "0" });
      if (search.trim()) qs.set("search", search.trim());
      const res = await fetch(`/api/chat/conversations?${qs.toString()}`);
      if (!res.ok) throw new Error(String(res.status));
      setConversations(await res.json());
    } catch {
      addToast("No se pudo cargar el historial.", "error");
    } finally {
      setHistoryLoading(false);
    }
  }, [addToast]);

  useEffect(() => {
    if (!historyOpen) return undefined;
    const id = setTimeout(() => loadConversations(historySearch), 300);
    return () => clearTimeout(id);
  }, [historyOpen, historySearch, loadConversations]);

  // -- historial: guardado automático ---------------------------------------

  const saveConversation = useCallback(async () => {
    const { nodes: tree, path: visible, currentConvId: convId, selectedModel: model,
      endpointUrl: url, params: p, reasoning: r, systemPrompt: sp } = stateRef.current;
    if (!visible || visible.length === 0) return null;
    // Se guarda el árbol entero: las ramas que no están activas también son
    // parte de la conversación.
    const msgs = tree;

    const config = { ...p, reasoning: r, system_prompt: sp, tools_enabled: toolsEnabledRef.current };
    try {
      if (convId) {
        const res = await fetch(`/api/chat/conversations/${convId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ messages: msgs, config, model_name: model }),
        });
        if (res.status === 404) {
          setCurrentConvId(null);
          return null;
        }
        if (!res.ok) throw new Error(String(res.status));
        return convId;
      }

      const res = await fetch("/api/chat/conversations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: msgs, config, model_name: model, endpoint: url }),
      });
      if (!res.ok) throw new Error(String(res.status));
      const data = await res.json();
      if (data?.id) setCurrentConvId(data.id);
      return data?.id ?? null;
    } catch {
      return null;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const scheduleSave = useCallback(() => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(async () => {
      saveTimerRef.current = null;
      await saveConversation();
      if (historyOpen) loadConversations(historySearch);
    }, AUTOSAVE_DEBOUNCE_MS);
  }, [saveConversation, historyOpen, historySearch, loadConversations]);

  // -- historial: acciones del panel ----------------------------------------

  const handleSelectConversation = useCallback(async (id) => {
    if (streaming) {
      addToast("Esperá a que termine la generación.", "info");
      return;
    }
    try {
      const res = await fetch(`/api/chat/conversations/${id}`);
      if (!res.ok) throw new Error(String(res.status));
      const conv = await res.json();
      // toTree migra las conversaciones guardadas como lista plana antes de
      // que existieran las ramas.
      setNodes(toTree(conv.messages));
      setCurrentConvId(conv.id);
      setLiveMetrics(null);
      setChatError(null);
      setAttachments([]);
      if (conv.model_name && models.includes(conv.model_name)) {
        setSelectedModel(conv.model_name);
      }
      if (conv.config?.system_prompt) setSystemPrompt(conv.config.system_prompt);
      setToolsEnabled(Boolean(conv.config?.tools_enabled));
      setHistoryOpen(false);
    } catch {
      addToast("No se pudo abrir la conversación.", "error");
    }
  }, [streaming, models, addToast, setCurrentConvId]);

  const handleDeleteConversation = useCallback(async (id) => {
    if (!window.confirm("¿Eliminar esta conversación del historial?")) return;
    try {
      const res = await fetch(`/api/chat/conversations/${id}`, { method: "DELETE" });
      if (!res.ok && res.status !== 404) throw new Error(String(res.status));
      setConversations((prev) => prev.filter((c) => c.id !== id));
      if (id === stateRef.current.currentConvId) setCurrentConvId(null);
      addToast("Conversación eliminada.", "success");
    } catch {
      addToast("No se pudo eliminar la conversación.", "error");
    }
  }, [addToast, setCurrentConvId]);

  const startNewConversation = useCallback(async () => {
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
      await saveConversation();
    }
    setNodes(emptyTree());
    setCurrentConvId(null);
    setLiveMetrics(null);
    setEstimate(null);
    setChatError(null);
    setAttachments([]);
    sessionStorage.removeItem(SESSION_KEY);
    if (historyOpen) loadConversations(historySearch);
  }, [saveConversation, setCurrentConvId, historyOpen, historySearch, loadConversations]);

  // -- adjuntos --------------------------------------------------------------
  // Una tanda (selección múltiple, drop o paste) = un request; el backend
  // procesa los archivos en paralelo y los chips de esa tanda resuelven
  // juntos.

  const addFiles = useCallback(async (files) => {
    if (!files || files.length === 0) return;

    const pending = files.map((file) => ({
      localId: uuid(),
      filename: file.name,
      mime: file.type || "",
      size: file.size,
      kind: file.type.startsWith("image/") ? "image" : "text",
      status: "pending",
      text: "",
      note: null,
      error: null,
      tokens_estimate: 0,
    }));

    setAttachments((prev) => [...prev, ...pending]);

    const form = new FormData();
    for (const file of files) form.append("files", file, file.name);
    form.append("vision", String(Boolean(capabilities?.vision)));

    try {
      const res = await fetch("/api/chat/attachments", { method: "POST", body: form });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `El servidor respondió ${res.status}`);
      }
      const data = await res.json();
      const processed = data.attachments || [];

      setAttachments((prev) =>
        prev.map((chip) => {
          const index = pending.findIndex((p) => p.localId === chip.localId);
          if (index === -1 || !processed[index]) return chip;
          return { ...processed[index], localId: chip.localId };
        })
      );

      const skipped = processed.filter((a) => a.status === "vision_unsupported");
      if (skipped.length > 0) {
        addToast(
          `El modelo activo no acepta imágenes: ${skipped.length} adjunto(s) no se van a enviar.`,
          "warning"
        );
      }
    } catch (err) {
      setAttachments((prev) =>
        prev.map((chip) =>
          pending.some((p) => p.localId === chip.localId)
            ? { ...chip, status: "error", error: String(err.message || err) }
            : chip
        )
      );
      addToast("No se pudieron procesar los adjuntos.", "error");
    }
  }, [capabilities, addToast]);

  // -- dictado ----------------------------------------------------------------
  // El texto cae en el textarea y queda editable: nunca se envía solo.

  const handleTranscript = useCallback((text) => {
    if (!text) return;
    setInput((prev) => (prev ? `${prev.replace(/\s+$/, "")} ${text}` : text));
    setInterimTranscript("");
  }, []);

  const handleDictationError = useCallback((message) => {
    setInterimTranscript("");
    addToast(message, "error");
  }, [addToast]);

  const removeAttachment = useCallback((localId) => {
    setAttachments((prev) => prev.filter((a) => a.localId !== localId));
  }, []);

  // -- drag & drop sobre toda el área de conversación -------------------------
  // El contador de profundidad evita el parpadeo que produce dragleave al
  // pasar por encima de cada hijo.

  function handleDragEnter(event) {
    if (!Array.from(event.dataTransfer?.types || []).includes("Files")) return;
    dragDepthRef.current += 1;
    setDragging(true);
  }

  function handleDragLeave() {
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setDragging(false);
  }

  function handleDragOver(event) {
    if (Array.from(event.dataTransfer?.types || []).includes("Files")) {
      event.preventDefault();
    }
  }

  function handleDrop(event) {
    event.preventDefault();
    dragDepthRef.current = 0;
    setDragging(false);
    const files = Array.from(event.dataTransfer?.files || []);
    if (files.length > 0) addFiles(files);
  }

  // -- envío -----------------------------------------------------------------

  /**
   * Corre una generación contra el endpoint y vuelca el stream en el nodo
   * `assistantId`.
   *
   * Lo usan los cuatro caminos del Bloque 4 — enviar, editar y reenviar,
   * regenerar y continuar — porque salvo qué mensajes se mandan y en qué
   * nodo se escribe, todo lo demás (métricas, tools, razonamiento, aborto)
   * es idéntico.
   */
  const runGeneration = useCallback(
    async ({ payloadMessages, assistantId, continuation = false, reasoningOverride = null }) => {
      const effective = reasoningOverride || reasoning;
      const withSystem = systemPrompt.trim()
        ? [{ role: "system", content: systemPrompt.trim() }, ...payloadMessages]
        : payloadMessages;

      setChatError(null);
      setStreaming(true);
      setLiveMetrics(null);
      setLiveToolActivity([]);
      metricsRef.current = null;
      metricsFlushRef.current = 0;

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const res = await fetch("/api/chat/completions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          signal: controller.signal,
          body: JSON.stringify({
            endpoint: endpointUrl,
            api_key: apiKey,
            messages: withSystem,
            model: selectedModel,
            ...params,
            stream: true,
            thinking_enabled: effective.mode !== "off",
            budget_tokens: effective.budget,
            preserve_thinking: effective.mode === "preserve",
            reasoning_effort: effective.effort,
            tools_enabled: toolsEnabled,
            continuation,
          }),
        });

        if (!res.ok || !res.body) throw new Error(`El servidor respondió ${res.status}`);

        await consumeSSE(res, controller.signal, (event) => {
          if (event.type === "token") {
            setNodes((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, content: (m.content || "") + event.content } : m
              )
            );
          } else if (event.type === "thinking_token") {
            setNodes((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, thinking: (m.thinking || "") + event.content } : m
              )
            );
          } else if (event.type === "metrics") {
            metricsRef.current = event;
            const t = performance.now();
            if (t - metricsFlushRef.current >= METRICS_FLUSH_MS) {
              metricsFlushRef.current = t;
              setLiveMetrics(event);
            }
          } else if (event.type === "tool_call") {
            setLiveToolActivity((prev) => [
              ...prev,
              {
                id: event.id,
                name: event.name,
                arguments: event.arguments,
                status: "running",
                ok: null,
                summary: "",
                sources: [],
              },
            ]);
          } else if (event.type === "tool_result") {
            setLiveToolActivity((prev) =>
              prev.map((entry) =>
                entry.id === event.id
                  ? {
                      ...entry,
                      status: "done",
                      ok: event.ok,
                      summary: event.summary,
                      sources: event.sources,
                    }
                  : entry
              )
            );
          } else if (event.type === "done") {
            metricsRef.current = event;
            setLiveMetrics(event);
            setNodes((prev) =>
              prev.map((m) => {
                if (m.id !== assistantId) return m;
                // Al continuar se acumulan los intercambios de tools de las
                // dos pasadas en vez de pisar los de la primera.
                const priorExchange = continuation ? m.tool_exchange || [] : [];
                const priorActivity = continuation ? m.tool_activity || [] : [];
                const exchange = [...priorExchange, ...(event.tool_messages || [])];
                const activity = [...priorActivity, ...(event.tool_activity || [])];
                return {
                  ...m,
                  finish_reason: event.finish_reason,
                  tool_exchange: exchange.length ? exchange : undefined,
                  tool_activity: activity.length ? activity : undefined,
                  metrics: {
                    tps: event.tps,
                    ttft_ms: event.ttft_ms,
                    tokens_total: event.total_tokens,
                    tokens_thinking: event.tokens_thinking,
                  },
                };
              })
            );
          } else if (event.type === "error") {
            setChatError(event.message);
          }
        });
      } catch (err) {
        if (err.name !== "AbortError") {
          setChatError("Error de conexión con el endpoint de chat.");
          addToast("Error de conexión con el endpoint de chat.", "error");
        }
      } finally {
        if (metricsRef.current) setLiveMetrics(metricsRef.current);
        setLiveToolActivity([]);
        setStreaming(false);
        abortRef.current = null;
        scheduleSave();
      }
    },
    [reasoning, systemPrompt, endpointUrl, apiKey, selectedModel, params, toolsEnabled,
     addToast, scheduleSave]
  );

  /** Nodo vacío del asistente donde va a caer el stream. */
  function assistantPlaceholder() {
    return {
      role: "assistant",
      content: "",
      thinking: "",
      timestamp: nowIso(),
      metrics: null,
    };
  }

  const handleSend = useCallback(async () => {
    const text = input.trim();
    const ready = attachments.filter((a) => a.status === "ready");
    if ((!text && ready.length === 0) || streaming || busyWithAttachments) return;

    if (estimate && !estimate.fits) {
      const ok = window.confirm(
        "Este envío supera el contexto del modelo y es probable que se trunque el historial. ¿Enviar igual?"
      );
      if (!ok) return;
    }

    const userMessage = {
      role: "user",
      content: text,
      // Se guardan todos, incluso los descartados: el historial muestra lo
      // que el usuario adjuntó, y toPayloadContent filtra por status.
      attachments: attachments.map(({ localId, ...rest }) => rest),
      thinking: null,
      timestamp: nowIso(),
      metrics: null,
    };

    const parentId = path.length > 0 ? path[path.length - 1].id : ROOT_ID;
    const { nodes: next, ids } = appendChain(nodes, parentId, [
      userMessage,
      assistantPlaceholder(),
    ]);

    setNodes(next);
    setInput("");
    setAttachments([]);

    const payloadMessages = visiblePath(next)
      .filter((m) => m.id !== ids[1])
      .flatMap(toPayloadMessages);

    await runGeneration({ payloadMessages, assistantId: ids[1] });
  }, [input, attachments, busyWithAttachments, estimate, streaming, nodes, path, runGeneration]);

  /**
   * Editar un mensaje del usuario y reenviarlo.
   *
   * No pisa el original: crea un hermano bajo el mismo padre, así que la
   * versión anterior y todo lo que colgaba de ella siguen accesibles con los
   * controles "< 1/2 >".
   */
  const handleEdit = useCallback(
    async (message, text) => {
      if (streaming) return;

      const { nodes: next, ids } = appendChain(nodes, message.parent_id, [
        {
          role: "user",
          content: text,
          attachments: message.attachments || [],
          thinking: null,
          timestamp: nowIso(),
          metrics: null,
        },
        assistantPlaceholder(),
      ]);

      setNodes(next);

      const payloadMessages = visiblePath(next)
        .filter((m) => m.id !== ids[1])
        .flatMap(toPayloadMessages);

      await runGeneration({ payloadMessages, assistantId: ids[1] });
    },
    [streaming, nodes, runGeneration]
  );

  /** Regenerar: hermano nuevo del asistente bajo el mismo mensaje del usuario. */
  const handleRegenerate = useCallback(
    async (message, reasoningOverride) => {
      if (streaming) return;

      const { nodes: next, ids } = appendChain(nodes, message.parent_id, [
        assistantPlaceholder(),
      ]);

      setNodes(next);

      const payloadMessages = visiblePath(next)
        .filter((m) => m.id !== ids[0])
        .flatMap(toPayloadMessages);

      await runGeneration({ payloadMessages, assistantId: ids[0], reasoningOverride });
    },
    [streaming, nodes, runGeneration]
  );

  /**
   * Continuar una respuesta cortada por max_tokens.
   *
   * No crea rama: sigue escribiendo en el mismo nodo. El mensaje parcial va
   * al final del payload y el backend intenta que el modelo continúe ese
   * turno; si el chat template no lo soporta, reintenta solo pidiéndoselo
   * de forma explícita.
   */
  const handleContinue = useCallback(
    async (message) => {
      if (streaming) return;

      const payloadMessages = visiblePath(nodes).flatMap(toPayloadMessages);
      setNodes((prev) => patchNode(prev, message.id, { finish_reason: null }));
      await runGeneration({
        payloadMessages,
        assistantId: message.id,
        continuation: true,
      });
    },
    [streaming, nodes, runGeneration]
  );

  const handleSwitchBranch = useCallback((message, direction) => {
    setNodes((prev) => switchBranch(prev, message, direction));
  }, []);

  function handleStop() {
    abortRef.current?.abort();
  }

  const handleClear = useCallback(async () => {
    const { path: visible, currentConvId: convId } = stateRef.current;
    if (visible.length > 0 && !convId) {
      if (window.confirm("¿Guardar esta conversación en el historial antes de limpiar?")) {
        if (saveTimerRef.current) {
          clearTimeout(saveTimerRef.current);
          saveTimerRef.current = null;
        }
        await saveConversation();
      }
    }
    setNodes(emptyTree());
    setCurrentConvId(null);
    setLiveMetrics(null);
    setEstimate(null);
    setChatError(null);
    setAttachments([]);
    sessionStorage.removeItem(SESSION_KEY);
    if (historyOpen) loadConversations(historySearch);
  }, [saveConversation, setCurrentConvId, historyOpen, historySearch, loadConversations]);

  // -- shortcuts de teclado ---------------------------------------------------
  // Ctrl+Enter: enviar | Escape: cancelar generación | Ctrl+L: limpiar (confirm)
  // Ctrl+E: toggle panel de export | Ctrl+H: toggle historial.

  useEffect(() => {
    function onKeyDown(e) {
      const ctrlOrCmd = e.ctrlKey || e.metaKey;

      if (ctrlOrCmd && e.key === "Enter") {
        e.preventDefault();
        handleSend();
      } else if (e.key === "Escape" && streaming) {
        e.preventDefault();
        handleStop();
      } else if (ctrlOrCmd && (e.key === "l" || e.key === "L")) {
        e.preventDefault();
        if (window.confirm("¿Limpiar la conversación actual?")) {
          handleClear();
        }
      } else if (ctrlOrCmd && (e.key === "e" || e.key === "E")) {
        e.preventDefault();
        setExportPanelOpen((prev) => !prev);
      } else if (ctrlOrCmd && (e.key === "h" || e.key === "H")) {
        e.preventDefault();
        setHistoryOpen((prev) => !prev);
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [handleSend, handleClear, streaming]);

  function handleImportFile(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const parsed = JSON.parse(reader.result);
        if (Array.isArray(parsed.messages)) {
          setNodes(toTree(parsed.messages));
          setCurrentConvId(null);
        }
      } catch {
        setChatError("El archivo importado no es un JSON de conversación válido.");
      }
    };
    reader.readAsText(file);
    event.target.value = "";
  }

  const pingColor =
    activeEndpointStatus === "ok" ? "bg-emerald-400" : activeEndpointStatus === "error" ? "bg-red-400" : "bg-glyvex-muted/50";

  return (
    <div className="flex gap-6 h-[calc(100vh-8rem)]">
      {/* ---------------- Panel izquierdo ---------------- */}
      {!compact && (
      <aside className="w-[300px] shrink-0 overflow-y-auto space-y-3 pr-2">
        <div className="bg-glyvex-card rounded-lg border border-white/10 p-4 space-y-3">
          <div>
            <span className="block text-sm text-glyvex-muted mb-1">Endpoint</span>
            <div className="flex items-center gap-2">
              <span className={`w-2 h-2 rounded-full shrink-0 ${pingColor}`} />
              <select className={inputClasses} value={endpointUrl} onChange={(e) => setEndpointUrl(e.target.value)}>
                {endpoints.map((e) => (
                  <option key={e.url} value={e.url}>{e.name}</option>
                ))}
                {!endpoints.some((e) => e.url === endpointUrl) && (
                  <option value={endpointUrl}>{endpointUrl}</option>
                )}
              </select>
            </div>
            <div className="flex gap-2 mt-2">
              <input
                className={inputClasses}
                value={manualEndpoint}
                onChange={(e) => setManualEndpoint(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && useManualEndpoint()}
                placeholder="http://host:puerto"
              />
              <button
                type="button"
                onClick={useManualEndpoint}
                className="px-2 py-1.5 rounded-md text-xs border border-white/10 text-glyvex-muted hover:text-glyvex-text hover:bg-black/30 shrink-0"
              >
                Usar
              </button>
            </div>
          </div>

          <label className="block">
            <span className="block text-sm text-glyvex-muted mb-1">Modelo</span>
            <select className={inputClasses} value={selectedModel} onChange={(e) => setSelectedModel(e.target.value)}>
              {models.length === 0 && <option value="">(sin detectar)</option>}
              {models.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
            {capabilities && (
              <span className="block text-xs text-glyvex-muted mt-1 tabular-nums">
                {modelContextSize > 0
                  ? `Contexto: ${modelContextSize.toLocaleString()} tokens`
                  : "Contexto: sin detectar"}
                {capabilities.vision ? " · visión" : ""}
                {capabilities.tools ? " · tools" : ""}
                {capabilities.source === "heuristic" && (
                  <span title="El endpoint no expone /props: esto se dedujo del nombre del modelo.">
                    {" "}· estimado
                  </span>
                )}
              </span>
            )}
          </label>

          <label className="block">
            <span className="block text-sm text-glyvex-muted mb-1">API key (opcional)</span>
            <input className={inputClasses} value={apiKey} onChange={(e) => setApiKey(e.target.value)} type="password" />
          </label>
        </div>

        <CollapsibleSection title="System prompt" open={systemPromptOpen} onToggle={() => setSystemPromptOpen((v) => !v)}>
          <textarea
            className={inputClasses + " min-h-[80px] resize-y"}
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            placeholder="Instrucciones de sistema…"
          />
        </CollapsibleSection>

        <CollapsibleSection title="Parámetros" open={paramsOpen} onToggle={() => setParamsOpen((v) => !v)}>
          <Slider label="Temperature" value={params.temperature} min={0} max={2} step={0.05} onChange={(v) => updateParam({ temperature: v })} formatValue={(v) => v.toFixed(2)} />
          <Slider label="Top P" value={params.top_p} min={0} max={1} step={0.01} onChange={(v) => updateParam({ top_p: v })} formatValue={(v) => v.toFixed(2)} />
          <Slider label="Top K" value={params.top_k} min={0} max={100} step={1} onChange={(v) => updateParam({ top_k: v })} />
          <Slider label="Min P" value={params.min_p} min={0} max={1} step={0.01} onChange={(v) => updateParam({ min_p: v })} formatValue={(v) => v.toFixed(2)} />
          <Slider label="Repeat penalty" value={params.repeat_penalty} min={1} max={1.5} step={0.01} onChange={(v) => updateParam({ repeat_penalty: v })} formatValue={(v) => v.toFixed(2)} />
          <label className="block">
            <span className="block text-xs text-glyvex-muted mb-1">Max tokens</span>
            <input type="number" className={inputClasses} value={params.max_tokens} onChange={(e) => updateParam({ max_tokens: Number(e.target.value) })} />
          </label>
          <label className="block">
            <span className="block text-xs text-glyvex-muted mb-1">Seed (-1 = aleatorio)</span>
            <input type="number" className={inputClasses} value={params.seed} onChange={(e) => updateParam({ seed: Number(e.target.value) })} />
          </label>
        </CollapsibleSection>

        <div className="border-t border-white/10 pt-3 space-y-2">
          <button
            type="button"
            onClick={handleClear}
            className="w-full flex items-center gap-2 px-3 py-2 rounded-md text-sm border border-white/10 text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-card"
          >
            <Trash2 size={14} />
            Limpiar conversación
          </button>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => exportAsJson(path, selectedModel)}
              className="flex-1 flex items-center justify-center gap-1 px-2 py-2 rounded-md text-xs border border-white/10 text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-card"
            >
              <Download size={13} /> JSON
            </button>
            <button
              type="button"
              onClick={() => exportAsMarkdown(path, selectedModel)}
              className="flex-1 flex items-center justify-center gap-1 px-2 py-2 rounded-md text-xs border border-white/10 text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-card"
            >
              <Download size={13} /> Markdown
            </button>
          </div>
          <button
            type="button"
            onClick={() => importInputRef.current?.click()}
            className="w-full flex items-center gap-2 px-3 py-2 rounded-md text-sm border border-white/10 text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-card"
          >
            <Upload size={14} />
            Importar
          </button>
          <input ref={importInputRef} type="file" accept="application/json" className="hidden" onChange={handleImportFile} />
        </div>
      </aside>
      )}

      {/* ---------------- Área de chat ---------------- */}
      <section
        className="relative flex-1 flex flex-col min-w-0 rounded-lg border border-white/10 overflow-hidden"
        onDragEnter={handleDragEnter}
        onDragLeave={handleDragLeave}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
      >
        {dragging && (
          <div className="absolute inset-0 z-40 flex flex-col items-center justify-center gap-2 bg-glyvex-accent/10 border-2 border-dashed border-glyvex-accent/60 pointer-events-none">
            <FilePlus2 size={28} className="text-glyvex-accent" />
            <span className="text-sm text-glyvex-text">Soltá los archivos para adjuntarlos</span>
          </div>
        )}

        <div className="flex items-center justify-between border-b border-white/10">
          <MetricsBar
            metrics={liveMetrics}
            estimate={estimate}
            contextTotal={modelContextSize}
            streaming={streaming}
          />
          <div className="flex items-center shrink-0">
            <button
              type="button"
              onClick={() => setHistoryOpen((v) => !v)}
              className={
                "flex items-center gap-1.5 px-3 text-xs " +
                (historyOpen ? "text-glyvex-accent" : "text-glyvex-muted hover:text-glyvex-text")
              }
              title="Historial de conversaciones (Ctrl+H)"
            >
              <History size={15} />
            </button>
            <button
              type="button"
              onClick={() => setCompact((v) => !v)}
              className="flex items-center gap-1.5 px-3 text-xs text-glyvex-muted hover:text-glyvex-text"
              title={compact ? "Mostrar panel de configuración" : "Modo compacto"}
            >
              {compact ? <PanelLeftOpen size={15} /> : <PanelLeftClose size={15} />}
            </button>
          </div>
        </div>

        {exportPanelOpen && (
          <div className="flex items-center gap-2 px-4 py-2 border-b border-white/10 bg-black/20">
            <span className="text-xs text-glyvex-muted mr-1">Exportar (Ctrl+E):</span>
            <button
              type="button"
              onClick={() => exportAsJson(path, selectedModel)}
              className="flex items-center gap-1 px-2 py-1 rounded-md text-xs border border-white/10 text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-card"
            >
              <Download size={12} /> JSON
            </button>
            <button
              type="button"
              onClick={() => exportAsMarkdown(path, selectedModel)}
              className="flex items-center gap-1 px-2 py-1 rounded-md text-xs border border-white/10 text-glyvex-muted hover:text-glyvex-text hover:bg-glyvex-card"
            >
              <Download size={12} /> Markdown
            </button>
          </div>
        )}

        {historyOpen && (
          <HistoryPanel
            conversations={conversations}
            loading={historyLoading}
            search={historySearch}
            currentId={currentConvId}
            onSearch={setHistorySearch}
            onSelect={handleSelectConversation}
            onDelete={handleDeleteConversation}
            onNew={startNewConversation}
            onClose={() => setHistoryOpen(false)}
          />
        )}

        <div
          ref={containerRef}
          onScroll={handleScroll}
          className="flex-1 overflow-y-auto p-4 space-y-4"
        >
          {path.length === 0 ? (
            <p className="text-sm text-glyvex-muted text-center mt-10">
              Escribí un mensaje para empezar la conversación.
            </p>
          ) : (
            path.map((m, index) => (
              <MessageBubble
                key={m.id}
                message={m}
                branch={siblingInfo(nodes, m)}
                streaming={streaming && m.role === "assistant" && index === path.length - 1}
                busy={streaming}
                reasoning={reasoning}
                onSwitchBranch={handleSwitchBranch}
                onEdit={handleEdit}
                onRegenerate={handleRegenerate}
                onContinue={handleContinue}
              />
            ))
          )}
          {streaming && liveToolActivity.length > 0 && (
            <ToolActivityLive entries={liveToolActivity} />
          )}
          <div ref={bottomRef} />
        </div>

        {showJump && (
          <button
            type="button"
            onClick={() => scrollToBottom()}
            title="Ir al final"
            className="absolute bottom-28 left-1/2 -translate-x-1/2 z-10 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-white/10 bg-glyvex-card text-xs text-glyvex-text shadow-lg shadow-black/40 hover:bg-black/40"
          >
            <ArrowDown size={13} />
            {streaming ? "Seguir la respuesta" : "Ir al final"}
          </button>
        )}

        {chatError && <p className="px-4 pb-2 text-sm text-red-400">{chatError}</p>}

        <ChatComposer
          input={input}
          onInputChange={setInput}
          attachments={attachments}
          onAddFiles={addFiles}
          onRemoveAttachment={removeAttachment}
          reasoning={reasoning}
          onReasoningChange={setReasoning}
          streaming={streaming}
          onSend={handleSend}
          onStop={handleStop}
          estimate={estimate}
          visionReady={visionReady}
          capabilitiesSource={capabilities?.source}
          busyWithAttachments={busyWithAttachments}
          toolsEnabled={toolsEnabled}
          onToolsToggle={setToolsEnabled}
          toolsSupported={toolsSupported}
          toolsStatus={toolsStatus}
          sttStatus={sttStatus}
          onTranscript={handleTranscript}
          onInterimTranscript={setInterimTranscript}
          interimTranscript={interimTranscript}
          onDictationError={handleDictationError}
        />
      </section>
    </div>
  );
}
