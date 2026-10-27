# Themes — Glyvex AI Suite

> **Language:** [Español](THEMES_CHANGELOG.es.md)

5 themes cycled from the header palette button (or from Config):
dark → light → carbon → metallic → matrix.

## Files
- `src/lib/theme.js` — THEMES, THEME_META, nextTheme(), cycleTheme()
- `src/index.css` — data tokens (chart-*, load-*) + carbon/metallic/matrix finishes
- `src/assets/themes/` — code-generated textures (no third-party licenses)
- `src/components/MatrixRain.jsx` — digital rain (canvas, 24 fps, pauses in a hidden tab, respects prefers-reduced-motion)
- `src/App.jsx` — cycle button, .gx-app/.gx-shell/.gx-nav-on hooks, MatrixRain
- `src/pages/Monitor.jsx`, `Benchmark.jsx`, `Launcher.jsx` — colors to CSS variables, .gx-bar/.gx-recess/.gx-raised/.gx-range/.gx-chart hooks
- `src/pages/Config.jsx` — Matrix option + sync with the header button
- `src/locales/{es,en}.json` — themeMatrix, themeCycle

## Regression guarantee
Dark and light verified pixel-by-pixel against the original build (0.000%
difference) in Chat, Launcher, Benchmark, Monitor, Reports, and Config.

## Backend
If the backend validates `app.theme` when saving the config, it must accept `"matrix"`.
