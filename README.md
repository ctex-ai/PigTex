# PigTex Desktop

This is the public desktop-only source tree for PigTex.

It intentionally excludes the marketing website, deployment templates, private prompt/data packs, local databases, release binaries, and all real secrets.

## Included

- Electron renderer and main-process code
- FastAPI backend used by the desktop app
- Public-safe docs, tests, build configs, and example environment files

## Not included

- Website and download-manifest source
- Repo-level `data/` prompt packs and Skill Foundry registries
- `deploy/` and `ops/` infrastructure material
- Real `.env` files, local databases, logs, `node_modules`, and packaged installers

## Fresh setup

Renderer:

```powershell
npm ci
Copy-Item .env.example .env
```

Backend:

```powershell
cd backend
python -m venv venv
.\venv\Scripts\pip.exe install -r requirements.txt
Copy-Item .env.example .env
```

Use `.env.example` in this folder for the desktop renderer, and `backend/.env.example` for the backend.

## Optional private prompt packs

This public repo can run without the private prompt/data packs. If you keep those packs outside the repo, point the backend at them with environment variables:

```powershell
PIGTEX_DATA_DIR=
PIGTEX_PROMPT_PACKS_DIR=
PIGTEX_SKILL_FOUNDRY_DIR=
```

- `PIGTEX_DATA_DIR` or `PIGTEX_PROMPT_PACKS_DIR`: external directory that contains `system_prompts/`, `enhancement_rules/`, and related JSON packs
- `PIGTEX_SKILL_FOUNDRY_DIR`: external Skill Foundry storage directory

If these variables are not set, the backend degrades safely and uses local per-device Skill Foundry storage where needed.

## Structure

- `src/`: renderer UI and frontend logic
- `electron/`: Electron main process and preload code
- `backend/`: Python backend used by the desktop app
- `docs/`: public-safe desktop documentation
- `assets/`: source-managed app assets
- `public/`: Vite public assets served by path at runtime
- `scripts/`: desktop build and release helper scripts

## Commands

```powershell
npm test
npm run lint:security
npm run build:electron
npm run build:win
npm run build:win:release
npm run release:stage
```

Backend tests:

```powershell
cd backend
venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Release staging

```powershell
npm run build:win:release
npm run release:stage
```

- `build:win:release` creates a stable Windows installer in `release/`
- `release:stage` copies the stable `.exe` and matching `.blockmap` into `release-staged/`
- Preview artifacts such as `PigTex-<version>-preview.exe` are for QA only

## Publishing notes

- Choose and add an open-source license before pushing this repo public
- Do not commit real `.env` files, signing material, local databases, or packaged installers
- Keep private prompt/data packs outside this repository
