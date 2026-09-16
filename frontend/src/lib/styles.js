/**
 * Clases compartidas entre las páginas y los componentes que se extrajeron
 * de ellas. Viven acá para que no haya definiciones divergiendo con el
 * tiempo.
 */

/** Inputs del chat: compactos, para caber en la barra lateral y el composer. */
export const inputClasses =
  "w-full bg-black/30 border border-white/10 rounded-md px-2 py-1.5 text-sm " +
  "text-glyvex-text placeholder:text-glyvex-muted/60 focus:outline-none " +
  "focus:ring-2 focus:ring-glyvex-accent/60";

/** Inputs de formulario en páginas de configuración: más aire. */
export const formInputClasses =
  "w-full bg-black/30 border border-white/10 rounded-md px-3 py-2 text-sm " +
  "text-glyvex-text placeholder:text-glyvex-muted/60 focus:outline-none " +
  "focus:ring-2 focus:ring-glyvex-accent/60";
