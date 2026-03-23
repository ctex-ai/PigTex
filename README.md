<p align="center">
  <img src="./assets/pigtex_logo.png" alt="PigTex logo" width="180" />
</p>

<h1 align="center">PigTex Desktop</h1>

<p align="center">
  <strong>Desktop AI workstation for focused chat, workspace memory, secure credentials, and release-grade Windows delivery.</strong>
</p>

<p align="center">
  Electron + React renderer, FastAPI backend, website-served desktop updates, and a public repository curated specifically for desktop contributors.
</p>

<p align="center">
  <a href="https://github.com/ctex-ai/PigTex/releases/latest">
    <img src="https://img.shields.io/github/v/release/ctex-ai/PigTex?display_name=tag&label=Release&color=2563EB" alt="Latest release" />
  </a>
  <a href="https://github.com/ctex-ai/PigTex/actions/workflows/ci.yml">
    <img src="https://github.com/ctex-ai/PigTex/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI status" />
  </a>
  <a href="./LICENSE">
    <img src="https://img.shields.io/github/license/ctex-ai/PigTex?color=7C3AED" alt="MIT license" />
  </a>
  <img src="https://img.shields.io/badge/Platform-Windows%2010%2B-0A66C2?logo=windows11&logoColor=white" alt="Windows 10+" />
  <img src="https://img.shields.io/badge/Electron-40-1F2937?logo=electron&logoColor=9FEAF9" alt="Electron 40" />
  <img src="https://img.shields.io/badge/React-18-111827?logo=react&logoColor=61DAFB" alt="React 18" />
  <img src="https://img.shields.io/badge/FastAPI-Backend-059669?logo=fastapi&logoColor=white" alt="FastAPI backend" />
</p>

<p align="center">
  <a href="https://github.com/ctex-ai/PigTex/releases/latest"><strong>Download Latest Release</strong></a>
  ·
  <a href="./.github/CONTRIBUTING.md"><strong>Contributing</strong></a>
  ·
  <a href="./.github/SECURITY.md"><strong>Security</strong></a>
  ·
  <a href="./docs/trust-policy.md"><strong>Trust Policy</strong></a>
</p>

<p align="center">
  <a href="https://texapi.dev" target="_blank" rel="noopener noreferrer">
    <img src="./assets/texapi_logo.png" alt="TexAPI partner logo" height="42" />
  </a>
</p>

<p align="center">
  <sub><strong>Integrated partner:</strong> <a href="https://texapi.dev" target="_blank" rel="noopener noreferrer">TexAPI</a> is an API gateway provider that gives PigTex access to a broad model catalog through a single API key, with managed routing and BYOK-friendly endpoint control.</sub>
</p>

> [!IMPORTANT]
> This repository is the public desktop-only source tree for PigTex.
> It intentionally excludes the marketing website, deployment infrastructure, private prompt/data packs, local databases, packaged installers, and all real secrets.

## Why PigTex

<table>
  <tr>
    <td width="33%" valign="top">
      <strong>Workspace-aware memory</strong><br />
      System rules and workspace rules can be kept separate so longer-running desktop work stays organized.
    </td>
    <td width="33%" valign="top">
      <strong>Flexible model routing</strong><br />
      Connect through TexAPI or switch to direct providers with user-managed endpoints, models, and credentials.
    </td>
    <td width="33%" valign="top">
      <strong>Desktop-native release flow</strong><br />
      Windows packaging, release staging, and production-safe desktop delivery are part of the public tree.
    </td>
  </tr>
  <tr>
    <td width="33%" valign="top">
      <strong>Privacy-conscious defaults</strong><br />
      Secure local credential storage is used on supported platforms, and cloud backup remains opt-in.
    </td>
    <td width="33%" valign="top">
      <strong>Bilingual product surface</strong><br />
      Core desktop flows are built for Vietnamese and English users instead of shipping placeholder localization.
    </td>
    <td width="33%" valign="top">
      <strong>Public-repo discipline</strong><br />
      Community docs, release guards, and a curated file tree keep the repository presentable for external readers.
    </td>
  </tr>
</table>

## Product Tour

<table>
  <tr>
    <td width="50%" valign="top">
      <img src="./assets/chat.gif" alt="PigTex chat workflow demo" width="100%" />
      <strong>Focused chat workflow</strong><br />
      A desktop-first assistant surface built for longer sessions instead of throwaway prompts.
    </td>
    <td width="50%" valign="top">
      <img src="./assets/file.gif" alt="PigTex file workflow demo" width="100%" />
      <strong>Workspace and file context</strong><br />
      Local project context, editor flow, and structured desktop work stay inside one app.
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <img src="./assets/endpoint.gif" alt="PigTex endpoint configuration demo" width="100%" />
      <strong>Endpoint and model control</strong><br />
      Switch between TexAPI and direct providers with user-managed credentials and endpoints.
    </td>
    <td width="50%" valign="top">
      <img src="./assets/backup.gif" alt="PigTex backup workflow demo" width="100%" />
      <strong>Opt-in backup and sync</strong><br />
      Cloud backup stays explicit, visible, and separate from local-first desktop usage.
    </td>
  </tr>
</table>

## Architecture

```mermaid
flowchart LR
    UI[React Renderer] --> IPC[Electron Main and Preload]
    IPC --> API[FastAPI Backend]
    API --> LOCAL[Local storage and workspace state]
    API --> CLOUD[Opt-in cloud backup and sync]
    IPC --> UPDATE[Website-served desktop updates]
    IPC --> SECURE[OS secure credential storage]
```

## Quick Start

### 1. Install the renderer

```powershell
npm ci
Copy-Item .env.example .env
```

### 2. Install the backend

```powershell
cd backend
python -m venv venv
.\venv\Scripts\pip.exe install -r requirements.txt
Copy-Item .env.example .env
```

Use `.env.example` in the repository root for the renderer and `backend/.env.example` for the backend.

### 3. Run the core checks

```powershell
npm run lint:security
npm test
npm run build:electron
cd backend
venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Release Workflow

Stable release builds target the production backend root. Before packaging, set:

```powershell
$env:VITE_PIGTEX_API_BASE='https://pigtex.id.vn'
```

Then run:

```powershell
npm run build:win:release
npm run release:stage
```

- `build:win:release` creates the stable Windows installer in `/release`
- `release:stage` stages the stable `.exe` and matching `.blockmap` for the production download host
- Stable packaged builds check the hosted desktop manifest and open the website download flow when a newer installer is available

## Repository Layout

| Path | Purpose |
| --- | --- |
| `src/` | React renderer UI and desktop-facing frontend logic |
| `electron/` | Electron main-process and preload code |
| `backend/` | FastAPI backend used by the desktop app |
| `assets/` | Source-managed logos and desktop assets |
| `public/` | Public runtime assets served by Vite |
| `scripts/build/` | Packaging helpers, RCEdit utilities, and build guards |
| `scripts/release/` | Release staging, validation, and GitHub publish helpers |
| `scripts/signing/` | Windows signing hooks and GA signing validation |
| `scripts/dev/` | Local development launch helpers |
| `docs/` | Public-safe trust and contributor-facing documentation |

## Public Repo Scope

This repository includes:

- Electron renderer and main-process code
- FastAPI backend used by the desktop app
- Public-safe docs, tests, build configs, and example environment files

This repository does not include:

- Website and download-manifest source
- `deploy/`, `ops/`, or other private infrastructure material
- Real `.env` files, local databases, logs, `node_modules`, or packaged installers
- Private prompt/data packs and internal operating material

<details>
  <summary><strong>Optional private prompt packs</strong></summary>

This public repo can run without the private prompt/data packs. If you keep those packs outside the repo, point the backend at them with:

```powershell
PIGTEX_DATA_DIR=
PIGTEX_PROMPT_PACKS_DIR=
PIGTEX_SKILL_FOUNDRY_DIR=
```

- `PIGTEX_DATA_DIR` or `PIGTEX_PROMPT_PACKS_DIR`: external directory that contains `system_prompts/`, `enhancement_rules/`, and related JSON packs
- `PIGTEX_SKILL_FOUNDRY_DIR`: external prompt-catalog storage directory

If these variables are not set, the backend degrades safely and uses local per-device storage where needed.

</details>

## Community and Trust

- [Contributing guide](./.github/CONTRIBUTING.md)
- [Security policy](./.github/SECURITY.md)
- [Code of conduct](./.github/CODE_OF_CONDUCT.md)
- [Trust policy](./docs/trust-policy.md)
- [Latest release](https://github.com/ctex-ai/PigTex/releases/latest)

## License

Licensed under [MIT](./LICENSE).
