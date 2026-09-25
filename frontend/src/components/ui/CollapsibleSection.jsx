import { ChevronDown, ChevronRight } from "lucide-react";

export default function CollapsibleSection({ title, open, onToggle, children }) {
  return (
    <div className="border-t border-glyvex-border-soft pt-3">
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
