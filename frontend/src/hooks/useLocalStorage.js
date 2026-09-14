import { useEffect, useState } from "react";

/**
 * Hook de estado persistido en localStorage (no cookies).
 * Misma firma que useState: [value, setValue].
 *
 * - Lee el valor inicial de localStorage de forma perezosa (solo en el primer render).
 * - Cada cambio de `value` se serializa a JSON y se guarda bajo `key`.
 * - Si localStorage no está disponible (modo privado, SSR, etc.) o el valor
 *   guardado está corrupto, cae de vuelta a `defaultValue` sin romper la app.
 */
export function useLocalStorage(key, defaultValue) {
  const [value, setValue] = useState(() => {
    try {
      const raw = window.localStorage.getItem(key);
      return raw !== null ? JSON.parse(raw) : defaultValue;
    } catch {
      return defaultValue;
    }
  });

  useEffect(() => {
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
    } catch {
      // Cuota excedida o localStorage bloqueado: se ignora, el estado
      // sigue funcionando en memoria para esta sesión.
    }
  }, [key, value]);

  return [value, setValue];
}
