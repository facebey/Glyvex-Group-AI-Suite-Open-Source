/**
 * Clases compartidas entre las páginas y los componentes que se extrajeron
 * de ellas. Viven acá para que no haya definiciones divergiendo con el
 * tiempo.
 */

/** Inputs del chat: compactos, para caber en la barra lateral y el composer. */
const inputBase =
  "w-full bg-glyvex-veil-disabled border border-glyvex-border-soft rounded-md px-2 py-1.5 text-sm " +
  "focus:outline-none focus:ring-2 focus:ring-glyvex-accent/60";

export const inputClasses =
  inputBase + " text-glyvex-text placeholder:text-glyvex-muted/60";

/** Variante para inputs sobre el fondo crudo (composer): en metallic el
    fondo es oscuro y el texto debe usar los tokens bg-text/bg-muted. */
export const composerInputClasses =
  inputBase + " text-glyvex-bg-text placeholder:text-glyvex-bg-muted/60";

/** Inputs de formulario en páginas de configuración: más aire. */
export const formInputClasses =
  "w-full bg-glyvex-veil-disabled border border-glyvex-border-soft rounded-md px-3 py-2 text-sm " +
  "text-glyvex-text placeholder:text-glyvex-muted/60 focus:outline-none " +
  "focus:ring-2 focus:ring-glyvex-accent/60";
