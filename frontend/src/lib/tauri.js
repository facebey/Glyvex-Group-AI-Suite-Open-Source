// Puente con la shell Tauri (solo existe dentro de la app empaquetada;
// en el navegador de dev `isTauri()` es false y todo queda inactivo).
// Los imports son dinámicos para no cargar el IPC de Tauri en el browser.

export function isTauri() {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export async function onSidecarState(callback) {
  const { listen } = await import("@tauri-apps/api/event");
  return listen("sidecar-state", (event) => callback(event.payload));
}

export async function restartSidecar() {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke("sidecar_restart");
}

// T6.1: detiene el sidecar (backend) para liberar los locks de sus .dll/.pyd
// ANTES de que el instalador del auto-update lo sobrescriba (si no, el NSIS
// falla en "Error abriendo archivo para escritura" sobre _internal/).
export async function stopSidecar() {
  if (!isTauri()) return;
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke("stop_sidecar");
}

// Puerto real del backend (lo reporta el sidecar por stdout; en release es
// dinámico). Rechaza con error si aún no ha sido reportado.
export async function getBackendPort() {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke("get_backend_port");
}

// T5.2: la shell arrancó con --provision (post-install: directo a la
// pantalla de provisión).
export async function hasProvisionFlag() {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke("provision_requested");
}

// Abre un URL en el navegador externo. La webview no abre <a target="_blank">
// (no hay shell por defecto); shell.open lo delega al handler del SO. Fuera de
// Tauri (navegador de dev) no hace nada: el <a> normal ya cubre ese caso.
export async function openExternal(url) {
  if (!isTauri()) return;
  const { open } = await import("@tauri-apps/plugin-shell");
  return open(url);
}

// T6.1: auto-update. check() resuelve con el objeto Update (tiene
// .downloadAndInstall) o con null si ya está en la última versión.
export async function checkForUpdate() {
  if (!isTauri()) return null;
  const { check } = await import("@tauri-apps/plugin-updater");
  return check();
}

// Tras instalar, reinicia la app (en Windows el instalador reemplaza y la
// app se cierra sola).
export async function relaunchApp() {
  if (!isTauri()) return;
  const { relaunch } = await import("@tauri-apps/plugin-process");
  return relaunch();
}

