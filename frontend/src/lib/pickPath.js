import { isTauri } from "./tauri.js";

// PUB-3: selectores nativos de archivo/carpeta (tauri-plugin-dialog).
// Fuera de Tauri (navegador de dev) devuelven null: el botón Browse se
// oculta y el campo queda como input de texto.

// filters: [{ name, extensions: [...] }] — p. ej. GGUF para modelos.
export async function pickFile(filters = null) {
  if (!isTauri()) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  return open({ multiple: false, directory: false, filters });
}

export async function pickDirectories() {
  if (!isTauri()) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  return open({ multiple: true, directory: true });
}
