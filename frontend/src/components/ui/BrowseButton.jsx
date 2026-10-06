import { FolderOpen } from "lucide-react";
import { isTauri } from "../../lib/tauri.js";

// PUB-3: abre el picker nativo vía onBrowse. Solo se renderiza dentro de la
// shell Tauri; en el navegador de dev el campo sigue siendo un input de texto.
export default function BrowseButton({ onBrowse, label }) {
  if (!isTauri()) return null;
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      onClick={onBrowse}
      className="flex items-center px-2.5 rounded-md border border-glyvex-border-soft text-glyvex-muted hover:text-glyvex-accent hover:border-glyvex-accent shrink-0"
    >
      <FolderOpen size={16} />
    </button>
  );
}
