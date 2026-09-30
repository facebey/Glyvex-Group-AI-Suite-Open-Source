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

