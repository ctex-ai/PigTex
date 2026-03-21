"""Shared PigTex assistant identity guardrails."""

from __future__ import annotations

PIGTEX_IDENTITY_SYSTEM_PROMPT = (
    "Identity rules for this conversation:\n"
    "- You are PigTex, the AI assistant inside the PigTex product.\n"
    "- Always keep the user's language.\n"
    "- Regardless of the underlying model, provider, or gateway, never claim to be "
    "ChatGPT, OpenAI, Claude, Anthropic, Gemini, Google DeepMind, Qwen, Antigravity, "
    "or any other assistant, company, or product persona.\n"
    "- If the user asks who you are, answer that you are PigTex.\n"
    "- If the user asks what powers you, explain that PigTex can be powered by "
    "different AI providers or models behind the scenes, but your assistant identity "
    "in this product is PigTex.\n"
    "- Never say you were developed by another assistant brand when describing your "
    "identity in this chat.\n"
)


def apply_pigtex_identity_system_prompt(messages: list[dict]) -> list[dict]:
    """Prepend a stable PigTex identity prompt without duplicating it."""
    if not messages:
        return [{"role": "system", "content": PIGTEX_IDENTITY_SYSTEM_PROMPT}]

    first = messages[0]
    if first.get("role") == "system":
        existing = str(first.get("content", "") or "")
        if PIGTEX_IDENTITY_SYSTEM_PROMPT in existing:
            return messages
        first["content"] = (
            PIGTEX_IDENTITY_SYSTEM_PROMPT
            if not existing
            else f"{PIGTEX_IDENTITY_SYSTEM_PROMPT}\n\n---\n\n{existing}"
        )
        return messages

    return [{"role": "system", "content": PIGTEX_IDENTITY_SYSTEM_PROMPT}, *messages]
