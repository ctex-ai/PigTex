"""Runtime guardrails for PigTex prompt orchestration."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable

from .packs import PromptPackStore

logger = logging.getLogger(__name__)

_RUNTIME_BLOCK_TITLE = "## PigTex Runtime Contract"
_RUNTIME_MAX_CHARS = 2400
_DANGEROUS_RUNTIME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(all\s+)?previous\s+(instructions|rules|messages|system)", re.I),
    re.compile(r"(reveal|show|repeat|print).*(system\s+prompt|internal\s+instructions?)", re.I),
    re.compile(r"(override|replace)\s+(the\s+)?system", re.I),
    re.compile(r"\b(act\s+as|pretend\s+you\s+are|you\s+are\s+now)\b", re.I),
    re.compile(r"\bDAN\b", re.I),
)
_INTERNAL_PAYLOAD_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\[pigtex_tool_result\]", re.I),
    re.compile(r"\[file_agent_context\]", re.I),
    re.compile(r"```(?:\s*)(?:pigtex_fs|file_agent)", re.I),
    re.compile(r"<pigtex(?:_|\.)(?:write|create|patch|read|delete|mkdir|rename|rm|ls|list)\b", re.I),
    re.compile(r"<read_code>|<write_code>", re.I),
)
_STREAM_HOLD_MAX_CHARS = 96
_STREAM_HOLD_TAIL_CHARS = 28
_STREAM_SENTENCE_BOUNDARY_RE = re.compile(r"([.!?。！？]+(?:\s|$))")


def sanitize_runtime_instruction(raw_text: str) -> str:
    text = " ".join((raw_text or "").replace("\x00", "").split()).strip()
    if not text:
        return ""
    if len(text) > _RUNTIME_MAX_CHARS:
        text = text[:_RUNTIME_MAX_CHARS].rstrip()
    for pattern in _DANGEROUS_RUNTIME_PATTERNS:
        if pattern.search(text):
            logger.warning("Dropped unsafe runtime instruction: %s", pattern.pattern)
            return ""
    return text


def build_runtime_instruction_block(raw_text: str) -> str:
    text = sanitize_runtime_instruction(raw_text)
    if not text:
        return ""
    return f"{_RUNTIME_BLOCK_TITLE}\n{text}"


def is_internal_orchestration_payload(text: str | None) -> bool:
    normalized = (text or "").strip()
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in _INTERNAL_PAYLOAD_PATTERNS)


def _drop_matching_lines(lines: Iterable[str], pattern: str) -> list[str]:
    kept: list[str] = []
    for line in lines:
        try:
            if re.search(pattern, line):
                continue
        except re.error:
            kept.append(line)
            continue
        kept.append(line)
    return kept


def apply_output_filters(text: str, *, allow_internal_payload: bool = False) -> str:
    value = text or ""
    if not value:
        return value
    if allow_internal_payload and is_internal_orchestration_payload(value):
        return value

    filters = PromptPackStore.load_json_file("quality_filters", "output_filters.json")
    raw_filters = filters.get("filters", [])
    if not isinstance(raw_filters, list):
        return value

    current = value
    for item in raw_filters:
        if not isinstance(item, dict):
            continue
        patterns = item.get("patterns", [])
        if not isinstance(patterns, list):
            continue
        action = str(item.get("action", "")).strip().lower()
        for pattern in patterns:
            if not isinstance(pattern, str) or not pattern.strip():
                continue
            try:
                if action == "remove":
                    current = re.sub(pattern, "", current, flags=re.MULTILINE | re.DOTALL)
                elif action in {"flag_for_review", "flag_for_rewrite"}:
                    current = "\n".join(_drop_matching_lines(current.splitlines(), pattern))
            except re.error:
                logger.warning("Invalid output filter regex skipped: %s", pattern)

    current = re.sub(r"[ \t]+\n", "\n", current)
    current = re.sub(r"\n{3,}", "\n\n", current)
    return current.strip()


@dataclass
class StreamSanitizerState:
    raw_visible_text: str = ""
    filtered_visible_text: str = ""
    pending_text: str = ""


def sanitize_sse_event_block(event_text: str, state: StreamSanitizerState) -> str:
    """Sanitize one complete SSE event block while preserving SSE framing."""
    if not event_text:
        return ""

    output_blocks: list[str] = []
    current_block_lines: list[str] = []

    def _flush_current_block() -> None:
        if current_block_lines:
            output_blocks.append("\n".join(current_block_lines) + "\n\n")
            current_block_lines.clear()

    for original_line in event_text.splitlines():
        line = original_line.rstrip("\r")
        stripped = line.lstrip()
        if not stripped.startswith("data:"):
            current_block_lines.append(line)
            continue

        prefix_len = len(line) - len(stripped)
        prefix = line[:prefix_len]
        payload_text = stripped[5:].lstrip()

        if payload_text == "[DONE]":
            flushed = flush_stream_sanitizer(state)
            if flushed:
                _flush_current_block()
                output_blocks.append(flushed)
            current_block_lines.append(f"{prefix}data: [DONE]")
            continue

        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            current_block_lines.append(line)
            continue

        is_terminal = _is_terminal_stream_payload(payload)
        extracted_text = _extract_text_from_stream_payload(payload)
        safe_delta = ""
        if extracted_text:
            safe_delta = _consume_sanitized_stream_delta(
                state,
                extracted_text,
                force_flush=is_terminal,
            )
            _replace_text_in_stream_payload(payload, safe_delta)
            current_block_lines.append(f"{prefix}data: {json.dumps(payload, ensure_ascii=False)}")
            continue

        if is_terminal:
            flushed = flush_stream_sanitizer(state)
            if flushed:
                _flush_current_block()
                output_blocks.append(flushed)
        current_block_lines.append(f"{prefix}data: {json.dumps(payload, ensure_ascii=False)}")

    _flush_current_block()

    if not output_blocks:
        return ""
    return "".join(output_blocks)


def flush_stream_sanitizer(state: StreamSanitizerState) -> str:
    """Flush any held text into one synthetic SSE delta event."""
    safe_delta = _consume_sanitized_stream_delta(state, "", force_flush=True)
    if not safe_delta:
        return ""
    payload = {
        "choices": [
            {
                "index": 0,
                "delta": {"content": safe_delta},
                "finish_reason": None,
            }
        ]
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _consume_sanitized_stream_delta(
    state: StreamSanitizerState,
    delta_text: str,
    *,
    force_flush: bool = False,
) -> str:
    pending = f"{state.pending_text}{delta_text}"
    flushable, remainder = _split_flushable_stream_text(pending, force_flush=force_flush)
    state.pending_text = remainder
    if not flushable:
        return ""

    next_raw = f"{state.raw_visible_text}{flushable}"
    next_filtered = apply_output_filters(next_raw, allow_internal_payload=True)
    safe_delta = _safe_stream_suffix(state.filtered_visible_text, next_filtered)
    state.raw_visible_text = next_raw
    state.filtered_visible_text = next_filtered
    return safe_delta


def _split_flushable_stream_text(text: str, *, force_flush: bool) -> tuple[str, str]:
    if not text:
        return "", ""
    if force_flush:
        return text, ""

    newline_index = text.rfind("\n")
    if newline_index >= 0:
        return text[: newline_index + 1], text[newline_index + 1 :]

    sentence_matches = list(_STREAM_SENTENCE_BOUNDARY_RE.finditer(text))
    if sentence_matches:
        boundary = sentence_matches[-1].end()
        return text[:boundary], text[boundary:]

    if len(text) <= _STREAM_HOLD_MAX_CHARS:
        return "", text

    last_space = text.rfind(" ")
    if last_space <= 0:
        boundary = max(0, len(text) - _STREAM_HOLD_TAIL_CHARS)
        return text[:boundary], text[boundary:]

    return text[: last_space + 1], text[last_space + 1 :]


def _safe_stream_suffix(previous: str, current: str) -> str:
    if not current:
        return ""
    if current.startswith(previous):
        return current[len(previous):]
    common_prefix_len = 0
    max_common = min(len(previous), len(current))
    while common_prefix_len < max_common and previous[common_prefix_len] == current[common_prefix_len]:
        common_prefix_len += 1
    return current[common_prefix_len:]


def _extract_text_from_stream_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""

    payload_type = payload.get("type")
    if isinstance(payload_type, str):
        normalized_type = payload_type.strip().lower()
        if normalized_type == "content_block_delta":
            delta = payload.get("delta")
            if isinstance(delta, dict):
                value = delta.get("text")
                if isinstance(value, str):
                    return value
            if isinstance(delta, str):
                return delta
        if normalized_type in {"response.text.delta", "response.output_text.delta"}:
            delta = payload.get("delta")
            if isinstance(delta, str):
                return delta
        if normalized_type.endswith("output_text.delta"):
            delta = payload.get("delta")
            if isinstance(delta, str):
                return delta
        if normalized_type.endswith("message.delta"):
            delta = payload.get("delta")
            nested = _extract_text_from_payload_node(delta)
            if nested:
                return nested

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = first_choice.get("delta")
        nested = _extract_text_from_payload_node(delta)
        if nested:
            return nested
        message = first_choice.get("message")
        nested_message = _extract_text_from_payload_node(message)
        if nested_message:
            return nested_message
        choice_text = first_choice.get("text")
        if isinstance(choice_text, str):
            return choice_text

    return _extract_text_from_payload_node(payload)


def _extract_text_from_payload_node(node: Any) -> str:
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        parts: list[str] = []
        for item in node:
            extracted = _extract_text_from_payload_node(item)
            if extracted:
                parts.append(extracted)
        return "".join(parts)
    if not isinstance(node, dict):
        return ""

    for key in ("text", "delta", "output_text", "value", "token", "response"):
        value = node.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            nested_value = value.get("value")
            if isinstance(nested_value, str) and nested_value:
                return nested_value

    for key in ("content", "parts", "part", "item", "message", "output", "content_block", "candidates"):
        nested = _extract_text_from_payload_node(node.get(key))
        if nested:
            return nested
    return ""


def _replace_text_in_stream_payload(payload: Any, new_text: str) -> bool:
    if not isinstance(payload, dict):
        return False

    payload_type = payload.get("type")
    if isinstance(payload_type, str):
        normalized_type = payload_type.strip().lower()
        if normalized_type == "content_block_delta":
            delta = payload.get("delta")
            if isinstance(delta, dict):
                delta["text"] = new_text
                return True
            if isinstance(delta, str):
                payload["delta"] = new_text
                return True
        if normalized_type in {"response.text.delta", "response.output_text.delta"}:
            if isinstance(payload.get("delta"), str):
                payload["delta"] = new_text
                return True
        if normalized_type.endswith("output_text.delta"):
            if isinstance(payload.get("delta"), str):
                payload["delta"] = new_text
                return True
        if normalized_type.endswith("message.delta"):
            delta = payload.get("delta")
            if _replace_text_in_payload_node(delta, new_text):
                return True

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0] if isinstance(choices[0], dict) else None
        if isinstance(first_choice, dict):
            if _replace_text_in_payload_node(first_choice.get("delta"), new_text):
                return True
            if _replace_text_in_payload_node(first_choice.get("message"), new_text):
                return True
            if isinstance(first_choice.get("text"), str):
                first_choice["text"] = new_text
                return True

    return _replace_text_in_payload_node(payload, new_text)


def _replace_text_in_payload_node(node: Any, new_text: str) -> bool:
    if isinstance(node, list):
        replaced = False
        for item in node:
            if _replace_text_in_payload_node(item, new_text if not replaced else ""):
                replaced = True
        return replaced

    if not isinstance(node, dict):
        return False

    text_field = node.get("text")
    if isinstance(text_field, str):
        node["text"] = new_text
        return True
    if isinstance(text_field, dict) and isinstance(text_field.get("value"), str):
        text_field["value"] = new_text
        return True

    for key in ("delta", "output_text", "value", "token", "response"):
        value = node.get(key)
        if isinstance(value, str):
            node[key] = new_text
            return True

    content_value = node.get("content")
    if isinstance(content_value, str):
        node["content"] = new_text
        return True

    for key in ("content", "parts", "part", "item", "message", "output", "content_block", "candidates"):
        if _replace_text_in_payload_node(node.get(key), new_text):
            return True
    return False


def _is_terminal_stream_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("done") is True:
        return True

    payload_type = payload.get("type")
    if isinstance(payload_type, str):
        normalized_type = payload_type.strip().lower()
        if normalized_type in {
            "response.completed",
            "response.done",
            "response.failed",
            "response.cancelled",
            "response.canceled",
            "message.completed",
            "message_stop",
            "chat.completion.completed",
        }:
            return True
        if normalized_type == "message_delta":
            delta = payload.get("delta")
            if isinstance(delta, dict):
                stop_reason = delta.get("stop_reason")
                if isinstance(stop_reason, str) and stop_reason.strip():
                    return True

    status_value = payload.get("status")
    if isinstance(status_value, str) and status_value.strip().lower() in {
        "done", "completed", "complete", "finished", "stop",
    }:
        return True

    response_obj = payload.get("response")
    if isinstance(response_obj, dict):
        response_status = response_obj.get("status")
        if isinstance(response_status, str) and response_status.strip().lower() in {
            "done", "completed", "complete", "finished", "stop",
        }:
            return True

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0] if isinstance(choices[0], dict) else {}
        finish_reason = first_choice.get("finish_reason")
        if isinstance(finish_reason, str) and finish_reason.strip():
            return True

    candidates = payload.get("candidates")
    if isinstance(candidates, list) and candidates:
        first_candidate = candidates[0] if isinstance(candidates[0], dict) else {}
        finish_reason = first_candidate.get("finishReason") or first_candidate.get("finish_reason")
        if isinstance(finish_reason, str):
            normalized_finish = finish_reason.strip().lower()
            if normalized_finish and normalized_finish not in {"finish_reason_unspecified", "unspecified"}:
                return True
    return False
