# Contributing to Glyvex-AI-Suite

> **Language:** [Español](CONTRIBUTING.es.md)

Thanks for wanting to contribute. Here is how the flow works.

## Basic rules

- **100% local**: neither the app nor its tests may depend on the network,
  cloud, a GPU, or real models. A test that touches the real world does not
  get in.
- **Tests green**: the full suite must pass before proposing any change.
- **One change per PR**: do not mix features + fixes + refactors.
- **Language of code and messages**: English (intent comments, commits, PRs).

## Good first contributions (up-for-grabs)

Bounded tasks, suitable for entering the project, that do not break the 100%
local rule:

- Extend `TROUBLESHOOTING.md` with real cases you solve.
- Increase test coverage in low-coverage modules (see `pytest --cov`).
- Add a sample benchmark set to `backend/prompts/` (with well-defined scoring
  keywords).
- Document formal Linux/macOS support (it is in the README roadmap).

See the [Roadmap](README.md#roadmap) for broader context.

## Setup

```bash
# Backend (Python 3.11+) — from the repo root
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# optional: GPU (nvidia-ml-py), speech-to-text (faster-whisper), etc.
.venv/bin/pip install -r requirements-optional.txt

# Frontend (Node.js 20 LTS+)
cd frontend
npm install
```

Full startup with `./start.sh` (Linux) or the equivalent scripts in
`scripts/`. For development: backend with `uvicorn main:app --reload --port
7981` from `backend/`, and frontend with `npm run build && npm run preview`
(from `frontend/`).

## Tests

```bash
# From the repo root
pytest tests/ -v
```

- pytest + pytest-asyncio + httpx. Shared fixtures live in
  `tests/conftest.py`: fake binaries answering `--help`/`--version`, a mock
  LLM server on `:18080`, and per-test isolated state.
- Baseline: **~600 tests** (588 passing + 9 skipped per platform, as of
  v0.7.1-beta).

## Git

- Descriptive branches from `main`: `feature/<topic>`, `fix/<topic>`,
  `docs/<topic>`.
- Commits with conventional prefixes (in English): `feat(llm): ...`,
  `fix(backend): ...`, `test(llm): ...`, `docs: ...`.
- Never commit runtime state: `data/config.json`, `data/models.json`,
  logs, `node_modules`. See `.gitignore`.

## Code conventions

- **No comments** unless the why is not obvious. The repo uses intent
  comments (in English), not code narration.
- **Pydantic v2** in the backend; `model_validator` for cross-field
  validation.
- React frontend: global state via own contexts (no external state
  libraries), components in `frontend/src/pages/`.

### If you touch `LaunchConfig` (launcher)

Adding or changing a field requires updating **all four** sides:

1. The field's docstring/comment in `backend/launcher.py`.
2. `build_llama_server_command` (the flag it emits).
3. The `FRONTEND_LAUNCH_PAYLOAD` test in `tests/test_launcher.py` (must
   mirror `DEFAULT_LAUNCH_CONFIG`).
4. The frontend (`frontend/src/pages/Launcher.jsx`).

Features against older llama.cpp builds are protected by the **binary probe**
(`probe_binary` + `filter_command`): flags the binary does not know are
dropped and reported, the launch is not broken.

## Documentation

- `README.md` — full detail on modules, security and release; its
  "Documentation" section is the index of all docs.
- `docs/` — `QUICKSTART.md` (quick start), `ARCHITECTURE.md` (code map),
  `modules.md` (M0–M8), `launcher-params.md` (parameter reference),
  `PRIVACY.md` (what leaves the machine), `TROUBLESHOOTING.md`,
  `speech-to-text.md`, `searxng/`.
- If your change adds user-visible behavior, update the README (or the
  corresponding doc) in the same PR.
- `CHANGELOG.md` — the maintainer updates it on every release.

## Versioning and releases

Semver `major.minor.patch` (see the "Versioning" section of the README). The
version lives in `APP_VERSION` (`backend/main.py`) and `package.json`
(frontend) and must be kept identical in both. The maintainer creates the
tag `vX.Y.Z` and generates the distributable zip via `distribution/` (see
`distribution/README.md`).
