#!/usr/bin/env python3
"""
Benchmark /api/v1/chat/completions streaming quality.

Metrics:
- TTFT (time to first token)
- End-to-end latency
- Chunk jitter (p95 inter-chunk gap)
- Estimated output tokens/sec
- Error rate

Example:
  python scripts/benchmark_v1_stream.py ^
    --base-url http://127.0.0.1:8000 ^
    --auth-token <JWT> ^
    --api-key <PROVIDER_API_KEY> ^
    --api-provider openai ^
    --model gpt-4.1-mini ^
    --runs 8
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

import httpx

if hasattr(sys.stdout, "reconfigure"):
    # Avoid Windows console encoding crashes when upstream error text contains non-ASCII.
    sys.stdout.reconfigure(errors="backslashreplace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(errors="backslashreplace")


def percentile(values: List[float], q: float) -> float:
    if not values:
        return -1.0
    if len(values) == 1:
        return float(values[0])
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(ordered[lo])
    ratio = pos - lo
    return float(ordered[lo] * (1.0 - ratio) + ordered[hi] * ratio)


def extract_content(payload: Dict[str, Any]) -> str:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else None
        if isinstance(first, dict):
            delta = first.get("delta")
            if isinstance(delta, dict):
                content = delta.get("content")
                if isinstance(content, str):
                    return content
            message = first.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content
    direct = payload.get("content")
    if isinstance(direct, str):
        return direct
    return ""


@dataclass
class RunResult:
    run_index: int
    ok: bool
    status_code: int
    request_id: str
    conversation_id: str
    ttft_ms: float
    total_ms: float
    chunk_count: int
    jitter_p95_ms: float
    output_chars: int
    est_tokens: float
    est_tokens_per_sec: float
    error: str


async def benchmark_once(
    client: httpx.AsyncClient,
    base_url: str,
    auth_token: str,
    api_key: str,
    api_base_url: Optional[str],
    api_provider: Optional[str],
    model: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
    use_memory: bool,
    run_index: int,
) -> RunResult:
    endpoint = f"{base_url.rstrip('/')}/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
        "X-Request-ID": str(uuid.uuid4()),
    }
    if api_key:
        headers["X-API-Key"] = api_key
    if api_base_url:
        headers["X-API-Base-URL"] = api_base_url
    if api_provider:
        headers["X-API-Provider"] = api_provider

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "use_memory": use_memory,
        "use_knowledge": True,
        "use_facts": True,
        "use_history": True,
    }

    started = time.perf_counter()
    first_content_at: Optional[float] = None
    content_timestamps: List[float] = []
    response_text_parts: List[str] = []
    request_id = ""
    conversation_id = ""

    try:
        async with client.stream("POST", endpoint, headers=headers, json=payload, timeout=300.0) as response:
            request_id = response.headers.get("X-Request-ID", "")
            conversation_id = response.headers.get("X-Conversation-ID", "")
            if response.status_code != 200:
                body_preview = (await response.aread()).decode("utf-8", errors="replace")[:500]
                return RunResult(
                    run_index=run_index,
                    ok=False,
                    status_code=response.status_code,
                    request_id=request_id,
                    conversation_id=conversation_id,
                    ttft_ms=-1.0,
                    total_ms=(time.perf_counter() - started) * 1000.0,
                    chunk_count=0,
                    jitter_p95_ms=-1.0,
                    output_chars=0,
                    est_tokens=0.0,
                    est_tokens_per_sec=0.0,
                    error=f"http_{response.status_code}: {body_preview}",
                )

            async for line in response.aiter_lines():
                if not line:
                    continue
                line = line.strip()
                if line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                if not data.startswith("{"):
                    # Plain-text style stream chunk
                    content = data
                else:
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    err = obj.get("error")
                    if err:
                        return RunResult(
                            run_index=run_index,
                            ok=False,
                            status_code=200,
                            request_id=request_id,
                            conversation_id=conversation_id,
                            ttft_ms=-1.0,
                            total_ms=(time.perf_counter() - started) * 1000.0,
                            chunk_count=len(content_timestamps),
                            jitter_p95_ms=-1.0,
                            output_chars=sum(len(p) for p in response_text_parts),
                            est_tokens=0.0,
                            est_tokens_per_sec=0.0,
                            error=f"stream_error: {err}",
                        )
                    content = extract_content(obj)
                    if not conversation_id:
                        cid = obj.get("conversation_id")
                        if isinstance(cid, str):
                            conversation_id = cid

                if not content:
                    continue

                now = time.perf_counter()
                if first_content_at is None:
                    first_content_at = now
                content_timestamps.append(now)
                response_text_parts.append(content)

    except Exception as exc:
        return RunResult(
            run_index=run_index,
            ok=False,
            status_code=0,
            request_id=request_id,
            conversation_id=conversation_id,
            ttft_ms=-1.0,
            total_ms=(time.perf_counter() - started) * 1000.0,
            chunk_count=len(content_timestamps),
            jitter_p95_ms=-1.0,
            output_chars=sum(len(p) for p in response_text_parts),
            est_tokens=0.0,
            est_tokens_per_sec=0.0,
            error=str(exc),
        )

    ended = time.perf_counter()
    total_ms = (ended - started) * 1000.0
    ttft_ms = ((first_content_at - started) * 1000.0) if first_content_at else -1.0
    gaps_ms = [
        (content_timestamps[i] - content_timestamps[i - 1]) * 1000.0
        for i in range(1, len(content_timestamps))
    ]
    jitter_p95_ms = percentile(gaps_ms, 0.95) if gaps_ms else -1.0
    output_text = "".join(response_text_parts)
    est_tokens = len(output_text.split()) * 1.3
    active_seconds = max(0.001, ((ended - first_content_at) if first_content_at else (ended - started)))
    est_tps = est_tokens / active_seconds

    return RunResult(
        run_index=run_index,
        ok=True,
        status_code=200,
        request_id=request_id,
        conversation_id=conversation_id,
        ttft_ms=ttft_ms,
        total_ms=total_ms,
        chunk_count=len(content_timestamps),
        jitter_p95_ms=jitter_p95_ms,
        output_chars=len(output_text),
        est_tokens=est_tokens,
        est_tokens_per_sec=est_tps,
        error="",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark PigTex v1 streaming endpoint")
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
    parser.add_argument("--prompt", default="Please write a short summary about reliable streaming APIs.")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--no-use-memory", action="store_true", help="Disable memory context features")
    parser.add_argument("--output-json", default="")
    return parser


async def main_async() -> int:
    args = build_parser().parse_args()
    if not args.auth_token:
        raise SystemExit("--auth-token is required (or set PIGTEX_AUTH_TOKEN)")
    if not args.api_key:
        raise SystemExit("--api-key is required (or set PIGTEX_API_KEY)")

    async with httpx.AsyncClient() as client:
        results: List[RunResult] = []
        for idx in range(1, args.runs + 1):
            result = await benchmark_once(
                client=client,
                base_url=args.base_url,
                auth_token=args.auth_token,
                api_key=args.api_key,
                api_base_url=args.api_base_url or None,
                api_provider=args.api_provider or None,
                model=args.model,
                prompt=args.prompt,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                use_memory=not bool(args.no_use_memory),
                run_index=idx,
            )
            results.append(result)
            status = "OK" if result.ok else "FAIL"
            print(
                f"[run {idx:02d}] {status} "
                f"ttft={result.ttft_ms:.1f}ms total={result.total_ms:.1f}ms "
                f"chunks={result.chunk_count} jitter_p95={result.jitter_p95_ms:.1f}ms "
                f"tps={result.est_tokens_per_sec:.1f}"
            )
            if result.error:
                print(f"         error={result.error}")

    ok_results = [r for r in results if r.ok]
    fail_count = len(results) - len(ok_results)
    print("\n=== Summary ===")
    print(f"runs={len(results)} ok={len(ok_results)} fail={fail_count} fail_rate={fail_count / max(1, len(results)):.2%}")

    if ok_results:
        ttft_values = [r.ttft_ms for r in ok_results if r.ttft_ms >= 0]
        total_values = [r.total_ms for r in ok_results if r.total_ms >= 0]
        jitter_values = [r.jitter_p95_ms for r in ok_results if r.jitter_p95_ms >= 0]
        tps_values = [r.est_tokens_per_sec for r in ok_results if r.est_tokens_per_sec >= 0]

        print(f"ttft_ms: mean={statistics.mean(ttft_values):.1f} p50={percentile(ttft_values, 0.50):.1f} p95={percentile(ttft_values, 0.95):.1f}")
        print(f"total_ms: mean={statistics.mean(total_values):.1f} p50={percentile(total_values, 0.50):.1f} p95={percentile(total_values, 0.95):.1f}")
        if jitter_values:
            print(f"jitter_p95_ms: mean={statistics.mean(jitter_values):.1f} p50={percentile(jitter_values, 0.50):.1f} p95={percentile(jitter_values, 0.95):.1f}")
        if tps_values:
            print(f"tokens_per_sec: mean={statistics.mean(tps_values):.1f} p50={percentile(tps_values, 0.50):.1f} p95={percentile(tps_values, 0.95):.1f}")

    if args.output_json:
        payload = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "base_url": args.base_url,
            "api_provider": args.api_provider or "",
            "model": args.model,
            "runs": len(results),
            "results": [asdict(r) for r in results],
        }
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\nSaved report: {args.output_json}")

    return 0 if fail_count == 0 else 1


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
