# Checklist de Release

> **Idioma:** [English](RELEASE-CHECKLIST.md)

Procedimiento paso a paso para publicar una release de Glyvex-AI-Suite, basado
en el flujo real usado desde v0.7.0-beta. El [README](../README.md) tiene el
resumen; esta página tiene la secuencia completa, los comandos reales y los
gotchas aprendidos por el camino.

## 0. Preparación

- [ ] Working tree limpio; las ramas feature ya mergueadas a `main` (FF preferido).
- [ ] Baseline verde antes de tocar versiones:

  ```powershell
  python -m pytest tests/ -q
  cd frontend; npm test; npm run build
  ```

## 1. Bump de versión — los 6 archivos JUNTOS

La versión debe moverse **junta** en estos 6 archivos (separarlas en commits
causó los desfasajes de 0.6.0 vs 0.7.1-beta y 0.7.1 vs 0.7.4):

| Archivo | Campo |
|---|---|
| `backend/main.py` | `APP_VERSION` |
| `frontend/package.json` | `version` |
| `frontend/package-lock.json` | `version` (raíz + `packages."")`) |
| `src-tauri/Cargo.toml` | `[package] version` |
| `src-tauri/Cargo.lock` | `version` del paquete `glyvex-ai-suite` |
| `src-tauri/tauri.conf.json` | `version` |

- [ ] Los 6 muestran la misma versión (el bundler nombra el exe desde `tauri.conf.json`; `build-latest-json.ps1` la lee de ahí).
- [ ] Entrada en el CHANGELOG: bilingüe, inglés primero y sección `## Español`.
- [ ] Commit: `chore(release): bump to vX.Y.Z-beta`.

## 2. Tag + push (repo privado)

- [ ] `git tag -a vX.Y.Z-beta -m "<resumen de la release>"`
- [ ] `git push origin main vX.Y.Z-beta` (solo con aprobación explícita)

## 3. Build (Windows)

- [ ] Clave de firma del updater: `$env:TAURI_SIGNING_PRIVATE_KEY = (Get-Content .tauri\glyvex.key -Raw)`
- [ ] Sidecar (frontend + PyInstaller + smoke test en el puerto 7981, que debe estar libre):

  ```powershell
  .\build.ps1
  ```

  También recorta `babel\locale-data` a en/es y enlaza `src-tauri\resources\glyvex-backend`.
- [ ] Instalador NSIS — desde la **raíz del repo** (nunca `npm run tauri build`
  desde `frontend/`; hace panic porque `tauri.conf.json` vive en `src-tauri/`):

  ```powershell
  frontend\node_modules\.bin\tauri.cmd build --ci --config <override.json>
  ```

  El override debe ser **mínimo** — `{"build": {"beforeBuildCommand": null}}`
  (mantenerlo gitignored): `tauri-cli` hace merge base ← override, y un
  override de copia completa sigue ganando con valores viejos.
- [ ] Artefactos en `src-tauri\target\release\bundle\nsis\`:
  - `Glyvex AI Suite_X.Y.Z-beta_x64-setup.exe`
  - `Glyvex AI Suite_X.Y.Z-beta_x64-setup.exe.sig`
- [ ] Anotar el SHA-256 del exe (entra en la tabla de las release notes).
- [ ] **No cambiar** el `productName` del NSIS (`Glyvex AI Suite`) ni el
  `startMenuFolder` (`Glyvex-AI-Suite`): cambiarlos mueve el directorio de
  instalación + la key de registro y rompen el upgrade in-place (pasó en v0.7.7).
- [ ] Manifiesto del updater:

  ```powershell
  .\build-latest-json.ps1
  ```

  Lee la versión de `tauri.conf.json`, embebe el contenido del `.sig` como
  firma, arma la URL de descarga con **puntos** (GitHub renombra los espacios
  de los assets a puntos) y las versiones de plugins desde `Cargo.lock`.

## 4. Sync público

- [ ] `.\distribution\sync-oss.ps1` — exporta el estado commiteado (sin
  `distribution/`), aplica la capa `distribution/final/`, commit y push a
  `facebey/Glyvex-Group-AI-Suite-Open-Source`.

## 5. GitHub Release (repo público)

- [ ] Notes en un archivo `.md` escrito con un editor (UTF-8, sin BOM) —
  **nunca** pipear la salida de `gh` por cmdlets de texto de PowerShell
  (mojibake UTF-8). Bilingüe: inglés primero, sección `## Español`, link
  `> **Idioma:** [Español](#español)`, y tabla SHA-256 de los assets.
- [ ] Crear **no-prerelease** (para que `releases/latest` resuelva para el
  updater y el link de descarga del README):

  ```powershell
  gh release create vX.Y.Z-beta --target main --title "vX.Y.Z-beta" --notes-file <file>.md
  ```

  `--target` toma **nombre de rama** (un SHA corto falla con HTTP 422).
- [ ] Subir los tres assets: `.exe`, `.sig`, `latest.json`
  (`gh release upload --clobber` en re-releases; si se reemplaza el exe,
  actualizar la tabla SHA-256 de las notes).
- [ ] Después de crear o editar las notes, verificar que los acentos
  sobrevivieron: `gh release view vX.Y.Z-beta --json body --jq .body`

## 6. Verificación

- [ ] Canal de update: `https://github.com/facebey/Glyvex-Group-AI-Suite-Open-Source/releases/latest/download/latest.json`
  → 200 y `version` = la nueva.
- [ ] Validar el auto-update end-to-end desde una instalación pública real
  (detectar → descargar → instalar).
- [ ] Revisar alertas de Dependabot en ambos repos (privado + público).

## Gotchas (aprendidos a la dura)

- `npm run tauri build` desde `frontend/` → panic (ver paso 3).
- `tauri-cli --config` hace merge base ← override → usar el override mínimo.
- `bundle.mainBinaryName` no existe en tauri-cli 2.12; el nombre del binario
  sale de Cargo `[[bin]]`.
- El `gh` CLI normaliza espacios → puntos en nombres de assets
  (`Glyvex.AI.Suite_…`); la URL de `latest.json` ya lo contempla.
- `latest.json` debe subirse en **todas** las releases (mismo nombre de
  archivo en todas) para que las instalaciones viejas siempre apunten a la última.
- NSIS `productName` / `startMenuFolder` están congelados (ver paso 3).
