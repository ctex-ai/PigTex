#!/usr/bin/env python3
"""
Synthetic memory continuity check for /api/v1/chat/completions.

Flow:
1) Send a fact to remember.
2) Ask model to recall the fact in same conversation.
3) Mark pass/fail based on expected tokens in reply.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import uuid
from typing import Any, Dict, List

import httpx


def extract_content(payload: Dict[str, Any]) -> str:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else None
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content
            delta = first.get("delta")
            if isinstance(delta, dict):
                content = delta.get("content")
                if isinstance(content, str):
                    return content
    direct = payload.get("content")
    if isinstance(direct, str):
        return direct
    return ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Synthetic memory continuity check")
    parser.add_argument("--base-url", default=os.getenv("PIGTEX_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--auth-token", default=os.getenv("PIGTEX_AUTH_TOKEN", ""))
    parser.add_argument(
        "--api-key",
        "--texapi-key",
        dest="api_key",
        default=os.getenv("PIGTEX_API_KEY") or os.getenv("PIGTEX_TEXAPI_KEY", ""),
        help="Provider API key (legacy alias: --texapi-key)",
    )
    parser.add_argument(
        "--api-base-url",
        "--texapi-base-url",
        dest="api_base_url",
        default=os.getenv("PIGTEX_API_BASE_URL") or os.getenv("PIGTEX_TEXAPI_BASE_URL", ""),
        help="Provider base URL (legacy alias: --texapi-base-url)",
    )
    parser.add_argument(
        "--api-provider",
        default=os.getenv("PIGTEX_API_PROVIDER", ""),
        help="Provider name: openai | anthropic | gemini | custom",
    )
    parser.add_argument("--model", default=os.getenv("PIGTEX_MODEL", "gpt-4.1-mini"))
    parser.add_argument("--name", default="Minh")
    parser.add_argument("--editor", default="Neovim")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--output-json", default="")
    return parser.parse_args()


async def run_check(args: argparse.Namespace) -> Dict[str, Any]:
    if not args.auth_token:
        raise SystemExit("--auth-token is required (or set PIGTEX_AUTH_TOKEN)")
    if not args.api_key:
        raise SystemExit("--api-key is required (or set PIGTEX_API_KEY)")

    endpoint = f"{args.base_url.rstrip('/')}/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {args.auth_token}",
        "Content-Type": "application/json",
        "X-Request-ID": str(uuid.uuid4()),
        "X-API-Key": args.api_key,
    }
    if args.api_base_url:
        headers["X-API-Base-URL"] = args.api_base_url
    if args.api_provider:
        headers["X-API-Provider"] = args.api_provider

    remember_message = (
        f"Please remember for later: my name is {args.name} and my favorite editor is {args.editor}."
    )
    recall_message = "What is my name and favorite editor? Reply in one short sentence."

    payload_common = {
        "model": args.model,
        "stream": False,
        "temperature": 0.2,
        "use_memory": True,
        "use_knowledge": True,
        "use_facts": True,
        "use_history": True,
    }

    started_at = time.perf_counter()
    async with httpx.AsyncClient(timeout=args.timeout_seconds) as client:
        # Turn 1: save memory
        p1 = dict(payload_common)
        p1["messages"] = [{"role": "user", "content": remember_message}]
        r1 = await client.post(endpoint, headers=headers, json=p1)
        if r1.status_code != 200:
            return {
                "ok": False,
                "stage": "remember",
                "status_code": r1.status_code,
                "error": r1.text[:800],
            }
        d1 = r1.json()
        conversation_id = d1.get("conversation_id")
        if not isinstance(conversation_id, str) or not conversation_id:
            return {
                "ok": False,
                "stage": "remember",
                "status_code": 200,
                "error": "missing_conversation_id",
            }

        # Turn 2: recall memory
        p2 = dict(payload_common)
        p2["conversation_id"] = conversation_id
        p2["messages"] = [{"role": "user", "content": recall_message}]
        r2 = await client.post(endpoint, headers=headers, json=p2)
        if r2.status_code != 200:
            return {
                "ok": False,
                "stage": "recall",
                "status_code": r2.status_code,
                "conversation_id": conversation_id,
                "error": r2.text[:800],
            }
        d2 = r2.json()
        reply_text = extract_content(d2)

    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    expected_tokens = [args.name.strip().lower(), args.editor.strip().lower()]
    normalized_reply = (reply_text or "").lower()
    missing = [token for token in expected_tokens if token and token not in normalized_reply]
    ok = len(missing) == 0

    return {
        "ok": ok,
        "stage": "complete",
        "status_code": 200,
        "model": args.model,
        "conversation_id": conversation_id,
        "elapsed_ms": elapsed_ms,
        "reply_preview": (reply_text or "")[:400],
        "missing_expected_tokens": missing,
    }


async def main_async() -> int:
    args = parse_args()
    result = await run_check(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"saved_report: {args.output_json}")
    return 0 if result.get("ok") else 1


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
