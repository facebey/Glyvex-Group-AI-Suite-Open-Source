import { ChevronDown, ChevronRight } from "lucide-react";
import { useLocalStorage } from "../../hooks/useLocalStorage";

// Card con header clickeable que colapsa/expande el contenido.
// El estado de apertura persiste por storageKey (localStorage).
// headerRight: slot opcional de controles dentro del header (botones,
// selects); se renderiza fuera del botón de toggle (no se puede anidar
// <button> dentro de <button>).
export default function CollapsiblePanel({ icon: Icon, title, storageKey, defaultOpen = true, headerRight, children }) {
  const [open, setOpen] = useLocalStorage(storageKey, defaultOpen);
  return (
    <div className="bg-glyvex-card rounded-lg border border-glyvex-border-soft p-5">
      <div className="flex w-full flex-wrap items-center gap-2 mb-4">
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="flex items-center gap-2 text-sm font-medium text-glyvex-muted uppercase tracking-wide"
        >
          {Icon && <Icon size={14} />}
          {title}
        </button>
        {headerRight}
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="ml-auto text-glyvex-muted hover:text-glyvex-text"
          aria-label={open ? "colapsar" : "expandir"}
        >
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>
      </div>
      {open && <div className="space-y-4">{children}</div>}
    </div>
  );
}
