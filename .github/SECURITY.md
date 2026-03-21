# Security Policy

## Scope

This repository is the public desktop-only source tree for PigTex. It includes:

- the Electron desktop client,
- the React renderer,
- the FastAPI backend used by the desktop app,
- public-safe build scripts, tests, and docs.

It intentionally excludes private prompt/data packs, deployment infrastructure, production secrets, release binaries, and internal operating material.

## Supported Versions

Security fixes are applied to the newest maintained code first.

| Version | Supported |
| --- | --- |
| `main` | Yes |
| Latest tagged desktop release | Yes |
| Older commits and untagged snapshots | No |

## Reporting a Vulnerability

Do not open public GitHub issues for security reports.

Use one of these paths instead:

1. Use GitHub private vulnerability reporting for this repository if the option is available.
2. If private reporting is not available, contact the repository maintainers privately through the repository owner or organization contact channel before public disclosure.

Please include:

- a short description of the issue,
- affected file paths, routes, or features,
- reproduction steps or a proof of concept,
- impact assessment,
- any suggested fix or mitigation if you have one.

## Response Expectations

The maintainers aim to:

- acknowledge a valid report within 5 business days,
- provide an initial triage outcome as soon as practical,
- coordinate a fix and disclosure timeline based on severity and exploitability.

Response times are best-effort and may vary depending on maintainer availability.

## Disclosure Expectations

- Give the maintainers a reasonable chance to investigate and fix the issue before public disclosure.
- Avoid publishing exploit details while a fix is still being prepared.
- Keep user data, tokens, credentials, and private infrastructure details out of public reports.

## Out of Scope

The following are usually out of scope unless they create a concrete exploit path:

- missing hardening on local development-only setups,
- generic best-practice suggestions without a demonstrated impact,
- vulnerabilities in software not shipped or maintained in this repository,
- issues that require access to private infrastructure, secrets, or excluded internal repositories that are not part of this public tree.
