# Contributing to PigTex

## Before You Start

PigTex in this repository is a desktop-only public source tree. Contributions should stay inside that scope.

Do not add:

- private prompt/data packs,
- deployment infrastructure from private environments,
- production secrets or real credentials,
- packaged installers, local databases, logs, or generated caches,
- unrelated website or internal mono-repo material.

## Good First Step

Before making a large change, open an issue or start a discussion so the direction is aligned early.

This is especially important for:

- architecture changes,
- new external dependencies,
- large UI rewrites,
- backend API shape changes,
- release or update-flow changes.

## Development Setup

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

## Required Checks

Run the relevant checks before opening a pull request.

Frontend:

```powershell
npm run lint:security
npm test
```

Backend:

```powershell
cd backend
python -m unittest discover -s tests -v
```

If your change is documentation-only, say so clearly in the pull request.

## Pull Request Guidelines

Keep pull requests focused and reviewable.

Preferred:

- one logical change per PR,
- clear title and short summary,
- tests added or updated when behavior changes,
- migration or release impact called out explicitly,
- screenshots or recordings for UI changes when useful.

Avoid:

- mixing refactors with unrelated behavior changes,
- committing generated artifacts,
- sneaking in config or dependency churn without explanation,
- force-pushing over someone else’s work in a shared branch without coordination.

## Coding Expectations

When contributing code:

- preserve the existing desktop-only public boundary,
- follow the current project structure and naming style,
- prefer small, explicit changes over broad rewrites,
- keep comments concise and useful,
- remove dead code and stale copy when you touch adjacent areas,
- do not commit placeholder secrets that look real.

## Security Expectations

- Never report vulnerabilities in public issues.
- Follow the process in [SECURITY.md](./SECURITY.md).
- Never commit `.env` files, tokens, certificates, signing materials, or private operational docs.

## UI and UX Changes

For UI changes:

- preserve the current product tone,
- avoid introducing obvious placeholder copy,
- keep desktop flows coherent in both English and Vietnamese where applicable,
- do not add heavy assets without a clear reason.

## Commit Style

Use short, descriptive commit messages in the imperative mood.

Examples:

- `Add update manifest validation`
- `Fix desktop auth persistence regression`
- `Polish public repository docs`

## Review Standard

The maintainers may reject contributions that are technically correct but not aligned with the repository boundary, release quality, or security posture.

That is normal. The goal is a clean public desktop repository, not maximum change volume.
