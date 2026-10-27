import { useTranslation } from "react-i18next";
import { formatMoney } from "../lib/sessionCost.js";

// El ahorro es lo que hay que leer de un vistazo: número fuerte en verde ok
// (--color-glyvex-ok existe en TODOS los temas, así el ahorro queda verde
// aunque el accent del tema cambie). El costo de nube de referencia pasa a pie
// de página tachado: contexto, no protagonista.
export default function SavingsHero({ savings, reference, providerName, currency }) {
  const { t } = useTranslation();
  return (
    <div className="rounded-lg border border-glyvex-ok/40 bg-glyvex-ok/10 px-4 py-3">
      <p className="text-[11px] uppercase tracking-wide text-glyvex-muted">
        {t("monitor.savings.label")}
      </p>
      <p className="mt-1 text-3xl font-bold text-glyvex-ok tabular-nums">
        {formatMoney(savings, currency)}
      </p>
      <p className="mt-1 text-xs text-glyvex-muted">
        {t("monitor.savings.footSame", { name: providerName })}{" "}
        <span className="text-glyvex-muted-2 line-through tabular-nums">
          {formatMoney(reference, currency)}
        </span>
        {" · "}
        {t("monitor.savings.footPaid", { local: formatMoney(0, currency) })}
      </p>
    </div>
  );
}

// Chip compacto para el header de CollapsiblePanel (slot headerRight): el
// número fuerte se ve incluso con el panel colapsado.
export function SavingsChip({ cost, currency }) {
  const { t } = useTranslation();
  return (
    <span className="text-xs font-medium rounded-full border border-glyvex-ok/40 bg-glyvex-ok/10 px-2.5 py-1 text-glyvex-ok whitespace-nowrap tabular-nums">
      {t("monitor.savings.chip", { cost: formatMoney(cost, currency) })}
    </span>
  );
}
