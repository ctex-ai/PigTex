# PigTex Desktop

This is the public desktop-only source tree for PigTex.

It intentionally excludes the marketing website, deployment templates, private prompt/data packs, local databases, release binaries, and all real secrets.

## Included

- Electron renderer and main-process code
- FastAPI backend used by the desktop app
- Public-safe docs, tests, build configs, and example environment files

## Not included

- Website and download-manifest source
- Repo-level `data/` prompt packs and optional prompt-catalog registries
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
- `PIGTEX_SKILL_FOUNDRY_DIR`: external prompt-catalog storage directory

If these variables are not set, the backend degrades safely and uses local per-device storage where needed.

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
npm run release:publish
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
npm run release:publish
```

- `build:win:release` creates a stable Windows installer in `release/`
- `release:stage` copies the stable `.exe`, matching `.blockmap`, and `latest.yml` into `release-staged/`
- `release:publish` uploads the staged stable assets to the `ctex-ai/PigTex` GitHub Release for that version
- Preview artifacts such as `PigTex-<version>-preview.exe` are for QA only
- Stable packaged builds check GitHub Releases for updates and install newer Windows releases in-app

## Publishing notes

- Licensed under [MIT](./LICENSE)
- Do not commit real `.env` files, signing material, local databases, or packaged installers
- Keep private prompt/data packs outside this repository

## Community

- [Contributing guide](./.github/CONTRIBUTING.md)
- [Security policy](./.github/SECURITY.md)
- [Code of conduct](./.github/CODE_OF_CONDUCT.md)
