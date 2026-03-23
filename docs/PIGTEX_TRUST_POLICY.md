# PigTex Trust Policy

Last updated: 2026-03-12
Audience: engineering, product, ops, and anyone making customer-facing trust claims.

## 1. Purpose

This document defines PigTex's minimum trust posture for individual-user production releases.

## 2. Security baseline already required

PigTex already requires these conditions in production:

- strong non-default JWT secret,
- authenticated Redis URL when Redis-backed production features are enabled,
- Electron secure storage for locally saved API keys on supported platforms,
- `contextIsolation: true`,
- `nodeIntegration: false`.

Reference files:

- [config.py](../backend/app/config.py)
- [main.ts](../electron/main.ts)

## 3. Dependency and CVE review policy

Every release candidate must include a documented dependency review.

Minimum commands:

```powershell
cd Website
npm audit --audit-level=high

cd ..\App_desktop
npm audit --audit-level=high

cd backend
pip list
```

If `pip-audit` is available in the release environment, run:

```powershell
pip-audit -r requirements.txt
```

Release owner rule:

- no known critical issue is accepted without an owner, written rationale, and deadline.

## 4. Crash reporting posture

Current policy:

- crash reporting is `disabled by default` until PigTex publishes explicit disclosure for what crash data is collected,
- any future crash reporting must be opt-in or clearly disclosed before activation,
- do not claim "no telemetry" if crash reporting or similar diagnostics are ever enabled.

Operational implication:

- before enabling any crash reporting SDK, update privacy copy and this policy first.

## 5. Code signing policy

Current policy:

- unsigned Windows artifacts are acceptable only for pre-GA testing or controlled distribution,
- public Windows GA requires code signing,
- any unsigned release must be labeled clearly as preview/test distribution and must not be marketed as a polished GA artifact.

## 6. Customer-facing trust claims allowed now

Safe claims:

- PigTex is privacy-conscious.
- PigTex separates local device storage from server-side service data.
- Cloud backup is opt-in.
- PigTex uses secure local storage for saved API keys on supported platforms.

Unsafe claims unless proven for the shipped artifact:

- "100% local-only"
- "everything stays on your device"
- "zero telemetry"
- "fully offline" as a universal statement

## 7. Current decisions

These checklist items are now decided:

- dependency/CVE review is mandatory per release,
- crash reporting remains disabled until separately disclosed,
- code signing is required before public Windows GA.
