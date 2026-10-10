# Release Checklist

> **Language:** [Español](RELEASE-CHECKLIST.es.md)

Step-by-step release procedure for Glyvex-AI-Suite, based on the actual flow
used since v0.7.0-beta. The [README](../README.md) has the summary; this page
has the full sequence, the real commands, and the gotchas learned along the way.

## 0. Preparation

- [ ] Working tree clean; feature branches already merged into `main` (FF preferred).
- [ ] Green baseline before touching versions:

  ```powershell
  python -m pytest tests/ -q
  cd frontend; npm test; npm run build
  ```

## 1. Version bump — all 6 files at once

The version must move **together** in these 6 files (splitting them across
commits caused the 0.6.0 vs 0.7.1-beta and 0.7.1 vs 0.7.4 drifts):

| File | Field |
|---|---|
| `backend/main.py` | `APP_VERSION` |
| `frontend/package.json` | `version` |
| `frontend/package-lock.json` | `version` (root + `packages."")`) |
| `src-tauri/Cargo.toml` | `[package] version` |
| `src-tauri/Cargo.lock` | `version` of the `glyvex-ai-suite` package |
| `src-tauri/tauri.conf.json` | `version` |

- [ ] All 6 show the same version (the bundler names the exe from `tauri.conf.json`; `build-latest-json.ps1` reads it from there).
- [ ] CHANGELOG entry: bilingual, English first, then a `## Español` section.
- [ ] Commit: `chore(release): bump to vX.Y.Z-beta`.

## 2. Tag + push (private repo)

- [ ] `git tag -a vX.Y.Z-beta -m "<release summary>"`
- [ ] `git push origin main vX.Y.Z-beta` (only with explicit approval)

## 3. Build (Windows)

- [ ] Signing key for the updater: `$env:TAURI_SIGNING_PRIVATE_KEY = (Get-Content .tauri\glyvex.key -Raw)`
- [ ] Sidecar (frontend + PyInstaller + smoke test on port 7981, which must be free):

  ```powershell
  .\build.ps1
  ```

  It also trims `babel\locale-data` to en/es and links `src-tauri\resources\glyvex-backend`.
- [ ] NSIS installer — from the **repo root** (never `npm run tauri build` from
  `frontend/`; it panics because `tauri.conf.json` lives in `src-tauri/`):

  ```powershell
  frontend\node_modules\.bin\tauri.cmd build --ci --config <override.json>
  ```

  The override must be **minimal** — `{"build": {"beforeBuildCommand": null}}`
  (keep it gitignored): `tauri-cli` merges base ← override, and a full-copy
  override keeps winning with stale values.
- [ ] Artifacts in `src-tauri\target\release\bundle\nsis\`:
  - `Glyvex AI Suite_X.Y.Z-beta_x64-setup.exe`
  - `Glyvex AI Suite_X.Y.Z-beta_x64-setup.exe.sig`
- [ ] Record the exe SHA-256 (goes into the release notes table).
- [ ] **Do not change** NSIS `productName` (`Glyvex AI Suite`) or `startMenuFolder`
  (`Glyvex-AI-Suite`): changing them moves the install dir + registry key and
  breaks in-place upgrades (happened with v0.7.7).
- [ ] Updater manifest:

  ```powershell
  .\build-latest-json.ps1
  ```

  Reads the version from `tauri.conf.json`, embeds the `.sig` content as the
  signature, builds the download URL with **dots** (GitHub renames spaces to
  dots in asset names) and the plugin versions from `Cargo.lock`.

## 4. Public sync

- [ ] `.\distribution\sync-oss.ps1` — exports the committed state (without
  `distribution/`), overlays the `distribution/final/` layer, commits and pushes
  to `facebey/Glyvex-Group-AI-Suite-Open-Source`.

## 5. GitHub Release (public repo)

- [ ] Notes in a `.md` file written with an editor (UTF-8, no BOM) — **never**
  pipe `gh` output through PowerShell text cmdlets (UTF-8 mojibake). Bilingual:
  English first, `## Español` section, `> **Language:** [Español](#español)`
  link, and a SHA-256 table for the assets.
- [ ] Create **non-prerelease** (so `releases/latest` resolves for the updater
  and the README download link):

  ```powershell
  gh release create vX.Y.Z-beta --target main --title "vX.Y.Z-beta" --notes-file <file>.md
  ```

  `--target` takes a **branch name** (a short SHA fails with HTTP 422).
- [ ] Upload the three assets: `.exe`, `.sig`, `latest.json`
  (`gh release upload --clobber` on re-releases; if you replace the exe,
  update the SHA-256 table in the notes).
- [ ] After creating or editing the notes, verify the accents survived:
  `gh release view vX.Y.Z-beta --json body --jq .body`

## 6. Verify

- [ ] Update channel: `https://github.com/facebey/Glyvex-Group-AI-Suite-Open-Source/releases/latest/download/latest.json`
  → 200 and `version` = the new one.
- [ ] Validate auto-update end-to-end from a real public installation
  (detect → download → install).
- [ ] Check Dependabot alerts on both repos (private + public).

## Gotchas (learned the hard way)

- `npm run tauri build` from `frontend/` → panic (see step 3).
- `tauri-cli --config` merges base ← override → use the minimal override.
- `bundle.mainBinaryName` does not exist in tauri-cli 2.12; the binary name
  comes from Cargo `[[bin]]`.
- The `gh` CLI normalizes spaces → dots in asset names (`Glyvex.AI.Suite_…`);
  the `latest.json` URL already accounts for it.
- `latest.json` must be uploaded to **every** release (same filename in all of
  them) so old installs always resolve to the newest.
- NSIS `productName` / `startMenuFolder` are frozen (see step 3).
