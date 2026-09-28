# Glyvex AI Suite: Brand kit

Abrí `brand-guide.html` para ver el kit completo con colores, tipografía y reglas de uso.

## Contenido

| Carpeta | Qué hay |
|---|---|
| `logo/` | Isotipo en SVG (normal y `-small` con trazos gruesos para ≤48 px) + PNG 512/1024 transparente |
| `lockups/` | Logo + nombre, horizontal y vertical, para fondo claro y oscuro (PNG transparente) |
| `icons/png/` | PNG 16, 24, 32, 48, 64, 128, 256 |
| `icons/glyvex-ai-suite.ico` | ICO con todos los tamaños (16–256) |
| `icons/tauri/` | Set con los nombres que espera Tauri (`32x32.png`, `128x128.png`, `128x128@2x.png`, `icon.ico`, `icon.icns`, `icon.png`, `Square*Logo.png`, `StoreLogo.png`) + `app-icon-1024.png` |
| `splash/` | 3 splash HTML (A oscuro, B claro, C horizontal) + previews PNG |
| `installer/nsis/` | Header 150×57 y sidebar 164×314, claro y oscuro (BMP 24-bit + PNG) |
| `installer/msi/` | Banner 493×58 y diálogo 493×312 de WiX con la marca nueva (BMP + PNG) |
| `fonts/` | Poppins Regular/Medium/Bold (TTF + WOFF2 subset) + `OFL.txt` |

## Colores

| Nombre | Hex | Uso |
|---|---|---|
| Violeta Glyvex | `#7c3aed` | Principal: anillo, núcleo, "AI" |
| Celeste | `#0ea5e9` | Nodos, eje vertical |
| Cian | `#06b6d4` | Nodos, final del degradé |
| Tinta | `#0b0d14` | Fondos oscuros |
| Texto | `#141827` | Texto sobre claro |
| Gris claro | `#eceef3` | Fondos claros (nunca blanco puro en instaladores) |

Degradé: `linear-gradient(90deg, #7c3aed, #0ea5e9 60%, #06b6d4)`

## Tauri v2: `tauri.conf.json`

Copiá `icons/tauri/*` a `src-tauri/icons/` y las imágenes del instalador a `src-tauri/installer/`.

```json
{
  "bundle": {
    "icon": [
      "icons/32x32.png",
      "icons/128x128.png",
      "icons/128x128@2x.png",
      "icons/icon.icns",
      "icons/icon.ico"
    ],
    "windows": {
      "nsis": {
        "installerIcon": "icons/icon.ico",
        "headerImage": "installer/nsis-header-claro.bmp",
        "sidebarImage": "installer/nsis-sidebar-claro.bmp"
      },
      "wix": {
        "bannerPath": "installer/wix-banner-493x58.bmp",
        "dialogImagePath": "installer/wix-dialog-493x312.bmp"
      }
    }
  }
}
```

Si querés regenerar los íconos con la CLI: `npx tauri icon icons/tauri/app-icon-1024.png`. Ojo: esa CLI no usa las versiones gruesas para 16–32 px, así que el `.ico` de este kit se lee mejor en la barra de tareas.

## Splash en Tauri v2

1. Copiá el splash elegido (por ejemplo `splash/splash-a-oscuro.html`) a la carpeta pública del frontend (Vite: `public/splashscreen.html`). Es autocontenido, con la fuente embebida y sin dependencias.
2. En `tauri.conf.json` declarás dos ventanas: la principal oculta y el splash visible.

```json
"app": {
  "windows": [
    { "label": "main", "title": "Glyvex AI Suite", "visible": false, "width": 1280, "height": 800 },
    { "label": "splashscreen", "url": "splashscreen.html", "width": 640, "height": 380,
      "decorations": false, "resizable": false, "center": true, "skipTaskbar": true }
  ]
}
```

3. Desde Rust vas actualizando el splash y lo cerrás cuando el backend está listo:

```rust
use tauri::Manager;

// dentro de setup() o de tu rutina de arranque
let splash = app.get_webview_window("splashscreen").unwrap();
splash.eval(&format!("setSplashVersion('{}')", app.package_info().version))?;
splash.eval("setSplashStatus('Iniciando backend…')")?;
splash.eval("setSplashProgress(0.3)")?;
// … arrancar servicios, llama-server, etc.
splash.eval("setSplashStatus('Cargando modelos…'); setSplashProgress(0.8)")?;

// cuando todo está listo:
splash.close()?;
app.get_webview_window("main").unwrap().show()?;
```

API del splash (JS global):

- `setSplashStatus(texto)`: cambia el texto de estado.
- `setSplashVersion('0.5.0')`: muestra la versión (agrega la "v" si falta).
- `setSplashProgress(0..1)`: barra determinada; `setSplashProgress(null)` vuelve a la animación indeterminada.

Para probarlo en el navegador: `splash-a-oscuro.html?v=0.5.0&p=0.6`

## Tipografía

Poppins (SIL Open Font License 1.1, ver `fonts/OFL.txt`). Se puede redistribuir con la app siempre que vaya acompañada de la licencia.

© 2026 Glyvex Group
