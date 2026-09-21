import { useCallback, useEffect, useRef, useState } from "react";
import i18n from "../lib/i18n.js";

const HISTORY_PAGE_SIZE = 50;
const AUTOSAVE_DEBOUNCE_MS = 1000;

/**
 * Historial de conversaciones: lista, búsqueda, guardado automático y
 * borrado.
 *
 * El guardado tiene debounce y lee el estado por referencia, no por closure:
 * cuando el timer dispara, un segundo después de que terminó la generación,
 * los valores capturados al programarlo ya estarían viejos.
 *
 * Persiste el árbol completo, no el camino visible — las ramas que quedaron
 * de lado también son parte de la conversación.
 */
export function useConversations({ stateRef, setCurrentConvId, onError }) {
  const [conversations, setConversations] = useState([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(false);

  const saveTimerRef = useRef(null);
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  const load = useCallback(async (term = "") => {
    setLoading(true);
    try {
      const qs = new URLSearchParams({ limit: String(HISTORY_PAGE_SIZE), offset: "0" });
      if (term.trim()) qs.set("search", term.trim());
      const res = await fetch(`/api/chat/conversations?${qs.toString()}`);
      if (!res.ok) throw new Error(String(res.status));
      setConversations(await res.json());
    } catch {
      onErrorRef.current?.(i18n.t("chatErrors.historyLoad"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    const id = setTimeout(() => load(search), 300);
    return () => clearTimeout(id);
  }, [open, search, load]);

  const save = useCallback(async () => {
    const {
      nodes: tree, path: visible, currentConvId: convId, selectedModel: model,
      endpointUrl: url, params: p, reasoning: r, systemPrompt: sp, toolsEnabled: te,
    } = stateRef.current;

    if (!visible || visible.length === 0) return null;

    const config = { ...p, reasoning: r, system_prompt: sp, tools_enabled: te };
    try {
      if (convId) {
        const res = await fetch(`/api/chat/conversations/${convId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ messages: tree, config, model_name: model }),
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
        body: JSON.stringify({ messages: tree, config, model_name: model, endpoint: url }),
      });
      if (!res.ok) throw new Error(String(res.status));
      const data = await res.json();
      if (data?.id) setCurrentConvId(data.id);
      return data?.id ?? null;
    } catch {
      return null;
    }
  }, [stateRef, setCurrentConvId]);

  const scheduleSave = useCallback(() => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(async () => {
      saveTimerRef.current = null;
      await save();
      if (open) load(search);
    }, AUTOSAVE_DEBOUNCE_MS);
  }, [save, open, search, load]);

  /** Fuerza el guardado pendiente, si lo hay. Útil antes de limpiar o cambiar. */
  const flushSave = useCallback(async () => {
    if (!saveTimerRef.current) return null;
    clearTimeout(saveTimerRef.current);
    saveTimerRef.current = null;
    return save();
  }, [save]);

  const remove = useCallback(async (id) => {
    try {
      const res = await fetch(`/api/chat/conversations/${id}`, { method: "DELETE" });
      if (!res.ok && res.status !== 404) throw new Error(String(res.status));
      setConversations((prev) => prev.filter((c) => c.id !== id));
      if (id === stateRef.current.currentConvId) setCurrentConvId(null);
      return true;
    } catch {
      onErrorRef.current?.(i18n.t("chatErrors.conversationDelete"));
      return false;
    }
  }, [stateRef, setCurrentConvId]);

  const fetchOne = useCallback(async (id) => {
    const res = await fetch(`/api/chat/conversations/${id}`);
    if (!res.ok) throw new Error(String(res.status));
    return res.json();
  }, []);

  useEffect(() => {
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    };
  }, []);

  return {
    conversations, loading, search, open,
    setSearch, setOpen, load, save, scheduleSave, flushSave, remove, fetchOne,
  };
}
