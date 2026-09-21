import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  ArrowDown, Download, History, PanelLeftClose, PanelLeftOpen,
} from "lucide-react";
import MetricsBar from "../components/MetricsBar.jsx";
import MessageBubble from "../components/MessageBubble.jsx";
import ChatComposer from "../components/ChatComposer.jsx";
import ChatSidebar from "../components/ChatSidebar.jsx";
import HistoryPanel from "../components/HistoryPanel.jsx";
import { streamPhase } from "../components/StreamStatus.jsx";
import { DEFAULT_REASONING } from "../components/ReasoningControl.jsx";
import { useToast } from "../components/ToastNotification.jsx";
import { useLocalStorage } from "../hooks/useLocalStorage.js";
import { useAutoScroll } from "../hooks/useAutoScroll.js";
import { useChatStream } from "../hooks/useChatStream.js";
import { useConversations } from "../hooks/useConversations.js";
import { toPayloadMessages } from "../lib/payload.js";
import { exportAsJson, exportAsMarkdown } from "../lib/export.js";
import {
  ROOT_ID, appendChain, emptyTree, patchNode, siblingInfo, switchBranch, toTree, visiblePath,
} from "../lib/conversationTree.js";

const SESSION_KEY = "glyvex_chat_messages";

// Texto que se suma al system prompt cuando el toggle "convención de
// archivos" está activo (ver appendFileConvention). Vive en los locales
// (chat.fileConventionPrompt), no en el textarea del usuario, para no
// pisarle lo que ya haya escrito: se concatena, nunca reemplaza.

const DEFAULT_PARAMS = {
  temperature: 0.7,
  top_p: 0.9,
  top_k: 40,
  min_p: 0.0,
  repeat_penalty: 1.1,
  max_tokens: 4096,
  seed: -1,
};

/** La estimación de contexto pega contra /tokenize del upstream, así que se
 *  espera a que el usuario pare de escribir antes de pedirla. */
const ESTIMATE_DEBOUNCE_MS = 700;

function uuid() {
  return crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function nowIso() {
  return new Date().toISOString();
}

/** Nodo vacío del asistente donde va a caer el stream. */
function assistantPlaceholder() {
  return { role: "assistant", content: "", thinking: "", timestamp: nowIso(), metrics: null };
}

export default function Chat() {
  const { t } = useTranslation();
  const { addToast } = useToast();

  // -- configuración del endpoint -------------------------------------------

  const [endpoints, setEndpoints] = useState([]);
  const [endpointUrl, setEndpointUrl] = useLocalStorage(
    "glyvex_chat_endpoint",
    "http://127.0.0.1:8080"
  );
  const [models, setModels] = useState([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [capabilities, setCapabilities] = useState(null);
  const [apiKey, setApiKey] = useState("");

  const [systemPrompt, setSystemPrompt] = useState("");
  // Se suma al system prompt en buildRequest, no lo reemplaza: si el
  // usuario ya escribió algo en el cuadro, la convención se concatena al
  // final. Si el cuadro está vacío, se manda sola.
  const [appendFileConvention, setAppendFileConvention] = useLocalStorage(
    "glyvex_chat_file_convention",
    false
  );
  const [reasoning, setReasoning] = useLocalStorage("glyvex_chat_reasoning", DEFAULT_REASONING);
  const [params, setParams] = useLocalStorage("glyvex_chat_params", DEFAULT_PARAMS);

  const [compact, setCompact] = useLocalStorage("glyvex_compact_chat", false);
  const [exportPanelOpen, setExportPanelOpen] = useState(false);

  // -- conversación ----------------------------------------------------------
  // Es un árbol (ver lib/conversationTree.js): editar o regenerar crean ramas
  // hermanas en vez de pisar lo anterior. `nodes` es lo que se persiste,
  // `path` la rama visible.

  const [nodes, setNodes] = useState(emptyTree);
  const path = useMemo(() => visiblePath(nodes), [nodes]);

  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState([]);
  const [dragging, setDragging] = useState(false);
  const [estimate, setEstimate] = useState(null);
  const [currentConvId, setCurrentConvId] = useLocalStorage("glyvex_current_conv_id", null);

  // -- tools y dictado -------------------------------------------------------

  const [toolsEnabled, setToolsEnabled] = useState(false);
  const [toolsStatus, setToolsStatus] = useState(null);
  const [sttStatus, setSttStatus] = useState(null);
  const [interimTranscript, setInterimTranscript] = useState("");

  const dragDepthRef = useRef(0);
  const inputRef = useRef("");
  inputRef.current = input;

  // Snapshot para los callbacks diferidos (el autosave con debounce), que de
  // otro modo verían valores viejos por el closure.
  const stateRef = useRef({});
  stateRef.current = {
    nodes, path, currentConvId, selectedModel, endpointUrl,
    params, reasoning, systemPrompt, toolsEnabled,
  };

  const history = useConversations({
    stateRef,
    setCurrentConvId,
    onError: (message) => addToast(message, "error"),
  });

  const stream = useChatStream({
    setNodes,
    onFinish: history.scheduleSave,
    onError: (message) => addToast(message, "error"),
  });

  const { streaming } = stream;
  const visionReady = Boolean(capabilities?.vision);
  const toolsSupported = Boolean(capabilities?.tools);
  const modelContextSize = capabilities?.context_size ?? 0;
  const busyWithAttachments = attachments.some((a) => a.status === "pending");

  const { containerRef, bottomRef, showJump, scrollToBottom, handleScroll } = useAutoScroll({
    dependency: nodes,
    streaming,
  });

  // -- endpoints conocidos ---------------------------------------------------

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

  // -- modelos del endpoint activo -------------------------------------------

  useEffect(() => {
    if (!endpointUrl) return undefined;
    let cancelled = false;
    fetch(`${endpointUrl.replace(/\/$/, "")}/v1/models`)
      .then((r) => r.json())
      .then((data) => {
        if (cancelled) return;
        const list = (data.data || []).map((m) => m.id);
        setModels(list);
        if (list.length > 0 && !list.includes(selectedModel)) setSelectedModel(list[0]);
      })
      .catch(() => {
        if (!cancelled) setModels([]);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [endpointUrl]);

  // -- capacidades reales del endpoint ---------------------------------------
  // Reemplaza la heurística sobre el nombre del modelo: /props de llama-server
  // dice si hay mmproj cargado, si el chat template soporta tools y con cuánto
  // contexto está corriendo de verdad.

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

  // Estado de las imágenes ya adjuntadas según lo que soporte el modelo
  // activo. Es BIDIRECCIONAL a propósito: antes solo degradaba
  // ready -> vision_unsupported y no existía el camino de vuelta, así que una
  // imagen adjuntada mientras /capabilities todavía cargaba quedaba marcada
  // para siempre y handleSend la filtraba en silencio. El backend ahora
  // guarda los bytes pase lo que pase, así que el status se puede recalcular.
  useEffect(() => {
    if (!capabilities) return;
    const vision = Boolean(capabilities.vision);
    setAttachments((prev) =>
      prev.map((a) => {
        if (a.kind !== "image") return a;
        if (!vision && a.status === "ready") {
          return { ...a, status: "vision_unsupported" };
        }
        // Vuelta atrás: la imagen sigue en disco y el modelo sí acepta
        // imágenes, así que se vuelve a habilitar para el envío.
        if (vision && a.status === "vision_unsupported") {
          return { ...a, status: "ready" };
        }
        return a;
      })
    );
  }, [capabilities]);

  // -- disponibilidad de tools y dictado -------------------------------------

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      fetch("/api/chat/tools/status").then((r) => (r.ok ? r.json() : null)).catch(() => null),
      fetch("/api/stt/status").then((r) => (r.ok ? r.json() : null)).catch(() => null),
    ]).then(([tools, stt]) => {
      if (cancelled) return;
      setToolsStatus(tools);
      setSttStatus(stt);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Si el modelo activo no sabe llamar tools, se apaga solo.
  useEffect(() => {
    if (capabilities && !capabilities.tools && toolsEnabled) setToolsEnabled(false);
  }, [capabilities, toolsEnabled]);

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
      // Si la rama visible termina en una respuesta con contexto medido por
      // el servidor, esa medición reemplaza al historial y solo se estima lo
      // nuevo. Es más exacta: incluye plantilla, tools y razonamiento, que
      // re-tokenizando el texto visible quedaban afuera.
      const last = path[path.length - 1];
      const baseTokens =
        last?.role === "assistant" && last.metrics?.context_tokens > 0
          ? last.metrics.context_tokens
          : 0;
      const pending = baseTokens ? [] : path.flatMap(toPayloadMessages);
      const ready = attachments.filter((a) => a.status === "ready");
      if (ready.length > 0 || inputRef.current.trim()) {
        pending.push(
          ...toPayloadMessages({ role: "user", content: inputRef.current, attachments: ready })
        );
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
            base_tokens: baseTokens,
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
  }, [path, attachments, draftBucket, systemPrompt, appendFileConvention, endpointUrl, apiKey, params.max_tokens, streaming]);

  // -- persistencia entre rutas ----------------------------------------------
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

  useEffect(() => {
    return () => stream.stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // -- historial --------------------------------------------------------------

  const resetConversation = useCallback(() => {
    setNodes(emptyTree());
    setCurrentConvId(null);
    setEstimate(null);
    setAttachments([]);
    stream.setError(null);
    sessionStorage.removeItem(SESSION_KEY);
  }, [setCurrentConvId, stream]);

  const handleSelectConversation = useCallback(
    async (id) => {
      if (streaming) {
        addToast(t("chat.waitGeneration"), "info");
        return;
      }
      try {
        const conv = await history.fetchOne(id);
        // toTree migra las conversaciones guardadas como lista plana antes de
        // que existieran las ramas.
        setNodes(toTree(conv.messages));
        setCurrentConvId(conv.id);
        setAttachments([]);
        stream.setError(null);
        if (conv.model_name && models.includes(conv.model_name)) {
          setSelectedModel(conv.model_name);
        }
        if (conv.config?.system_prompt) setSystemPrompt(conv.config.system_prompt);
        setToolsEnabled(Boolean(conv.config?.tools_enabled));
        history.setOpen(false);
      } catch {
        addToast(t("chat.openFailed"), "error");
      }
    },
    [streaming, models, addToast, setCurrentConvId, history, stream, t]
  );

  const handleDeleteConversation = useCallback(
    async (id) => {
      if (!window.confirm(t("chat.deleteConfirm"))) return;
      const ok = await history.remove(id);
      if (ok) addToast(t("chat.deleted"), "success");
    },
    [history, addToast, t]
  );

  const startNewConversation = useCallback(async () => {
    await history.flushSave();
    resetConversation();
    if (history.open) history.load(history.search);
  }, [history, resetConversation]);

  const handleClear = useCallback(async () => {
    const { path: visible, currentConvId: convId } = stateRef.current;
    if (visible.length > 0 && !convId) {
      if (window.confirm(t("chat.saveBeforeClearConfirm"))) {
        await history.flushSave();
        await history.save();
      }
    }
    resetConversation();
    if (history.open) history.load(history.search);
  }, [history, resetConversation, t]);

  // -- adjuntos ---------------------------------------------------------------
  // Una tanda (selección múltiple, drop o paste) = un request por archivo,
  // disparados en paralelo: cada chip resuelve por su cuenta.
  //
  // El tope "archivos por mensaje" se aplica acá en el cliente: como cada
  // archivo va en su propio request, el chequeo por request del backend no
  // lo vería. El valor sale del config del servidor (attachments).

  const [maxFilesPerMessage, setMaxFilesPerMessage] = useState(10);
  useEffect(() => {
    fetch("/api/config")
      .then((r) => (r.ok ? r.json() : null))
      .then((c) => {
        const n = c?.attachments?.max_files_per_message;
        if (Number.isInteger(n) && n > 0) setMaxFilesPerMessage(n);
      })
      .catch(() => {});
  }, []);

  /**
   * Libera el object URL de un chip. Los blob: URL viven hasta que se los
   * revoca explícitamente, así que sin esto cada imagen adjuntada queda
   * retenida en memoria hasta recargar la página.
   */
  const revokePreview = useCallback((chip) => {
    if (chip?.previewUrl) {
      try { URL.revokeObjectURL(chip.previewUrl); } catch { /* ya revocado */ }
    }
  }, []);

  /** Sube un archivo y devuelve el descriptor ya procesado por el backend. */
  const uploadOne = useCallback(
    async (file, localId, vision) => {
      const form = new FormData();
      form.append("files", file, file.name);
      form.append("vision", String(vision));

      try {
        const res = await fetch("/api/chat/attachments", { method: "POST", body: form });
        if (!res.ok) {
          const detail = await res.json().catch(() => ({}));
          throw new Error(detail.detail || t("chat.serverResponded", { status: res.status }));
        }
        const processed = (await res.json()).attachments || [];
        const result = processed[0];
        if (!result) throw new Error(t("chat.attachmentMissing"));

        // previewUrl se conserva: el backend devuelve su propia `url`, pero
        // el blob local ya está decodificado y evita un salto visual.
        setAttachments((prev) =>
          prev.map((chip) =>
            chip.localId === localId
              ? { ...result, localId, previewUrl: chip.previewUrl || null }
              : chip
          )
        );
        return result;
      } catch (err) {
        setAttachments((prev) =>
          prev.map((chip) =>
            chip.localId === localId
              ? { ...chip, status: "error", error: String(err.message || err) }
              : chip
          )
        );
        return null;
      }
    },
    [t]
  );

  const addFiles = useCallback(
    async (files) => {
      if (!files || files.length === 0) return;

      let batch = files;
      if (files.length > maxFilesPerMessage) {
        batch = files.slice(0, maxFilesPerMessage);
        addToast(
          t("chat.maxFilesExceeded", { max: maxFilesPerMessage, skipped: files.length - batch.length }),
          "warning"
        );
      }

      // Si todavía no llegó /capabilities, se asume que SÍ hay visión. El
      // backend guarda la imagen igual y el efecto de arriba corrige el
      // status en cuanto se sepa: es preferible a descartarla de entrada.
      const vision = capabilities ? Boolean(capabilities.vision) : true;
      const pending = batch.map((file) => {
        const isImage = (file.type || "").startsWith("image/");
        return {
          localId: uuid(),
          filename: file.name,
          mime: file.type || "",
          size: file.size,
          kind: isImage ? "image" : "text",
          status: "pending",
          text: "",
          note: null,
          error: null,
          tokens_estimate: 0,
          // Miniatura inmediata mientras sube, sin esperar al backend ni
          // meter base64 en el estado. Se revoca al reemplazar el chip o al
          // quitarlo (ver revokePreview) para no filtrar memoria.
          previewUrl: isImage ? URL.createObjectURL(file) : null,
        };
      });

      setAttachments((prev) => [...prev, ...pending]);

      // Un request por archivo, disparados juntos: cada chip resuelve por su
      // cuenta. Un .txt no tiene por qué mostrar el spinner cuatro segundos
      // porque en la misma tanda vino un PDF escaneado de 200 páginas.
      const results = await Promise.all(
        batch.map((file, index) => uploadOne(file, pending[index].localId, vision))
      );

      const done = results.filter(Boolean);
      const failed = results.length - done.length;
      const skipped = done.filter((a) => a.status === "vision_unsupported").length;

      if (skipped > 0) {
        addToast(t("chat.visionUnsupported", { count: skipped }), "warning");
      }
      if (failed > 0) {
        addToast(failed === 1 ? t("chat.oneAttachmentFailed") : t("chat.attachmentsFailed", { count: failed }), "error");
      }
    },
    [capabilities, uploadOne, addToast, maxFilesPerMessage, t]
  );

  const removeAttachment = useCallback((localId) => {
    setAttachments((prev) => {
      const going = prev.find((a) => a.localId === localId);
      if (going) revokePreview(going);
      return prev.filter((a) => a.localId !== localId);
    });
  }, [revokePreview]);

  // -- dictado ----------------------------------------------------------------
  // El texto cae en el textarea y queda editable: nunca se envía solo.

  const handleTranscript = useCallback((text) => {
    if (!text) return;
    setInput((prev) => (prev ? `${prev.replace(/\s+$/, "")} ${text}` : text));
    setInterimTranscript("");
  }, []);

  const handleDictationError = useCallback(
    (message) => {
      setInterimTranscript("");
      addToast(message, "error");
    },
    [addToast]
  );

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
    if (Array.from(event.dataTransfer?.types || []).includes("Files")) event.preventDefault();
  }

  function handleDrop(event) {
    event.preventDefault();
    dragDepthRef.current = 0;
    setDragging(false);
    const files = Array.from(event.dataTransfer?.files || []);
    if (files.length > 0) addFiles(files);
  }

  // -- generación -------------------------------------------------------------

  const buildRequest = useCallback(
    (payloadMessages, reasoningOverride = null) => {
      const effective = reasoningOverride || reasoning;
      // Si el chat template no acepta role=system, el prompt de sistema se
      // antepone al primer mensaje del usuario en vez de perderse.
      const systemText = [
        systemPrompt.trim(),
        appendFileConvention ? t("chat.fileConventionPrompt") : "",
      ]
        .filter(Boolean)
        .join("\n\n");
      let withSystem = payloadMessages;
      if (systemText) {
        if (capabilities && capabilities.system_role === false) {
          withSystem = payloadMessages.map((m, i) =>
            i === 0 && typeof m.content === "string"
              ? { ...m, content: `${systemText}\n\n${m.content}` }
              : m
          );
        } else {
          withSystem = [{ role: "system", content: systemText }, ...payloadMessages];
        }
      }
      return {
        endpoint: endpointUrl,
        api_key: apiKey,
        messages: withSystem,
        model: selectedModel,
        ...params,
        stream: true,
        thinking_enabled: effective.mode !== "off",
        budget_tokens: effective.budget,
        preserve_thinking: effective.mode === "preserve",
        // No se manda si la plantilla del modelo no lo entiende: es un
        // parámetro que llama-server pasa al template y sin soporte queda
        // ignorado en el mejor caso.
        reasoning_effort:
          capabilities && capabilities.reasoning_effort === false ? "none" : effective.effort,
        tools_enabled: toolsEnabled,
      };
    },
    [reasoning, systemPrompt, appendFileConvention, endpointUrl, apiKey, selectedModel, params, toolsEnabled, capabilities, t]
  );

  const handleSend = useCallback(async () => {
    const text = input.trim();
    const ready = attachments.filter((a) => a.status === "ready");
    if ((!text && ready.length === 0) || streaming || busyWithAttachments) return;

    if (estimate && !estimate.fits) {
      const ok = window.confirm(t("chat.contextOverflowConfirm"));
      if (!ok) return;
    }

    const userMessage = {
      role: "user",
      content: text,
      // Se guardan todos, incluso los descartados: el historial muestra lo que
      // el usuario adjuntó, y toPayloadContent filtra por status.
      // previewUrl NO se persiste: es un blob: local que muere con la pestaña.
      // El mensaje guardado usa `url` (/api/chat/attachments/<id>/raw).
      attachments: attachments.map(({ localId, previewUrl, ...rest }) => rest),
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
    attachments.forEach(revokePreview);
    setAttachments([]);

    const payloadMessages = visiblePath(next)
      .filter((m) => m.id !== ids[1])
      .flatMap(toPayloadMessages);

    await stream.run({ request: buildRequest(payloadMessages), assistantId: ids[1] });
  }, [input, attachments, busyWithAttachments, estimate, streaming, nodes, path, stream, buildRequest, revokePreview, t]);

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

      await stream.run({ request: buildRequest(payloadMessages), assistantId: ids[1] });
    },
    [streaming, nodes, stream, buildRequest]
  );

  /** Regenerar: hermano nuevo del asistente bajo el mismo mensaje del usuario. */
  const handleRegenerate = useCallback(
    async (message, reasoningOverride) => {
      if (streaming) return;

      const { nodes: next, ids } = appendChain(nodes, message.parent_id, [assistantPlaceholder()]);
      setNodes(next);

      const payloadMessages = visiblePath(next)
        .filter((m) => m.id !== ids[0])
        .flatMap(toPayloadMessages);

      await stream.run({
        request: buildRequest(payloadMessages, reasoningOverride),
        assistantId: ids[0],
      });
    },
    [streaming, nodes, stream, buildRequest]
  );

  /**
   * Continuar una respuesta cortada por max_tokens.
   *
   * No crea rama: sigue escribiendo en el mismo nodo. El mensaje parcial va al
   * final del payload y el backend intenta que el modelo continúe ese turno;
   * si el chat template no lo soporta, reintenta solo pidiéndoselo de forma
   * explícita.
   */
  const handleContinue = useCallback(
    async (message) => {
      if (streaming) return;
      const payloadMessages = visiblePath(nodes).flatMap(toPayloadMessages);
      setNodes((prev) => patchNode(prev, message.id, { finish_reason: null }));
      await stream.run({
        request: buildRequest(payloadMessages),
        assistantId: message.id,
        continuation: true,
      });
    },
    [streaming, nodes, stream, buildRequest]
  );

  const handleSwitchBranch = useCallback((message, direction) => {
    setNodes((prev) => switchBranch(prev, message, direction));
  }, []);

  // -- shortcuts de teclado ---------------------------------------------------
  // Ctrl+Enter: enviar | Escape: cancelar | Ctrl+L: limpiar (confirm)
  // Ctrl+E: panel de export | Ctrl+H: historial.

  useEffect(() => {
    function onKeyDown(e) {
      const ctrlOrCmd = e.ctrlKey || e.metaKey;

      if (ctrlOrCmd && e.key === "Enter") {
        e.preventDefault();
        handleSend();
      } else if (e.key === "Escape" && streaming) {
        e.preventDefault();
        stream.stop();
      } else if (ctrlOrCmd && (e.key === "l" || e.key === "L")) {
        e.preventDefault();
        if (window.confirm(t("chat.clearConfirm"))) handleClear();
      } else if (ctrlOrCmd && (e.key === "e" || e.key === "E")) {
        e.preventDefault();
        setExportPanelOpen((prev) => !prev);
      } else if (ctrlOrCmd && (e.key === "h" || e.key === "H")) {
        e.preventDefault();
        history.setOpen((prev) => !prev);
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [handleSend, handleClear, streaming, t]);

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
        stream.setError(t("chat.invalidImport"));
      }
    };
    reader.readAsText(file);
    event.target.value = "";
  }

  return (
    <div className="flex gap-6 h-[calc(100vh-8rem)]">
      {!compact && (
        <ChatSidebar
          endpoints={endpoints}
          endpointUrl={endpointUrl}
          onEndpointChange={setEndpointUrl}
          endpointStatus={activeEndpointStatus}
          models={models}
          selectedModel={selectedModel}
          onModelChange={setSelectedModel}
          capabilities={capabilities}
          apiKey={apiKey}
          onApiKeyChange={setApiKey}
          systemPrompt={systemPrompt}
          onSystemPromptChange={setSystemPrompt}
          appendFileConvention={appendFileConvention}
          onAppendFileConventionChange={setAppendFileConvention}
          params={params}
          onParamChange={(patch) => setParams((prev) => ({ ...prev, ...patch }))}
          onClear={handleClear}
          onExportJson={() => exportAsJson(path, selectedModel)}
          onExportMarkdown={() => exportAsMarkdown(path, selectedModel)}
          onImport={handleImportFile}
        />
      )}

      <section
        className="relative flex-1 flex flex-col min-w-0 rounded-lg border border-white/10 overflow-hidden"
        onDragEnter={handleDragEnter}
        onDragLeave={handleDragLeave}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
      >
        {dragging && (
          <div className="absolute inset-0 z-40 flex flex-col items-center justify-center gap-2 bg-glyvex-accent/10 border-2 border-dashed border-glyvex-accent/60 pointer-events-none">
            <span className="text-sm text-glyvex-text">{t("chat.dropHint")}</span>
          </div>
        )}

        <div className="flex items-center justify-between border-b border-white/10">
          <MetricsBar
            metrics={stream.liveMetrics}
            estimate={estimate}
            contextTotal={modelContextSize}
            streaming={streaming}
          />
          <div className="flex items-center shrink-0">
            <button
              type="button"
              onClick={() => history.setOpen((v) => !v)}
              className={
                "flex items-center gap-1.5 px-3 text-xs " +
                (history.open ? "text-glyvex-accent" : "text-glyvex-muted hover:text-glyvex-text")
              }
              title={t("chat.historyTitle")}
            >
              <History size={15} />
            </button>
            <button
              type="button"
              onClick={() => setCompact((v) => !v)}
              className="flex items-center gap-1.5 px-3 text-xs text-glyvex-muted hover:text-glyvex-text"
              title={compact ? t("chat.showSettings") : t("chat.compactMode")}
            >
              {compact ? <PanelLeftOpen size={15} /> : <PanelLeftClose size={15} />}
            </button>
          </div>
        </div>

        {exportPanelOpen && (
          <div className="flex items-center gap-2 px-4 py-2 border-b border-white/10 bg-black/20">
            <span className="text-xs text-glyvex-muted mr-1">{t("chat.exportLabel")}</span>
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

        {history.open && (
          <HistoryPanel
            conversations={history.conversations}
            loading={history.loading}
            search={history.search}
            currentId={currentConvId}
            onSearch={history.setSearch}
            onSelect={handleSelectConversation}
            onDelete={handleDeleteConversation}
            onNew={startNewConversation}
            onClose={() => history.setOpen(false)}
          />
        )}

        <div
          ref={containerRef}
          onScroll={handleScroll}
          className="flex-1 overflow-y-auto overflow-x-hidden p-4 space-y-4"
        >
          {path.length === 0 ? (
            <p className="text-sm text-glyvex-muted text-center mt-10">
              {t("chat.emptyState")}
            </p>
          ) : (
            path.map((m, index) => {
              const isLive = streaming && m.role === "assistant" && index === path.length - 1;
              return (
              <MessageBubble
                key={m.id}
                message={m}
                branch={siblingInfo(nodes, m)}
                streaming={isLive}
                livePhase={
                  isLive
                    ? streamPhase({ message: m, metrics: stream.liveMetrics, tools: stream.toolActivity })
                    : null
                }
                liveTools={isLive ? stream.toolActivity : null}
                busy={streaming}
                reasoning={reasoning}
                onSwitchBranch={handleSwitchBranch}
                onEdit={handleEdit}
                onRegenerate={handleRegenerate}
                onContinue={handleContinue}
              />
              );
            })
          )}
          <div ref={bottomRef} />
        </div>

        {showJump && (
          <button
            type="button"
            onClick={() => scrollToBottom()}
            title={t("chat.goToBottom")}
            className="absolute bottom-28 left-1/2 -translate-x-1/2 z-10 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-white/10 bg-glyvex-card text-xs text-glyvex-text shadow-lg shadow-black/40 hover:bg-black/40"
          >
            <ArrowDown size={13} />
            {streaming ? t("chat.followAnswer") : t("chat.goToBottom")}
          </button>
        )}

        {stream.error && <p className="px-4 pb-2 text-sm text-red-400">{stream.error}</p>}

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
          onStop={stream.stop}
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
