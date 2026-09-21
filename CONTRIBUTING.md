# Contribuyendo a Glyvex-AI-Suite

Gracias por querer contribuir. Acá está cómo funciona el flujo.

## Reglas básicas

- **100% local**: nada de la app ni de sus tests puede depender de red,
  nube, GPU o modelos reales. Un test que toque el mundo real no entra.
- **Tests en verde**: la suite completa debe pasar antes de proponer cualquier
  cambio.
- **Un cambio por PR**: no mezclar feature + fixes + refactor.
- **Idioma del código y los mensajes**: español (comentarios de intención,
  commits, PRs).

## Buenas primeras contribuciones (up-for-grabs)

Tareas acotadas, aptas para entrar al proyecto, que no rompen la regla de 100%
local:

- Traducir a inglés las docs nuevas: `QUICKSTART.md`, `ARQUITECTURA.md`,
  `PRIVACIDAD.md` y `modulos.md` (hoy en español).
- Ampliar `TROUBLESHOOTING.md` con casos reales que resuelvas.
- Aumentar cobertura de tests en módulos con poca (ver `pytest --cov`).
- Agregar un set de benchmark de ejemplo a `backend/prompts/` (con keywords de
  scoring bien definidas).
- Documentar el soporte formal de Linux/macOS (está en el roadmap del README).

Mirá el [Roadmap](README.md#roadmap) para el contexto mayor.

## Setup

```bash
# Backend (Python 3.11+) — desde la raíz del repo
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# opcionales: GPU (nvidia-ml-py), voz a texto (faster-whisper), etc.
.venv/bin/pip install -r requirements-optional.txt

# Frontend (Node.js 20 LTS+)
cd frontend
npm install
```

Arranque completo con `./start.sh` (Linux) o los scripts equivalentes en
`scripts/`. En desarrollo: backend con `uvicorn main:app --reload --port
7981` desde `backend/` y frontend con `npm run build && npm run preview`
(desde `frontend/`).

## Tests

```bash
# Desde la raíz del repo
pytest tests/ -v
```

- pytest + pytest-asyncio + httpx. Los fixtures compartidos viven en
  `tests/conftest.py`: binarios fake que responden `--help`/`--version`,
  un LLM server mock en `:18080` y estado aislado por test.
- Baseline: **297 tests**.

## Git

- Ramas descriptivas desde `main`: `feature/<tema>`, `fix/<tema>`,
  `docs/<tema>`.
- Commits con prefijo conventional en español: `feat(llm): ...`,
  `fix(backend): ...`, `test(llm): ...`, `docs: ...`.
- No commitear estado runtime: `data/config.json`, `data/models.json`,
  logs, `node_modules`. Ver `.gitignore`.

## Convenciones de código

- **Sin comentarios** salvo cuando el por-qué no es obvio. El repo usa
  comentarios de intención (en español), no de narración de código.
- **Pydantic v2** en backend; `model_validator` para validaciones cruzadas.
- Frontend React: estado global por contextos propios (sin librerías de
  estado externas), componentes en `frontend/src/pages/`.

### Si tocás `LaunchConfig` (launcher)

Agregar o cambiar un campo exige actualizar **los cuatro** lados:

1. La docstring/comentario del campo en `backend/launcher.py`.
2. `build_llama_server_command` (el flag que emite).
3. El test de `FRONTEND_LAUNCH_PAYLOAD` en `tests/test_launcher.py` (debe
   espejar `DEFAULT_LAUNCH_CONFIG`).
4. El frontend (`frontend/src/pages/Launcher.jsx`).

Las features contra builds viejas de llama.cpp se protegen con el **probe de
binario** (`probe_binary` + `filter_command`): los flags que el binario no
conoce se descartan y se reportan, no se rompe el lanzamiento.

## Documentación

- `README.md` — detalle completo de módulos, seguridad y release; su sección
  "Documentación" es el índice de todas las docs.
- `docs/` — `QUICKSTART.md` (arranque rápido), `ARQUITECTURA.md` (mapa del
  código), `modulos.md` (M0–M8), `launcher-params.md` (referencia de
  parámetros), `PRIVACIDAD.md` (qué sale de la máquina), `TROUBLESHOOTING.md`,
  `voz-a-texto.md`, `searxng/`.
- Si tu cambio agrega comportamiento visible para el usuario, actualizá el
  README (o el doc correspondiente) en la misma PR.
- `CHANGELOG.md` — el maintainer lo actualiza en cada release.

## Versionado y releases

Semántica `mayor.minor.patch` (ver sección "Versionado" del README). La
versión vive en `APP_VERSION` (`backend/main.py`) y `package.json`
(frontend) y debe mantenerse igual en ambos. El maintainer crea el tag `vX.Y.Z` y genera el
zip distributable vía `distribution/` (ver `distribution/README.md`).
