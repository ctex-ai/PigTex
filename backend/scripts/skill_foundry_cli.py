"""
PigTex Skill Foundry CLI.

Ingest raw markdown/json skill repositories, score them, and compile
a draft skill registry for review, then publish an active runtime registry.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "data" / "system_prompts").exists():
            return parent
    raise RuntimeError("Cannot locate repo root containing data/system_prompts")


REPO_ROOT = _find_repo_root()
BACKEND_ROOT = REPO_ROOT / "App_desktop" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.prompting import LLMFoundryJudge, SkillFoundry, SkillJudgeConfig, build_foundry_from_env  # noqa: E402


def _build_cli_foundry(args: argparse.Namespace) -> SkillFoundry:
    if args.judge_model and args.judge_api_key and args.judge_api_base_url:
        judge = LLMFoundryJudge(
            SkillJudgeConfig(
                model=args.judge_model,
                api_key=args.judge_api_key,
                api_base_url=args.judge_api_base_url,
            )
        )
        return SkillFoundry(judge=judge)
    return build_foundry_from_env()


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_compile(args: argparse.Namespace) -> int:
    foundry = _build_cli_foundry(args)
    input_path = Path(args.input) if args.input else foundry.incoming_path()
    if not input_path.is_absolute():
        input_path = (foundry.incoming_path() / input_path).resolve()
    if not input_path.exists():
        raise SystemExit(f"Input path not found: {input_path}")

    report = foundry.compile_from_path(
        input_path,
        dry_run=bool(args.dry_run),
        max_files=args.max_files,
    )
    _print_json(report)
    return 0


def cmd_registry(args: argparse.Namespace) -> int:
    foundry = build_foundry_from_env()
    _print_json(
        {
            "summary": foundry.registry_summary(),
            "registry": foundry.load_registry(),
            "draft_registry": foundry.load_draft_registry(),
            "skill_store": foundry.load_skill_store(),
            "catalog": foundry.load_catalog(),
            "releases": foundry.list_releases(),
        }
    )
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    foundry = build_foundry_from_env()
    keywords = [item.strip() for item in (args.keywords or "").split(",") if item.strip()]
    matches = foundry.resolve_matches(
        user_message=args.message,
        detected_intent=args.intent,
        keywords=keywords or None,
    )
    _print_json(
        {
            "intent": args.intent,
            "keywords": keywords,
            "matches": matches,
            "formatted": foundry.format_runtime_skills(matches),
        }
    )
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    foundry = build_foundry_from_env()
    payload = foundry.publish_draft(
        released_by=args.released_by,
        note=args.note or "",
        force=bool(args.force),
    )
    _print_json(payload)
    return 0


def cmd_cleanup_rejected(args: argparse.Namespace) -> int:
    foundry = build_foundry_from_env()
    payload = foundry.cleanup_rejected_artifacts(args.days)
    _print_json(payload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PigTex Skill Foundry CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    compile_parser = subparsers.add_parser("compile", help="Compile raw skill files into draft registry")
    compile_parser.add_argument("--input", help="Relative path inside data/skill_foundry/incoming or absolute path")
    compile_parser.add_argument("--dry-run", action="store_true", help="Analyze only, do not write registry files")
    compile_parser.add_argument("--max-files", type=int, default=None, help="Optional max number of files to scan")
    compile_parser.add_argument("--judge-model", default=os.getenv("PIGTEX_SKILL_FOUNDRY_JUDGE_MODEL"))
    compile_parser.add_argument("--judge-api-key", default=os.getenv("PIGTEX_SKILL_FOUNDRY_API_KEY"))
    compile_parser.add_argument("--judge-api-base-url", default=os.getenv("PIGTEX_SKILL_FOUNDRY_API_BASE_URL"))
    compile_parser.set_defaults(func=cmd_compile)

    registry_parser = subparsers.add_parser("registry", help="Show runtime registry and catalog")
    registry_parser.set_defaults(func=cmd_registry)

    publish_parser = subparsers.add_parser("publish", help="Promote current draft registry to active runtime registry")
    publish_parser.add_argument("--released-by", required=True)
    publish_parser.add_argument("--note", default="")
    publish_parser.add_argument("--force", action="store_true", help="Bypass publish gate blockers")
    publish_parser.set_defaults(func=cmd_publish)

    cleanup_parser = subparsers.add_parser("cleanup-rejected", help="Purge rejected raw artifacts older than N days")
    cleanup_parser.add_argument("--days", type=int, required=True, help="Delete rejected artifacts older than this many days")
    cleanup_parser.set_defaults(func=cmd_cleanup_rejected)

    resolve_parser = subparsers.add_parser("resolve", help="Resolve runtime skill matches for a user message")
    resolve_parser.add_argument("--message", required=True)
    resolve_parser.add_argument("--intent", default=None)
    resolve_parser.add_argument("--keywords", default="", help="Comma-separated keyword list")
    resolve_parser.set_defaults(func=cmd_resolve)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
