# Temas — Glyvex AI Suite

5 temas en ciclo desde el botón de paleta del header (o desde Config):
dark → claro → carbon → metallic → matrix.

## Archivos
- `src/lib/theme.js` — THEMES, THEME_META, nextTheme(), cycleTheme()
- `src/index.css` — tokens de datos (chart-*, load-*) + acabados carbon/metallic/matrix
- `src/assets/themes/` — texturas generadas por código (sin licencias de terceros)
- `src/components/MatrixRain.jsx` — lluvia digital (canvas, 24 fps, pausa en pestaña oculta, respeta prefers-reduced-motion)
- `src/App.jsx` — botón de ciclo, hooks .gx-app/.gx-shell/.gx-nav-on, MatrixRain
- `src/pages/Monitor.jsx`, `Benchmark.jsx`, `Launcher.jsx` — colores a variables CSS, hooks .gx-bar/.gx-recess/.gx-raised/.gx-range/.gx-chart
- `src/pages/Config.jsx` — opción Matrix + sincronización con el botón del header
- `src/locales/{es,en}.json` — themeMatrix, themeCycle

## Garantía de regresión
Dark y claro verificados píxel a píxel contra el build original (0,000 % de diferencia)
en Chat, Launcher, Benchmark, Monitor, Reports y Config.

## Backend
Si el backend valida `app.theme` al guardar la config, debe aceptar `"matrix"`.
