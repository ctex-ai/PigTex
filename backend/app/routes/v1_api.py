"""
PigTex V1 API - OpenAI-Compatible Endpoint
=============================================
Giống như Cursor AI, Kilo Code, Roo Code:
- Endpoint: /v1/chat/completions (OpenAI-compatible)
- Yêu cầu: User login + BYOK (Provider API key/base URL)
- Tích hợp: Local-first memory (SQLite context)
- Forward: Request tới upstream provider API

Flow:
1. User login → JWT token
2. Client truyền Provider API key + base URL trong request/header
3. User gọi /v1/chat/completions → validate key → Local memory context → forward tới upstream
"""

import base64
import asyncio
import ipaddress
import json
import uuid
import re
import os
import logging
import unicodedata
import httpx
from time import perf_counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Any, Dict
from dataclasses import dataclass
from urllib.parse import urlparse, quote_plus

from fastapi import APIRouter, Depends, HTTPException, Query, status, Header, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import User, UsageRecord
from ..assistant_identity import apply_pigtex_identity_system_prompt
from ..prompting import (
    PromptPackStore,
    apply_output_filters,
    build_runtime_instruction_block,
    flush_stream_sanitizer,
    is_internal_orchestration_payload,
    sanitize_sse_event_block,
    StreamSanitizerState,
)
from .auth_utils import get_current_user
from ..rate_limit import v1_rate_limit

# Local-first memory
from ..memory import get_memory_coordinator
from ..local_storage import LocalDatabase
from ..local_storage.local_db import get_storage_dir
from ..local_storage.local_models import LocalConversation, LocalFact, LocalMemoryAssertion, LocalMessage
from .images import (
    MIME_TO_EXT,
    load_owned_image_from_serve_path,
    save_base64_image_to_disk,
)
from ..search import SearchCoordinator, SearchContext
from ..services.learning_service import LearningService
from ..services.texapi_partner_service import TexApiPartnerService
from ..upstream_request import UpstreamRequestConfig
from ..provider_registry import (
    DEFAULT_PROVIDER_BASE_URLS,
    PROVIDER_ALIASES,
    SUPPORTED_API_PROVIDERS,
    build_public_provider_catalog,
    infer_provider_from_api_key as _infer_provider_from_api_key,
    infer_provider_from_base_url as _infer_provider_from_base_url,
    is_first_party_provider_url as _is_first_party_provider_url,
    normalize_api_provider as _normalize_api_provider,
)

router = APIRouter(
    tags=["V1 API"],
    dependencies=[Depends(v1_rate_limit)],
)
settings = get_settings()
logger = logging.getLogger(__name__)
TEXAPI_PARTNER_SOURCE = "texapi_partner"


def _get_url_hostname(value: str) -> str:
    try:
        return (urlparse((value or "").strip()).hostname or "").strip().lower()
    except Exception:
        return ""


def _is_safe_public_media_url(value: str) -> bool:
    try:
        parsed = urlparse((value or "").strip())
    except Exception:
        return False

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False

    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        return False
    if hostname in {"localhost"} or hostname.endswith(".localhost") or hostname.endswith(".local"):
        return False

    try:
        parsed_ip = ipaddress.ip_address(hostname)
    except ValueError:
        return True

    return not (
        parsed_ip.is_private
        or parsed_ip.is_loopback
        or parsed_ip.is_link_local
        or parsed_ip.is_multicast
        or parsed_ip.is_reserved
        or parsed_ip.is_unspecified
    )


def _should_attach_upstream_auth_for_media_url(
    media_url: str,
    cfg: Optional["ResolvedUpstreamConfig"],
    declared_base_url: Optional[str] = None,
) -> bool:
    target_host = _get_url_hostname(media_url)
    if not target_host:
        return False

    candidate_hosts = {
        _get_url_hostname(declared_base_url or ""),
        _get_url_hostname(cfg.base_url if cfg else ""),
    }
    candidate_hosts.discard("")
    return target_host in candidate_hosts

CODE_FORMAT_SYSTEM_PROMPT = (
    "When you provide code, always use fenced Markdown code blocks with a language tag. "
    "Never compress code into one line. Keep indentation clean, preserve line breaks, "
    "and keep comments concise and useful."
)

MARKDOWN_RAW_SYSTEM_PROMPT = (
    "If the user requests Markdown content to copy/paste (for example Discord posts, README templates, "
    "announcements, bios, docs), return the final output as raw Markdown inside exactly one fenced code "
    "block with language tag `markdown`. Do not render markdown formatting outside that code block. "
    "Prefer returning only the code block unless the user explicitly asks for explanation."
)

DEFAULT_CONVERSATION_TITLES = {
    "",
    "new conversation",
    "new chat",
    "untitled",
}
WORKSPACE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# Reuse upstream chat client to avoid repeated TCP/TLS setup cost on hot path.
_CHAT_CLIENT: Optional[httpx.AsyncClient] = None
_CHAT_CLIENT_LOCK = asyncio.Lock()
_CHAT_CLIENT_TIMEOUT = httpx.Timeout(connect=8.0, read=180.0, write=30.0, pool=8.0)
_CHAT_CLIENT_LIMITS = httpx.Limits(max_connections=200, max_keepalive_connections=60, keepalive_expiry=45.0)
STREAM_READ_TIMEOUT_SECONDS = 180.0
_SEARCH_COORDINATOR: Optional[SearchCoordinator] = None
_SEARCH_COORDINATOR_LOCK = asyncio.Lock()
MAX_FILE_ATTACHMENTS_IN_CONTEXT = 4
MAX_FILE_ATTACHMENT_CHARS_PER_FILE = 8_000
MAX_FILE_ATTACHMENT_TOTAL_CHARS = 24_000
MAX_FILE_ATTACHMENT_CHUNKS_PER_FILE = 3
_IMAGE_PROMPT_LOG_PREVIEW_CHARS = 360

_VIETNAMESE_CHAR_RE = re.compile(
    r"[ăâđêôơưĂÂĐÊÔƠƯ"
    r"áàảãạấầẩẫậắằẳẵặ"
    r"éèẻẽẹếềểễệ"
    r"íìỉĩị"
    r"óòỏõọốồổỗộớờởỡợ"
    r"úùủũụứừửữự"
    r"ýỳỷỹỵ]"
)
_EMOJI_STYLE_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")

_TURN_COMPLEXITY_HINT_RE = re.compile(
    r"(compare|analysis|research|debug|root cause|refactor|architecture|plan|roadmap|trade-?off|audit|benchmark|migrate|"
    r"phân tích|so sánh|đánh giá|kế hoạch|kiến trúc|tối ưu|kiểm tra|điều tra|xử lý lỗi)"
)
_TURN_RECENCY_HINT_RE = re.compile(
    r"(latest|today|current|news|recent|breaking|price|release|cve|security update|"
    r"mới nhất|hôm nay|hiện tại|tin tức|cập nhật|giá|thời gian thực)"
)
_TURN_PRICE_HINT_RE = re.compile(
    r"(price|pricing|cost|rate|fee|fees|how much|quote|"
    r"giá|gia|bao nhiêu|bao nhieu|phí|phi|tỷ giá|tỉ giá|ty gia|mức giá)"
)
_TURN_VERIFICATION_HINT_RE = re.compile(
    r"(verify|verification|fact ?check|citation|source|evidence|accuracy|legal|law|regulation|medical|finance|security|"
    r"xác minh|kiểm chứng|nguồn|bằng chứng|đúng sai|pháp lý|y tế|tài chính|bảo mật)"
)
_TURN_MULTI_PART_HINT_RE = re.compile(r"(^|\n)\s*(?:[-*]|\d+[.)])\s+", re.MULTILINE)

_USAGE_MODEL_RATES_USD_PER_1M: list[tuple[str, float, float]] = [
    ("gpt-5-minimal", 0.25, 2.0),
    ("gpt-5-low", 0.25, 2.0),
    ("gpt-5.1-codex-mini", 0.3, 1.5),
    ("gpt-5-mini", 0.3, 2.4),
    ("gpt-5", 1.25, 10.0),
    ("gpt-4.1-mini", 0.4, 1.6),
    ("gpt-4.1", 2.0, 8.0),
    ("gpt-4o-mini", 0.15, 0.6),
    ("gpt-4o", 2.5, 10.0),
    ("o3-mini", 1.1, 4.4),
    ("o3", 2.0, 8.0),
    ("o1-mini", 1.1, 4.4),
    ("o1", 2.0, 8.0),
    ("claude-3.5-haiku", 1.0, 5.0),
    ("claude-3.7-sonnet", 3.0, 15.0),
    ("claude-3.5-sonnet", 3.0, 15.0),
    ("gemini-2.0-flash", 0.3, 0.6),
    ("gemini-1.5-flash", 0.3, 0.6),
    ("gemini-1.5-pro", 3.5, 10.5),
]


def _safe_positive_float(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
        if parsed >= 0:
            return parsed
    except (TypeError, ValueError):
        pass
    return fallback


_DEFAULT_USAGE_INPUT_USD_PER_1M = _safe_positive_float(
    os.getenv("PIGTEX_USAGE_INPUT_USD_PER_1M"),
    0.15,
)
_DEFAULT_USAGE_OUTPUT_USD_PER_1M = _safe_positive_float(
    os.getenv("PIGTEX_USAGE_OUTPUT_USD_PER_1M"),
    0.60,
)
try:
    _CHAT_EMPTY_COMPLETION_RETRIES = max(
        0,
        min(2, int(os.getenv("PIGTEX_CHAT_EMPTY_COMPLETION_RETRIES", "1").strip()))
    )
except ValueError:
    _CHAT_EMPTY_COMPLETION_RETRIES = 1


def _is_image_prompt_log_enabled() -> bool:
    raw = os.getenv("PIGTEX_IMAGE_PROMPT_LOG_ENABLED", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _is_image_prompt_text_log_enabled() -> bool:
    raw = os.getenv("PIGTEX_IMAGE_PROMPT_LOG_TEXT", "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _compact_prompt_log_text(text: str, max_chars: int = _IMAGE_PROMPT_LOG_PREVIEW_CHARS) -> str:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max(0, max_chars - 3)].rstrip() + "..."


async def _get_chat_client() -> httpx.AsyncClient:
    """Get shared AsyncClient for chat upstream calls."""
    global _CHAT_CLIENT
    if _CHAT_CLIENT and not _CHAT_CLIENT.is_closed:
        return _CHAT_CLIENT

    async with _CHAT_CLIENT_LOCK:
        if _CHAT_CLIENT and not _CHAT_CLIENT.is_closed:
            return _CHAT_CLIENT
        _CHAT_CLIENT = httpx.AsyncClient(
            timeout=_CHAT_CLIENT_TIMEOUT,
            limits=_CHAT_CLIENT_LIMITS,
            follow_redirects=False,
        )
        return _CHAT_CLIENT


async def close_chat_client() -> None:
    """Shutdown hook for shared AsyncClient."""
    global _CHAT_CLIENT
    if _CHAT_CLIENT and not _CHAT_CLIENT.is_closed:
        await _CHAT_CLIENT.aclose()
    _CHAT_CLIENT = None


async def warmup_chat_client() -> None:
    """
    BYOK-only upstream routing cannot be warmed at startup because credentials
    are request-scoped.
    """
    return


async def _get_search_coordinator() -> SearchCoordinator:
    """Lazily initialize a shared SearchCoordinator."""
    global _SEARCH_COORDINATOR
    if _SEARCH_COORDINATOR is not None:
        return _SEARCH_COORDINATOR

    async with _SEARCH_COORDINATOR_LOCK:
        if _SEARCH_COORDINATOR is None:
            _SEARCH_COORDINATOR = SearchCoordinator(settings)
    return _SEARCH_COORDINATOR


def _release_db_connection(db: Optional[Session], *, reason: str, request_id: str = "") -> None:
    """
    End any open transaction so long upstream/network waits do not pin
    a pooled DB connection for the entire request lifetime.
    """
    if db is None:
        return
    try:
        db.rollback()
    except Exception as exc:
        logger.debug(
            "db_release_skip request_id=%s reason=%s error=%s",
            request_id,
            reason,
            exc,
        )


# =============================================================================
# Schemas (OpenAI-Compatible)
# =============================================================================

class V1ChatMessage(BaseModel):
    role: str  # system, user, assistant
    content: Any  # str or list[{type: "text"|"image_url", ...}] for multimodal


class V1FileChunk(BaseModel):
    index: int
    label: Optional[str] = None
    text: str
    char_count: Optional[int] = None
    truncated: Optional[bool] = False


class V1FileAttachment(BaseModel):
    id: str
    filename: str
    mime_type: str
    size: int
    extracted_text: str
    text_chars: Optional[int] = None
    truncated: Optional[bool] = False
    chunks: Optional[List[V1FileChunk]] = None


class V1ChatCompletionRequest(BaseModel):
    """OpenAI-compatible chat completion request"""
    model: Optional[str] = None
    messages: List[V1ChatMessage]
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False
    top_p: Optional[float] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None

    # BYOK provider config (preferred)
    api_key: Optional[str] = None
    api_base_url: Optional[str] = None
    
    # PigTex extensions
    conversation_id: Optional[str] = None    # Continue existing conversation
    workspace_id: Optional[str] = None       # Project context
    mode: Optional[str] = "fast"             # fast | deep (execution budget hint)
    runtime_instruction: Optional[str] = None  # Runtime-only instruction (not persisted)
    use_memory: Optional[bool] = True        # Enable memory system
    use_knowledge: Optional[bool] = True     # Include local knowledge retrieval
    use_facts: Optional[bool] = True         # Include extracted/explicit facts
    use_history: Optional[bool] = True       # Include local conversation history
    use_web_search: Optional[bool] = None    # Enable web search augmentation
    web_search_mode: Optional[str] = "auto"  # auto | fast | deep (compat: realtime | verify)
    web_search_max_results: Optional[int] = Field(default=None, ge=1, le=10)
    web_search_deep_read: Optional[bool] = None
    web_search_deep_verify: Optional[bool] = None
    learning_mode: Optional[str] = "off"     # auto | off | teacher
    learning_program_id: Optional[str] = None
    file_attachments: Optional[List[V1FileAttachment]] = None


class V1ImageGenerationRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    model: Optional[str] = None
    n: Optional[int] = Field(default=1, ge=1, le=8)
    size: Optional[str] = None
    quality: Optional[str] = None
    response_format: Optional[str] = None
    style: Optional[str] = None
    background: Optional[str] = None
    user: Optional[str] = None
    prompt_enhance: Optional[bool] = False
    prompt_profile: Optional[str] = None
    api_key: Optional[str] = None
    api_base_url: Optional[str] = None


class V1ImageEditRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    image: str = Field(..., description="data URL, local /api/images/serve/* URL, or remote image URL")
    mask: Optional[str] = Field(default=None, description="Optional mask image input")
    model: Optional[str] = None
    n: Optional[int] = Field(default=1, ge=1, le=8)
    size: Optional[str] = None
    quality: Optional[str] = None
    response_format: Optional[str] = None
    user: Optional[str] = None
    prompt_enhance: Optional[bool] = False
    prompt_profile: Optional[str] = None
    api_key: Optional[str] = None
    api_base_url: Optional[str] = None


class V1AudioSpeechRequest(BaseModel):
    model: str
    input: str = Field(..., min_length=1)
    voice: Optional[str] = None
    response_format: Optional[str] = "mp3"
    speed: Optional[float] = None
    prompt_enhance: Optional[bool] = True
    prompt_profile: Optional[str] = None
    purpose: Optional[str] = None
    audience: Optional[str] = None
    language: Optional[str] = None
    voice_character: Optional[str] = None
    emotion_arc: Optional[str] = None
    accent: Optional[str] = None
    speaking_rate: Optional[str] = None
    pronunciation_dictionary: Optional[str] = None
    brand_terms: Optional[list[str]] = None
    api_key: Optional[str] = None
    api_base_url: Optional[str] = None


class V1VideoGenerationRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    model: Optional[str] = None
    n: Optional[int] = Field(default=1, ge=1, le=4)
    size: Optional[str] = None
    duration: Optional[str] = None
    quality: Optional[str] = None
    response_format: Optional[str] = None
    style: Optional[str] = None
    aspect_ratio: Optional[str] = None
    user: Optional[str] = None
    prompt_enhance: Optional[bool] = True
    prompt_profile: Optional[str] = None
    objective: Optional[str] = None
    audience: Optional[str] = None
    offer: Optional[str] = None
    tone: Optional[str] = None
    reference_style: Optional[str] = None
    brand_palette: Optional[str] = None
    cta: Optional[str] = None
    api_key: Optional[str] = None
    api_base_url: Optional[str] = None


class V1RealtimeSessionRequest(BaseModel):
    model: str
    voice: Optional[str] = None
    modalities: Optional[list[str]] = None
    instructions: Optional[str] = None
    api_key: Optional[str] = None
    api_base_url: Optional[str] = None


# =============================================================================
# Helpers
# =============================================================================

@dataclass
class ResolvedUpstreamConfig:
    """Resolved upstream API connection config for one request."""
    api_key: str
    base_url: str
    source: str  # request | texapi_partner
    api_provider: str = "openai"  # resolved provider: openai | anthropic | gemini | alibaba
    db_key_id: Optional[str] = None


@dataclass
class VideoTaskFetchResult:
    payload: Optional[dict[str, Any]] = None
    endpoint_not_supported: bool = False
    binary_content: Optional[bytes] = None
    binary_media_type: Optional[str] = None


def _align_provider_and_base_url(
    provider_mode: str,
    resolved_provider: str,
    resolved_base_url: str,
    resolved_api_key: str,
) -> tuple[str, str]:
    """
    Heal legacy mismatches between provider/base_url/key.

    Common stale case:
    - provider resolves to anthropic/gemini
    - base_url is still OpenAI default
    """
    normalized_base_url = (resolved_base_url or "").strip().rstrip("/")
    openai_default = DEFAULT_PROVIDER_BASE_URLS["openai"]
    inferred_from_key = _infer_provider_from_api_key(resolved_api_key)

    # Auto mode: trust key hints only when URL is missing or clearly first-party.
    # For proxy/gateway URLs, keep OpenAI-compatible mode and let upstream route by model.
    can_hint_from_key = not normalized_base_url or _is_first_party_provider_url(normalized_base_url)
    if (
        provider_mode == "auto"
        and inferred_from_key
        and resolved_provider == "openai"
        and can_hint_from_key
    ):
        resolved_provider = inferred_from_key

    # If provider is non-OpenAI but URL is still default OpenAI, switch to provider default.
    if (
        resolved_provider in DEFAULT_PROVIDER_BASE_URLS
        and resolved_provider != "openai"
        and normalized_base_url == openai_default
    ):
        normalized_base_url = DEFAULT_PROVIDER_BASE_URLS[resolved_provider]

    # Keep non-empty normalized URL after healing.
    if not normalized_base_url:
        normalized_base_url = DEFAULT_PROVIDER_BASE_URLS.get(resolved_provider, openai_default)

    return resolved_provider, normalized_base_url


def _ensure_provider_key_compatibility(
    provider: str,
    api_key: str,
    source: str,
    base_url: Optional[str] = None,
) -> None:
    """
    Guard against obvious provider/key mismatches that would always fail upstream.
    """
    # Proxy URLs can be multi-provider and usually expect OpenAI-compatible auth.
    if base_url and not _is_first_party_provider_url(base_url, provider):
        return

    hinted_provider = _infer_provider_from_api_key(api_key)
    if not hinted_provider or hinted_provider == provider:
        return

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "error": "provider_key_mismatch",
            "message": (
                f"API key appears to be for {hinted_provider}, "
                f"but endpoint mode is {provider}."
            ),
            "provider": provider,
            "key_provider": hinted_provider,
            "source": source,
        },
    )


def _normalize_base_url(base_url: Optional[str], provider: str = "auto") -> str:
    """Normalize base URL and apply provider-specific default when omitted."""
    url = (base_url or "").strip()
    if not url:
        default_provider = provider if provider in DEFAULT_PROVIDER_BASE_URLS else "openai"
        url = DEFAULT_PROVIDER_BASE_URLS.get(default_provider, DEFAULT_PROVIDER_BASE_URLS["openai"])
    return url.rstrip("/")


def _should_use_texapi_partner_flow(
    *,
    api_key: str,
    base_url: str,
) -> bool:
    service = TexApiPartnerService(settings=settings)
    return service.is_managed_gateway_selected(base_url, api_key=api_key)


async def _hydrate_texapi_partner_config(
    cfg: ResolvedUpstreamConfig,
    current_user: User,
    *,
    force_refresh: bool = False,
) -> ResolvedUpstreamConfig:
    if cfg.source != TEXAPI_PARTNER_SOURCE:
        return cfg

    service = TexApiPartnerService(settings=settings)
    token = await service.get_delegated_token(current_user, force_refresh=force_refresh)
    return ResolvedUpstreamConfig(
        api_key=token.token,
        base_url=service.gateway_base_url,
        source=TEXAPI_PARTNER_SOURCE,
        api_provider="openai",
        db_key_id=None,
    )


def _should_retry_texapi_partner_auth_error(response: httpx.Response, cfg: ResolvedUpstreamConfig) -> bool:
    if cfg.source != TEXAPI_PARTNER_SOURCE or response.status_code != 401:
        return False

    try:
        payload = response.json()
    except Exception:
        payload = None

    candidates: list[str] = []
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str):
            candidates.append(detail)
        elif isinstance(detail, dict):
            message = detail.get("message")
            error = detail.get("error")
            if isinstance(message, str):
                candidates.append(message)
            if isinstance(error, str):
                candidates.append(error)
        error = payload.get("error")
        if isinstance(error, dict):
            for key in ("message", "code", "type"):
                value = error.get(key)
                if isinstance(value, str):
                    candidates.append(value)
        for key in ("message", "error"):
            value = payload.get(key)
            if isinstance(value, str):
                candidates.append(value)

    combined = " ".join(candidates).lower()
    if not combined:
        return True
    return "expired" in combined or "invalid" in combined or "revoked" in combined


def _normalize_public_error_message(message: str, max_length: int = 240) -> str:
    clean_message = " ".join(str(message).split())
    if len(clean_message) > max_length:
        return f"{clean_message[:max_length - 3]}..."
    return clean_message


def _raise_removed_v1_keys_endpoint() -> None:
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "error": "endpoint_removed",
            "message": "Server-side provider key storage was removed. Use BYOK request credentials or the managed TexAPI gateway.",
            "replacement": "/api/v1/keys/validate",
        },
    )


def _extract_upstream_error_message(response: httpx.Response) -> str:
    """Parse the upstream response body and return the most useful error string."""
    try:
        body = response.json()
    except Exception:
        text = (response.text or "").strip()
        return text[:400] if text else f"HTTP {response.status_code}"

    if not isinstance(body, dict):
        return f"HTTP {response.status_code}"

    # Standard OpenAI/Anthropic/Gemini error shapes
    for path in [
        ["error", "message"],
        ["error", "error", "message"],
        ["message"],
        ["detail", "message"],
        ["detail"],
    ]:
        node: Any = body
        for key in path:
            if isinstance(node, dict):
                node = node.get(key)
            else:
                node = None
                break
        if isinstance(node, str) and node.strip():
            return node.strip()[:400]

    # Alibaba-style: output.message or code/message at top level
    output = body.get("output") if isinstance(body, dict) else None
    if isinstance(output, dict):
        msg = output.get("message") or output.get("text")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()[:400]
    code = body.get("code")
    msg = body.get("message") or body.get("msg")
    if isinstance(msg, str) and msg.strip():
        suffix = f" (code: {code})" if code else ""
        return msg.strip()[:400] + suffix

    return f"HTTP {response.status_code}"


def _raise_upstream_http_exception(response: httpx.Response, operation: str) -> None:
    response_preview = response.text[:500] if response.text else ""
    logger.error(
        "Upstream API %s failed status=%s body_preview=%s",
        operation,
        response.status_code,
        response_preview,
    )
    upstream_message = _extract_upstream_error_message(response)
    raise HTTPException(
        status_code=response.status_code,
        detail={
            "error": "upstream_api_error",
            "message": upstream_message,
            "status_code": response.status_code,
        },
    )

def _mask_key(key: str) -> str:
    """Mask API key for display."""
    if len(key) <= 12:
        return key[:4] + "..." + key[-4:]
    return key[:10] + "..." + key[-4:]


def _serialize_web_search_meta(search_context: Optional[SearchContext], enabled: bool) -> Dict[str, Any]:
    if not search_context:
        return {"enabled": bool(enabled), "status": "disabled" if not enabled else "skipped"}

    status_hint = str(getattr(search_context, "status_hint", "") or "").strip().lower()
    if status_hint in {"complete", "timeout", "skipped", "disabled", "error"}:
        status = status_hint
    elif not enabled:
        status = "disabled"
    elif search_context.has_results or search_context.search_queries or int(search_context.total_search_time_ms or 0) > 0:
        status = "complete"
    else:
        status = "skipped"
    payload: Dict[str, Any] = {
        "enabled": bool(enabled),
        "status": status,
        "search_intent": search_context.search_intent.value,
        "search_queries": search_context.search_queries,
        "total_search_time_ms": search_context.total_search_time_ms,
        "raw_results_count": search_context.raw_results_count,
    }

    mode = getattr(search_context, "mode", None)
    if mode is not None:
        payload["mode"] = getattr(mode, "value", str(mode))

    checked_at_utc = getattr(search_context, "checked_at_utc", None)
    if isinstance(checked_at_utc, str) and checked_at_utc.strip():
        payload["checked_at_utc"] = checked_at_utc.strip()

    confidence_score = getattr(search_context, "confidence_score", None)
    if isinstance(confidence_score, (int, float)):
        payload["confidence_score"] = round(max(0.0, min(1.0, float(confidence_score))), 3)

    conflicts_count = getattr(search_context, "conflicts_count", None)
    if isinstance(conflicts_count, int):
        payload["conflicts_count"] = max(0, conflicts_count)

    claims_verified_count = getattr(search_context, "claims_verified_count", None)
    if isinstance(claims_verified_count, int):
        payload["claims_verified_count"] = max(0, claims_verified_count)

    warnings = getattr(search_context, "warnings", None)
    if isinstance(warnings, list):
        cleaned_warnings = [str(item).strip() for item in warnings if str(item).strip()]
        if cleaned_warnings:
            payload["warnings"] = cleaned_warnings

    raw_claims = getattr(search_context, "claim_verification", None)
    if isinstance(raw_claims, list) and raw_claims:
        claim_payload: List[Dict[str, Any]] = []
        for item in raw_claims:
            if hasattr(item, "to_dict"):
                try:
                    serialized = item.to_dict()
                    if isinstance(serialized, dict):
                        claim_payload.append(serialized)
                        continue
                except Exception:
                    pass
            if isinstance(item, dict):
                claim_payload.append(item)
        if claim_payload:
            payload["claim_verification"] = claim_payload

    return payload


def _count_bullets_for_memory_section(facts_context: str) -> Dict[str, int]:
    counts = {
        "preferences": 0,
        "system_facts": 0,
        "workspace_facts": 0,
        "identity": 0,
        "user_facts": 0,
    }
    current_section: Optional[str] = None
    for raw_line in (facts_context or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("### "):
            heading = line[4:].strip().lower()
            if heading == "user preferences":
                current_section = "preferences"
            elif heading == "system memory":
                current_section = "system_facts"
            elif heading == "workspace memory":
                current_section = "workspace_facts"
            elif heading == "user identity":
                current_section = "identity"
            elif heading == "user facts":
                current_section = "user_facts"
            else:
                current_section = None
            continue
        if current_section and line.startswith("- "):
            counts[current_section] += 1
    return counts


def _serialize_memory_context_meta(
    context: Optional[Any],
    request: Optional["V1ChatCompletionRequest"],
    enabled: bool,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "enabled": bool(enabled),
        "use_knowledge": bool(getattr(request, "use_knowledge", True)) if request else False,
        "use_facts": bool(getattr(request, "use_facts", True)) if request else False,
        "use_history": bool(getattr(request, "use_history", True)) if request else False,
    }
    if not context:
        payload["knowledge_hits"] = 0
        payload["history_messages_used"] = 0
        payload["facts_used"] = 0
        return payload

    sources = getattr(context, "sources", None)
    context_sources: List[Dict[str, Any]] = []
    if isinstance(sources, list):
        for item in sources[:8]:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "").strip()
            if not item_id:
                continue
            context_sources.append({
                "index": int(item.get("index") or (len(context_sources) + 1)),
                "id": item_id,
                "title": str(item.get("title") or "").strip() or item_id,
                "type": str(item.get("type") or "knowledge").strip() or "knowledge",
            })
    if context_sources:
        payload["sources"] = context_sources
    payload["knowledge_hits"] = len(context_sources)

    context_messages = getattr(context, "messages", None)
    payload["history_messages_used"] = len(context_messages) if isinstance(context_messages, list) else 0

    facts_context = str(getattr(context, "facts_context", "") or "")
    section_counts = _count_bullets_for_memory_section(facts_context)
    payload["preference_facts_used"] = section_counts["preferences"]
    payload["identity_facts_used"] = section_counts["identity"]
    payload["user_facts_used"] = section_counts["user_facts"]
    payload["system_facts_used"] = section_counts["system_facts"] + section_counts["identity"]
    payload["workspace_facts_used"] = section_counts["workspace_facts"] + section_counts["user_facts"]
    payload["facts_used"] = (
        section_counts["preferences"]
        + section_counts["system_facts"]
        + section_counts["workspace_facts"]
        + section_counts["identity"]
        + section_counts["user_facts"]
    )

    total_tokens = getattr(context, "total_tokens", None)
    if isinstance(total_tokens, int):
        payload["context_tokens"] = max(0, total_tokens)
    return payload


_TOKEN_LIKE_MEMORY_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{4,63}$")


def _candidate_memory_keys_for_exact_query(user_text: str) -> List[str]:
    lowered = (user_text or "").strip().lower()
    if not lowered:
        return []
    if "naming convention" in lowered or ("convention" in lowered and "workspace" in lowered):
        return ["naming_convention"]
    if "nickname" in lowered and "editor" in lowered:
        return ["user_nickname", "editor"]
    if "nickname" in lowered:
        return ["user_nickname"]
    if "editor" in lowered or "ide" in lowered:
        return ["editor"]
    if "name" in lowered:
        return ["user_name", "user_nickname"]
    return []


def _maybe_expand_exact_memory_reply(
    *,
    assistant_content: str,
    request: "V1ChatCompletionRequest",
    coordinator: Optional[Any],
    conversation_id: Optional[str],
) -> str:
    if not assistant_content or not coordinator:
        return assistant_content

    latest_user_text = ""
    if request.messages:
        latest_user_text = _message_content_to_text(request.messages[-1].content)
    lowered = latest_user_text.strip().lower()
    if not lowered:
        return assistant_content

    exact_query_markers = (
        "exact",
        "token only",
        "name only",
        "saved memory",
        "current memory value",
    )
    if not any(marker in lowered for marker in exact_query_markers):
        return assistant_content

    candidate_keys = _candidate_memory_keys_for_exact_query(lowered)
    if not candidate_keys:
        return assistant_content

    core = assistant_content.strip().strip("`'\"")
    core = re.sub(r"[.。,;:!?]+$", "", core).strip()
    if len(core) < 3:
        return assistant_content

    candidate_values: List[str] = []
    seen_values: set[str] = set()

    def _append_candidate(value: Optional[str]) -> None:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen_values:
            return
        if not _TOKEN_LIKE_MEMORY_VALUE_RE.fullmatch(normalized):
            return
        seen_values.add(normalized)
        candidate_values.append(normalized)

    for key in candidate_keys:
        if request.workspace_id:
            ws_assertion = coordinator.local.find_active_memory_assertion(
                key=key,
                scope="workspace",
                workspace_id=request.workspace_id,
            )
            _append_candidate(getattr(ws_assertion, "value", None))
        if conversation_id:
            conv_assertion = coordinator.local.find_active_memory_assertion(
                key=key,
                scope="conversation",
                conversation_id=conversation_id,
            )
            _append_candidate(getattr(conv_assertion, "value", None))
        user_assertion = coordinator.local.find_active_memory_assertion(
            key=key,
            scope="user",
            workspace_id=None,
        )
        _append_candidate(getattr(user_assertion, "value", None))

    if not candidate_values:
        return assistant_content

    lowered_core = core.lower()
    exact_matches = [value for value in candidate_values if value.lower() == lowered_core]
    if exact_matches:
        return exact_matches[0]

    prefix_matches = [value for value in candidate_values if value.lower().startswith(lowered_core)]
    unique_prefix_matches = sorted(set(prefix_matches))
    if len(unique_prefix_matches) == 1:
        return unique_prefix_matches[0]

    return assistant_content


def _try_resolve_direct_memory_reply(
    *,
    request: "V1ChatCompletionRequest",
    coordinator: Optional[Any],
    conversation_id: Optional[str],
) -> Optional[str]:
    if not coordinator or not getattr(request, "use_memory", True):
        return None
    if not request.messages:
        return None

    latest_user_text = _message_content_to_text(request.messages[-1].content)
    lowered = latest_user_text.strip().lower()
    if not lowered:
        return None

    def _find_value(
        key: str,
        *,
        prefer_conversation: bool = False,
        prefer_workspace: bool = False,
    ) -> Optional[str]:
        local = coordinator.local
        if prefer_conversation and conversation_id:
            found = local.find_active_memory_assertion(
                key=key,
                scope="conversation",
                conversation_id=conversation_id,
            )
            value = str(getattr(found, "value", "") or "").strip()
            if value:
                return value
        if prefer_workspace and request.workspace_id:
            found = local.find_active_memory_assertion(
                key=key,
                scope="workspace",
                workspace_id=request.workspace_id,
            )
            value = str(getattr(found, "value", "") or "").strip()
            if value:
                return value
        found = local.find_active_memory_assertion(
            key=key,
            scope="user",
            workspace_id=None,
        )
        value = str(getattr(found, "value", "") or "").strip()
        if value:
            return value
        if not prefer_workspace and request.workspace_id:
            found = local.find_active_memory_assertion(
                key=key,
                scope="workspace",
                workspace_id=request.workspace_id,
            )
            value = str(getattr(found, "value", "") or "").strip()
            if value:
                return value
        if not prefer_conversation and conversation_id:
            found = local.find_active_memory_assertion(
                key=key,
                scope="conversation",
                conversation_id=conversation_id,
            )
            value = str(getattr(found, "value", "") or "").strip()
            if value:
                return value
        return None

    if "nickname=<value>; editor=<value>" in lowered and "nickname" in lowered and "editor" in lowered:
        nickname = _find_value("user_nickname", prefer_conversation=True)
        editor = _find_value("editor")
        if nickname and editor:
            return f"nickname={nickname}; editor={editor}"

    if "temporary nickname" in lowered:
        nickname = None
        if conversation_id:
            conv_assertion = coordinator.local.find_active_memory_assertion(
                key="user_nickname",
                scope="conversation",
                conversation_id=conversation_id,
            )
            nickname = str(getattr(conv_assertion, "value", "") or "").strip() or None
        if nickname:
            return nickname
        if "reply none" in lowered or "if none, reply none" in lowered:
            return "NONE"

    if "naming convention" in lowered and "token only" in lowered:
        naming = _find_value("naming_convention", prefer_workspace=True)
        if naming:
            return naming

    if "exact name" in lowered or ("saved memory" in lowered and "name only" in lowered):
        name = _find_value("user_name")
        if name:
            return name

    return None


def _load_style_preferences(coordinator: Optional[Any]) -> Dict[str, str]:
    if not coordinator:
        return {}

    prefs: Dict[str, str] = {}
    try:
        for pref in coordinator.local.get_preferences():
            key = str(getattr(pref, "key", "") or "").strip().lower()
            value = str(getattr(pref, "value", "") or "").strip()
            if not key or not value or key in prefs:
                continue
            prefs[key] = value
    except Exception as exc:
        logger.debug("Style preference load from local prefs skipped: %s", exc)

    for key in ("response_style", "tone_preference", "emoji_usage"):
        if key in prefs:
            continue
        try:
            assertion = coordinator.local.find_active_memory_assertion(
                key=key,
                scope="user",
                workspace_id=None,
            )
        except Exception as exc:
            logger.debug("Style preference load from assertions skipped key=%s error=%s", key, exc)
            continue
        value = str(getattr(assertion, "value", "") or "").strip()
        if value:
            prefs[key] = value

    return prefs


def _build_style_preferences_system_prompt(coordinator: Optional[Any]) -> str:
    prefs = _load_style_preferences(coordinator)
    if not prefs:
        return ""

    instructions: List[str] = []
    response_format = prefs.get("response_format", "").strip().lower()
    bullet_count = prefs.get("response_bullet_count", "").strip()
    response_style = prefs.get("response_style", "").strip().lower()
    emoji_usage = prefs.get("emoji_usage", "").strip().lower()
    action_line = prefs.get("response_action_line", "").strip().lower()

    if response_format == "bullet_points":
        if bullet_count.isdigit():
            instructions.append(f"Use exactly {bullet_count} bullet points.")
        else:
            instructions.append("Use bullet points.")
    if response_style in {"concise", "concise_structured", "brief"}:
        instructions.append("Keep each point concise.")
    if emoji_usage in {"off", "avoid"}:
        instructions.append("Do not use emoji.")
    if action_line == "required":
        instructions.append("End with a final line that starts exactly with 'Action:'.")

    if not instructions:
        return ""
    return "Persisted user style preferences:\n- " + "\n- ".join(instructions)


def _normalize_style_line(text: str) -> str:
    line = re.sub(r"^\s*(?:[-*]|\d+\.)\s*", "", text or "").strip()
    line = re.sub(r"[*_`#]+", "", line).strip()
    line = re.sub(r"\s+", " ", line)
    return line.strip(" -:;,.")


def _split_style_candidates(text: str) -> List[str]:
    cleaned = re.sub(r"(?im)^\s*action\s*:.*$", "", text or "").strip()
    if not cleaned:
        return []

    bullet_matches = re.findall(r"(?im)^\s*(?:[-*]|\d+\.)\s+(.+)$", cleaned)
    candidates = bullet_matches if bullet_matches else []

    if not candidates:
        for block in re.split(r"\n{2,}", cleaned):
            block = block.strip()
            if block:
                candidates.append(block)

    expanded: List[str] = []
    for candidate in candidates or [cleaned]:
        parts = re.split(r"(?<=[.!?])\s+|;\s+|\n+", candidate)
        for part in parts:
            normalized = _normalize_style_line(part)
            if normalized:
                expanded.append(normalized)

    deduped: List[str] = []
    seen: set[str] = set()
    for item in expanded:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _condense_style_item(text: str, max_chars: int = 120) -> str:
    line = _normalize_style_line(text)
    if not line:
        return ""
    line = re.split(r"(?<=[.!?])\s+", line, maxsplit=1)[0].strip()
    if len(line) <= max_chars:
        return line
    trimmed = line[:max_chars].rsplit(" ", 1)[0].strip()
    return trimmed or line[:max_chars].strip()


def _apply_style_preferences_output(assistant_content: str, coordinator: Optional[Any]) -> str:
    text = assistant_content or ""
    if not text or not coordinator:
        return text

    prefs = _load_style_preferences(coordinator)
    if not prefs:
        return text

    response_format = prefs.get("response_format", "").strip().lower()
    bullet_count_raw = prefs.get("response_bullet_count", "").strip()
    response_style = prefs.get("response_style", "").strip().lower()
    emoji_usage = prefs.get("emoji_usage", "").strip().lower()
    action_line = prefs.get("response_action_line", "").strip().lower()

    if emoji_usage in {"off", "avoid"}:
        text = _EMOJI_STYLE_RE.sub("", text)

    require_bullets = response_format == "bullet_points"
    require_action = action_line == "required"
    concise = response_style in {"concise", "concise_structured", "brief"}
    bullet_count = int(bullet_count_raw) if bullet_count_raw.isdigit() else 0

    if require_bullets:
        items = _split_style_candidates(text)
        if concise:
            items = [_condense_style_item(item) for item in items]
            items = [item for item in items if item]
        target_count = bullet_count if bullet_count > 0 else max(3, min(5, len(items) or 3))
        fallback_items = [
            "Focus on the highest-impact fix first",
            "Measure the result with one clear metric",
            "Validate the change before scaling it out",
        ]
        idx = 0
        while len(items) < target_count and idx < len(fallback_items):
            items.append(fallback_items[idx])
            idx += 1
        items = items[:target_count]
        text = "\n".join(f"- {item.rstrip('.').strip()}" for item in items if item.strip())

    if require_action:
        action_match = re.search(r"(?im)^\s*action\s*:\s*(.+)$", assistant_content or "")
        action_text = _normalize_style_line(action_match.group(1)) if action_match else ""
        if not action_text:
            if require_bullets:
                first_item = _normalize_style_line(_split_style_candidates(text)[0]) if _split_style_candidates(text) else ""
                action_text = f"Start with: {first_item}" if first_item else "Pick one item and execute it now"
            else:
                action_text = "Pick one item and execute it now"
        text = text.rstrip() + f"\nAction: {action_text}"

    return text.strip()


def _resolve_upstream_config(
    current_user: User,
    db: Session,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    header_api_key: Optional[str] = None,
    header_base_url: Optional[str] = None,
    api_provider: Optional[str] = None,
) -> ResolvedUpstreamConfig:
    """
    Resolve API credentials for a request.
    Priority:
    0) Request fields / headers (BYOK mode)
    """
    provider_mode = _normalize_api_provider(api_provider)

    # Priority 0: BYOK from request fields / headers
    resolved_key = (api_key or header_api_key or "").strip()
    resolved_base_url = _normalize_base_url(base_url or header_base_url, provider_mode)
    resolved_provider = (
        provider_mode
        if provider_mode != "auto"
        else _infer_provider_from_base_url(resolved_base_url)
    )
    resolved_provider, resolved_base_url = _align_provider_and_base_url(
        provider_mode=provider_mode,
        resolved_provider=resolved_provider,
        resolved_base_url=resolved_base_url,
        resolved_api_key=resolved_key,
    )

    if resolved_key:
        _ensure_provider_key_compatibility(
            provider=resolved_provider,
            api_key=resolved_key,
            source="request",
            base_url=resolved_base_url,
        )
        return ResolvedUpstreamConfig(
            api_key=resolved_key,
            base_url=resolved_base_url,
            source="request",
            api_provider=resolved_provider,
            db_key_id=None,
        )

    if _should_use_texapi_partner_flow(api_key=resolved_key, base_url=resolved_base_url):
        return ResolvedUpstreamConfig(
            api_key="",
            base_url=TexApiPartnerService(settings=settings).gateway_base_url,
            source=TEXAPI_PARTNER_SOURCE,
            api_provider="openai",
            db_key_id=None,
        )

    missing_msg = (
        "Missing API credentials. Provide API key via settings or headers "
        "(`X-API-Key` / `X-API-Base-URL` / `X-API-Provider`)."
    )

    logger.warning(
        "api_credentials_missing user_id=%s provider_mode=%s has_request_key=%s has_request_base_url=%s has_header_key=%s has_header_base_url=%s",
        str(getattr(current_user, "id", "")),
        provider_mode,
        bool((api_key or "").strip()),
        bool((base_url or "").strip()),
        bool((header_api_key or "").strip()),
        bool((header_base_url or "").strip()),
    )

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "error": "api_credentials_required",
            "message": missing_msg,
        },
    )


def _build_upstream_auth_headers(cfg: ResolvedUpstreamConfig) -> dict[str, str]:
    """
    Build the correct auth headers for the upstream API provider.
    OpenAI:    Authorization: Bearer <key>
    Anthropic: x-api-key: <key> + anthropic-version header
    Gemini:    x-goog-api-key: <key>
    Alibaba:   Authorization: Bearer <key> (OpenAI-compatible mode)
    """
    headers: dict[str, str] = {"Content-Type": "application/json"}
    provider = cfg.api_provider

    if provider == "anthropic":
        headers["x-api-key"] = cfg.api_key
        headers["anthropic-version"] = "2023-06-01"
    elif provider == "gemini":
        headers["x-goog-api-key"] = cfg.api_key
    else:
        # Default: OpenAI-compatible Bearer token
        headers["Authorization"] = f"Bearer {cfg.api_key}"

    return headers


def _build_upstream_url(cfg: ResolvedUpstreamConfig, path: str) -> str:
    """
    Build full URL for upstream API call.

    Prevent duplicate API version segments when users provide base URLs that
    already include `/v1` or `/v1beta` (for example DashScope-compatible URLs).
    """
    base = (cfg.base_url or "").rstrip("/")
    normalized_path = (path or "").strip()
    if not normalized_path:
        return base
    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"

    if cfg.source == TEXAPI_PARTNER_SOURCE and normalized_path.startswith("/v1/"):
        normalized_path = normalized_path[3:]
        if not normalized_path.startswith("/"):
            normalized_path = f"/{normalized_path}"

    parsed = urlparse(base)
    base_segments = [seg for seg in (parsed.path or "").split("/") if seg]
    path_segments = [seg for seg in normalized_path.split("/") if seg]

    # Example:
    # base=/compatible-mode/v1 + path=/v1/chat/completions
    # -> /compatible-mode/v1/chat/completions (avoid /v1/v1/)
    if (
        base_segments
        and path_segments
        and base_segments[-1].lower() == path_segments[0].lower()
        and re.fullmatch(r"v\d+[a-z0-9._-]*", path_segments[0].lower())
    ):
        path_segments = path_segments[1:]
        if not path_segments:
            return base
        normalized_path = "/" + "/".join(path_segments)

    return f"{base}{normalized_path}"


def _build_url_with_segment_overlap(base_url: str, path: str) -> Optional[str]:
    """
    Join URL path segments while removing duplicated overlap.

    Example:
    - base: https://host/api/v1
    - path: /api/v1/services/aigc/...
    => https://host/api/v1/services/aigc/...
    """
    parsed = urlparse((base_url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return None

    normalized_path = (path or "").strip()
    if not normalized_path:
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path or ''}"
    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"

    base_segments = [seg for seg in (parsed.path or "").split("/") if seg]
    path_segments = [seg for seg in normalized_path.split("/") if seg]
    if not path_segments:
        merged_path = "/" + "/".join(base_segments) if base_segments else "/"
        return f"{parsed.scheme}://{parsed.netloc}{merged_path}"

    lower_base = [seg.lower() for seg in base_segments]
    lower_path = [seg.lower() for seg in path_segments]
    max_overlap = min(len(lower_base), len(lower_path))
    overlap = 0
    for size in range(max_overlap, 0, -1):
        if lower_base[-size:] == lower_path[:size]:
            overlap = size
            break

    merged_segments = base_segments + path_segments[overlap:]
    merged_path = "/" + "/".join(merged_segments) if merged_segments else "/"
    return f"{parsed.scheme}://{parsed.netloc}{merged_path}"


def _build_alibaba_native_candidate_urls(cfg: ResolvedUpstreamConfig, native_path: str) -> list[str]:
    """
    Build robust candidate URLs for Alibaba native API paths.

    For compatible-mode base URLs, native APIs usually live at origin `/api/v1/...`.
    For custom base URLs/proxies, also try overlap-joined URL variants.
    """
    normalized_path = (native_path or "").strip()
    if not normalized_path:
        return []
    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"

    parsed = urlparse((cfg.base_url or "").strip())
    base_path = (parsed.path or "").lower()
    origin = _dashscope_origin_from_url(cfg.base_url)

    candidates: list[str] = []
    if origin and "/compatible-mode/" in base_path:
        candidates.append(f"{origin}{normalized_path}")

    overlap_joined = _build_url_with_segment_overlap(cfg.base_url, normalized_path)
    if overlap_joined:
        candidates.append(overlap_joined)

    candidates.append(_build_upstream_url(cfg, normalized_path))

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return deduped


def _load_qwen_image_prompt_pack(force_reload: bool = False) -> dict[str, Any]:
    return PromptPackStore.load_json_file(
        "enhancement_rules",
        "qwen_image_production_pack.json",
        force_reload=force_reload,
    )


def _load_voice_prompt_pack(force_reload: bool = False) -> dict[str, Any]:
    return PromptPackStore.load_json_file(
        "enhancement_rules",
        "voice_production_pack.json",
        force_reload=force_reload,
    )


def _load_video_prompt_pack(force_reload: bool = False) -> dict[str, Any]:
    return PromptPackStore.load_json_file(
        "enhancement_rules",
        "video_production_pack.json",
        force_reload=force_reload,
    )


class _SafePromptTemplateDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _safe_format_prompt_template(template: str, variables: dict[str, Any]) -> str:
    if not template:
        return ""
    normalized = {k: ("" if v is None else str(v)) for k, v in variables.items()}
    try:
        return template.format_map(_SafePromptTemplateDict(normalized))
    except Exception:
        return template


def _normalize_voice_prompt_profile(profile: Optional[str]) -> str:
    raw = (profile or "").strip().lower()
    if not raw:
        return "world_class"
    aliases = {
        "world_class": "world_class",
        "world-class": "world_class",
        "production": "world_class",
        "prod": "world_class",
        "default": "world_class",
    }
    return aliases.get(raw, raw)


def _normalize_video_prompt_profile(profile: Optional[str]) -> str:
    raw = (profile or "").strip().lower()
    if not raw:
        return "world_class"
    aliases = {
        "world_class": "world_class",
        "world-class": "world_class",
        "production": "world_class",
        "prod": "world_class",
        "default": "world_class",
    }
    return aliases.get(raw, raw)


def _collect_prompt_skill_quality_gates(pack: dict[str, Any], limit: int = 6) -> list[str]:
    skill = pack.get("prompt_skill")
    if not isinstance(skill, dict):
        return []
    raw = skill.get("quality_gate_checklist")
    if not isinstance(raw, list):
        return []
    collected: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        value = " ".join(item.split()).strip()
        if value:
            collected.append(value)
        if len(collected) >= limit:
            break
    return collected


def _parse_duration_seconds(duration: Optional[str], fallback: int = 30) -> int:
    raw = (duration or "").strip().lower()
    if not raw:
        return fallback
    match = re.search(r"\d{1,3}", raw)
    if not match:
        return fallback
    try:
        seconds = int(match.group(0))
        return max(5, min(180, seconds))
    except Exception:
        return fallback


_VIDEO_MARKETING_PATTERNS = (
    r"\bquang cao\b",
    r"\bqc\b",
    r"\bad\b",
    r"\bads\b",
    r"\badvert(?:isement|ising)?\b",
    r"\bcommercial\b",
    r"\bpromo(?:tional)?\b",
    r"\bmarketing\b",
    r"\bcampaign\b",
    r"\bcta\b",
    r"\bcall to action\b",
    r"\bkhuyen mai\b",
    r"\buu dai\b",
    r"\bthuong hieu\b",
    r"\bbrand film\b",
    r"\bra mat san pham\b",
)


def _normalize_matching_text(text: Optional[str]) -> str:
    lowered = (text or "").strip().lower()
    if not lowered:
        return ""
    normalized = unicodedata.normalize("NFD", lowered)
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return normalized.replace("đ", "d")


def _is_marketing_video_request(request: V1VideoGenerationRequest, base_prompt: str) -> bool:
    for field_name in ("objective", "audience", "offer", "cta", "brand_palette"):
        raw_value = getattr(request, field_name, None)
        if isinstance(raw_value, str) and raw_value.strip():
            return True

    normalized_prompt = _normalize_matching_text(base_prompt)
    if not normalized_prompt:
        return False

    return any(re.search(pattern, normalized_prompt) for pattern in _VIDEO_MARKETING_PATTERNS)


def _build_voice_directive_text(request: V1AudioSpeechRequest, base_text: str) -> str:
    pack = _load_voice_prompt_pack()
    skill = pack.get("prompt_skill") if isinstance(pack, dict) else {}
    template = ""
    if isinstance(skill, dict):
        template = str(skill.get("master_prompt_template") or "").strip()
    if not template:
        template = (
            "Generate a {duration_seconds}-second voiceover for {purpose}. Audience: {audience}. "
            "Language: {language}. Voice character: {voice_character}. Accent: {accent}. "
            "Emotional arc: {emotion_arc}. Speaking rate: {speaking_rate}. "
            "Must pronounce these terms exactly: {brand_terms}. "
            "Follow pronunciation dictionary: {pronunciation_dictionary}. "
            "Output a natural, confident read with clear pauses and no robotic cadence."
        )

    words = re.findall(r"\w+", base_text)
    estimated_duration = max(6, min(120, int(max(1, len(words)) / 2.4)))
    brand_terms = request.brand_terms or []
    brand_terms_joined = ", ".join(term.strip() for term in brand_terms if isinstance(term, str) and term.strip())

    variables = {
        "duration_seconds": estimated_duration,
        "purpose": request.purpose or "voiceover for conversion and clarity",
        "audience": request.audience or "broad consumer audience",
        "language": request.language or "Vietnamese",
        "voice_character": request.voice_character or request.voice or "natural confident narrator",
        "accent": request.accent or "neutral",
        "emotion_arc": request.emotion_arc or "clear -> engaging -> decisive CTA",
        "speaking_rate": request.speaking_rate or ("faster" if (request.speed or 1.0) > 1.05 else "natural"),
        "brand_terms": brand_terms_joined or "none",
        "pronunciation_dictionary": request.pronunciation_dictionary or "none",
    }
    rendered = _safe_format_prompt_template(template, variables).strip()
    quality_gates = _collect_prompt_skill_quality_gates(pack)
    if quality_gates:
        rendered = f"{rendered} Quality gates: {'; '.join(quality_gates)}."
    return rendered


def _replace_phrase_case_insensitive(text: str, source: str, replacement: str) -> str:
    pattern = re.compile(rf"(?<!\w){re.escape(source)}(?!\w)", re.IGNORECASE)
    return pattern.sub(replacement, text)


def _parse_pronunciation_dictionary(raw_value: Optional[str]) -> list[tuple[str, str]]:
    text = (raw_value or "").strip()
    if not text:
        return []

    mappings: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _append_mapping(source: Any, replacement: Any) -> None:
        src = str(source or "").strip()
        dst = str(replacement or "").strip()
        key = src.lower()
        if not src or not dst or key in seen:
            return
        seen.add(key)
        mappings.append((src, dst))

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        for source, replacement in parsed.items():
            _append_mapping(source, replacement)
        return mappings

    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict):
                _append_mapping(
                    item.get("term") or item.get("source") or item.get("from"),
                    item.get("pronunciation") or item.get("target") or item.get("to"),
                )
            elif isinstance(item, str):
                text = item.strip()
                for separator in ("=>", "->", ":", "="):
                    if separator in text:
                        source, replacement = text.split(separator, 1)
                        _append_mapping(source, replacement)
                        break
        return mappings

    for line in re.split(r"[\n;]+", text):
        normalized = line.strip()
        if not normalized:
            continue
        for separator in ("=>", "->", ":", "="):
            if separator in normalized:
                source, replacement = normalized.split(separator, 1)
                _append_mapping(source, replacement)
                break
    return mappings


def _resolve_voice_language_code(request: V1AudioSpeechRequest, base_text: str) -> str:
    language = (request.language or "").strip().lower()
    if language:
        if language.startswith("vi") or "vietnam" in language:
            return "vi"
        if language.startswith("en") or "english" in language:
            return "en"
    return "vi" if _VIETNAMESE_CHAR_RE.search(base_text or "") else "en"


def _canonicalize_brand_terms(text: str, brand_terms: Optional[list[str]]) -> str:
    current = text
    for brand_term in brand_terms or []:
        value = str(brand_term or "").strip()
        if not value:
            continue
        current = _replace_phrase_case_insensitive(current, value, value)
    return current


def _apply_pronunciation_overrides(text: str, raw_dictionary: Optional[str]) -> str:
    current = text
    for source, replacement in _parse_pronunciation_dictionary(raw_dictionary):
        current = _replace_phrase_case_insensitive(current, source, replacement)
    return current


def _expand_voice_symbols(text: str, language_code: str) -> str:
    replacements = {
        "vi": {
            "&": " và ",
            "%": " phần trăm ",
            "@": " a còng ",
        },
        "en": {
            "&": " and ",
            "%": " percent ",
            "@": " at ",
        },
    }
    current = text
    for source, replacement in replacements.get(language_code, {}).items():
        current = current.replace(source, replacement)
    return current


def _insert_breath_breaks(text: str, language_code: str) -> str:
    conjunctions = {
        "vi": {"và", "nhưng", "để", "vì", "khi", "nếu"},
        "en": {"and", "but", "because", "while", "when", "if", "so"},
    }
    selected = conjunctions.get(language_code, conjunctions["en"])
    sentences = re.split(r"(?<=[.!?…])\s+", text)
    rebuilt: list[str] = []

    for sentence in sentences:
        normalized = sentence.strip()
        if not normalized:
            continue
        words = normalized.split()
        if len(words) > 22 and "," not in normalized:
            midpoint = len(words) // 2
            split_index = -1
            for index, word in enumerate(words):
                token = re.sub(r"[^\wÀ-ỹ]+", "", word, flags=re.UNICODE).lower()
                if token in selected and 5 <= index <= len(words) - 5:
                    if split_index == -1 or abs(index - midpoint) < abs(split_index - midpoint):
                        split_index = index
            if split_index > 0:
                normalized = " ".join(words[:split_index]) + ", " + " ".join(words[split_index:])
        rebuilt.append(normalized)

    return "\n".join(rebuilt) if len(rebuilt) > 1 else " ".join(rebuilt)


def _optimize_voice_script_for_speech(request: V1AudioSpeechRequest, base_text: str) -> str:
    language_code = _resolve_voice_language_code(request, base_text)
    normalized = re.sub(r"[ \t]+", " ", base_text)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    normalized = _canonicalize_brand_terms(normalized, request.brand_terms)
    normalized = _apply_pronunciation_overrides(normalized, request.pronunciation_dictionary)
    normalized = _expand_voice_symbols(normalized, language_code)
    normalized = re.sub(r"\s{2,}", " ", normalized).strip()
    normalized = _insert_breath_breaks(normalized, language_code)
    if len(normalized) >= 40 and not re.search(r"[.!?…][\"')\]]?$", normalized):
        normalized += "."
    return normalized


def _enhance_voice_prompt_input(request: V1AudioSpeechRequest) -> str:
    base_text = (request.input or "").strip()
    if not base_text:
        return base_text
    if not bool(request.prompt_enhance):
        return base_text
    if _normalize_voice_prompt_profile(request.prompt_profile) != "world_class":
        return base_text

    # Keep the spoken text literal, but make it more TTS-friendly by
    # normalizing pronunciation-sensitive terms and adding breath cues.
    normalized = _optimize_voice_script_for_speech(request, base_text)
    directive = _build_voice_directive_text(request, normalized)
    if directive:
        logger.info(
            "Voice prompt skill applied profile=%s input_chars=%s output_chars=%s directive_chars=%s",
            _normalize_voice_prompt_profile(request.prompt_profile),
            len(base_text),
            len(normalized),
            len(directive),
        )
    return normalized


def _build_video_directive_text(request: V1VideoGenerationRequest, base_prompt: str) -> str:
    marketing_mode = _is_marketing_video_request(request, base_prompt)
    pack = _load_video_prompt_pack()
    skill = pack.get("prompt_skill") if marketing_mode and isinstance(pack, dict) else {}
    template = ""
    if isinstance(skill, dict):
        template = str(skill.get("master_prompt_template") or "").strip()
    if not template and marketing_mode:
        template = (
            "Create a {format} promotional video, duration {duration_seconds}s. Objective: {objective}. "
            "Audience: {audience}. Offer context: {offer}. Tone: {tone}. Visual style: {reference_style}. "
            "Brand palette: {brand_palette}. Keep the hook concise, make the pacing intentional, and reserve "
            "any closing action for the final beat. Use on-screen text, logos, end cards, or CTA buttons only "
            "when the user explicitly asks for them. Provide a concise shot plan with camera motion, scene "
            "composition, and transitions."
        )
    if not template and not marketing_mode:
        template = (
            "Create a {format} cinematic scene, duration {duration_seconds}s. Preserve the user's requested "
            "subject, mood, and environment. Tone: {tone}. Visual style: {reference_style}. Focus on natural "
            "motion, believable light, atmosphere, and smooth shot continuity. Avoid turning this into an "
            "advertisement, promo, product reel, or branded content. Avoid visible text, captions, subtitles, "
            "logos, UI, buttons, end cards, product cards, or watermarks unless the user explicitly asks for them."
        )

    format_hint = "video ad" if marketing_mode else "video"
    if request.aspect_ratio:
        format_hint = f"video ({request.aspect_ratio.strip()})"
    elif request.size:
        format_hint = f"video ({request.size.strip()})"

    variables = {
        "format": format_hint,
        "duration_seconds": _parse_duration_seconds(request.duration, fallback=30),
        "objective": request.objective or "communicate the key message clearly without feeling pushy",
        "audience": request.audience or "the intended viewer described by the brief",
        "offer": request.offer or "the core value or product focus already implied by the brief",
        "tone": request.tone or ("modern, confident, high-trust" if marketing_mode else "calm, immersive, natural"),
        "reference_style": request.reference_style or request.style or ("clean cinematic commercial" if marketing_mode else "cinematic atmospheric realism"),
        "brand_palette": request.brand_palette or "use only if the brief explicitly requires brand colors",
        "cta": request.cta or "a subtle closing action aligned to the user's brief",
    }
    rendered = _safe_format_prompt_template(template, variables).strip()
    quality_gates = _collect_prompt_skill_quality_gates(pack) if marketing_mode else []
    if quality_gates:
        rendered = f"{rendered} Quality gates: {'; '.join(quality_gates)}."
    return rendered


def _enhance_video_generation_prompt(request: V1VideoGenerationRequest) -> str:
    base_prompt = (request.prompt or "").strip()
    if not base_prompt:
        return base_prompt
    if not bool(request.prompt_enhance):
        return base_prompt
    if "Internal video direction (do not render as overlay text):" in base_prompt:
        return base_prompt
    if _normalize_video_prompt_profile(request.prompt_profile) != "world_class":
        return base_prompt

    marketing_mode = _is_marketing_video_request(request, base_prompt)
    directive = _build_video_directive_text(request, base_prompt)
    if not directive:
        return base_prompt

    logger.info(
        "Video prompt skill applied profile=%s marketing_mode=%s input_chars=%s directive_chars=%s",
        _normalize_video_prompt_profile(request.prompt_profile),
        marketing_mode,
        len(base_prompt),
        len(directive),
    )

    enhanced = (
        base_prompt
        + "\n\n"
        + "Internal video direction (do not render as overlay text): "
        + directive
    ).strip()
    if len(enhanced) <= 3600:
        return enhanced

    compact = (
        "keep a concise opening hook, maintain shot continuity, avoid visual artifacts, and use any closing action "
        "only if the brief is explicitly promotional"
        if marketing_mode
        else "preserve the requested mood and subject, maintain shot continuity, avoid visual artifacts, and avoid "
        "visible text or branding unless the user explicitly asks for them"
    )
    return (
        base_prompt
        + "\n\n"
        + "Internal video direction (do not render as overlay text): "
        + compact
    ).strip()


def _extract_quoted_text_segments(prompt: str) -> list[str]:
    if not prompt:
        return []
    segments = re.findall(r"\"([^\"]{1,240})\"", prompt)
    cleaned: list[str] = []
    for segment in segments:
        value = " ".join(segment.split()).strip()
        if value:
            cleaned.append(value)
    return cleaned


def _collect_qwen_negative_constraints(pack: dict[str, Any], limit: int = 12) -> list[str]:
    banks = pack.get("negative_prompt_banks")
    if not isinstance(banks, dict):
        return []

    ordered_keys = ["typography_defects", "layout_defects", "image_defects"]
    seen: set[str] = set()
    collected: list[str] = []

    for key in ordered_keys:
        values = banks.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, str):
                continue
            normalized = " ".join(value.split()).strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            collected.append(normalized)
            if len(collected) >= limit:
                return collected
    return collected


def _collect_qwen_quality_constraints(pack: dict[str, Any]) -> list[str]:
    defaults = pack.get("defaults")
    quality_contract = defaults.get("quality_contract") if isinstance(defaults, dict) else None
    if not isinstance(quality_contract, list):
        return [
            "high legibility typography",
            "clean kerning",
            "consistent baseline",
            "no duplicated letters",
            "no random symbols",
            "no gibberish text",
            "sharp edges",
            "balanced spacing",
        ]

    cleaned: list[str] = []
    for item in quality_contract:
        if not isinstance(item, str):
            continue
        value = " ".join(item.split()).strip()
        if value:
            cleaned.append(value.lower())
    return cleaned


def _enhance_qwen_image_prompt(
    prompt: str,
    *,
    for_edit: bool = False,
    profile: str = "qwen_vip",
) -> str:
    base_prompt = (prompt or "").strip()
    if not base_prompt:
        return base_prompt
    if "Internal quality requirements (do not render as image text):" in base_prompt:
        return base_prompt
    if profile != "qwen_vip":
        return base_prompt

    pack = _load_qwen_image_prompt_pack()
    quality_constraints = _collect_qwen_quality_constraints(pack)
    quoted_segments = _extract_quoted_text_segments(base_prompt)
    has_vietnamese = bool(_VIETNAMESE_CHAR_RE.search(base_prompt))

    instruction_parts: list[str] = []
    if for_edit:
        instruction_parts.append("preserve original composition and apply only requested edits")

    if quoted_segments:
        limited_segments = quoted_segments[:4]
        joined = "; ".join(f"\"{segment}\"" for segment in limited_segments)
        instruction_parts.append(f"render only these exact text strings when text is needed: {joined}")
        instruction_parts.append("do not add extra words, labels, symbols, or placeholder text")
        instruction_parts.append("keep requested text large, high-contrast, and cleanly spaced")
    if has_vietnamese and quoted_segments:
        instruction_parts.append("preserve vietnamese diacritics accurately and avoid decorative/script fonts")
        instruction_parts.append("prefer short text lines for better readability")
    if not quoted_segments:
        instruction_parts.append("do not introduce visible text unless explicitly requested by the user")

    positive_quality = [
        q for q in quality_constraints
        if "no " not in q and "gibberish" not in q and "random symbols" not in q and "duplicated" not in q
    ]
    if positive_quality:
        instruction_parts.append("prioritize " + ", ".join(positive_quality[:4]))
    else:
        instruction_parts.append("prioritize clean composition, readable typography, balanced spacing, and sharp edges")

    enhanced = (
        base_prompt
        + "\n\n"
        + "Internal quality requirements (do not render as image text): "
        + ". ".join(instruction_parts).strip()
    ).strip()
    if len(enhanced) <= 2600:
        return enhanced

    # Safety fallback when user prompt is already very long.
    compact_parts = [
        "do not render instruction words as image text",
        "keep text highly legible with clean spacing",
    ]
    if quoted_segments:
        compact_parts.append("only render explicitly quoted strings")
    if has_vietnamese and quoted_segments:
        compact_parts.append("preserve vietnamese diacritics")
    return (
        base_prompt
        + "\n\n"
        + "Internal quality requirements (do not render as image text): "
        + ". ".join(compact_parts)
    ).strip()


def _normalize_qwen_prompt_profile(profile: Optional[str]) -> str:
    raw = (profile or "").strip().lower()
    if not raw:
        return "qwen_vip"
    aliases = {
        "qwen_vip": "qwen_vip",
        "vip": "qwen_vip",
        "production": "qwen_vip",
        "prod": "qwen_vip",
    }
    return aliases.get(raw, raw)


DEFAULT_ANTHROPIC_MAX_TOKENS = 4096


def _build_upstream_models_url(cfg: ResolvedUpstreamConfig) -> str:
    """Resolve provider-specific models listing endpoint."""
    if cfg.api_provider == "gemini":
        return _build_upstream_url(cfg, "/v1beta/models")
    return _build_upstream_url(cfg, "/v1/models")


def _touch_legacy_key_usage(db: Session, db_key_id: Optional[str]) -> None:
    """No-op compatibility hook after removing server-side key storage."""
    del db, db_key_id


EXT_TO_MIME = {v: k for k, v in MIME_TO_EXT.items()}
MAX_REMOTE_IMAGE_SIZE = 20 * 1024 * 1024


def _build_data_url(mime_type: str, content: bytes) -> str:
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _decode_data_url(value: str) -> Optional[tuple[bytes, str]]:
    if not value.startswith("data:") or "," not in value:
        return None
    try:
        header, payload = value.split(",", 1)
        mime_type = header[5:].split(";")[0].strip().lower() or "image/png"
        try:
            content = base64.b64decode(payload, validate=True)
        except Exception:
            content = base64.b64decode(payload)
        if not content:
            return None
        return content, mime_type
    except Exception:
        return None


def _decode_raw_base64(value: str) -> Optional[bytes]:
    if not value:
        return None
    compact = value.strip().replace("\n", "").replace("\r", "")
    if not compact:
        return None
    if not re.fullmatch(r"[A-Za-z0-9+/=]+", compact):
        return None
    try:
        content = base64.b64decode(compact, validate=True)
        return content if content else None
    except Exception:
        return None


def _load_local_serve_image(value: str, owner_user_id: str) -> Optional[tuple[bytes, str, str]]:
    parsed = urlparse(value)
    path = parsed.path or value
    marker = "/api/images/serve/"
    if marker not in path:
        return None
    image_path = path.split(marker, 1)[1]
    return load_owned_image_from_serve_path(owner_user_id, image_path)


async def _download_image(url: str) -> Optional[tuple[bytes, str, str]]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None

    try:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
            response = await client.get(url)
    except Exception:
        return None

    if response.status_code != 200:
        return None

    content = response.content or b""
    if not content or len(content) > MAX_REMOTE_IMAGE_SIZE:
        return None

    content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
    if not content_type.startswith("image/"):
        content_type = "image/png"

    ext = MIME_TO_EXT.get(content_type, "png")
    path_name = Path(parsed.path or "").name
    if path_name and "." in path_name:
        filename = Path(path_name).name
    else:
        filename = f"downloaded_{uuid.uuid4().hex[:8]}.{ext}"
    return content, content_type, filename


async def _resolve_image_input_to_bytes(
    value: str,
    field_name: str,
    owner_user_id: str,
) -> tuple[bytes, str, str]:
    cleaned = (value or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail=f"{field_name} is required")

    from_data_url = _decode_data_url(cleaned)
    if from_data_url:
        content, mime_type = from_data_url
        ext = MIME_TO_EXT.get(mime_type, "png")
        return content, mime_type, f"{field_name}_{uuid.uuid4().hex[:8]}.{ext}"

    from_local_serve = _load_local_serve_image(cleaned, owner_user_id)
    if from_local_serve:
        return from_local_serve

    raw_b64 = _decode_raw_base64(cleaned)
    if raw_b64:
        return raw_b64, "image/png", f"{field_name}_{uuid.uuid4().hex[:8]}.png"

    from_remote = await _download_image(cleaned)
    if from_remote:
        return from_remote

    raise HTTPException(
        status_code=400,
        detail=(
            f"{field_name} must be a data URL, raw base64 image, "
            "a local /api/images/serve/* URL, or an HTTP(S) image URL"
        ),
    )


def _guess_audio_mime_type(filename: str, fallback: str = "audio/mpeg") -> str:
    lowered = (filename or "").strip().lower()
    if lowered.endswith(".wav"):
        return "audio/wav"
    if lowered.endswith(".mp3"):
        return "audio/mpeg"
    if lowered.endswith(".m4a"):
        return "audio/mp4"
    if lowered.endswith(".aac"):
        return "audio/aac"
    if lowered.endswith(".ogg"):
        return "audio/ogg"
    if lowered.endswith(".flac"):
        return "audio/flac"
    if lowered.endswith(".webm"):
        return "audio/webm"
    return fallback


def _build_audio_data_url(mime_type: str, content: bytes) -> str:
    normalized = (mime_type or "").strip().lower()
    if not normalized.startswith("audio/"):
        normalized = "audio/mpeg"
    return _build_data_url(normalized, content)


async def _download_audio(url: str, timeout_seconds: float = 40.0) -> Optional[tuple[bytes, str]]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
            response = await client.get(url)
    except Exception:
        return None
    if response.status_code != 200:
        return None
    content = response.content or b""
    if not content:
        return None
    content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
    if not content_type:
        content_type = _guess_audio_mime_type(parsed.path or "")
    return content, content_type


def _extract_audio_artifacts_from_payload(payload: Any) -> dict[str, Any]:
    artifact: dict[str, Any] = {}
    if not isinstance(payload, dict):
        return artifact

    output = payload.get("output")
    if not isinstance(output, dict):
        output = {}

    for key in ("audio_url", "url", "speech_url"):
        value = output.get(key)
        if isinstance(value, str) and value.strip():
            artifact["audio_url"] = value.strip()
            break

    audio_obj = output.get("audio")
    if isinstance(audio_obj, dict):
        audio_url = audio_obj.get("url")
        if isinstance(audio_url, str) and audio_url.strip():
            artifact["audio_url"] = audio_url.strip()
        audio_data = audio_obj.get("data") or audio_obj.get("audio")
        if isinstance(audio_data, str) and audio_data.strip():
            artifact["audio_data"] = audio_data.strip()
        audio_format = audio_obj.get("format")
        if isinstance(audio_format, str) and audio_format.strip():
            artifact["audio_format"] = audio_format.strip().lower()

    choices = output.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                if isinstance(part.get("audio"), str) and part.get("audio").strip():
                    artifact.setdefault("audio_url", part.get("audio").strip())
                if isinstance(part.get("audio_url"), str) and part.get("audio_url").strip():
                    artifact.setdefault("audio_url", part.get("audio_url").strip())
                if isinstance(part.get("data"), str) and part.get("data").strip():
                    artifact.setdefault("audio_data", part.get("data").strip())
                if isinstance(part.get("format"), str) and part.get("format").strip():
                    artifact.setdefault("audio_format", part.get("format").strip().lower())

    return artifact


def _extract_video_urls_from_payload(payload: Any) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    def add_url(value: Any):
        if not isinstance(value, str):
            return
        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            return
        seen.add(cleaned)
        urls.append(cleaned)

    def looks_like_video_url(value: str) -> bool:
        lowered = value.lower()
        if not lowered.startswith(("http://", "https://")):
            return False
        video_markers = (
            ".mp4",
            ".webm",
            ".mov",
            ".m3u8",
            ".avi",
            "video",
            "stream",
            "download",
        )
        return any(marker in lowered for marker in video_markers)

    if not isinstance(payload, dict):
        return urls

    def add_video_container(container: Any):
        if not isinstance(container, dict):
            return
        for key in ("url", "video_url", "download_url"):
            add_url(container.get(key))
        video_obj = container.get("video")
        if isinstance(video_obj, dict):
            for key in ("url", "video_url", "download_url"):
                add_url(video_obj.get(key))
        videos = container.get("videos")
        if isinstance(videos, list):
            for video in videos:
                if isinstance(video, str):
                    add_url(video)
                elif isinstance(video, dict):
                    for key in ("url", "video_url", "download_url"):
                        add_url(video.get(key))

    data = payload.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                add_url(item)
                continue
            if not isinstance(item, dict):
                continue
            for key in ("url", "video_url", "download_url"):
                add_url(item.get(key))
            add_video_container(item)
    elif isinstance(data, dict):
        add_video_container(data)

    output = payload.get("output")
    if isinstance(output, dict):
        add_video_container(output)

    for nested_key in ("result", "task"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            add_video_container(nested)

    choices = output.get("choices") if isinstance(output, dict) else payload.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                for key in ("video", "video_url", "url"):
                    add_url(part.get(key))
                video_obj = part.get("video")
                if isinstance(video_obj, dict):
                    for key in ("url", "video_url", "download_url"):
                        add_url(video_obj.get(key))

    for key in ("url", "video_url", "download_url"):
        add_url(payload.get(key))

    # Final safety net for proxy schemas that return custom URL key names.
    def walk(node: Any, depth: int = 0):
        if depth > 8:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, str):
                    key_name = str(key).lower()
                    if "url" in key_name or "video" in key_name:
                        add_url(value)
                    elif looks_like_video_url(value):
                        add_url(value)
                elif isinstance(value, (dict, list)):
                    walk(value, depth + 1)
            return
        if isinstance(node, list):
            for item in node:
                if isinstance(item, str) and looks_like_video_url(item):
                    add_url(item)
                elif isinstance(item, (dict, list)):
                    walk(item, depth + 1)

    walk(payload)

    return urls


def _extract_video_task_from_payload(payload: Any) -> tuple[Optional[str], Optional[str]]:
    if not isinstance(payload, dict):
        return None, None

    def resolve_from_container(container: Any) -> tuple[Optional[str], Optional[str]]:
        if not isinstance(container, dict):
            return None, None

        task_id: Optional[str] = None
        task_status: Optional[str] = None

        for key in ("task_id", "taskId", "taskID"):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                task_id = value.strip()
                break

        for key in ("task_status", "taskStatus", "status", "state", "task_state", "phase", "job_status"):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                task_status = value.strip()
                break

        if not task_id and task_status:
            fallback_id = container.get("id")
            if isinstance(fallback_id, str) and fallback_id.strip():
                task_id = fallback_id.strip()

        return task_id, task_status

    candidate_containers: list[Any] = [
        payload.get("output"),
        payload.get("data"),
        payload.get("result"),
        payload.get("task"),
        payload,
    ]

    for container in candidate_containers:
        task_id, task_status = resolve_from_container(container)
        if task_id or task_status:
            return task_id, task_status

    return None, None


def _extract_video_failure_message(payload: Any) -> Optional[str]:
    if not isinstance(payload, dict):
        return None

    seen_containers: set[int] = set()
    ignored_messages = {
        "failed",
        "error",
        "cancelled",
        "canceled",
        "task failed",
        "video generation failed",
    }

    def _clean_message(value: Any) -> Optional[str]:
        if not isinstance(value, str):
            return None

        cleaned = " ".join(value.split())
        if not cleaned:
            return None

        if cleaned[:1] in {"{", "["}:
            try:
                parsed = json.loads(cleaned)
            except Exception:
                parsed = None
            if parsed is not None:
                nested = _extract_from_value(parsed)
                if nested:
                    return nested

        if cleaned.strip().lower().rstrip(".!") in ignored_messages:
            return None

        return _normalize_public_error_message(cleaned)

    def _extract_from_container(container: Any) -> Optional[str]:
        if not isinstance(container, dict):
            return None

        container_id = id(container)
        if container_id in seen_containers:
            return None
        seen_containers.add(container_id)

        for key in (
            "error_message",
            "errorMessage",
            "failure_reason",
            "failureReason",
            "error_description",
            "errorDescription",
            "detail",
            "reason",
        ):
            nested = _extract_from_value(container.get(key))
            if nested:
                return nested

        nested = _extract_from_value(container.get("error"))
        if nested:
            return nested

        for key in ("message", "msg", "title"):
            nested = _extract_from_value(container.get(key))
            if nested:
                return nested

        nested = _extract_from_value(container.get("errors"))
        if nested:
            return nested

        for key in ("response", "output", "result", "task", "data"):
            nested = _extract_from_value(container.get(key))
            if nested:
                return nested

        return None

    def _extract_from_value(value: Any) -> Optional[str]:
        if isinstance(value, dict):
            return _extract_from_container(value)
        if isinstance(value, list):
            for item in value:
                nested = _extract_from_value(item)
                if nested:
                    return nested
            return None
        return _clean_message(value)

    for candidate in (
        payload.get("error"),
        payload.get("output"),
        payload.get("result"),
        payload.get("task"),
        payload.get("data"),
        payload,
    ):
        nested = _extract_from_value(candidate)
        if nested:
            return nested

    return None


def _extract_video_rows_from_container(container: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not isinstance(container, dict):
        return rows

    row: dict[str, Any] = {}
    for key in ("url", "video_url", "download_url", "b64_json", "mime_type", "id", "thumbnail_url", "revised_prompt"):
        value = container.get(key)
        if isinstance(value, str) and value.strip():
            row[key] = value.strip()
    if "video_url" in row and "url" not in row:
        row["url"] = row["video_url"]
    if row:
        rows.append(row)

    nested_video = container.get("video")
    if isinstance(nested_video, dict):
        rows.extend(_extract_video_rows_from_container(nested_video))

    nested_videos = container.get("videos")
    if isinstance(nested_videos, list):
        for item in nested_videos:
            if isinstance(item, dict):
                rows.extend(_extract_video_rows_from_container(item))
            elif isinstance(item, str) and item.strip():
                rows.append({"url": item.strip()})

    return rows


def _normalize_video_generation_response(payload: Any) -> dict[str, Any]:
    created = int(datetime.now().timestamp())
    if not isinstance(payload, dict):
        return {"created": created, "data": []}

    rows: list[dict[str, Any]] = []
    data = payload.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str) and item.strip():
                rows.append({"url": item.strip()})
                continue
            if not isinstance(item, dict):
                continue
            rows.extend(_extract_video_rows_from_container(item))
    elif isinstance(data, dict):
        rows.extend(_extract_video_rows_from_container(data))

    output = payload.get("output")
    if not rows and isinstance(output, dict):
        rows.extend(_extract_video_rows_from_container(output))

    result_container = payload.get("result")
    if not rows and isinstance(result_container, dict):
        rows.extend(_extract_video_rows_from_container(result_container))

    if not rows:
        for video_url in _extract_video_urls_from_payload(payload):
            rows.append({"url": video_url})

    result = dict(payload)
    result["created"] = payload.get("created") if isinstance(payload.get("created"), int) else created
    result["data"] = rows
    task_id, task_status = _extract_video_task_from_payload(payload)
    if task_id:
        result["task_id"] = task_id
    if task_status:
        result["task_status"] = task_status
    return result


def _normalize_video_generation_response_for_client(
    payload: Any,
    cfg: ResolvedUpstreamConfig,
) -> dict[str, Any]:
    normalized = _normalize_video_generation_response(payload)
    rows = normalized.get("data")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            for key in ("url", "video_url", "download_url", "thumbnail_url"):
                value = row.get(key)
                if isinstance(value, str) and value.strip():
                    cleaned = value.strip()
                    if cleaned.startswith(("http://", "https://", "data:")):
                        row[key] = cleaned
                    else:
                        row[key] = _build_upstream_url(cfg, cleaned)

    task_status = normalized.get("task_status")
    if _normalize_video_task_status(task_status) in {"FAILED", "ERROR", "CANCELED", "CANCELLED"}:
        failure_message = _extract_video_failure_message(payload)
        if failure_message:
            normalized["error_message"] = failure_message

    if isinstance(rows, list) and rows and _normalize_video_task_status(task_status) in {"SUCCEEDED", "SUCCESS", "COMPLETED", "DONE"}:
        normalized.pop("task_status", None)

    return normalized


def _build_video_generation_candidate_urls(cfg: ResolvedUpstreamConfig) -> list[str]:
    candidates: list[str] = []
    paths = [
        "/v1/videos/generations",
        "/v1/videos",
        "/v1/video/generations",
        "/v1/video",
    ]
    for path in paths:
        url = _build_upstream_url(cfg, path)
        if url:
            candidates.append(url)

    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _summarize_upstream_candidates(urls: list[str], max_items: int = 4) -> str:
    summarized: list[str] = []
    for item in urls[:max_items]:
        parsed = urlparse((item or "").strip())
        path = (parsed.path or "/").strip() or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        summarized.append(path)
    return ", ".join(summarized)


async def _fetch_upstream_capabilities(
    cfg: ResolvedUpstreamConfig,
    headers: dict[str, str],
) -> Optional[dict[str, Any]]:
    capabilities_url = _build_upstream_url(cfg, "/v1/capabilities")
    if not capabilities_url:
        return None
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(capabilities_url, headers=headers)
    except httpx.RequestError:
        return None
    if not response.is_success:
        return None
    try:
        payload = response.json()
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _normalize_video_task_status(task_status: Optional[str]) -> str:
    return (task_status or "").strip().upper()


def _is_pending_video_task_status(task_status: Optional[str]) -> bool:
    return _normalize_video_task_status(task_status) in {
        "PENDING",
        "RUNNING",
        "QUEUED",
        "SUBMITTED",
        "IN_PROGRESS",
        "PROCESSING",
    }


def _is_terminal_video_task_status(task_status: Optional[str]) -> bool:
    return _normalize_video_task_status(task_status) in {
        "SUCCEEDED",
        "SUCCESS",
        "COMPLETED",
        "DONE",
        "FAILED",
        "ERROR",
        "CANCELED",
        "CANCELLED",
    }


def _build_video_task_candidate_urls(cfg: ResolvedUpstreamConfig, task_id: str) -> list[str]:
    normalized_task_id = quote_plus((task_id or "").strip())
    if not normalized_task_id:
        return []

    candidates: list[str] = []
    generic_paths = [
        f"/v1/videos/{normalized_task_id}",
        f"/v1/videos/generations/{normalized_task_id}",
        f"/v1/videos/tasks/{normalized_task_id}",
        f"/v1/video/{normalized_task_id}",
        f"/v1/video/generations/{normalized_task_id}",
        f"/v1/video/tasks/{normalized_task_id}",
        f"/tasks/{normalized_task_id}",
    ]
    for path in generic_paths:
        url = _build_upstream_url(cfg, path)
        if url:
            candidates.append(url)

    if cfg.api_provider == "alibaba":
        candidates.extend(_build_alibaba_native_candidate_urls(cfg, f"/api/v1/tasks/{normalized_task_id}"))

    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


async def _fetch_video_generation_task_payload(
    client: httpx.AsyncClient,
    cfg: ResolvedUpstreamConfig,
    task_id: str,
    headers: dict[str, str],
) -> VideoTaskFetchResult:
    candidate_urls = _build_video_task_candidate_urls(cfg, task_id)
    if not candidate_urls:
        return VideoTaskFetchResult(endpoint_not_supported=True)

    last_response: Optional[httpx.Response] = None
    last_connection_error: Optional[Exception] = None
    endpoint_not_supported = False
    endpoint_supported = False
    transient_upstream_failure = False

    for task_url in candidate_urls:
        try:
            response = await client.get(task_url, headers=headers, timeout=45.0)
        except httpx.RequestError as exc:
            last_connection_error = exc
            continue

        last_response = response
        if response.status_code in {404, 405}:
            endpoint_not_supported = True
            continue
        if response.status_code in {502, 503, 504}:
            transient_upstream_failure = True
            logger.warning(
                "Video task lookup got transient upstream failure provider=%s base_url=%s task_id=%s url=%s status=%s",
                cfg.api_provider,
                cfg.base_url,
                task_id,
                task_url,
                response.status_code,
            )
            continue
        if not response.is_success:
            _raise_upstream_http_exception(response, "videos.generations.task")

        endpoint_supported = True
        content_type = (response.headers.get("content-type") or "").lower()
        if "application/json" not in content_type:
            return VideoTaskFetchResult(
                binary_content=response.content,
                binary_media_type=response.headers.get("content-type", "application/octet-stream"),
            )

        try:
            payload = response.json()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            return VideoTaskFetchResult(payload=payload)
        if isinstance(payload, list):
            rows: list[dict[str, Any]] = []
            for item in payload:
                if isinstance(item, str) and item.strip():
                    rows.append({"url": item.strip()})
                elif isinstance(item, dict):
                    rows.append(item)
            return VideoTaskFetchResult(payload={
                "task_id": task_id,
                "task_status": "SUCCEEDED" if rows else "PENDING",
                "data": rows,
            })
        if isinstance(payload, str) and payload.strip():
            lowered = payload.strip().lower()
            if any(token in lowered for token in ("success", "succeeded", "done", "completed")):
                normalized_status = "SUCCEEDED"
            elif any(token in lowered for token in ("fail", "error", "cancel")):
                normalized_status = "FAILED"
            else:
                normalized_status = "PENDING"
            return VideoTaskFetchResult(payload={
                "task_id": task_id,
                "task_status": normalized_status,
                "message": payload.strip()[:400],
                "data": [],
            })

        logger.warning(
            "Video task lookup returned unsupported payload type provider=%s base_url=%s task_id=%s url=%s type=%s",
            cfg.api_provider,
            cfg.base_url,
            task_id,
            task_url,
            type(payload).__name__,
        )

    if last_response is None and last_connection_error is not None:
        logger.warning(
            "Video task lookup connection failed provider=%s base_url=%s task_id=%s error=%s",
            cfg.api_provider,
            cfg.base_url,
            task_id,
            last_connection_error,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "api_connection_error",
                "message": f"Cannot connect to {cfg.api_provider} API.",
            },
        )

    if endpoint_not_supported and not endpoint_supported:
        return VideoTaskFetchResult(endpoint_not_supported=True)

    if transient_upstream_failure:
        return VideoTaskFetchResult(payload={
            "task_id": task_id,
            "task_status": "PENDING",
            "data": [],
        })

    if endpoint_supported:
        return VideoTaskFetchResult(payload={
            "task_id": task_id,
            "task_status": "PENDING",
            "data": [],
        })

    if last_response is not None:
        _raise_upstream_http_exception(last_response, "videos.generations.task")

    return VideoTaskFetchResult()


async def _poll_video_generation_task_result(
    client: httpx.AsyncClient,
    cfg: ResolvedUpstreamConfig,
    task_id: str,
    headers: dict[str, str],
    *,
    timeout_seconds: int = 180,
    interval_seconds: float = 3.0,
) -> Optional[dict[str, Any]]:
    deadline = perf_counter() + timeout_seconds
    last_payload: Optional[dict[str, Any]] = None

    while perf_counter() < deadline:
        result = await _fetch_video_generation_task_payload(
            client,
            cfg,
            task_id,
            headers,
        )
        payload = result.payload
        endpoint_not_supported = result.endpoint_not_supported
        if endpoint_not_supported:
            return last_payload
        if isinstance(payload, dict):
            last_payload = payload
            normalized = _normalize_video_generation_response(payload)
            if normalized.get("data") or _is_terminal_video_task_status(normalized.get("task_status")):
                return payload
        await asyncio.sleep(interval_seconds)

    return last_payload


def _decode_maybe_base64_audio(value: str) -> Optional[bytes]:
    trimmed = (value or "").strip()
    if not trimmed:
        return None
    decoded_data_url = _decode_data_url(trimmed)
    if decoded_data_url:
        return decoded_data_url[0]
    return _decode_raw_base64(trimmed)


def _build_upstream_file_headers(cfg: ResolvedUpstreamConfig) -> dict[str, str]:
    headers = _build_upstream_auth_headers(cfg)
    headers.pop("Content-Type", None)
    return headers


def _build_alibaba_realtime_ws_url(base_url: str, model: str) -> Optional[str]:
    parsed = urlparse((base_url or "").strip())
    if not parsed.netloc:
        return None
    scheme = "wss" if parsed.scheme == "https" else "ws"
    model_encoded = quote_plus((model or "").strip())
    if not model_encoded:
        return None
    return f"{scheme}://{parsed.netloc}/api-ws/v1/realtime?model={model_encoded}"


def _message_content_to_text(content: Any) -> str:
    """Best-effort text extraction from mixed OpenAI/Anthropic/Gemini message content."""
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            extracted = _message_content_to_text(item)
            if extracted:
                parts.append(extracted)
        return "".join(parts)

    if isinstance(content, dict):
        text_value = content.get("text")
        if isinstance(text_value, str):
            return text_value
        if isinstance(text_value, dict):
            nested_text = text_value.get("value")
            if isinstance(nested_text, str):
                return nested_text

        for key in ("content", "parts", "value", "delta", "output_text"):
            nested = content.get(key)
            extracted = _message_content_to_text(nested)
            if extracted:
                return extracted

    return ""


def _extract_image_url_from_message_part(part: Any) -> Optional[str]:
    if not isinstance(part, dict):
        return None

    if part.get("type") != "image_url":
        return None

    image_url = part.get("image_url")
    if isinstance(image_url, str):
        return image_url.strip() or None
    if isinstance(image_url, dict):
        url = image_url.get("url")
        if isinstance(url, str):
            return url.strip() or None
    return None


def _convert_openai_content_to_anthropic_blocks(content: Any) -> list[dict]:
    """Convert OpenAI-style content into Anthropic message blocks."""
    if isinstance(content, str):
        text = content.strip()
        return [{"type": "text", "text": text}] if text else []

    blocks: list[dict] = []
    if isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                text = part.strip()
                if text:
                    blocks.append({"type": "text", "text": text})
                continue

            if not isinstance(part, dict):
                continue

            part_type = str(part.get("type") or "").strip().lower()
            if part_type == "text":
                text = _message_content_to_text(part.get("text"))
                if text:
                    blocks.append({"type": "text", "text": text})
                continue

            image_url = _extract_image_url_from_message_part(part)
            if image_url:
                decoded = _decode_data_url(image_url)
                if decoded:
                    image_bytes, mime_type = decoded
                    blocks.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime_type,
                                "data": base64.b64encode(image_bytes).decode("ascii"),
                            },
                        }
                    )
                else:
                    blocks.append({"type": "text", "text": f"[image_url] {image_url}"})
                continue

            text = _message_content_to_text(part)
            if text:
                blocks.append({"type": "text", "text": text})

    if blocks:
        return blocks

    fallback_text = _message_content_to_text(content).strip()
    if fallback_text:
        return [{"type": "text", "text": fallback_text}]
    return []


def _convert_openai_messages_to_anthropic(messages: list[dict]) -> tuple[list[dict], str]:
    """Convert OpenAI role/content messages into Anthropic /v1/messages shape."""
    anthropic_messages: list[dict] = []
    system_parts: list[str] = []

    for message in messages:
        role = str(message.get("role") or "").strip().lower()
        content = message.get("content")

        if role == "system":
            text = _message_content_to_text(content).strip()
            if text:
                system_parts.append(text)
            continue

        if role not in {"user", "assistant"}:
            continue

        blocks = _convert_openai_content_to_anthropic_blocks(content)
        if not blocks:
            blocks = [{"type": "text", "text": ""}]
        anthropic_messages.append({"role": role, "content": blocks})

    return anthropic_messages, "\n\n".join(system_parts).strip()


def _convert_openai_content_to_gemini_parts(content: Any) -> list[dict]:
    """Convert OpenAI-style content into Gemini `parts`."""
    if isinstance(content, str):
        text = content.strip()
        return [{"text": text}] if text else []

    parts: list[dict] = []
    if isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                text = part.strip()
                if text:
                    parts.append({"text": text})
                continue

            if not isinstance(part, dict):
                continue

            part_type = str(part.get("type") or "").strip().lower()
            if part_type == "text":
                text = _message_content_to_text(part.get("text")).strip()
                if text:
                    parts.append({"text": text})
                continue

            image_url = _extract_image_url_from_message_part(part)
            if image_url:
                decoded = _decode_data_url(image_url)
                if decoded:
                    image_bytes, mime_type = decoded
                    parts.append(
                        {
                            "inlineData": {
                                "mimeType": mime_type,
                                "data": base64.b64encode(image_bytes).decode("ascii"),
                            }
                        }
                    )
                else:
                    parts.append({"text": f"[image_url] {image_url}"})
                continue

            text = _message_content_to_text(part).strip()
            if text:
                parts.append({"text": text})

    if parts:
        return parts

    fallback_text = _message_content_to_text(content).strip()
    if fallback_text:
        return [{"text": fallback_text}]
    return []


def _convert_openai_messages_to_gemini(messages: list[dict]) -> tuple[list[dict], Optional[dict]]:
    """Convert OpenAI role/content messages into Gemini generateContent payload shape."""
    contents: list[dict] = []
    system_parts: list[str] = []

    for message in messages:
        role = str(message.get("role") or "").strip().lower()
        content = message.get("content")

        if role == "system":
            text = _message_content_to_text(content).strip()
            if text:
                system_parts.append(text)
            continue

        if role not in {"user", "assistant"}:
            continue

        gemini_role = "model" if role == "assistant" else "user"
        parts = _convert_openai_content_to_gemini_parts(content)
        if not parts:
            parts = [{"text": ""}]
        contents.append({"role": gemini_role, "parts": parts})

    system_instruction: Optional[dict] = None
    if system_parts:
        system_instruction = {"parts": [{"text": "\n\n".join(system_parts).strip()}]}

    return contents, system_instruction


def _normalize_gemini_model_id(model_id: str) -> str:
    normalized = (model_id or "").strip()
    if normalized.startswith("models/"):
        normalized = normalized.split("/", 1)[1]
    return normalized


def _normalize_upstream_temperature(provider: str, temperature: Optional[float]) -> Optional[float]:
    if temperature is None:
        return None

    normalized = max(0.0, float(temperature))
    if provider == "alibaba":
        # DashScope rejects 2.0 exactly: valid range is [0.0, 2.0).
        normalized = min(normalized, 1.99)
    else:
        normalized = min(normalized, 2.0)
    return round(normalized, 2)


def _build_upstream_chat_request(
    cfg: ResolvedUpstreamConfig,
    request: V1ChatCompletionRequest,
    messages: list[dict],
) -> tuple[str, dict]:
    """Build provider-specific upstream URL + payload from normalized OpenAI-style messages."""
    provider = cfg.api_provider
    temperature = _normalize_upstream_temperature(provider, request.temperature)

    if provider == "anthropic":
        anthropic_messages, system_prompt = _convert_openai_messages_to_anthropic(messages)
        if not anthropic_messages:
            anthropic_messages = [{"role": "user", "content": [{"type": "text", "text": ""}]}]
        payload: dict = {
            "model": request.model,
            "messages": anthropic_messages,
            "stream": bool(request.stream),
            "max_tokens": request.max_tokens or DEFAULT_ANTHROPIC_MAX_TOKENS,
        }
        if system_prompt:
            payload["system"] = system_prompt
        if temperature is not None:
            payload["temperature"] = temperature
        if request.top_p is not None:
            payload["top_p"] = request.top_p
        return _build_upstream_url(cfg, "/v1/messages"), payload

    if provider == "gemini":
        contents, system_instruction = _convert_openai_messages_to_gemini(messages)
        generation_config: dict = {}
        if temperature is not None:
            generation_config["temperature"] = temperature
        if request.max_tokens:
            generation_config["maxOutputTokens"] = request.max_tokens
        if request.top_p is not None:
            generation_config["topP"] = request.top_p

        payload: dict = {"contents": contents or [{"role": "user", "parts": [{"text": ""}]}]}
        if system_instruction:
            payload["systemInstruction"] = system_instruction
        if generation_config:
            payload["generationConfig"] = generation_config

        model_id = _normalize_gemini_model_id(request.model)
        method_suffix = ":streamGenerateContent?alt=sse" if request.stream else ":generateContent"
        return _build_upstream_url(cfg, f"/v1beta/models/{model_id}{method_suffix}"), payload

    payload: dict = {
        "model": request.model,
        "messages": messages,
        "temperature": temperature,
        "stream": request.stream,
    }
    if request.max_tokens:
        payload["max_tokens"] = request.max_tokens
    if request.top_p is not None:
        payload["top_p"] = request.top_p
    if request.frequency_penalty is not None:
        payload["frequency_penalty"] = request.frequency_penalty
    if request.presence_penalty is not None:
        payload["presence_penalty"] = request.presence_penalty
    return _build_upstream_url(cfg, "/v1/chat/completions"), payload


def _is_dashscope_message_shape_error(
    upstream_url: str,
    status_code: int,
    error_text: str,
) -> bool:
    """Detect DashScope 400 errors caused by strict OpenAI-compatible message schema."""
    if status_code != 400:
        return False
    if "dashscope" not in (upstream_url or "").lower():
        return False

    lowered = (error_text or "").lower()
    has_user_role_error = "input.messages.0.role" in lowered and "input should be 'user'" in lowered
    has_content_list_error = "input.messages." in lowered and "content" in lowered and "valid list" in lowered
    return has_user_role_error or has_content_list_error


def _convert_content_to_openai_parts(content: Any) -> list[dict]:
    """Normalize message content to OpenAI-style part list: [{type:'text'| 'image_url', ...}]."""
    parts: list[dict] = []

    if isinstance(content, list):
        for item in content:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    parts.append({"type": "text", "text": text})
                continue

            if not isinstance(item, dict):
                extracted = _message_content_to_text(item).strip()
                if extracted:
                    parts.append({"type": "text", "text": extracted})
                continue

            part_type = str(item.get("type") or "").strip().lower()
            if part_type == "image_url":
                image_url = item.get("image_url")
                if isinstance(image_url, dict):
                    url = image_url.get("url")
                    if isinstance(url, str) and url.strip():
                        parts.append({"type": "image_url", "image_url": {"url": url.strip()}})
                        continue
                if isinstance(image_url, str) and image_url.strip():
                    parts.append({"type": "image_url", "image_url": {"url": image_url.strip()}})
                    continue

            if part_type == "text":
                text = _message_content_to_text(item.get("text")).strip()
                if text:
                    parts.append({"type": "text", "text": text})
                continue

            extracted = _message_content_to_text(item).strip()
            if extracted:
                parts.append({"type": "text", "text": extracted})

    if parts:
        return parts

    fallback_text = _message_content_to_text(content).strip()
    return [{"type": "text", "text": fallback_text}]


def _build_dashscope_message_shape_fallback_payload(request_data: dict) -> Optional[dict]:
    """
    Convert payload into DashScope-friendly chat shape:
    - first message role must be `user`
    - message content must be list parts
    - merge system messages into first user content
    """
    if not isinstance(request_data, dict):
        return None

    messages = request_data.get("messages")
    if not isinstance(messages, list):
        return None

    converted_messages: list[dict] = []
    system_parts: list[str] = []

    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip().lower()
        content = message.get("content")

        if role == "system":
            text = _message_content_to_text(content).strip()
            if text:
                system_parts.append(text)
            continue

        if role not in {"user", "assistant"}:
            continue

        converted_messages.append({
            "role": role,
            "content": _convert_content_to_openai_parts(content),
        })

    merged_system = "\n\n".join(system_parts).strip()
    if merged_system:
        system_block = {"type": "text", "text": f"[System]\n{merged_system}"}
        if converted_messages and converted_messages[0].get("role") == "user":
            first_content = converted_messages[0].get("content")
            if not isinstance(first_content, list):
                first_content = _convert_content_to_openai_parts(first_content)
                converted_messages[0]["content"] = first_content
            first_content.insert(0, system_block)
        else:
            converted_messages.insert(0, {"role": "user", "content": [system_block]})

    if not converted_messages:
        seed_text = merged_system or "Hello"
        converted_messages = [{
            "role": "user",
            "content": [{"type": "text", "text": seed_text}],
        }]

    if converted_messages[0].get("role") != "user":
        converted_messages.insert(0, {
            "role": "user",
            "content": [{"type": "text", "text": "Please continue."}],
        })

    if converted_messages == messages:
        return None

    next_payload = dict(request_data)
    next_payload["messages"] = converted_messages
    return next_payload


def _is_dashscope_upstream_url(upstream_url: str) -> bool:
    lowered = (upstream_url or "").lower()
    return "dashscope" in lowered and "/compatible-mode/" in lowered


def _is_dashscope_capability_error(status_code: int, error_text: str) -> bool:
    if status_code not in {400, 404}:
        return False
    lowered = (error_text or "").lower()
    hints = (
        "unsupported model",
        "does not support",
        "model not exist",
        "no available distributor",
        "no available channel",
        "parameter.enable_thinking must be set to false",
        "current user api does not support http call",
    )
    return any(hint in lowered for hint in hints)


def _is_dashscope_stream_only_error(error_text: str) -> bool:
    lowered = (error_text or "").lower()
    if "stream" not in lowered:
        return False
    return any(tag in lowered for tag in ("only", "must", "required", "enable_thinking"))


def _dashscope_origin_from_url(url: str) -> Optional[str]:
    parsed = urlparse((url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _dashscope_native_url_from_compatible(upstream_url: str, native_path: str) -> Optional[str]:
    origin = _dashscope_origin_from_url(upstream_url)
    if not origin:
        return None
    path = native_path if native_path.startswith("/") else f"/{native_path}"
    return f"{origin}{path}"


def _message_content_to_dashscope_native_parts(content: Any) -> list[dict]:
    parts: list[dict] = []

    if isinstance(content, list):
        for item in content:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    parts.append({"text": text})
                continue

            if not isinstance(item, dict):
                extracted = _message_content_to_text(item).strip()
                if extracted:
                    parts.append({"text": extracted})
                continue

            part_type = str(item.get("type") or "").strip().lower()
            if part_type == "text":
                text = _message_content_to_text(item.get("text")).strip()
                if text:
                    parts.append({"text": text})
                continue
            if part_type == "image_url":
                image_url = item.get("image_url")
                if isinstance(image_url, dict):
                    url = image_url.get("url")
                    if isinstance(url, str) and url.strip():
                        parts.append({"image": url.strip()})
                        continue
                elif isinstance(image_url, str) and image_url.strip():
                    parts.append({"image": image_url.strip()})
                    continue
            if part_type == "input_audio":
                audio = item.get("input_audio")
                if isinstance(audio, dict):
                    data = audio.get("data")
                    if isinstance(data, str) and data.strip():
                        parts.append({"audio": data.strip()})
                        continue

            extracted = _message_content_to_text(item).strip()
            if extracted:
                parts.append({"text": extracted})

    if parts:
        return parts

    fallback_text = _message_content_to_text(content).strip()
    if fallback_text:
        return [{"text": fallback_text}]
    return []


def _has_dashscope_native_image_parts(messages: list[dict]) -> bool:
    for message in messages:
        parts = _message_content_to_dashscope_native_parts(message.get("content"))
        for part in parts:
            if isinstance(part, dict) and isinstance(part.get("image"), str):
                return True
    return False


def _is_alibaba_native_multimodal_model(model_id: str) -> bool:
    lowered = (model_id or "").strip().lower()
    if not lowered:
        return False
    return any(
        tag in lowered
        for tag in ("-vl", "vl-", "omni", "qvq", "ocr", "captioner", "image")
    )


def _should_disable_alibaba_thinking(model_id: str, stream: bool) -> bool:
    if stream:
        return False
    lowered = (model_id or "").strip().lower()
    if not lowered:
        return False
    return lowered.startswith("qwen3-") or lowered.startswith("qwq-") or lowered.startswith("qvq-")


def _convert_openai_messages_to_dashscope_native(messages: list[dict], prefer_multimodal: bool) -> list[dict]:
    native_messages: list[dict] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip().lower()
        if role not in {"system", "user", "assistant"}:
            continue

        content = message.get("content")
        if prefer_multimodal:
            parts = _message_content_to_dashscope_native_parts(content)
            if not parts:
                parts = [{"text": ""}]
            native_messages.append({"role": role, "content": parts})
        else:
            text = _message_content_to_text(content).strip()
            native_messages.append({"role": role, "content": text})

    if not native_messages:
        native_messages = [{"role": "user", "content": ""}]
    return native_messages


def _build_dashscope_native_chat_fallback(
    upstream_url: str,
    request_data: dict,
    *,
    stream: bool,
) -> Optional[tuple[str, dict, dict]]:
    if not _is_dashscope_upstream_url(upstream_url):
        return None
    if not isinstance(request_data, dict):
        return None

    model = str(request_data.get("model") or "").strip()
    messages = request_data.get("messages")
    if not model or not isinstance(messages, list):
        return None

    has_image_parts = _has_dashscope_native_image_parts(messages)
    prefer_multimodal = has_image_parts or _is_alibaba_native_multimodal_model(model)
    native_messages = _convert_openai_messages_to_dashscope_native(messages, prefer_multimodal=prefer_multimodal)

    parameters: dict[str, Any] = {
        "result_format": "message",
    }
    normalized_temperature = _normalize_upstream_temperature("alibaba", request_data.get("temperature"))
    if normalized_temperature is not None:
        parameters["temperature"] = normalized_temperature
    if request_data.get("top_p") is not None:
        parameters["top_p"] = request_data.get("top_p")
    if request_data.get("max_tokens") is not None:
        parameters["max_tokens"] = request_data.get("max_tokens")
    if _should_disable_alibaba_thinking(model, stream):
        parameters["enable_thinking"] = False

    native_payload: dict[str, Any] = {
        "model": model,
        "input": {"messages": native_messages},
        "parameters": parameters,
    }

    if prefer_multimodal:
        native_path = "/api/v1/services/aigc/multimodal-generation/generation"
    else:
        native_path = "/api/v1/services/aigc/text-generation/generation"

    native_url = _dashscope_native_url_from_compatible(upstream_url, native_path)
    if not native_url:
        return None

    extra_headers: dict[str, str] = {}
    if stream:
        extra_headers["X-DashScope-SSE"] = "enable"
    return native_url, native_payload, extra_headers


async def _collect_dashscope_stream_as_nonstream_response(
    client: httpx.AsyncClient,
    upstream_url: str,
    request_data: dict,
    headers: dict[str, str],
    model_id: str,
) -> Optional[dict]:
    stream_payload = dict(request_data)
    stream_payload["stream"] = True

    merged_headers = dict(headers)
    merged_headers.setdefault("Content-Type", "application/json")

    try:
        async with client.stream(
            "POST",
            upstream_url,
            json=stream_payload,
            headers=merged_headers,
            timeout=httpx.Timeout(connect=8.0, read=STREAM_READ_TIMEOUT_SECONDS, write=30.0, pool=8.0),
        ) as stream_response:
            if stream_response.status_code != 200:
                return None

            full_text = ""
            async for line in stream_response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]" or not data.startswith("{"):
                    continue
                try:
                    payload = json.loads(data)
                except Exception:
                    continue
                extracted = _extract_text_from_chat_payload(payload)
                if extracted:
                    full_text += extracted

            if not full_text.strip():
                return None

            return {
                "id": f"chatcmpl-dashscope-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": int(datetime.now().timestamp()),
                "model": model_id or "unknown",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": full_text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": int(len(full_text.split()) * 1.3),
                    "total_tokens": int(len(full_text.split()) * 1.3),
                },
            }
    except Exception:
        return None


_PIGTEX_IMAGE_MODEL_HINTS = (
    "image",
    "t2i",
    "text-to-image",
    "qwen-image",
    "z-image",
    "wanx",
    "seedream",
    "doubao-seedream",
    "gpt-image",
    "dall-e",
    "imagen",
    "imagegen",
    "stable-diffusion",
    "sdxl",
    "flux",
    "ideogram",
    "recraft",
)

_PIGTEX_OPENAI_IMAGE_MODEL_HINTS = (
    "gpt-image",
    "dall-e",
    "flux",
    "stable-diffusion",
    "sdxl",
    "ideogram",
    "recraft",
)

_PIGTEX_ALIBABA_IMAGE_MODEL_HINTS = (
    "qwen-image",
    "wanx",
)

_PIGTEX_GEMINI_IMAGE_MODEL_HINTS = (
    "gemini",
    "image-preview",
)

_PIGTEX_AUDIO_MODEL_HINTS = (
    "tts",
    "asr",
    "speech",
    "voice",
)

_PIGTEX_VIDEO_MODEL_HINTS = (
    "video",
    "veo",
    "sora",
    "seedream",
    "t2v",
    "i2v",
    "r2v",
)

_PIGTEX_UNSUPPORTED_MODEL_HINTS = (
    "realtime",
    "livetranslate",
    "embedding",
)

_PIGTEX_MODEL_CAPABILITIES = frozenset(
    {
        "chat",
        "vision",
        "image_generation",
        "image_edit",
        "video_generation",
        "moderation",
    }
)


def _normalize_pigtex_model_type(raw_type: Any) -> Optional[str]:
    if not isinstance(raw_type, str):
        return None
    lowered = raw_type.strip().lower()
    if lowered in {"chat", "image", "audio", "video", "moderation"}:
        return lowered
    if lowered in {"vision", "image_generation", "images"}:
        return "image"
    if lowered in {"tts", "asr", "speech", "voice", "audio_generation", "audio_transcription"}:
        return "audio"
    if lowered in {"video_generation", "videos", "t2v", "i2v", "r2v"}:
        return "video"
    if lowered in {"t2i", "text_to_image", "text-to-image"}:
        return "image"
    return None


def _matches_pigtex_image_model_family(lowered_model_id: str) -> bool:
    if not lowered_model_id:
        return False
    if "-image-" in lowered_model_id or lowered_model_id.endswith("-image"):
        return True
    return any(hint in lowered_model_id for hint in _PIGTEX_IMAGE_MODEL_HINTS)


def _matches_openai_image_model_family(lowered_model_id: str) -> bool:
    if not lowered_model_id:
        return False
    if lowered_model_id.startswith("gpt-image") or lowered_model_id.startswith("dall-e"):
        return True
    return any(hint in lowered_model_id for hint in _PIGTEX_OPENAI_IMAGE_MODEL_HINTS)


def _matches_alibaba_image_model_family(lowered_model_id: str) -> bool:
    if not lowered_model_id:
        return False
    return any(hint in lowered_model_id for hint in _PIGTEX_ALIBABA_IMAGE_MODEL_HINTS)


def _matches_gemini_image_model_family(
    lowered_model_id: str,
    supported_methods: Optional[set[str]] = None,
) -> bool:
    if not lowered_model_id:
        return False
    if "gemini" in lowered_model_id and "image" in lowered_model_id:
        return True
    if any(hint in lowered_model_id for hint in _PIGTEX_GEMINI_IMAGE_MODEL_HINTS) and "image" in lowered_model_id:
        return True
    if supported_methods:
        lowered_methods = {method.strip().lower() for method in supported_methods if isinstance(method, str)}
        return any("image" in method for method in lowered_methods) and "image" in lowered_model_id
    return False


def _matches_pigtex_audio_model_family(lowered_model_id: str) -> bool:
    if not lowered_model_id:
        return False
    return any(hint in lowered_model_id for hint in _PIGTEX_AUDIO_MODEL_HINTS)


def _matches_pigtex_video_model_family(lowered_model_id: str) -> bool:
    if not lowered_model_id:
        return False
    return any(hint in lowered_model_id for hint in _PIGTEX_VIDEO_MODEL_HINTS)


def _normalize_pigtex_capabilities(raw_capabilities: Any) -> list[str]:
    if not isinstance(raw_capabilities, list):
        return []

    normalized: list[str] = []
    for item in raw_capabilities:
        if not isinstance(item, str):
            continue
        capability = item.strip().lower()
        if capability in _PIGTEX_MODEL_CAPABILITIES and capability not in normalized:
            normalized.append(capability)
    return normalized


def _infer_pigtex_model_capabilities(
    transport: str,
    model_id: str,
    model_type: str,
    *,
    supports_vision: bool = False,
    raw_capabilities: Any = None,
    supported_methods: Optional[set[str]] = None,
) -> list[str]:
    del transport
    normalized = set(_normalize_pigtex_capabilities(raw_capabilities))
    lowered = (model_id or "").strip().lower()

    if model_type == "moderation" or "moderation" in lowered:
        normalized.add("moderation")
        return sorted(normalized)

    if model_type == "chat":
        normalized.add("chat")
    if model_type == "chat" and supports_vision:
        normalized.add("vision")

    if (
        model_type == "image"
        or _matches_pigtex_image_model_family(lowered)
        or _matches_openai_image_model_family(lowered)
        or _matches_alibaba_image_model_family(lowered)
        or _matches_gemini_image_model_family(lowered, supported_methods)
    ):
        normalized.update({"image_generation", "image_edit"})

    if model_type == "video" or _matches_pigtex_video_model_family(lowered):
        normalized.add("video_generation")

    return sorted(normalized)


def _infer_pigtex_model_type(
    provider: str,
    model_id: str,
    *,
    raw_type: Any = None,
    supported_methods: Optional[set[str]] = None,
) -> str:
    normalized_type = _normalize_pigtex_model_type(raw_type)
    if normalized_type:
        return normalized_type

    lowered = (model_id or "").strip().lower()
    if not lowered:
        return "chat"

    if any(tag in lowered for tag in _PIGTEX_UNSUPPORTED_MODEL_HINTS):
        return "moderation"
    if "moderation" in lowered:
        return "moderation"

    if _matches_pigtex_video_model_family(lowered):
        return "video"
    if _matches_pigtex_image_model_family(lowered):
        return "image"
    if _matches_pigtex_audio_model_family(lowered):
        return "audio"

    if provider == "gemini" and supported_methods:
        lowered_methods = {method.strip().lower() for method in supported_methods if isinstance(method, str)}
        if _matches_gemini_image_model_family(lowered, lowered_methods):
            return "image"

    return "chat"


def _infer_pigtex_supports_vision(
    provider: str,
    model_id: str,
    model_type: str,
    *,
    supported_methods: Optional[set[str]] = None,
) -> bool:
    if model_type == "image":
        return True
    if model_type in {"audio", "video", "moderation"}:
        return False
    if provider == "gemini" and supported_methods:
        lowered_methods = {method.strip().lower() for method in supported_methods if isinstance(method, str)}
        return "generatecontent" in lowered_methods and model_type != "moderation"

    lowered = (model_id or "").strip().lower()
    if not lowered:
        return False
    return any(tag in lowered for tag in ("-vl", "vl-", "omni", "ocr", "vision", "image"))


def _normalize_model_provider_flag(value: Any) -> Optional[dict[str, Any]]:
    if not isinstance(value, dict):
        return None

    label = value.get("label")
    if not isinstance(label, str) or not label.strip():
        return None

    normalized: dict[str, Any] = {
        "label": label.strip(),
    }
    code = value.get("code")
    if isinstance(code, str) and code.strip():
        normalized["code"] = code.strip()
    tone = value.get("tone")
    if isinstance(tone, str) and tone.strip().lower() in {"neutral", "accent", "success", "warning", "danger"}:
        normalized["tone"] = tone.strip().lower()
    disabled = value.get("disabled")
    if isinstance(disabled, bool):
        normalized["disabled"] = disabled

    return normalized


def _normalize_pigtex_models_data(models: list[dict], provider: str) -> list[dict]:
    normalized: list[dict] = []
    for model in models:
        if not isinstance(model, dict):
            continue
        model_id_raw = model.get("id") or model.get("name")
        if not isinstance(model_id_raw, str) or not model_id_raw.strip():
            continue
        model_id = model_id_raw.strip()

        model_type = _infer_pigtex_model_type(
            provider,
            model_id,
            raw_type=model.get("type"),
            supported_methods=model.get("supported_methods"),
        )

        supports_streaming = model.get("supports_streaming")
        if not isinstance(supports_streaming, bool):
            supports_streaming = True
        if "realtime" in model_id.lower():
            supports_streaming = False

        supports_vision = model.get("supports_vision")
        if not isinstance(supports_vision, bool):
            supports_vision = _infer_pigtex_supports_vision(
                provider,
                model_id,
                model_type,
                supported_methods=model.get("supported_methods"),
            )

        max_output = model.get("max_output")
        if not isinstance(max_output, int) or max_output <= 0:
            max_output = 8192

        capabilities = _infer_pigtex_model_capabilities(
            provider,
            model_id,
            model_type,
            supports_vision=supports_vision,
            raw_capabilities=model.get("capabilities"),
            supported_methods=model.get("supported_methods"),
        )

        normalized.append(
            {
                "id": model_id,
                "owned_by": model.get("owned_by") or provider,
                "provider_id": model.get("provider_id") or model.get("owned_by") or provider,
                "transport": model.get("transport") or provider,
                "type": model_type,
                "name": model.get("name") or model_id,
                "description": model.get("description"),
                "supports_streaming": supports_streaming,
                "supports_vision": supports_vision,
                "max_output": max_output,
                "tier": model.get("tier") or "plus",
                "capabilities": capabilities,
                "recommendation_flag": _normalize_model_provider_flag(model.get("recommendation_flag")),
                "status_flag": _normalize_model_provider_flag(model.get("status_flag")),
            }
        )

    return normalized


def _normalize_upstream_models_payload(provider: str, payload: Any) -> dict:
    """
    Normalize provider-specific model payloads into OpenAI-style `{ data: [...] }`.
    """
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return {
            "object": payload.get("object") if isinstance(payload.get("object"), str) else "list",
            "data": _normalize_pigtex_models_data(payload.get("data") or [], provider),
        }

    normalized_data: list[dict] = []

    if provider == "gemini" and isinstance(payload, dict):
        models = payload.get("models")
        if isinstance(models, list):
            for model in models:
                if not isinstance(model, dict):
                    continue
                model_name = model.get("name")
                if not isinstance(model_name, str) or not model_name.strip():
                    continue
                model_id = model_name.split("/", 1)[1] if model_name.startswith("models/") else model_name
                methods = model.get("supportedGenerationMethods")
                if not isinstance(methods, list):
                    methods = []
                method_set = {str(method) for method in methods}
                model_type = _infer_pigtex_model_type("gemini", model_id, supported_methods=method_set)
                supports_vision = _infer_pigtex_supports_vision(
                    "gemini",
                    model_id,
                    model_type,
                    supported_methods=method_set,
                )
                normalized_data.append(
                    {
                        "id": model_id,
                        "owned_by": "gemini",
                        "provider_id": "gemini",
                        "transport": "gemini",
                        "type": model_type,
                        "name": model.get("displayName") or model_id,
                        "description": model.get("description"),
                        "supports_streaming": "streamGenerateContent" in method_set,
                        "supports_vision": supports_vision,
                        "max_output": 8192,
                        "tier": "plus",
                        "supported_methods": sorted(method_set),
                        "capabilities": _infer_pigtex_model_capabilities(
                            "gemini",
                            model_id,
                            model_type,
                            supports_vision=supports_vision,
                            supported_methods=method_set,
                        ),
                        "recommendation_flag": _normalize_model_provider_flag(model.get("recommendation_flag")),
                        "status_flag": _normalize_model_provider_flag(model.get("status_flag")),
                    }
                )

    if not normalized_data and isinstance(payload, dict):
        candidates = payload.get("models")
        if isinstance(candidates, list):
            for model in candidates:
                if not isinstance(model, dict):
                    continue
                model_id = model.get("id") or model.get("name")
                if not isinstance(model_id, str) or not model_id.strip():
                    continue
                inferred_type = _infer_pigtex_model_type(provider, model_id, raw_type=model.get("type"))
                inferred_supports_vision = _infer_pigtex_supports_vision(provider, model_id, str(inferred_type))

                normalized_data.append(
                    {
                        "id": model_id,
                        "owned_by": provider,
                        "provider_id": model.get("provider_id") or model.get("owned_by") or provider,
                        "transport": model.get("transport") or provider,
                        "type": inferred_type,
                        "name": model.get("display_name") or model.get("name") or model_id,
                        "description": model.get("description"),
                        "supports_streaming": True,
                        "supports_vision": inferred_supports_vision,
                        "max_output": 8192,
                        "tier": "plus",
                        "capabilities": _infer_pigtex_model_capabilities(
                            provider,
                            model_id,
                            str(inferred_type),
                            supports_vision=inferred_supports_vision,
                            raw_capabilities=model.get("capabilities"),
                        ),
                        "recommendation_flag": _normalize_model_provider_flag(model.get("recommendation_flag")),
                        "status_flag": _normalize_model_provider_flag(model.get("status_flag")),
                    }
                )

    if normalized_data:
        normalized_data = _normalize_pigtex_models_data(normalized_data, provider)

    return {"object": "list", "data": normalized_data}


def _normalize_models_payload_for_config(cfg: "ResolvedUpstreamConfig", payload: Any) -> dict:
    normalized = _normalize_upstream_models_payload(cfg.api_provider, payload)
    if cfg.source != TEXAPI_PARTNER_SOURCE:
        return normalized

    data = normalized.get("data")
    if not isinstance(data, list):
        return normalized

    remapped: list[dict[str, Any]] = []
    for model in data:
        if not isinstance(model, dict):
            continue
        entry = dict(model)
        upstream_provider = entry.get("provider_id") or entry.get("owned_by") or entry.get("transport")
        if isinstance(upstream_provider, str) and upstream_provider.strip():
            entry["upstream_provider_id"] = upstream_provider.strip()
        entry["owned_by"] = "TexAPI"
        entry["provider_id"] = "texapi"
        entry["transport"] = "openai"
        remapped.append(entry)

    normalized["data"] = remapped
    return normalized


async def _persist_image_response_data(items: Any, owner_user_id: str) -> list[dict]:
    if not isinstance(items, list):
        return []

    persisted_items: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        current = dict(item)
        url_value = current.get("url")
        serve_url = current.get("serve_url")

        if isinstance(serve_url, str) and "/api/images/serve/" in serve_url:
            persisted_items.append(current)
            continue

        data_url: Optional[str] = None

        b64_json = current.get("b64_json")
        if isinstance(b64_json, str) and b64_json.strip():
            data_url = f"data:image/png;base64,{b64_json.strip()}"
            current.setdefault("mime_type", "image/png")
        elif isinstance(url_value, str) and url_value.strip():
            trimmed_url = url_value.strip()
            if trimmed_url.startswith("data:"):
                data_url = trimmed_url
            elif "/api/images/serve/" in trimmed_url:
                parsed = urlparse(trimmed_url)
                current["serve_url"] = parsed.path or trimmed_url
                current["url"] = current["serve_url"]
                persisted_items.append(current)
                continue
            else:
                downloaded = await _download_image(trimmed_url)
                if downloaded:
                    content, mime_type, _ = downloaded
                    data_url = _build_data_url(mime_type, content)
                    current.setdefault("mime_type", mime_type)

        if data_url:
            persisted = save_base64_image_to_disk(str(uuid.uuid4()), data_url, owner_user_id)
            if persisted:
                current["serve_url"] = persisted
                current["url"] = persisted

        persisted_items.append(current)

    return persisted_items


def _raise_unsupported_provider_capability(
    provider: str,
    operation: str,
    message: Optional[str] = None,
) -> None:
    raise HTTPException(
        status_code=400,
        detail={
            "error": "unsupported_provider_capability",
            "message": message
            or f"Provider transport '{provider}' does not support {operation} in PigTex standard routing.",
            "provider": provider,
            "operation": operation,
        },
    )


def _raise_voice_feature_disabled(operation: str) -> None:
    raise HTTPException(
        status_code=403,
        detail={
            "error": "voice_feature_disabled",
            "message": "Voice features are disabled on this PigTex build.",
            "operation": operation,
        },
    )


def _require_explicit_model_id(
    raw_model: Optional[str],
    *,
    provider: str,
    operation: str,
) -> str:
    model_id = (raw_model or "").strip()
    if model_id:
        return model_id
    raise HTTPException(
        status_code=400,
        detail={
            "error": "model_required",
            "message": (
                f"Model is required for provider '{provider}' {operation}. "
                "PigTex does not auto-select or remap models for this route."
            ),
            "provider": provider,
            "operation": operation,
        },
    )


def _normalize_gemini_image_aspect_ratio(size: Optional[str]) -> Optional[str]:
    normalized = (size or "").strip().lower()
    if not normalized:
        return None

    direct_map = {
        "1024x1024": "1:1",
        "1536x1024": "3:2",
        "1024x1536": "2:3",
        "1792x1024": "16:9",
        "1024x1792": "9:16",
    }
    if normalized in direct_map:
        return direct_map[normalized]

    if "x" not in normalized:
        return None

    width_str, height_str = normalized.split("x", 1)
    try:
        width = int(width_str)
        height = int(height_str)
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None

    ratio = width / height
    candidates = [
        ("1:1", 1.0),
        ("16:9", 16 / 9),
        ("9:16", 9 / 16),
        ("4:3", 4 / 3),
        ("3:4", 3 / 4),
        ("3:2", 3 / 2),
        ("2:3", 2 / 3),
    ]
    best_label, _ = min(candidates, key=lambda item: abs(item[1] - ratio))
    return best_label


def _extract_gemini_candidate_parts(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []

    parts: list[dict[str, Any]] = []
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return parts

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content")
        if not isinstance(content, dict):
            continue
        candidate_parts = content.get("parts")
        if not isinstance(candidate_parts, list):
            continue
        for part in candidate_parts:
            if isinstance(part, dict):
                parts.append(part)
    return parts


def _extract_gemini_image_data_items(payload: Any) -> tuple[list[dict[str, Any]], list[str]]:
    data_items: list[dict[str, Any]] = []
    revised_prompts: list[str] = []

    for part in _extract_gemini_candidate_parts(payload):
        inline_data = part.get("inlineData") or part.get("inline_data")
        if isinstance(inline_data, dict):
            raw_data = inline_data.get("data")
            if isinstance(raw_data, str) and raw_data.strip():
                mime_type = inline_data.get("mimeType") or inline_data.get("mime_type") or "image/png"
                data_items.append(
                    {
                        "b64_json": raw_data.strip(),
                        "mime_type": mime_type,
                    }
                )
                continue

        text = part.get("text")
        if isinstance(text, str) and text.strip():
            revised_prompts.append(text.strip())

    return data_items, revised_prompts


def _build_gemini_image_openai_response(payload: Any) -> dict[str, Any]:
    data_items, revised_prompts = _extract_gemini_image_data_items(payload)

    normalized_data: list[dict[str, Any]] = []
    for index, item in enumerate(data_items):
        row = dict(item)
        if revised_prompts:
            row["revised_prompt"] = revised_prompts[min(index, len(revised_prompts) - 1)]
        normalized_data.append(row)

    return {
        "created": int(datetime.now().timestamp()),
        "data": normalized_data,
    }


async def _generate_image_via_gemini_native(
    cfg: ResolvedUpstreamConfig,
    request: V1ImageGenerationRequest,
) -> dict[str, Any]:
    model_id = _normalize_gemini_model_id(
        _require_explicit_model_id(
            request.model,
            provider="gemini",
            operation="image_generation",
        )
    )
    payload: dict[str, Any] = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": request.prompt.strip()}],
            }
        ],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
        },
    }
    if request.n and request.n > 1:
        payload["generationConfig"]["candidateCount"] = request.n

    aspect_ratio = _normalize_gemini_image_aspect_ratio(request.size)
    if aspect_ratio:
        payload["generationConfig"]["imageConfig"] = {"aspectRatio": aspect_ratio}

    if request.background and str(request.background).strip():
        payload["contents"][0]["parts"].append(
            {"text": f"Background preference: {str(request.background).strip()}"}
        )

    upstream_url = _build_upstream_url(cfg, f"/v1beta/models/{model_id}:generateContent")
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                upstream_url,
                json=payload,
                headers=_build_upstream_auth_headers(cfg),
            )
    except httpx.RequestError as exc:
        logger.warning("Gemini image generation connection failed base_url=%s error=%s", cfg.base_url, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "api_connection_error",
                "message": "Cannot connect to Gemini API.",
            },
        )

    if not response.is_success:
        _raise_upstream_http_exception(response, "gemini.images.generate")

    return _build_gemini_image_openai_response(response.json())


async def _edit_image_via_gemini_native(
    cfg: ResolvedUpstreamConfig,
    request: V1ImageEditRequest,
    *,
    image_bytes: bytes,
    image_mime: str,
) -> dict[str, Any]:
    if request.mask:
        _raise_unsupported_provider_capability(
            "gemini",
            "image_edit_mask",
            "Gemini image editing in PigTex standard routing does not support mask inputs.",
        )

    model_id = _normalize_gemini_model_id(
        _require_explicit_model_id(
            request.model,
            provider="gemini",
            operation="image_edit",
        )
    )
    payload: dict[str, Any] = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "inlineData": {
                            "mimeType": image_mime,
                            "data": base64.b64encode(image_bytes).decode("ascii"),
                        }
                    },
                    {"text": request.prompt.strip()},
                ],
            }
        ],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
        },
    }
    if request.n and request.n > 1:
        payload["generationConfig"]["candidateCount"] = request.n

    aspect_ratio = _normalize_gemini_image_aspect_ratio(request.size)
    if aspect_ratio:
        payload["generationConfig"]["imageConfig"] = {"aspectRatio": aspect_ratio}

    upstream_url = _build_upstream_url(cfg, f"/v1beta/models/{model_id}:generateContent")
    try:
        async with httpx.AsyncClient(timeout=240.0) as client:
            response = await client.post(
                upstream_url,
                json=payload,
                headers=_build_upstream_auth_headers(cfg),
            )
    except httpx.RequestError as exc:
        logger.warning("Gemini image edit connection failed base_url=%s error=%s", cfg.base_url, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "api_connection_error",
                "message": "Cannot connect to Gemini API.",
            },
        )

    if not response.is_success:
        _raise_upstream_http_exception(response, "gemini.images.edit")

    return _build_gemini_image_openai_response(response.json())


def _resolve_gemini_tts_voice(request: V1AudioSpeechRequest) -> str:
    voice = (request.voice or "").strip()
    lowered = voice.lower()
    if lowered in {"alloy", "ash", "coral", "echo", "sage", "shimmer", "cherry", "serena", "ethan", "chelsie"}:
        return "Kore"
    return voice or "Kore"


def _extract_gemini_audio_bytes(payload: Any) -> tuple[Optional[bytes], Optional[str]]:
    for part in _extract_gemini_candidate_parts(payload):
        inline_data = part.get("inlineData") or part.get("inline_data")
        if not isinstance(inline_data, dict):
            continue
        mime_type = str(inline_data.get("mimeType") or inline_data.get("mime_type") or "").strip()
        if not mime_type.startswith("audio/"):
            continue
        raw_data = inline_data.get("data")
        if not isinstance(raw_data, str) or not raw_data.strip():
            continue
        try:
            return base64.b64decode(raw_data), mime_type or "audio/wav"
        except Exception:
            continue
    return None, None


async def _generate_speech_via_gemini_native(
    cfg: ResolvedUpstreamConfig,
    request: V1AudioSpeechRequest,
) -> tuple[bytes, str]:
    model_id = _normalize_gemini_model_id(
        _require_explicit_model_id(
            request.model,
            provider="gemini",
            operation="audio_speech",
        )
    )
    payload: dict[str, Any] = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": request.input.strip()}],
            }
        ],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": _resolve_gemini_tts_voice(request),
                    }
                }
            },
        },
    }

    upstream_url = _build_upstream_url(cfg, f"/v1beta/models/{model_id}:generateContent")
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                upstream_url,
                json=payload,
                headers=_build_upstream_auth_headers(cfg),
            )
    except httpx.RequestError as exc:
        logger.warning("Gemini audio speech connection failed base_url=%s error=%s", cfg.base_url, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "api_connection_error",
                "message": "Cannot connect to Gemini API.",
            },
        )

    if not response.is_success:
        _raise_upstream_http_exception(response, "gemini.audio.speech")

    audio_bytes, audio_mime = _extract_gemini_audio_bytes(response.json())
    if not audio_bytes:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "gemini_tts_no_audio_output",
                "message": "Gemini TTS response did not include audio content.",
            },
        )

    return audio_bytes, audio_mime or "audio/wav"


def _apply_code_format_instruction(messages: list[dict]) -> list[dict]:
    if not messages:
        return [{"role": "system", "content": CODE_FORMAT_SYSTEM_PROMPT}]

    first = messages[0]
    if first.get("role") == "system":
        if CODE_FORMAT_SYSTEM_PROMPT in first.get("content", ""):
            return messages
        first["content"] = CODE_FORMAT_SYSTEM_PROMPT + "\n\n---\n\n" + first.get("content", "")
        return messages

    return [{"role": "system", "content": CODE_FORMAT_SYSTEM_PROMPT}, *messages]


def _latest_user_message(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            # Multimodal: content is list of {type: "text"/"image_url", ...}
            return _extract_text_from_content_payload(content)
    return ""


def _is_internal_tool_turn_text(text: str) -> bool:
    normalized = (text or "").strip()
    if not normalized:
        return False
    return is_internal_orchestration_payload(normalized)


def _normalize_turn_mode(mode: Optional[str]) -> str:
    normalized = (mode or "").strip().lower()
    return "deep" if normalized == "deep" else "fast"


def _normalize_requested_search_mode(mode: Optional[str]) -> str:
    normalized = (mode or "auto").strip().lower()
    if normalized in {"deep", "verify", "deep_verify", "research"}:
        return "deep"
    if normalized in {"fast", "realtime", "live", "latest"}:
        return "fast"
    if normalized in {"url", "url_read"}:
        return "url_read"
    return "auto"


def _estimate_turn_complexity(user_text: str) -> int:
    normalized = (user_text or "").strip()
    if not normalized:
        return 0

    lowered = normalized.lower()
    score = 0
    if len(normalized) >= 180:
        score += 1
    if len(normalized) >= 420:
        score += 1
    if _TURN_COMPLEXITY_HINT_RE.search(lowered):
        score += 1
    if _TURN_VERIFICATION_HINT_RE.search(lowered):
        score += 1
    if _TURN_MULTI_PART_HINT_RE.search(normalized):
        score += 1
    if normalized.count("?") >= 2:
        score += 1
    return min(score, 6)


def _has_recency_intent(user_text: str) -> bool:
    lowered = (user_text or "").strip().lower()
    if not lowered:
        return False
    return bool(_TURN_RECENCY_HINT_RE.search(lowered))


def _needs_strict_verification(user_text: str) -> bool:
    lowered = (user_text or "").strip().lower()
    if not lowered:
        return False
    return bool(_TURN_VERIFICATION_HINT_RE.search(lowered))


def _needs_price_precision(user_text: str) -> bool:
    lowered = (user_text or "").strip().lower()
    if not lowered:
        return False
    return bool(_TURN_PRICE_HINT_RE.search(lowered))


def _derive_web_search_policy(
    request: V1ChatCompletionRequest,
    latest_user_text: str,
) -> Dict[str, Any]:
    if _is_internal_tool_turn_text(latest_user_text):
        return {
            "turn_mode": _normalize_turn_mode(request.mode),
            "complexity_score": 0,
            "recency_intent": False,
            "strict_verification": False,
            "recommended_search": False,
            "requested_mode": "auto",
            "resolved_mode": "auto",
            "deep_read": False,
            "deep_verify": False,
            "max_results": 4,
            "reason_label": "internal_tool_turn",
        }

    turn_mode = _normalize_turn_mode(request.mode)
    complexity_score = _estimate_turn_complexity(latest_user_text)
    recency_intent = _has_recency_intent(latest_user_text)
    price_intent = _needs_price_precision(latest_user_text)
    strict_verification = _needs_strict_verification(latest_user_text)

    raw_requested_mode = (request.web_search_mode or "auto").strip().lower()
    normalized_requested_mode = _normalize_requested_search_mode(raw_requested_mode)
    mode_explicitly_requested = raw_requested_mode not in {"", "auto"}

    resolved_mode = normalized_requested_mode if mode_explicitly_requested else "auto"
    if resolved_mode == "auto":
        if price_intent:
            resolved_mode = "fast"
        if strict_verification and (turn_mode == "deep" or complexity_score >= 2):
            resolved_mode = "deep"
        elif recency_intent:
            resolved_mode = "fast"

    deep_read_explicit = request.web_search_deep_read is not None
    deep_verify_explicit = request.web_search_deep_verify is not None
    deep_read = bool(request.web_search_deep_read)
    deep_verify = bool(request.web_search_deep_verify)

    if not deep_read_explicit:
        deep_read = (
            resolved_mode in {"deep", "url_read"}
            or price_intent
            or (turn_mode == "deep" and complexity_score >= 2)
        )
    if not deep_verify_explicit:
        deep_verify = (
            resolved_mode == "deep"
            and (strict_verification or turn_mode == "deep" or complexity_score >= 3)
        )

    if request.web_search_max_results is not None:
        max_results = int(request.web_search_max_results)
    else:
        if resolved_mode == "deep" or deep_verify:
            max_results = 8 if turn_mode == "deep" else 6
        elif turn_mode == "deep" and (complexity_score >= 2 or recency_intent):
            max_results = 6
        elif price_intent:
            max_results = 6
        elif recency_intent:
            max_results = 5
        else:
            max_results = 4
    max_results = max(1, min(10, int(max_results)))

    recommend_search = bool(
        latest_user_text
        and (
            mode_explicitly_requested
            or resolved_mode in {"deep", "fast", "url_read"}
            or price_intent
            or recency_intent
            or strict_verification
            or complexity_score >= 3
        )
    )

    reasons: list[str] = []
    if recency_intent:
        reasons.append("recency")
    if price_intent:
        reasons.append("price")
    if strict_verification:
        reasons.append("verification")
    if complexity_score >= 3:
        reasons.append("complexity")
    if turn_mode == "deep":
        reasons.append("mode_deep")
    if mode_explicitly_requested:
        reasons.append("explicit")

    return {
        "turn_mode": turn_mode,
        "complexity_score": complexity_score,
        "recency_intent": recency_intent,
        "price_intent": price_intent,
        "strict_verification": strict_verification,
        "recommended_search": recommend_search,
        "requested_mode": raw_requested_mode or "auto",
        "resolved_mode": resolved_mode,
        "deep_read": deep_read,
        "deep_verify": deep_verify,
        "max_results": max_results,
        "reason_label": ",".join(reasons) if reasons else "none",
    }


def _should_force_markdown_fence(user_text: str) -> bool:
    text = user_text.lower().strip()
    if not text:
        return False

    has_target = (
        "markdown" in text
        or "discord" in text
        or "readme" in text
        or bool(re.search(r"\bmd\b", text))
    )
    has_intent = any(
        phrase in text
        for phrase in (
            "tạo", "viết", "soạn", "mẫu", "template", "đoạn", "snippet",
            "copy", "paste", "dán", "dùng", "sử dụng",
            "create", "generate", "make",
        )
    )
    return has_target and has_intent


def _apply_markdown_raw_instruction(messages: list[dict], user_text: str) -> list[dict]:
    if not _should_force_markdown_fence(user_text):
        return messages

    if not messages:
        return [{"role": "system", "content": MARKDOWN_RAW_SYSTEM_PROMPT}]

    first = messages[0]
    if first.get("role") == "system":
        if MARKDOWN_RAW_SYSTEM_PROMPT in first.get("content", ""):
            return messages
        first["content"] = MARKDOWN_RAW_SYSTEM_PROMPT + "\n\n---\n\n" + first.get("content", "")
        return messages

    return [{"role": "system", "content": MARKDOWN_RAW_SYSTEM_PROMPT}, *messages]


def _prepend_system_message(messages: list[dict], content: str) -> list[dict]:
    """Insert/merge a system message at the beginning."""
    text = (content or "").strip()
    if not text:
        return messages

    if messages and messages[0].get("role") == "system":
        existing = messages[0].get("content", "")
        messages[0]["content"] = text if not existing else f"{text}\n\n---\n\n{existing}"
        return messages

    return [{"role": "system", "content": text}, *messages]


def _merge_request_system_messages(messages: list[dict], request_messages: List[V1ChatMessage]) -> list[dict]:
    """Merge caller-provided system messages into first system block."""
    sys_parts = [m.content for m in request_messages if m.role == "system" and m.content]
    if not sys_parts:
        return messages
    return _prepend_system_message(messages, "\n\n".join(sys_parts))


def _truncate_context_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars].rstrip(), True


def _tokenize_file_context_query(text: str) -> set[str]:
    normalized = _normalize_matching_text(text)
    tokens = {
        token
        for token in re.findall(r"[a-z0-9_]+", normalized)
        if len(token) >= 2
    }
    stopwords = {
        "the", "and", "for", "with", "this", "that", "from", "into", "about",
        "hay", "la", "va", "cho", "voi", "cua", "nhung", "trong", "mot", "cac",
        "nhu", "nay", "kia", "gi", "nao", "ve", "tu", "tren", "duoi", "sau",
        "file", "pdf", "docx",
    }
    return {token for token in tokens if token not in stopwords}


def _score_file_chunk(chunk: V1FileChunk, query_tokens: set[str], filename_tokens: set[str]) -> float:
    chunk_text = _normalize_matching_text(chunk.text)
    if not chunk_text:
        return -1.0

    chunk_tokens = _tokenize_file_context_query(chunk_text)
    label_tokens = _tokenize_file_context_query(chunk.label or "")
    overlap = len(query_tokens.intersection(chunk_tokens))
    label_overlap = len(query_tokens.intersection(label_tokens))
    filename_overlap = len(filename_tokens.intersection(chunk_tokens))
    position_bonus = max(0.0, 0.35 - ((max(1, int(chunk.index)) - 1) * 0.08))
    content_density = min(1.0, len(chunk_text) / 1200.0)
    structural_bonus = 0.12 if label_tokens else 0.0

    if not query_tokens:
        return position_bonus + structural_bonus + (0.2 * content_density)

    relevance_signal = (overlap * 1.8) + (label_overlap * 2.3) + (filename_overlap * 0.35)
    if relevance_signal <= 0:
        return -0.5 + position_bonus + (0.1 * content_density)
    return relevance_signal + position_bonus + structural_bonus + (0.15 * content_density)


def _select_file_chunks_for_context(
    item: V1FileAttachment,
    query_text: str,
    max_chars: int,
    max_chunks: int = MAX_FILE_ATTACHMENT_CHUNKS_PER_FILE,
) -> list[tuple[V1FileChunk, str, bool]]:
    chunks = [
        chunk for chunk in (item.chunks or [])
        if isinstance(chunk, V1FileChunk) and (chunk.text or "").strip()
    ]
    if not chunks:
        fallback_chunk = V1FileChunk(
            index=1,
            label="Chunk 1",
            text=item.extracted_text,
            char_count=item.text_chars,
            truncated=item.truncated,
        )
        chunks = [fallback_chunk]

    query_tokens = _tokenize_file_context_query(query_text)
    filename_tokens = _tokenize_file_context_query(Path(item.filename or "").stem)
    ranked = sorted(
        (
            (chunk, _score_file_chunk(chunk, query_tokens, filename_tokens))
            for chunk in chunks
        ),
        key=lambda item: (-item[1], int(item[0].index or 0)),
    )

    selected: list[tuple[V1FileChunk, str, bool]] = []
    consumed = 0
    for chunk, score in ranked[:max(1, max_chunks * 2)]:
        remaining_budget = max_chars - consumed
        if remaining_budget <= 0 or len(selected) >= max_chunks:
            break
        if query_tokens and score <= 0:
            continue
        snippet, snippet_truncated = _truncate_context_text((chunk.text or "").strip(), remaining_budget)
        if not snippet:
            continue
        selected.append((chunk, snippet, snippet_truncated))
        consumed += len(snippet)

    if selected:
        return selected

    fallback_snippet, fallback_truncated = _truncate_context_text((chunks[0].text or "").strip(), max_chars)
    if not fallback_snippet:
        return []
    return [(chunks[0], fallback_snippet, fallback_truncated)]


def _build_file_context_system_prompt(
    file_attachments: Optional[List[V1FileAttachment]],
    query_text: str = "",
) -> Optional[str]:
    if not file_attachments:
        return None

    lines: list[str] = [
        "The user attached files. The following extracted text is supplemental context.",
        "Use it when relevant, cite the filename in your answer, and mention uncertainty if content seems partial.",
    ]
    if _needs_price_precision(query_text):
        lines.append(
            "When the answer depends on prices, fees, quotas, or other numeric limits in the attached files, "
            "quote the exact number if present; otherwise give the tightest defensible range and cite the filename plus chunk label."
        )
    included = 0
    consumed_chars = 0

    for item in file_attachments[:MAX_FILE_ATTACHMENTS_IN_CONTEXT]:
        extracted = (item.extracted_text or "").strip()
        if not extracted:
            continue

        remaining_budget = MAX_FILE_ATTACHMENT_TOTAL_CHARS - consumed_chars
        if remaining_budget <= 0:
            break

        max_for_item = min(MAX_FILE_ATTACHMENT_CHARS_PER_FILE, remaining_budget)
        chunk_payloads = _select_file_chunks_for_context(item, query_text, max_for_item)
        if not chunk_payloads:
            continue

        safe_name = Path(item.filename or f"file_{included + 1}").name or f"file_{included + 1}"
        mime_type = (item.mime_type or "application/octet-stream").strip().lower()
        lines.append(f"[Attached File {included + 1}] {safe_name} ({mime_type}, {item.size} bytes)")
        for chunk, snippet, snippet_truncated in chunk_payloads:
            label = (chunk.label or f"Chunk {chunk.index}").strip()
            lines.extend(
                [
                    f"[Relevant chunk {chunk.index}] {label}",
                    "```text",
                    snippet,
                    "```",
                ]
            )
            if bool(chunk.truncated) or snippet_truncated:
                lines.append(f"Note: chunk {chunk.index} from {safe_name} was truncated.")
            consumed_chars += len(snippet)

        if bool(item.truncated):
            lines.append(f"Note: extracted content for {safe_name} was truncated.")

        included += 1

    if included == 0:
        return None

    omitted_count = max(0, len(file_attachments) - included)
    if omitted_count > 0:
        lines.append(f"Note: {omitted_count} additional file(s) omitted due to context limits.")

    return "\n".join(lines)


def _build_file_attachment_refs(
    file_attachments: Optional[List[V1FileAttachment]],
    max_items: int = 8,
) -> List[str]:
    if not file_attachments:
        return []

    refs: list[str] = []
    for item in file_attachments[:max_items]:
        safe_name = Path(item.filename or "file").name or "file"
        mime_type = (item.mime_type or "application/octet-stream").strip().lower()
        chars = item.text_chars if isinstance(item.text_chars, int) and item.text_chars >= 0 else len(item.extracted_text or "")
        refs.append(f"[file] {safe_name} ({mime_type}, {chars} chars)")
    return refs


def _fact_to_memory_dict(fact: LocalFact) -> dict:
    """Map LocalFact into V1 memory API shape."""
    return {
        "id": fact.id,
        "content": fact.to_sentence(),
        "subject": fact.subject,
        "predicate": fact.predicate,
        "object": fact.object,
        "category": fact.category,
        "confidence": fact.confidence,
        "source": fact.source_type,
        "source_conversation_id": fact.source_id,
        "workspace_id": fact.workspace_id,
        "scope": fact.scope,
        "access_count": fact.access_count,
        "created_at": fact.created_at.isoformat() if fact.created_at else None,
        "updated_at": fact.updated_at.isoformat() if fact.updated_at else None,
        "confirmed_at": fact.confirmed_at.isoformat() if fact.confirmed_at else None,
    }


def _normalize_memory_key(raw_key: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", (raw_key or "").strip().lower()).strip("_")
    if not slug:
        return ""
    aliases = {
        "name": "user_name",
        "age": "user_age",
        "tone": "tone_preference",
    }
    return aliases.get(slug, slug)


def _assertion_scope_to_memory_scope(scope: str) -> str:
    return "workspace" if (scope or "").strip().lower() == "workspace" else "system"


def _assertion_source(category: str) -> str:
    return "user_input" if (category or "").strip().lower() == "explicit_memory" else "pattern_extraction"


def _assertion_to_memory_dict(assertion: LocalMemoryAssertion) -> dict:
    predicate = (assertion.key or "").strip()
    obj = (assertion.value or "").strip()
    return {
        "id": assertion.id,
        "content": f"User {predicate.replace('_', ' ')} {obj}".strip(),
        "subject": "User",
        "predicate": predicate,
        "object": obj,
        "category": assertion.category or "general",
        "confidence": float(assertion.confidence or 0.0),
        "source": _assertion_source(assertion.category),
        "source_conversation_id": assertion.conversation_id,
        "workspace_id": assertion.workspace_id,
        "scope": _assertion_scope_to_memory_scope(assertion.scope),
        "access_count": int(assertion.access_count or 0),
        "created_at": assertion.created_at.isoformat() if assertion.created_at else None,
        "updated_at": assertion.updated_at.isoformat() if assertion.updated_at else None,
        "confirmed_at": assertion.confirmed_at.isoformat() if assertion.confirmed_at else None,
        "type": assertion.type,
        "key": assertion.key,
        "value": assertion.value,
        "status": assertion.status,
        "expires_at": assertion.expires_at.isoformat() if assertion.expires_at else None,
    }


def _rules_file_path(user_id: str, workspace_id: Optional[str] = None) -> Path:
    """Resolve per-user rules file path in local storage."""
    root = get_storage_dir(user_id) / "brain" / "rules"
    base = root
    if workspace_id:
        if not WORKSPACE_ID_RE.fullmatch(workspace_id):
            raise HTTPException(status_code=400, detail="Invalid workspace_id format")
        base = root / "workspaces" / workspace_id

    base.mkdir(parents=True, exist_ok=True)
    rule_path = (base / "PIGTEX.md").resolve()
    root_resolved = root.resolve()
    if root_resolved not in rule_path.parents:
        raise HTTPException(status_code=400, detail="Invalid rules path")
    return rule_path


def _normalize_optional_workspace_id(workspace_id: Optional[str]) -> Optional[str]:
    """Normalize optional workspace id from query params."""
    if workspace_id is None:
        return None
    normalized = workspace_id.strip()
    if not normalized:
        return None
    if not WORKSPACE_ID_RE.fullmatch(normalized):
        raise HTTPException(status_code=400, detail="Invalid workspace_id format")
    return normalized


def _extract_text_from_content_payload(content: Any) -> str:
    """Extract text from provider-specific content payload shapes."""
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            extracted = _extract_text_from_content_payload(item)
            if extracted:
                parts.append(extracted)
        return "".join(parts)

    if isinstance(content, dict):
        text_field = content.get("text")
        if isinstance(text_field, str):
            return text_field
        if isinstance(text_field, dict):
            value = text_field.get("value")
            if isinstance(value, str):
                return value
        delta_text = content.get("delta")
        if isinstance(delta_text, str):
            return delta_text
        output_text = content.get("output_text")
        if isinstance(output_text, str):
            return output_text
        value_field = content.get("value")
        if isinstance(value_field, str):
            return value_field

        for key in ("content", "parts", "part", "item", "message", "output"):
            nested_content = content.get(key)
            nested = _extract_text_from_content_payload(nested_content)
            if nested:
                return nested

        content_block = content.get("content_block")
        nested_content_block = _extract_text_from_content_payload(content_block)
        if nested_content_block:
            return nested_content_block

        candidates = content.get("candidates")
        if isinstance(candidates, list):
            for candidate in candidates:
                nested_candidate = _extract_text_from_content_payload(candidate)
                if nested_candidate:
                    return nested_candidate

        response_payload = content.get("response")
        nested_response = _extract_text_from_content_payload(response_payload)
        if nested_response:
            return nested_response

    return ""


def _extract_text_from_chat_payload(payload: Any) -> str:
    """Extract assistant text from OpenAI-compatible or provider-variant payload."""
    if not isinstance(payload, dict):
        return ""

    payload_type = payload.get("type")
    if isinstance(payload_type, str):
        normalized_type = payload_type.strip().lower()
        if normalized_type == "content_block_delta":
            delta = payload.get("delta")
            if isinstance(delta, dict):
                value = delta.get("text")
                if isinstance(value, str) and value:
                    return value
            if isinstance(delta, str):
                return delta
        if normalized_type == "content_block_start":
            content_block = payload.get("content_block")
            if isinstance(content_block, dict):
                block_type = str(content_block.get("type") or "").strip().lower()
                if block_type in {"text", "output_text"}:
                    block_text = _extract_text_from_content_payload(content_block)
                    if block_text:
                        return block_text
        if normalized_type in {"response.text.delta", "response.output_text.delta"}:
            delta_text = payload.get("delta")
            if isinstance(delta_text, str):
                return delta_text
        if normalized_type.endswith("output_text.delta"):
            delta_text = payload.get("delta")
            if isinstance(delta_text, str):
                return delta_text
        if normalized_type in {"response.text.done", "response.output_text.done"}:
            done_text = payload.get("text")
            if isinstance(done_text, str):
                return done_text
            output_text = payload.get("output_text")
            if isinstance(output_text, str):
                return output_text
            delta_text = payload.get("delta")
            if isinstance(delta_text, str):
                return delta_text
        if normalized_type.endswith("output_text.done"):
            done_text = payload.get("text")
            if isinstance(done_text, str):
                return done_text
        if normalized_type == "response.refusal.delta":
            reasoning_text = payload.get("delta")
            if isinstance(reasoning_text, str):
                return reasoning_text
        if normalized_type.endswith("content_part.added"):
            part_text = _extract_text_from_content_payload(payload.get("part"))
            if part_text:
                return part_text
        if normalized_type in {"response.content_part.done", "response.content_part.added"}:
            part_text = _extract_text_from_content_payload(payload.get("part"))
            if part_text:
                return part_text
        if normalized_type in {"response.output_item.added", "response.output_item.done"}:
            item_text = _extract_text_from_content_payload(payload.get("item"))
            if item_text:
                return item_text
        if normalized_type.endswith("message.delta"):
            delta_payload = payload.get("delta")
            delta_text = _extract_text_from_content_payload(delta_payload)
            if delta_text:
                return delta_text

    item_payload = payload.get("item")
    if isinstance(item_payload, dict):
        item_text = _extract_text_from_content_payload(item_payload.get("content"))
        if item_text:
            return item_text

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0] if isinstance(choices[0], dict) else {}

        delta = first_choice.get("delta")
        if isinstance(delta, dict):
            delta_text = _extract_text_from_content_payload(delta.get("content"))
            if delta_text:
                return delta_text
            plain_delta_text = delta.get("text")
            if isinstance(plain_delta_text, str):
                return plain_delta_text

        message = first_choice.get("message")
        if isinstance(message, dict):
            message_text = _extract_text_from_content_payload(message.get("content"))
            if message_text:
                return message_text

        choice_text = first_choice.get("text")
        if isinstance(choice_text, str):
            return choice_text

    root_message = payload.get("message")
    if isinstance(root_message, dict):
        root_message_text = _extract_text_from_content_payload(root_message.get("content"))
        if root_message_text:
            return root_message_text

    root_content = _extract_text_from_content_payload(payload.get("content"))
    if root_content:
        return root_content

    output_text = payload.get("output_text")
    if isinstance(output_text, str):
        return output_text

    output = payload.get("output")
    output_text_from_output = _extract_text_from_content_payload(output)
    if output_text_from_output:
        return output_text_from_output

    response_payload = payload.get("response")
    response_text_from_output = _extract_text_from_content_payload(response_payload)
    if response_text_from_output:
        return response_text_from_output

    candidates = payload.get("candidates")
    if isinstance(candidates, list):
        candidates_text = _extract_text_from_content_payload(candidates)
        if candidates_text:
            return candidates_text

    response_text = payload.get("response")
    if isinstance(response_text, str):
        return response_text

    token_text = payload.get("token")
    if isinstance(token_text, str):
        return token_text

    delta_root = payload.get("delta")
    if isinstance(delta_root, str):
        return delta_root
    if isinstance(delta_root, dict):
        delta_root_text = _extract_text_from_content_payload(delta_root.get("content"))
        if delta_root_text:
            return delta_root_text

    return ""


def _extract_tool_calls(payload: Any) -> list[dict]:
    """Extract OpenAI-like tool calls for diagnostic fallback rendering."""
    if not isinstance(payload, dict):
        return []

    payload_type = payload.get("type")
    if isinstance(payload_type, str):
        normalized_type = payload_type.strip().lower()
        if normalized_type == "content_block_start":
            block = payload.get("content_block")
            if isinstance(block, dict) and str(block.get("type") or "").strip().lower() == "tool_use":
                name = block.get("name")
                args = block.get("input")
                if isinstance(name, str) and name:
                    if isinstance(args, str):
                        serialized_args = args
                    else:
                        try:
                            serialized_args = json.dumps(args or {}, ensure_ascii=False)
                        except Exception:
                            serialized_args = ""
                    return [{"function": {"name": name, "arguments": serialized_args}}]
        if normalized_type in {"response.output_item.added", "response.output_item.done"}:
            item = payload.get("item")
            if isinstance(item, dict):
                item_type = str(item.get("type") or "").strip().lower()
                if item_type in {"function_call", "tool_call"}:
                    name = item.get("name")
                    arguments = item.get("arguments")
                    if isinstance(name, str) and name:
                        return [{
                            "function": {
                                "name": name,
                                "arguments": arguments if isinstance(arguments, str) else ""
                            }
                        }]
        if normalized_type in {"response.function_call_arguments.delta", "response.tool_call_arguments.delta"}:
            name = payload.get("name")
            delta_args = payload.get("delta")
            if isinstance(name, str) and name:
                return [{
                    "function": {
                        "name": name,
                        "arguments": delta_args if isinstance(delta_args, str) else ""
                    }
                }]

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return []
    first_choice = choices[0] if isinstance(choices[0], dict) else {}

    delta = first_choice.get("delta")
    if isinstance(delta, dict) and isinstance(delta.get("tool_calls"), list):
        return [tc for tc in delta.get("tool_calls", []) if isinstance(tc, dict)]

    message = first_choice.get("message")
    if isinstance(message, dict) and isinstance(message.get("tool_calls"), list):
        return [tc for tc in message.get("tool_calls", []) if isinstance(tc, dict)]

    return []


def _is_stream_finished_payload(payload: Any) -> bool:
    """Detect terminal stream payload without waiting for explicit [DONE] line."""
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
    if isinstance(status_value, str):
        if status_value.strip().lower() in {"done", "completed", "complete", "finished", "stop"}:
            return True

    response_obj = payload.get("response")
    if isinstance(response_obj, dict):
        response_status = response_obj.get("status")
        if isinstance(response_status, str):
            if response_status.strip().lower() in {"done", "completed", "complete", "finished", "stop"}:
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
        if isinstance(finish_reason, str) and finish_reason.strip():
            normalized_finish = finish_reason.strip().lower()
            if normalized_finish not in {"finish_reason_unspecified", "unspecified"}:
                return True

    return False


def _tool_calls_to_text(tool_calls: list[dict]) -> str:
    """Convert tool calls to readable text so UI never renders empty response."""
    if not tool_calls:
        return ""
    lines = ["Model requested tool call(s):"]
    for tc in tool_calls[:6]:
        fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
        name = fn.get("name") if isinstance(fn, dict) else None
        args = fn.get("arguments") if isinstance(fn, dict) else None
        if isinstance(name, str) and name:
            if isinstance(args, str) and args.strip():
                args_preview = args.strip()
                if len(args_preview) > 220:
                    args_preview = args_preview[:220] + " ..."
                lines.append(f"- {name}({args_preview})")
            else:
                lines.append(f"- {name}()")
        else:
            lines.append("- <unknown_tool>()")
    return "\n".join(lines)


def _is_internal_agent_payload(text: Optional[str]) -> bool:
    """Detect internal agent/tool orchestration payloads that should not enter user memory."""
    if is_internal_orchestration_payload(text):
        return True
    normalized = (text or "").strip().lower()
    return normalized.startswith("your previous pigtex_fs block was invalid.")


def _is_default_conversation_title(title: Optional[str]) -> bool:
    """Check whether a title is still a placeholder/default."""
    normalized = (title or "").strip().lower()
    return normalized in DEFAULT_CONVERSATION_TITLES


def _build_fallback_title(user_text: str) -> str:
    """Fallback deterministic title when AI title generation is unavailable."""
    normalized = " ".join((user_text or "").split()).strip()
    if not normalized:
        return "New Conversation"
    if len(normalized) > 72:
        normalized = normalized[:69].rstrip() + "..."
    if normalized:
        normalized = normalized[0].upper() + normalized[1:]
    return normalized


def _sanitize_generated_title(raw_title: str) -> str:
    """Normalize/sanitize generated title text for persistence."""
    text = (raw_title or "").strip()
    if not text:
        return ""

    text = text.replace("```", " ")
    text = text.splitlines()[0] if "\n" in text else text
    text = re.sub(r"^\s*(title|conversation title|topic)\s*:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*[-*#\d\.\)\s]+", "", text)
    text = text.strip().strip("\"'`")
    text = re.sub(r"\s+", " ", text)
    text = text.rstrip(".,;:!?- ")

    if len(text) > 72:
        text = text[:72].rstrip(".,;:!?- ")

    if _is_default_conversation_title(text):
        return ""
    return text


async def _generate_conversation_title_from_ai(
    upstream_url: str,
    headers: Dict[str, str],
    model: str,
    user_text: str,
    assistant_text: str
) -> Optional[str]:
    """Generate a concise conversation title from first-turn context."""
    prompt = (
        "You generate chat titles.\n"
        "Return exactly ONE concise title (3-8 words).\n"
        "Rules:\n"
        "- Match the user's language.\n"
        "- Specific and descriptive.\n"
        "- No quotes, no markdown, no numbering.\n"
        "- No trailing punctuation.\n"
        "- Output title text only."
    )

    context_payload = (
        f"User:\n{(user_text or '').strip()[:900]}\n\n"
        f"Assistant:\n{(assistant_text or '').strip()[:1200]}"
    )

    title_request = {
        "model": model or "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": context_payload},
        ],
        "temperature": 0.2,
        "max_tokens": 32,
        "stream": False,
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            upstream_url,
            json=title_request,
            headers=headers
        )
        if response.status_code != 200:
            return None

        payload = response.json()
        generated = _extract_text_from_chat_payload(payload)
        if not generated:
            return None
        title = _sanitize_generated_title(generated)
        return title or None


async def _auto_title_conversation_if_needed(
    coordinator: Optional[Any],
    conversation_id: Optional[str],
    request: V1ChatCompletionRequest,
    upstream_url: str,
    headers: Dict[str, str],
    assistant_text: str,
) -> None:
    """
    Auto-title a conversation after first meaningful response.

    Safe/non-fatal: all errors are swallowed to avoid impacting chat flow.
    """
    if not coordinator or not conversation_id:
        return

    assistant_text = (assistant_text or "").strip()
    if not assistant_text:
        return

    try:
        conv = coordinator.local.get_conversation(conversation_id)
        if not conv:
            return

        request_messages = [{"role": m.role, "content": m.content} for m in request.messages]
        latest_user_text = _latest_user_message(request_messages).strip()

        if not latest_user_text:
            for msg in reversed(coordinator.local.get_messages(conversation_id, limit=12)):
                if msg.role == "user" and msg.content.strip():
                    latest_user_text = msg.content.strip()
                    break

        if not latest_user_text:
            return

        current_title = (conv.title or "").strip()
        fallback_title = _build_fallback_title(latest_user_text)
        legacy_seed_title = latest_user_text[:50] + "..." if len(latest_user_text) > 50 else latest_user_text

        should_auto_title = _is_default_conversation_title(current_title)
        if not should_auto_title and current_title:
            should_auto_title = current_title in {fallback_title, legacy_seed_title}
        if not should_auto_title:
            return

        generated_title: Optional[str] = None
        try:
            generated_title = await _generate_conversation_title_from_ai(
                upstream_url=upstream_url,
                headers=headers,
                model=request.model,
                user_text=latest_user_text,
                assistant_text=assistant_text,
            )
        except Exception:
            generated_title = None

        next_title = generated_title or fallback_title
        next_title = _sanitize_generated_title(next_title)
        if not next_title:
            return
        if current_title == next_title:
            return

        conv.title = next_title
        conv.updated_at = datetime.now()
        coordinator.local.save_conversation(conv)
    except Exception:
        return


def _extract_stream_error(payload: Any) -> Optional[str]:
    """Extract stream error message if present."""
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, str) and error.strip():
        return error
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message
    return None




# =============================================================================
# API Key Management
# =============================================================================

@router.post("/v1/keys")
async def create_or_update_provider_key(
    current_user: User = Depends(get_current_user),
):
    del current_user
    _raise_removed_v1_keys_endpoint()


@router.get("/v1/keys")
async def get_provider_key(
    current_user: User = Depends(get_current_user),
):
    del current_user
    _raise_removed_v1_keys_endpoint()


@router.delete("/v1/keys/{key_id}")
async def delete_provider_key(
    key_id: str,
    current_user: User = Depends(get_current_user),
):
    del key_id, current_user
    _raise_removed_v1_keys_endpoint()


# =============================================================================
# Validate Provider API Key (test connection)
# =============================================================================

@router.post("/v1/keys/validate")
async def validate_provider_key(
    api_key: Optional[str] = Query(None, description="Provider API key (BYOK)"),
    api_base_url: Optional[str] = Query(None, description="Provider API base URL (BYOK)"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_api_base_url: Optional[str] = Header(None, alias="X-API-Base-URL"),
    x_api_provider: Optional[str] = Header(None, alias="X-API-Provider"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Test API connection.
    Preferred: pass BYOK credentials in query/header.
    """
    cfg = _resolve_upstream_config(
        current_user=current_user,
        db=db,
        api_key=api_key,
        base_url=api_base_url,
        header_api_key=x_api_key,
        header_base_url=x_api_base_url,
        api_provider=x_api_provider,
    )
    base_url = cfg.base_url
    provider = cfg.api_provider
    upstream_headers = _build_upstream_auth_headers(cfg)
    models_url = _build_upstream_models_url(cfg)
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                models_url,
                headers=upstream_headers,
            )
            
            if response.status_code == 200:
                models_data = _normalize_models_payload_for_config(cfg, response.json())
                _touch_legacy_key_usage(db, cfg.db_key_id)
                return {
                    "valid": True,
                    "message": f"Kết nối thành công ({provider.upper()}).",
                    "provider": provider,
                    "models_count": len(models_data.get("data", [])),
                    "base_url": base_url,
                    "source": cfg.source,
                }
            elif response.status_code == 401:
                return {
                    "valid": False,
                    "message": f"API key không hợp lệ hoặc đã hết hạn ({provider}).",
                    "provider": provider,
                    "status_code": 401,
                    "source": cfg.source,
                }
            else:
                return {
                    "valid": False,
                    "message": f"API trả về lỗi: {response.status_code}",
                    "provider": provider,
                    "status_code": response.status_code,
                    "source": cfg.source,
                }
    except httpx.RequestError as e:
        logger.warning(
            "API key validation connection failed provider=%s base_url=%s source=%s error=%s",
            provider,
            base_url,
            cfg.source,
            e,
        )
        return {
            "valid": False,
            "message": f"Không thể kết nối tới {provider}.",
            "provider": provider,
            "base_url": base_url,
        }


# =============================================================================
# /v1/chat/completions - THE MAIN ENDPOINT
# =============================================================================

@router.post("/v1/chat/completions")
async def v1_chat_completions(
    request: V1ChatCompletionRequest,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_api_base_url: Optional[str] = Header(None, alias="X-API-Base-URL"),
    x_api_provider: Optional[str] = Header(None, alias="X-API-Provider"),
    raw_request: Request = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    OpenAI-compatible chat completions endpoint with local-first memory.
    """
    current_user_id = str(current_user.id)
    request_id = (
        (raw_request.headers.get("X-Request-ID") if raw_request else None)
        or str(uuid.uuid4())
    )
    request_started_at = perf_counter()

    cfg = _resolve_upstream_config(
        current_user=current_user,
        db=db,
        api_key=request.api_key,
        base_url=request.api_base_url,
        header_api_key=x_api_key,
        header_base_url=x_api_base_url,
        api_provider=x_api_provider,
    )
    cfg = await _hydrate_texapi_partner_config(cfg, current_user)
    request.model = _require_explicit_model_id(
        request.model,
        provider=cfg.api_provider,
        operation="chat_completions",
    )
    logger.info(
        "v1_chat_request_start request_id=%s user_id=%s model=%s stream=%s conversation_id=%s provider=%s",
        request_id,
        current_user_id,
        request.model,
        bool(request.stream),
        request.conversation_id,
        cfg.api_provider,
    )
    _release_db_connection(db, reason="chat_config_resolved", request_id=request_id)
    logger.info(
        "v1_chat_request_config request_id=%s source=%s provider=%s base_url=%s",
        request_id,
        cfg.source,
        cfg.api_provider,
        cfg.base_url,
    )
    base_url = cfg.base_url
    upstream_url = _build_upstream_url(cfg, "/v1/chat/completions")

    incoming_messages = [{"role": m.role, "content": m.content} for m in request.messages]
    latest_user_text = _latest_user_message(incoming_messages)

    async def _prepare_chat_request():
        """Heavy pre-processing. For streaming, runs inside generator (lazy)."""
        prepare_started_at = perf_counter()
        nonlocal current_user
        try:
            try:
                current_user = db.merge(current_user)
            except Exception:
                pass
            _coordinator = None
            _search_context: Optional[SearchContext] = None
            _conversation_id = request.conversation_id
            _memory_context = None
            _memory_context_meta = _serialize_memory_context_meta(
                context=None,
                request=request,
                enabled=bool(request.use_memory),
            )
            _learning_context: Optional[Dict[str, Any]] = None

            # ── Web search decision ──
            # Priority: explicit user request > query policy recommendation > weak model trigger > global default
            turn_search_policy = _derive_web_search_policy(request, latest_user_text)
            internal_tool_turn = _is_internal_tool_turn_text(latest_user_text)
            if internal_tool_turn:
                search_enabled = False
                logger.info(
                    "v1_internal_tool_turn request_id=%s disable_web_search=True",
                    request_id,
                )
            elif request.use_web_search is not None:
                search_enabled = request.use_web_search
            else:
                search_enabled = bool(settings.web_search_enabled_default)
                if not search_enabled and bool(turn_search_policy.get("recommended_search")):
                    search_enabled = True
                    logger.info(
                        "v1_policy_auto_search request_id=%s mode=%s complexity=%s reason=%s",
                        request_id,
                        turn_search_policy.get("resolved_mode"),
                        turn_search_policy.get("complexity_score"),
                        turn_search_policy.get("reason_label"),
                    )
                # Auto-enable web search for weak/free models to compensate knowledge gaps
                if not search_enabled and latest_user_text:
                    try:
                        from ..memory.prompt_injector import PromptInjector
                        _pi = PromptInjector(db)
                        _intent = _pi.detect_intent(latest_user_text)
                        if _pi.get_web_search_recommendation(request.model, latest_user_text, _intent):
                            search_enabled = True
                            logger.info(
                                "v1_weak_model_auto_search request_id=%s model=%s intent=%s",
                                request_id, request.model, _intent,
                            )
                    except Exception as e:
                        logger.debug("Weak model search recommendation skipped: %s", e)

            if request.use_memory:
                _msgs, _coordinator, _conversation_id, _memory_context = await _build_messages_with_local_memory(
                    request=request, current_user=current_user, db=db,
                    incoming_messages=incoming_messages, latest_user_text=latest_user_text,
                    upstream_config=UpstreamRequestConfig(
                        api_key=cfg.api_key,
                        base_url=cfg.base_url,
                        api_provider=cfg.api_provider,
                    ),
                    request_id=request_id,
                )
                _memory_context_meta = _serialize_memory_context_meta(
                    context=_memory_context,
                    request=request,
                    enabled=True,
                )
            else:
                _msgs = list(incoming_messages)
                _conversation_id = None
                _memory_context_meta = _serialize_memory_context_meta(
                    context=None,
                    request=request,
                    enabled=False,
                )

            if not request.use_memory:
                file_context_prompt = _build_file_context_system_prompt(
                    request.file_attachments,
                    query_text=latest_user_text,
                )
                if file_context_prompt:
                    _msgs = _prepend_system_message(_msgs, file_context_prompt)

            if search_enabled and latest_user_text:
                try:
                    search_coordinator = await _get_search_coordinator()
                    search_timeout_seconds = max(
                        6.0,
                        float(
                            getattr(
                                settings,
                                "web_search_total_timeout_seconds",
                                float(getattr(settings, "web_search_timeout_seconds", 12.0) or 12.0) + 8.0,
                            )
                            or 0.0
                        ),
                    )
                    requested_search_mode = str(turn_search_policy.get("resolved_mode") or "auto")
                    deep_read_requested = bool(turn_search_policy.get("deep_read"))
                    deep_verify_requested = bool(turn_search_policy.get("deep_verify"))
                    search_max_results = int(turn_search_policy.get("max_results") or 5)
                    logger.info(
                        "v1_search_policy request_id=%s enabled=%s turn_mode=%s requested_mode=%s resolved_mode=%s "
                        "deep_read=%s deep_verify=%s max_results=%s complexity=%s reason=%s",
                        request_id,
                        bool(search_enabled),
                        turn_search_policy.get("turn_mode"),
                        turn_search_policy.get("requested_mode"),
                        requested_search_mode,
                        deep_read_requested,
                        deep_verify_requested,
                        search_max_results,
                        turn_search_policy.get("complexity_score"),
                        turn_search_policy.get("reason_label"),
                    )
                    _search_context = await search_coordinator.run(
                        user_message=latest_user_text,
                        force=True,
                        max_results=search_max_results,
                        deep_read=deep_read_requested,
                        mode=requested_search_mode,
                        deep_verify=deep_verify_requested,
                        total_timeout_seconds=search_timeout_seconds,
                    )
                    if _search_context.has_results:
                        _msgs = _prepend_system_message(_msgs, _search_context.to_prompt_section())
                except asyncio.TimeoutError:
                    logger.warning(
                        "Web search pipeline timed out unexpectedly request_id=%s timeout_seconds=%s",
                        request_id,
                        search_timeout_seconds,
                    )
                    _search_context = SearchContext(
                        status_hint="timeout",
                        warnings=[
                            "Web search hit the server time budget before sources were ready. PigTex continued with any evidence fetched so far."
                        ],
                    )
                    if _search_context.has_results:
                        _msgs = _prepend_system_message(_msgs, _search_context.to_prompt_section())
                except Exception as e:
                    logger.warning("Web search pipeline failed request_id=%s error=%s", request_id, e)
                    _search_context = SearchContext(
                        status_hint="error",
                        warnings=[
                            "Web search failed before live sources could be prepared. PigTex continued without live search evidence."
                        ],
                    )

            learning_mode = str(request.learning_mode or "auto").strip().lower()
            if learning_mode not in {"auto", "off", "teacher"}:
                learning_mode = "auto"
            if latest_user_text and learning_mode != "off":
                try:
                    learning_service = LearningService(db, current_user)
                    _learning_context = learning_service.sync_chat_copilot(
                        conversation_id=_conversation_id,
                        workspace_id=request.workspace_id,
                        message_text=latest_user_text,
                        learning_mode=learning_mode,
                        preferred_program_id=request.learning_program_id,
                    )
                    learning_prompt = (
                        _learning_context.get("system_prompt")
                        if isinstance(_learning_context, dict)
                        else None
                    )
                    if isinstance(learning_prompt, str) and learning_prompt.strip():
                        _msgs = _prepend_system_message(_msgs, learning_prompt)
                except Exception as e:
                    logger.warning("Learning copilot skipped request_id=%s error=%s", request_id, e)
                    _learning_context = None

            runtime_block = build_runtime_instruction_block(request.runtime_instruction or "")
            if runtime_block:
                _msgs = _prepend_system_message(_msgs, runtime_block)

            _msgs = _apply_code_format_instruction(_msgs)
            _msgs = _apply_markdown_raw_instruction(_msgs, latest_user_text)
            _msgs = apply_pigtex_identity_system_prompt(_msgs)
            _upstream_url, _rd = _build_upstream_chat_request(cfg, request, _msgs)
            _hdrs = _build_upstream_auth_headers(cfg)
            _touch_legacy_key_usage(db, cfg.db_key_id)
            prepare_ms = int((perf_counter() - prepare_started_at) * 1000)
            logger.info(
                "v1_prepare_done request_id=%s user_id=%s stream=%s prepare_ms=%s messages=%s provider=%s web_search=%s web_results=%s",
                request_id,
                current_user_id,
                bool(request.stream),
                prepare_ms,
                len(_msgs),
                cfg.api_provider,
                bool(search_enabled),
                int(getattr(_search_context, "raw_results_count", 0) or 0),
            )
            return {
                "upstream_url": _upstream_url,
                "request_data": _rd, "headers": _hdrs,
                "coordinator": _coordinator, "conversation_id": _conversation_id,
                "search_context": _search_context,
                "web_search_enabled": bool(search_enabled),
                "memory_context_meta": _memory_context_meta,
                "learning_context": _learning_context,
            }
        finally:
            _release_db_connection(db, reason="chat_prepare_done", request_id=request_id)

    try:
        if request.stream:
            return await _handle_streaming(
                cfg=cfg,
                upstream_url=upstream_url,
                request=request,
                current_user=current_user,
                current_user_id=current_user_id,
                db=db,
                conversation_id=request.conversation_id,
                lazy_prepare=_prepare_chat_request,
                request_id=request_id,
                request_started_at=request_started_at,
            )
        else:
            prep = await _prepare_chat_request()
            return await _handle_non_streaming(
                cfg=cfg,
                upstream_url=prep.get("upstream_url") or upstream_url,
                request_data=prep["request_data"],
                headers=prep["headers"],
                request=request,
                current_user=current_user,
                current_user_id=current_user_id,
                db=db,
                coordinator=prep.get("coordinator"),
                conversation_id=prep.get("conversation_id"),
                search_context=prep.get("search_context"),
                web_search_enabled=bool(prep.get("web_search_enabled")),
                memory_context_meta=prep.get("memory_context_meta"),
                learning_context=prep.get("learning_context"),
                request_id=request_id,
                request_started_at=request_started_at,
            )
    
    except httpx.RequestError as e:
        _track_usage(db, current_user_id, request.model, 0, 0, "/v1/chat/completions", "error")
        logger.warning(
            "Upstream chat connection failed request_id=%s base_url=%s error=%s",
            request_id,
            base_url,
            e,
        )
        
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "upstream_api_connection_error",
                "message": "Không thể kết nối tới upstream API.",
                "base_url": base_url,
            }
        )


# =============================================================================
# Local-first memory context builder
# =============================================================================

def _resolve_history_token_budget(model: str, stream: bool) -> int:
    """
    Tune local-history window to balance recall and TTFT.
    Stream mode uses a tighter budget to reduce prompt prefill latency.
    """
    lowered_model = (model or "").lower()
    if any(tag in lowered_model for tag in ("gpt-5", "gpt-4.1", "claude-4", "claude-3.7", "o1", "o3", "gemini-3", "gemini-2.5")):
        base_budget = 7200
    elif any(tag in lowered_model for tag in ("mini", "flash", "haiku", "nano")):
        base_budget = 4200
    else:
        base_budget = 5600

    if stream:
        # Stream path favors quicker prefill/start over deep history depth.
        base_budget = int(base_budget * 0.55)

    return max(1800 if stream else 2600, min(9000, base_budget))


def _estimate_message_content_tokens(content: Any) -> int:
    if isinstance(content, str):
        return int(len(content.split()) * 1.3)
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    total += int(len(str(part.get("text", "")).split()) * 1.3)
                elif part.get("type") == "image_url":
                    total += 85
        return total
    return 0


def _estimate_messages_tokens(messages: List[Dict[str, Any]]) -> int:
    return sum(_estimate_message_content_tokens(message.get("content")) for message in messages)


def _resolve_usage_rates_for_model(model: str) -> tuple[float, float, bool]:
    normalized_model = (model or "").strip().lower()
    for model_hint, input_rate, output_rate in _USAGE_MODEL_RATES_USD_PER_1M:
        if model_hint in normalized_model:
            return input_rate, output_rate, True
    return _DEFAULT_USAGE_INPUT_USD_PER_1M, _DEFAULT_USAGE_OUTPUT_USD_PER_1M, False


def _estimate_usage_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> tuple[float, bool]:
    safe_prompt_tokens = max(0, int(prompt_tokens or 0))
    safe_completion_tokens = max(0, int(completion_tokens or 0))
    input_rate, output_rate, is_rate_known = _resolve_usage_rates_for_model(model)

    cost = (
        (safe_prompt_tokens / 1_000_000.0) * input_rate
        + (safe_completion_tokens / 1_000_000.0) * output_rate
    )
    return max(0.0, round(cost, 10)), is_rate_known

async def _build_messages_with_local_memory(
    request: V1ChatCompletionRequest,
    current_user: User,
    db: Session,
    incoming_messages: list[dict],
    latest_user_text: str,
    upstream_config: Optional[UpstreamRequestConfig] = None,
    request_id: str = "",
) -> tuple[list[dict], Any, Optional[str], Any]:
    """Build request messages using MemoryCoordinator + local SQLite."""
    non_system_messages = [m for m in incoming_messages if m.get("role") != "system"]
    has_client_history = len(non_system_messages) > 1
    use_history = request.use_history is not False and not has_client_history
    use_knowledge = request.use_knowledge is not False
    use_facts = request.use_facts is not False

    coordinator = get_memory_coordinator(db, current_user.id)
    coordinator.set_request_upstream_context(
        upstream_config,
        ai_model=request.model,
    )
    history_budget_tokens = _resolve_history_token_budget(request.model, bool(request.stream))
    coordinator.working.max_tokens = history_budget_tokens

    if request.conversation_id:
        if use_history:
            conv = coordinator.load_conversation(request.conversation_id)
        else:
            conv = coordinator.local.get_conversation(request.conversation_id)
            if conv:
                coordinator.working.clear()
                coordinator.working.conversation_id = conv.id
                coordinator.working.workspace_id = conv.workspace_id
        if not conv:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found"
            )
    else:
        conv = coordinator.create_conversation(
            title="New Conversation",
            workspace_id=request.workspace_id
        )

    # ── Persist images/files and embed references in stored message ──
    original_user_msg = next(
        (m for m in reversed(request.messages) if m.role == "user"),
        None,
    )
    stored_user_text = latest_user_text
    if original_user_msg and isinstance(original_user_msg.content, list):
        # Multimodal message: save images to disk and build markdown refs
        image_refs = []
        for part in original_user_msg.content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                img_url = (part.get("image_url") or {}).get("url", "")
                if img_url.startswith("data:"):
                    import uuid as _uuid
                    img_id = str(_uuid.uuid4())
                    serve_url = save_base64_image_to_disk(img_id, img_url, current_user.id)
                    if serve_url:
                        image_refs.append(f"![image]({serve_url})")
        if image_refs:
            # Append image refs to stored text
            refs_block = "\n".join(image_refs)
            stored_user_text = f"{latest_user_text}\n\n{refs_block}" if latest_user_text else refs_block

    file_refs = _build_file_attachment_refs(request.file_attachments)
    if file_refs:
        refs_block = "\n".join(file_refs)
        stored_user_text = f"{stored_user_text}\n\n{refs_block}" if stored_user_text else refs_block

    if stored_user_text and not _is_internal_agent_payload(stored_user_text):
        coordinator.add_message("user", stored_user_text)

    # Keep context builder robust when caller sends no user message.
    query_text = latest_user_text or ""
    latency_mode = "low_latency" if bool(request.stream) else "balanced"
    context = await coordinator.build_context(
        user_message=query_text,
        model=request.model,
        user_tier=current_user.plan,
        include_knowledge=use_knowledge,
        include_facts=use_facts,
        include_history=use_history,
        latency_mode=latency_mode,
    )

    messages_for_api = coordinator.format_context_for_api(
        context,
        query_text,
        append_user_message=False
    )
    messages_for_api = _merge_request_system_messages(messages_for_api, request.messages)

    # If caller already sends history, or local history is disabled, keep caller messages.
    # Also keep caller messages when request has no user message (tooling edge-case).
    if has_client_history or not use_history or not latest_user_text:
        messages_for_api.extend(non_system_messages)

    # ── Multimodal fix: replace last user message with original content ──
    # The memory system stores user messages as text-only. If the original
    # request contains multimodal content (images), the last user message
    # in messages_for_api is text-only and we must swap it with the
    # original multimodal message so the AI model actually receives images.
    original_user_msg = next(
        (m for m in reversed(request.messages) if m.role == "user"),
        None,
    )
    if original_user_msg and isinstance(original_user_msg.content, list):
        # Find the last user message in the assembled list and replace it
        for i in range(len(messages_for_api) - 1, -1, -1):
            if messages_for_api[i].get("role") == "user":
                messages_for_api[i] = {
                    "role": "user",
                    "content": original_user_msg.content,
                }
                break

    file_context_prompt = _build_file_context_system_prompt(
        request.file_attachments,
        query_text=latest_user_text,
    )
    if file_context_prompt:
        messages_for_api = _prepend_system_message(messages_for_api, file_context_prompt)
    style_preferences_prompt = _build_style_preferences_system_prompt(coordinator)
    if style_preferences_prompt:
        messages_for_api = _prepend_system_message(messages_for_api, style_preferences_prompt)

    logger.info(
        "v1_context_ready request_id=%s conversation_id=%s model=%s stream=%s messages=%s approx_tokens=%s context_tokens=%s context_sources=%s history_budget=%s use_history=%s has_client_history=%s use_knowledge=%s use_facts=%s",
        request_id,
        conv.id,
        request.model,
        bool(request.stream),
        len(messages_for_api),
        _estimate_messages_tokens(messages_for_api),
        int(getattr(context, "total_tokens", 0) or 0),
        len(getattr(context, "sources", []) or []),
        history_budget_tokens,
        use_history,
        has_client_history,
        use_knowledge,
        use_facts,
    )

    return messages_for_api, coordinator, conv.id, context


# =============================================================================
# /v1/media/fetch - Authenticated media proxy for generated assets
# =============================================================================


@router.get("/v1/media/fetch")
async def v1_fetch_media_asset(
    url: str = Query(..., description="Absolute HTTP(S) URL for a generated media asset"),
    api_key: Optional[str] = Query(None, description="Provider API key (BYOK)"),
    api_base_url: Optional[str] = Query(None, description="Provider API base URL (BYOK)"),
    api_provider: Optional[str] = Query(None, description="Provider ID (openai/anthropic/gemini/alibaba/auto)"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_api_base_url: Optional[str] = Header(None, alias="X-API-Base-URL"),
    x_api_provider: Optional[str] = Header(None, alias="X-API-Provider"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    clean_url = (url or "").strip()
    if not _is_safe_public_media_url(clean_url):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_media_url",
                "message": "Media URL must be a public HTTP(S) address.",
            },
        )

    declared_base_url = api_base_url or x_api_base_url
    should_attach_auth = _should_attach_upstream_auth_for_media_url(
        clean_url,
        None,
        declared_base_url=declared_base_url,
    )
    cfg: Optional[ResolvedUpstreamConfig] = None
    if should_attach_auth or api_key or x_api_key or declared_base_url or x_api_provider:
        cfg = _resolve_upstream_config(
            current_user=current_user,
            db=db,
            api_key=api_key,
            base_url=api_base_url,
            header_api_key=x_api_key,
            header_base_url=x_api_base_url,
            api_provider=api_provider or x_api_provider,
        )
        should_attach_auth = _should_attach_upstream_auth_for_media_url(
            clean_url,
            cfg,
            declared_base_url=declared_base_url,
        )

    headers: Dict[str, str] = {}
    if cfg and should_attach_auth:
        headers.update(_build_upstream_auth_headers(cfg))
        _touch_legacy_key_usage(db, cfg.db_key_id)

    try:
        async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
            response = await client.get(clean_url, headers=headers)
    except httpx.RequestError as exc:
        logger.warning("Generated media fetch failed url=%s error=%s", clean_url, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "media_fetch_failed",
                "message": "Could not fetch generated media from upstream.",
            },
        ) from exc

    if not response.is_success:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "media_fetch_failed",
                "message": f"Upstream media fetch returned HTTP {response.status_code}.",
                "status": response.status_code,
            },
        )

    return Response(
        content=response.content,
        media_type=response.headers.get("content-type", "application/octet-stream"),
    )


# =============================================================================
# /v1/providers - Public provider catalog
# =============================================================================


class V1ProviderCatalogItem(BaseModel):
    id: str
    label: str
    kind: str
    upstream_mode: str
    request_api_provider: str
    default_base_url: str
    docs_url: str
    auth_style: str
    supports_byok: bool
    managed_by_server: bool
    aliases: List[str] = Field(default_factory=list)


class V1ProviderCatalogResponse(BaseModel):
    data: List[V1ProviderCatalogItem]


@router.get("/v1/providers", response_model=V1ProviderCatalogResponse)
async def v1_list_providers(
    current_user: User = Depends(get_current_user),
):
    del current_user
    return {
        "data": build_public_provider_catalog(),
    }


# =============================================================================
# /v1/models - List available models
# =============================================================================

@router.get("/v1/models")
async def v1_list_models(
    api_key: Optional[str] = Query(None, description="Provider API key (BYOK)"),
    api_base_url: Optional[str] = Query(None, description="Provider API base URL (BYOK)"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_api_base_url: Optional[str] = Header(None, alias="X-API-Base-URL"),
    x_api_provider: Optional[str] = Header(None, alias="X-API-Provider"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    List models from upstream API.
    Preferred: pass BYOK credentials per request/header.
    """
    cfg = _resolve_upstream_config(
        current_user=current_user,
        db=db,
        api_key=api_key,
        base_url=api_base_url,
        header_api_key=x_api_key,
        header_base_url=x_api_base_url,
        api_provider=x_api_provider,
    )
    cfg = await _hydrate_texapi_partner_config(cfg, current_user)
    base_url = cfg.base_url
    upstream_headers = _build_upstream_auth_headers(cfg)
    models_url = _build_upstream_models_url(cfg)
    logger.info(
        "v1_models_request source=%s provider=%s base_url=%s key_preview=%s",
        cfg.source,
        cfg.api_provider,
        base_url,
        _mask_key(cfg.api_key),
    )
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            max_attempts = 3
            last_response: Optional[httpx.Response] = None
            for attempt in range(1, max_attempts + 1):
                response = await client.get(
                    models_url,
                    headers=upstream_headers,
                )
                last_response = response

                if response.status_code == 200:
                    _touch_legacy_key_usage(db, cfg.db_key_id)
                    return _normalize_models_payload_for_config(cfg, response.json())

                if attempt == 1 and _should_retry_texapi_partner_auth_error(response, cfg):
                    cfg = await _hydrate_texapi_partner_config(cfg, current_user, force_refresh=True)
                    upstream_headers = _build_upstream_auth_headers(cfg)
                    models_url = _build_upstream_models_url(cfg)
                    continue

                if response.status_code not in {502, 503, 504} or attempt == max_attempts:
                    break

                await asyncio.sleep(0.25 * attempt)

            _raise_upstream_http_exception(last_response or response, "models.list")
    except httpx.RequestError as e:
        logger.warning(
            "Model list connection failed provider=%s base_url=%s source=%s error=%s",
            cfg.api_provider,
            base_url,
            cfg.source,
            e,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "api_connection_error",
                "message": f"Cannot connect to {cfg.api_provider} API.",
            },
        )


# =============================================================================
# /v1/audio/* - OpenAI-Compatible Audio Endpoints
# =============================================================================

async def _transcribe_audio_via_alibaba_compatible(
    cfg: ResolvedUpstreamConfig,
    *,
    model: str,
    audio_bytes: bytes,
    audio_mime: str,
    language: Optional[str] = None,
    prompt: Optional[str] = None,
) -> str:
    user_parts: list[dict] = []
    if prompt and prompt.strip():
        user_parts.append({"type": "text", "text": prompt.strip()})
    if language and language.strip():
        user_parts.append({"type": "text", "text": f"Language hint: {language.strip()}"})
    user_parts.append({"type": "input_audio", "input_audio": {"data": _build_audio_data_url(audio_mime, audio_bytes)}})

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": user_parts}],
        "stream": False,
    }
    if language and language.strip():
        payload["asr_options"] = {"language_hints": [language.strip()]}
    upstream_url = _build_upstream_url(cfg, "/v1/chat/completions")
    headers = _build_upstream_auth_headers(cfg)
    headers["Content-Type"] = "application/json"

    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await client.post(upstream_url, json=payload, headers=headers)
    if response.status_code != 200:
        _raise_upstream_http_exception(response, "alibaba.audio.transcriptions")

    result = response.json()
    text = _extract_text_from_chat_payload(result).strip()
    if not text:
        text = _message_content_to_text(result).strip()
    if not text:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "alibaba_transcription_empty",
                "message": "Alibaba ASR response did not contain transcription text.",
            },
        )
    return text


def _resolve_alibaba_asr_model(model: str) -> str:
    return (model or "").strip()


def _resolve_alibaba_tts_language_type(request: V1AudioSpeechRequest, input_text: str) -> str:
    language = (request.language or "").strip().lower()
    if not language:
        return "Auto" if _VIETNAMESE_CHAR_RE.search(input_text or "") else "English"

    aliases = {
        "en": "English",
        "english": "English",
        "zh": "Chinese",
        "zh-cn": "Chinese",
        "zh-tw": "Chinese",
        "chinese": "Chinese",
        "ja": "Japanese",
        "japanese": "Japanese",
        "ko": "Korean",
        "korean": "Korean",
        "auto": "Auto",
        "vi": "Auto",
        "vietnamese": "Auto",
        "vi-vn": "Auto",
    }
    return aliases.get(language, "Auto")


def _resolve_alibaba_tts_model(request: V1AudioSpeechRequest) -> str:
    return (request.model or "").strip()


def _resolve_alibaba_tts_voice(request: V1AudioSpeechRequest) -> str:
    requested_voice = (request.voice or "").strip()
    if not requested_voice:
        return "Cherry"

    if requested_voice.lower() in {"alloy", "ash", "coral", "echo", "sage", "shimmer"}:
        return "Cherry"

    return requested_voice


def _build_alibaba_tts_instruction(request: V1AudioSpeechRequest, input_text: str) -> str:
    if not bool(request.prompt_enhance):
        return ""
    if _normalize_voice_prompt_profile(request.prompt_profile) != "world_class":
        return ""
    return _build_voice_directive_text(request, input_text)


async def _generate_speech_via_alibaba_native(
    cfg: ResolvedUpstreamConfig,
    request: V1AudioSpeechRequest,
    *,
    prepared_input: Optional[str] = None,
) -> tuple[bytes, str]:
    input_text = (prepared_input or request.input or "").strip()
    if not input_text:
        raise HTTPException(status_code=400, detail="Input text is required")

    resolved_model = _resolve_alibaba_tts_model(request)
    voice_instruction = _build_alibaba_tts_instruction(request, input_text)
    payload: dict[str, Any] = {
        "model": resolved_model,
        "input": {
            "text": input_text,
            "voice": _resolve_alibaba_tts_voice(request),
            "language_type": _resolve_alibaba_tts_language_type(request, input_text),
        },
    }
    parameters: dict[str, Any] = {}
    if voice_instruction and "instruct" in resolved_model.lower():
        parameters["instructions"] = voice_instruction
        parameters["optimize_instructions"] = True
    if parameters:
        payload["parameters"] = parameters

    native_path = "/api/v1/services/aigc/multimodal-generation/generation"
    candidate_urls = _build_alibaba_native_candidate_urls(cfg, native_path)
    headers = _build_upstream_auth_headers(cfg)
    headers["Content-Type"] = "application/json"

    result: Optional[dict[str, Any]] = None
    async with httpx.AsyncClient(timeout=240.0) as client:
        response: Optional[httpx.Response] = None
        last_error_response: Optional[httpx.Response] = None
        for index, upstream_url in enumerate(candidate_urls):
            response = await client.post(upstream_url, json=payload, headers=headers)
            if response.status_code == 200:
                result = response.json()
                break
            last_error_response = response
            if response.status_code == 404 and index < len(candidate_urls) - 1:
                logger.warning(
                    "Alibaba audio.speech native endpoint 404, retrying alternative url=%s",
                    upstream_url,
                )
                continue
            _raise_upstream_http_exception(response, "alibaba.audio.speech")

        if result is None:
            _raise_upstream_http_exception(
                last_error_response or response,
                "alibaba.audio.speech",
            )

        output = result.get("output") if isinstance(result, dict) else {}
        task_id = output.get("task_id") if isinstance(output, dict) and isinstance(output.get("task_id"), str) else None
        task_status = output.get("task_status") if isinstance(output, dict) and isinstance(output.get("task_status"), str) else None
        if (task_status or "").strip().upper() in {"PENDING", "RUNNING", "QUEUED"} and task_id:
            task_payload = await _poll_alibaba_task_result(client, cfg, task_id, headers)
            if isinstance(task_payload, dict):
                result = task_payload

    artifact = _extract_audio_artifacts_from_payload(result)
    audio_data = artifact.get("audio_data")
    if isinstance(audio_data, str):
        decoded = _decode_maybe_base64_audio(audio_data)
        if decoded:
            audio_format = str(artifact.get("audio_format") or request.response_format or "mp3").lower()
            format_map = {
                "mp3": "audio/mpeg",
                "wav": "audio/wav",
                "ogg": "audio/ogg",
                "aac": "audio/aac",
                "flac": "audio/flac",
            }
            return decoded, format_map.get(audio_format, "audio/mpeg")

    audio_url = artifact.get("audio_url")
    if isinstance(audio_url, str) and audio_url.strip():
        downloaded = await _download_audio(audio_url.strip(), timeout_seconds=60.0)
        if downloaded:
            return downloaded

    raise HTTPException(
        status_code=502,
        detail={
            "error": "alibaba_tts_no_audio_output",
            "message": "Alibaba TTS response did not include audio content.",
        },
    )


@router.post("/v1/audio/transcriptions")
async def v1_audio_transcriptions(
    file: UploadFile = File(...),
    model: Optional[str] = Form(None),
    language: Optional[str] = Form(None),
    prompt: Optional[str] = Form(None),
    response_format: Optional[str] = Form("json"),
    temperature: Optional[float] = Form(None),
    api_key: Optional[str] = Form(None),
    api_base_url: Optional[str] = Form(None),
    api_provider: Optional[str] = Form(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_api_base_url: Optional[str] = Header(None, alias="X-API-Base-URL"),
    x_api_provider: Optional[str] = Header(None, alias="X-API-Provider"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _raise_voice_feature_disabled("audio_transcription")

    cfg = _resolve_upstream_config(
        current_user=current_user,
        db=db,
        api_key=api_key,
        base_url=api_base_url,
        header_api_key=x_api_key,
        header_base_url=x_api_base_url,
        api_provider=x_api_provider or api_provider,
    )

    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Audio file is empty")

    audio_mime = (file.content_type or "").strip().lower()
    if not audio_mime:
        audio_mime = _guess_audio_mime_type(file.filename or "")
    model_id = _require_explicit_model_id(
        model,
        provider=cfg.api_provider,
        operation="audio_transcription",
    )

    if cfg.api_provider == "alibaba":
        transcript = await _transcribe_audio_via_alibaba_compatible(
            cfg,
            model=_resolve_alibaba_asr_model(model_id),
            audio_bytes=audio_bytes,
            audio_mime=audio_mime,
            language=language,
            prompt=prompt,
        )
        _touch_legacy_key_usage(db, cfg.db_key_id)
        if (response_format or "json").strip().lower() in {"text", "txt"}:
            return Response(content=transcript, media_type="text/plain; charset=utf-8")
        return {"text": transcript}

    upstream_url = _build_upstream_url(cfg, "/v1/audio/transcriptions")
    headers = _build_upstream_file_headers(cfg)
    form_data: dict[str, Any] = {"model": model_id}
    if language:
        form_data["language"] = language
    if prompt:
        form_data["prompt"] = prompt
    if response_format:
        form_data["response_format"] = response_format
    if temperature is not None:
        form_data["temperature"] = str(temperature)

    async with httpx.AsyncClient(timeout=240.0) as client:
        response = await client.post(
            upstream_url,
            data=form_data,
            files={
                "file": (
                    file.filename or f"audio_{uuid.uuid4().hex[:8]}.wav",
                    audio_bytes,
                    audio_mime or "application/octet-stream",
                )
            },
            headers=headers,
        )
    if response.status_code != 200:
        _raise_upstream_http_exception(response, "audio.transcriptions")

    _touch_legacy_key_usage(db, cfg.db_key_id)
    content_type = (response.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        return response.json()
    return Response(
        content=response.content,
        media_type=response.headers.get("content-type", "text/plain; charset=utf-8"),
    )


@router.post("/v1/audio/translations")
async def v1_audio_translations(
    file: UploadFile = File(...),
    model: Optional[str] = Form(None),
    prompt: Optional[str] = Form(None),
    response_format: Optional[str] = Form("json"),
    temperature: Optional[float] = Form(None),
    api_key: Optional[str] = Form(None),
    api_base_url: Optional[str] = Form(None),
    api_provider: Optional[str] = Form(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_api_base_url: Optional[str] = Header(None, alias="X-API-Base-URL"),
    x_api_provider: Optional[str] = Header(None, alias="X-API-Provider"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _raise_voice_feature_disabled("audio_translation")

    # Alibaba does not expose a direct OpenAI-style translation endpoint in compatible mode;
    # route translation requests through transcription with translation instruction.
    translation_prompt = "Translate this audio to English."
    if prompt and prompt.strip():
        translation_prompt = f"{translation_prompt}\n\nAdditional instruction: {prompt.strip()}"
    return await v1_audio_transcriptions(
        file=file,
        model=model,
        language=None,
        prompt=translation_prompt,
        response_format=response_format,
        temperature=temperature,
        api_key=api_key,
        api_base_url=api_base_url,
        api_provider=api_provider,
        x_api_key=x_api_key,
        x_api_base_url=x_api_base_url,
        x_api_provider=x_api_provider,
        current_user=current_user,
        db=db,
    )


@router.post("/v1/audio/speech")
async def v1_audio_speech(
    request: V1AudioSpeechRequest,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_api_base_url: Optional[str] = Header(None, alias="X-API-Base-URL"),
    x_api_provider: Optional[str] = Header(None, alias="X-API-Provider"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _raise_voice_feature_disabled("audio_speech")

    cfg = _resolve_upstream_config(
        current_user=current_user,
        db=db,
        api_key=request.api_key,
        base_url=request.api_base_url,
        header_api_key=x_api_key,
        header_base_url=x_api_base_url,
        api_provider=x_api_provider,
    )
    request.model = _require_explicit_model_id(
        request.model,
        provider=cfg.api_provider,
        operation="audio_speech",
    )
    speech_input = _enhance_voice_prompt_input(request)

    if cfg.api_provider == "anthropic":
        _raise_unsupported_provider_capability("anthropic", "audio_speech")

    if cfg.api_provider == "gemini":
        audio_bytes, audio_mime = await _generate_speech_via_gemini_native(cfg, request)
        _touch_legacy_key_usage(db, cfg.db_key_id)
        return Response(content=audio_bytes, media_type=audio_mime or "audio/wav")

    if cfg.api_provider == "alibaba":
        audio_bytes, audio_mime = await _generate_speech_via_alibaba_native(
            cfg,
            request,
            prepared_input=speech_input,
        )
        _touch_legacy_key_usage(db, cfg.db_key_id)
        return Response(content=audio_bytes, media_type=audio_mime or "audio/mpeg")

    upstream_url = _build_upstream_url(cfg, "/v1/audio/speech")
    headers = _build_upstream_auth_headers(cfg)
    headers["Content-Type"] = "application/json"
    upstream_payload: dict[str, Any] = {
        "model": request.model,
        "input": speech_input,
    }
    if request.voice:
        upstream_payload["voice"] = request.voice
    if request.response_format:
        upstream_payload["response_format"] = request.response_format
    if request.speed is not None:
        upstream_payload["speed"] = request.speed

    async with httpx.AsyncClient(timeout=240.0) as client:
        response = await client.post(upstream_url, json=upstream_payload, headers=headers)
    if response.status_code != 200:
        _raise_upstream_http_exception(response, "audio.speech")
    _touch_legacy_key_usage(db, cfg.db_key_id)

    content_type = response.headers.get("content-type", "audio/mpeg")
    if "application/json" in (content_type or "").lower():
        return response.json()
    return Response(content=response.content, media_type=content_type)


# =============================================================================
# /v1/realtime/* - Realtime Session Helper
# =============================================================================

@router.post("/v1/realtime/sessions")
async def v1_realtime_sessions(
    request: V1RealtimeSessionRequest,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_api_base_url: Optional[str] = Header(None, alias="X-API-Base-URL"),
    x_api_provider: Optional[str] = Header(None, alias="X-API-Provider"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cfg = _resolve_upstream_config(
        current_user=current_user,
        db=db,
        api_key=request.api_key,
        base_url=request.api_base_url,
        header_api_key=x_api_key,
        header_base_url=x_api_base_url,
        api_provider=x_api_provider,
    )
    request.model = _require_explicit_model_id(
        request.model,
        provider=cfg.api_provider,
        operation="realtime_session",
    )

    if cfg.api_provider == "alibaba":
        ws_url = _build_alibaba_realtime_ws_url(cfg.base_url, request.model)
        if not ws_url:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_realtime_base_url",
                    "message": "Cannot derive Alibaba realtime websocket URL from base URL.",
                },
            )
        expires_at = int((datetime.now() + timedelta(hours=1)).timestamp())
        _touch_legacy_key_usage(db, cfg.db_key_id)
        return {
            "id": f"rt_sess_{uuid.uuid4().hex[:16]}",
            "object": "realtime.session",
            "model": request.model,
            "expires_at": expires_at,
            "modalities": request.modalities or ["text", "audio"],
            "voice": request.voice,
            "instructions": request.instructions,
            "client_secret": {
                "value": "",
                "expires_at": expires_at,
            },
            "ws_url": ws_url,
            "provider": "alibaba",
            "auth": {
                "type": "bearer_header",
                "header": "Authorization",
                "scheme": "Bearer",
            },
        }

    upstream_url = _build_upstream_url(cfg, "/v1/realtime/sessions")
    headers = _build_upstream_auth_headers(cfg)
    headers["Content-Type"] = "application/json"
    payload: dict[str, Any] = {
        "model": request.model,
    }
    if request.voice:
        payload["voice"] = request.voice
    if request.modalities:
        payload["modalities"] = request.modalities
    if request.instructions:
        payload["instructions"] = request.instructions

    async with httpx.AsyncClient(timeout=45.0) as client:
        response = await client.post(upstream_url, json=payload, headers=headers)
    if response.status_code != 200:
        _raise_upstream_http_exception(response, "realtime.sessions")

    _touch_legacy_key_usage(db, cfg.db_key_id)
    return response.json()


def _is_alibaba_image_model(model_id: str) -> bool:
    lowered = (model_id or "").strip().lower()
    if not lowered:
        return False
    return (
        lowered.startswith("qwen-image")
        or lowered.startswith("z-image")
        or lowered.startswith("wanx")
        or lowered.startswith("wan2")
        or "image" in lowered
    )


def _should_apply_qwen_image_prompt_enhancer(
    cfg: ResolvedUpstreamConfig,
    *,
    model_id: str,
    prompt_enhance: Optional[bool],
    prompt_profile: Optional[str],
) -> bool:
    if cfg.api_provider != "alibaba":
        return False
    if not bool(prompt_enhance):
        return False
    if not _is_alibaba_image_model(model_id):
        return False
    profile = _normalize_qwen_prompt_profile(prompt_profile)
    return profile == "qwen_vip"


def _normalize_alibaba_image_size(size: Optional[str]) -> Optional[str]:
    if not size:
        return None
    trimmed = size.strip().lower()
    if not trimmed:
        return None
    if re.fullmatch(r"\d+\s*[x*]\s*\d+", trimmed):
        normalized = re.sub(r"\s+", "", trimmed)
        return normalized.replace("x", "*")
    return size.strip()


def _extract_alibaba_image_data_items(payload: Any) -> tuple[list[dict], list[str], Optional[str], Optional[str]]:
    data_items: list[dict] = []
    revised_prompts: list[str] = []

    if not isinstance(payload, dict):
        return data_items, revised_prompts, None, None

    output = payload.get("output")
    if not isinstance(output, dict):
        output = {}

    task_id = output.get("task_id") if isinstance(output.get("task_id"), str) else None
    task_status = output.get("task_status") if isinstance(output.get("task_status"), str) else None

    choices = output.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, list):
                content = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                image_url = None
                if isinstance(part.get("image"), str) and part.get("image").strip():
                    image_url = part.get("image").strip()
                elif isinstance(part.get("url"), str) and part.get("url").strip():
                    image_url = part.get("url").strip()
                elif isinstance(part.get("image_url"), str) and part.get("image_url").strip():
                    image_url = part.get("image_url").strip()

                if image_url:
                    data_items.append({"url": image_url})

                text_value = part.get("text")
                if isinstance(text_value, str) and text_value.strip():
                    revised_prompts.append(text_value.strip())

    if not data_items:
        images = output.get("images")
        if isinstance(images, list):
            for image in images:
                if isinstance(image, str) and image.strip():
                    data_items.append({"url": image.strip()})
                elif isinstance(image, dict):
                    image_url = (
                        image.get("url")
                        or image.get("image")
                        or image.get("image_url")
                    )
                    if isinstance(image_url, str) and image_url.strip():
                        data_items.append({"url": image_url.strip()})

    if not data_items:
        results = output.get("results")
        if isinstance(results, list):
            for result in results:
                if not isinstance(result, dict):
                    continue
                image_url = result.get("url") or result.get("image")
                if isinstance(image_url, str) and image_url.strip():
                    data_items.append({"url": image_url.strip()})

    return data_items, revised_prompts, task_id, task_status


async def _poll_alibaba_task_result(
    client: httpx.AsyncClient,
    cfg: ResolvedUpstreamConfig,
    task_id: str,
    headers: dict[str, str],
    *,
    timeout_seconds: int = 180,
    interval_seconds: float = 2.0,
) -> Optional[dict]:
    task_urls = _build_alibaba_native_candidate_urls(cfg, f"/api/v1/tasks/{task_id}")
    if not task_urls:
        return None
    deadline = perf_counter() + timeout_seconds
    last_payload: Optional[dict] = None

    while perf_counter() < deadline:
        for task_url in task_urls:
            try:
                response = await client.get(task_url, headers=headers, timeout=30.0)
            except Exception:
                continue
            if response.status_code != 200:
                continue
            try:
                payload = response.json()
            except Exception:
                payload = None
            if not isinstance(payload, dict):
                continue

            last_payload = payload
            _, _, _, task_status = _extract_alibaba_image_data_items(payload)
            normalized_status = (task_status or "").strip().upper()
            if normalized_status in {"SUCCEEDED", "FAILED", "CANCELED", "CANCELLED"}:
                return payload
        await asyncio.sleep(interval_seconds)

    return last_payload


def _build_alibaba_image_openai_response(
    payload: dict,
    *,
    revised_prompts: Optional[list[str]] = None,
) -> dict:
    data_items, extracted_prompts, _, _ = _extract_alibaba_image_data_items(payload)
    prompts = list(revised_prompts or [])
    prompts.extend(extracted_prompts)

    normalized_data: list[dict] = []
    for index, item in enumerate(data_items):
        if not isinstance(item, dict):
            continue
        row = dict(item)
        if prompts and index < len(prompts):
            row["revised_prompt"] = prompts[index]
        normalized_data.append(row)

    return {
        "created": int(datetime.now().timestamp()),
        "data": normalized_data,
    }


async def _generate_image_via_alibaba_native(
    cfg: ResolvedUpstreamConfig,
    request: V1ImageGenerationRequest,
) -> dict:
    model_id = _require_explicit_model_id(
        request.model,
        provider="alibaba",
        operation="image_generation",
    )
    if not _is_alibaba_image_model(model_id):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported_alibaba_image_model",
                "message": (
                    f"Model '{model_id}' is not an Alibaba image model. "
                    "Choose a qwen-image/wanx/z-image model."
                ),
            },
        )

    original_prompt_text = request.prompt.strip()
    prompt_text = original_prompt_text
    profile = _normalize_qwen_prompt_profile(request.prompt_profile)
    enhancer_applied = False
    if _should_apply_qwen_image_prompt_enhancer(
        cfg,
        model_id=model_id,
        prompt_enhance=request.prompt_enhance,
        prompt_profile=request.prompt_profile,
    ):
        prompt_text = _enhance_qwen_image_prompt(prompt_text, for_edit=False, profile=profile)
        enhancer_applied = prompt_text != original_prompt_text
        logger.info("Applied qwen image prompt enhancer profile=%s model=%s", profile, model_id)
    elif request.prompt_enhance:
        # Prompt enhance requested but skipped due provider/model/profile mismatch.
        logger.info(
            "Skipped qwen image prompt enhancer provider=%s model=%s profile=%s",
            cfg.api_provider,
            model_id,
            profile,
        )

    if _is_image_prompt_log_enabled():
        quoted_segments = _extract_quoted_text_segments(original_prompt_text)
        logger.info(
            (
                "Image prompt pipeline route=generations provider=%s model=%s enhancer_applied=%s "
                "profile=%s prompt_enhance=%s original_chars=%d final_chars=%d quoted_segments=%d has_vietnamese=%s"
            ),
            cfg.api_provider,
            model_id,
            enhancer_applied,
            profile,
            bool(request.prompt_enhance),
            len(original_prompt_text),
            len(prompt_text),
            len(quoted_segments),
            bool(_VIETNAMESE_CHAR_RE.search(original_prompt_text)),
        )
        if _is_image_prompt_text_log_enabled():
            logger.info(
                "Image prompt final preview route=generations: %s",
                _compact_prompt_log_text(prompt_text),
            )

    content = [{"text": prompt_text}]
    parameters: dict[str, Any] = {
        "n": request.n or 1,
        "watermark": False,
        "prompt_extend": False,
    }
    normalized_size = _normalize_alibaba_image_size(request.size)
    if normalized_size and model_id != "qwen-image-edit":
        parameters["size"] = normalized_size

    payload: dict[str, Any] = {
        "model": model_id,
        "input": {"messages": [{"role": "user", "content": content}]},
        "parameters": parameters,
    }

    native_path = "/api/v1/services/aigc/multimodal-generation/generation"
    candidate_urls = _build_alibaba_native_candidate_urls(cfg, native_path)
    headers = _build_upstream_auth_headers(cfg)
    headers["Content-Type"] = "application/json"

    result: Optional[dict[str, Any]] = None
    async with httpx.AsyncClient(timeout=240.0) as client:
        response: Optional[httpx.Response] = None
        last_error_response: Optional[httpx.Response] = None
        for index, upstream_url in enumerate(candidate_urls):
            response = await client.post(upstream_url, json=payload, headers=headers)
            if response.status_code == 200:
                result = response.json()
                break
            last_error_response = response
            if response.status_code == 404 and index < len(candidate_urls) - 1:
                logger.warning(
                    "Alibaba images.multimodal-generation endpoint 404, retrying alternative url=%s",
                    upstream_url,
                )
                continue
            _raise_upstream_http_exception(response, "alibaba.images.multimodal-generation")

        if result is None:
            _raise_upstream_http_exception(
                last_error_response or response,
                "alibaba.images.multimodal-generation",
            )

        data_items, revised_prompts, task_id, task_status = _extract_alibaba_image_data_items(result)
        if (task_status or "").strip().upper() in {"PENDING", "RUNNING", "QUEUED"} and task_id:
            task_payload = await _poll_alibaba_task_result(client, cfg, task_id, headers)
            if isinstance(task_payload, dict):
                result = task_payload
                data_items, revised_prompts, _, _ = _extract_alibaba_image_data_items(result)

        if not data_items:
            # keep raw payload for debugging if Alibaba response shape changes
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "alibaba_image_response_unrecognized",
                    "message": "Alibaba image API returned no image data.",
                },
            )

    return _build_alibaba_image_openai_response(result, revised_prompts=revised_prompts)


async def _edit_image_via_alibaba_native(
    cfg: ResolvedUpstreamConfig,
    request: V1ImageEditRequest,
    image_data_url: str,
    mask_data_url: Optional[str] = None,
) -> dict:
    model_id = _require_explicit_model_id(
        request.model,
        provider="alibaba",
        operation="image_edit",
    )
    if not _is_alibaba_image_model(model_id):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported_alibaba_image_model",
                "message": (
                    f"Model '{model_id}' is not an Alibaba image model. "
                    "Choose a qwen-image edit model."
                ),
            },
        )

    original_prompt_text = request.prompt.strip()
    prompt_text = original_prompt_text
    profile = _normalize_qwen_prompt_profile(request.prompt_profile)
    enhancer_applied = False
    if _should_apply_qwen_image_prompt_enhancer(
        cfg,
        model_id=model_id,
        prompt_enhance=request.prompt_enhance,
        prompt_profile=request.prompt_profile,
    ):
        prompt_text = _enhance_qwen_image_prompt(prompt_text, for_edit=True, profile=profile)
        enhancer_applied = prompt_text != original_prompt_text
        logger.info("Applied qwen image edit prompt enhancer profile=%s model=%s", profile, model_id)
    elif request.prompt_enhance:
        logger.info(
            "Skipped qwen image edit prompt enhancer provider=%s model=%s profile=%s",
            cfg.api_provider,
            model_id,
            profile,
        )

    if _is_image_prompt_log_enabled():
        quoted_segments = _extract_quoted_text_segments(original_prompt_text)
        logger.info(
            (
                "Image prompt pipeline route=edits provider=%s model=%s enhancer_applied=%s "
                "profile=%s prompt_enhance=%s original_chars=%d final_chars=%d quoted_segments=%d has_vietnamese=%s"
            ),
            cfg.api_provider,
            model_id,
            enhancer_applied,
            profile,
            bool(request.prompt_enhance),
            len(original_prompt_text),
            len(prompt_text),
            len(quoted_segments),
            bool(_VIETNAMESE_CHAR_RE.search(original_prompt_text)),
        )
        if _is_image_prompt_text_log_enabled():
            logger.info(
                "Image prompt final preview route=edits: %s",
                _compact_prompt_log_text(prompt_text),
            )

    content: list[dict] = [{"image": image_data_url}, {"text": prompt_text}]
    if mask_data_url:
        content.append({"image": mask_data_url})

    parameters: dict[str, Any] = {
        "n": request.n or 1,
        "watermark": False,
        "prompt_extend": False,
    }
    normalized_size = _normalize_alibaba_image_size(request.size)
    if normalized_size and model_id != "qwen-image-edit":
        parameters["size"] = normalized_size

    payload: dict[str, Any] = {
        "model": model_id,
        "input": {"messages": [{"role": "user", "content": content}]},
        "parameters": parameters,
    }

    native_path = "/api/v1/services/aigc/multimodal-generation/generation"
    candidate_urls = _build_alibaba_native_candidate_urls(cfg, native_path)
    headers = _build_upstream_auth_headers(cfg)
    headers["Content-Type"] = "application/json"

    result: Optional[dict[str, Any]] = None
    async with httpx.AsyncClient(timeout=300.0) as client:
        response: Optional[httpx.Response] = None
        last_error_response: Optional[httpx.Response] = None
        for index, upstream_url in enumerate(candidate_urls):
            response = await client.post(upstream_url, json=payload, headers=headers)
            if response.status_code == 200:
                result = response.json()
                break
            last_error_response = response
            if response.status_code == 404 and index < len(candidate_urls) - 1:
                logger.warning(
                    "Alibaba images.edit endpoint 404, retrying alternative url=%s",
                    upstream_url,
                )
                continue
            _raise_upstream_http_exception(response, "alibaba.images.edit")

        if result is None:
            _raise_upstream_http_exception(
                last_error_response or response,
                "alibaba.images.edit",
            )

        data_items, revised_prompts, task_id, task_status = _extract_alibaba_image_data_items(result)
        if (task_status or "").strip().upper() in {"PENDING", "RUNNING", "QUEUED"} and task_id:
            task_payload = await _poll_alibaba_task_result(client, cfg, task_id, headers)
            if isinstance(task_payload, dict):
                result = task_payload
                data_items, revised_prompts, _, _ = _extract_alibaba_image_data_items(result)

        if not data_items:
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "alibaba_image_response_unrecognized",
                    "message": "Alibaba image edit API returned no image data.",
                },
            )

    return _build_alibaba_image_openai_response(result, revised_prompts=revised_prompts)


# =============================================================================
# /v1/images/* - OpenAI-Compatible Image Endpoints
# =============================================================================

@router.post("/v1/images/generations")
async def v1_image_generations(
    request: V1ImageGenerationRequest,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_api_base_url: Optional[str] = Header(None, alias="X-API-Base-URL"),
    x_api_provider: Optional[str] = Header(None, alias="X-API-Provider"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cfg = _resolve_upstream_config(
        current_user=current_user,
        db=db,
        api_key=request.api_key,
        base_url=request.api_base_url,
        header_api_key=x_api_key,
        header_base_url=x_api_base_url,
        api_provider=x_api_provider,
    )

    prompt = request.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")

    if cfg.api_provider == "anthropic":
        _raise_unsupported_provider_capability("anthropic", "image_generation")

    if cfg.api_provider == "gemini":
        result = await _generate_image_via_gemini_native(cfg, request)
        if isinstance(result, dict):
            result["data"] = await _persist_image_response_data(result.get("data"), current_user.id)
        _touch_legacy_key_usage(db, cfg.db_key_id)
        return result

    if cfg.api_provider == "alibaba":
        result = await _generate_image_via_alibaba_native(cfg, request)
        if isinstance(result, dict):
            result["data"] = await _persist_image_response_data(result.get("data"), current_user.id)
        _touch_legacy_key_usage(db, cfg.db_key_id)
        return result

    payload: Dict[str, Any] = {
        "prompt": prompt,
        "response_format": request.response_format or "b64_json",
    }

    for key in ("model", "size", "quality", "style", "background", "user"):
        value = getattr(request, key)
        if value is not None and str(value).strip():
            payload[key] = value
    if request.n is not None:
        payload["n"] = request.n

    upstream_url = _build_upstream_url(cfg, "/v1/images/generations")
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                upstream_url,
                json=payload,
                headers=_build_upstream_auth_headers(cfg),
            )
    except httpx.RequestError as e:
        logger.warning("Image generation connection failed provider=%s base_url=%s error=%s", cfg.api_provider, cfg.base_url, e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "api_connection_error",
                "message": f"Cannot connect to {cfg.api_provider} API.",
            },
        )

    if not response.is_success:
        _raise_upstream_http_exception(response, "images.generations")

    result = response.json()
    if isinstance(result, dict):
        result["data"] = await _persist_image_response_data(result.get("data"), current_user.id)

    _touch_legacy_key_usage(db, cfg.db_key_id)
    return result


@router.post("/v1/images/edits")
async def v1_image_edits(
    request: V1ImageEditRequest,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_api_base_url: Optional[str] = Header(None, alias="X-API-Base-URL"),
    x_api_provider: Optional[str] = Header(None, alias="X-API-Provider"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cfg = _resolve_upstream_config(
        current_user=current_user,
        db=db,
        api_key=request.api_key,
        base_url=request.api_base_url,
        header_api_key=x_api_key,
        header_base_url=x_api_base_url,
        api_provider=x_api_provider,
    )

    prompt = request.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")

    image_bytes, image_mime, image_filename = await _resolve_image_input_to_bytes(
        request.image,
        "image",
        current_user.id,
    )
    if cfg.api_provider == "anthropic":
        _raise_unsupported_provider_capability("anthropic", "image_edit")

    if cfg.api_provider == "gemini":
        result = await _edit_image_via_gemini_native(
            cfg,
            request,
            image_bytes=image_bytes,
            image_mime=image_mime,
        )
        if isinstance(result, dict):
            result["data"] = await _persist_image_response_data(result.get("data"), current_user.id)
        _touch_legacy_key_usage(db, cfg.db_key_id)
        return result

    if cfg.api_provider == "alibaba":
        image_data_url = _build_data_url(image_mime, image_bytes)
        mask_data_url: Optional[str] = None
        if request.mask:
            mask_bytes, mask_mime, _mask_filename = await _resolve_image_input_to_bytes(
                request.mask,
                "mask",
                current_user.id,
            )
            mask_data_url = _build_data_url(mask_mime, mask_bytes)
        result = await _edit_image_via_alibaba_native(
            cfg=cfg,
            request=request,
            image_data_url=image_data_url,
            mask_data_url=mask_data_url,
        )
        if isinstance(result, dict):
            result["data"] = await _persist_image_response_data(result.get("data"), current_user.id)
        _touch_legacy_key_usage(db, cfg.db_key_id)
        return result

    form_data: Dict[str, str] = {
        "prompt": prompt,
        "response_format": request.response_format or "b64_json",
    }
    for key in ("model", "size", "quality", "user"):
        value = getattr(request, key)
        if value is not None and str(value).strip():
            form_data[key] = str(value)
    if request.n is not None:
        form_data["n"] = str(request.n)

    files: Dict[str, tuple[str, bytes, str]] = {
        "image": (image_filename, image_bytes, image_mime),
    }

    if request.mask:
        mask_bytes, mask_mime, mask_filename = await _resolve_image_input_to_bytes(
            request.mask,
            "mask",
            current_user.id,
        )
        files["mask"] = (mask_filename, mask_bytes, mask_mime)

    upstream_url = _build_upstream_url(cfg, "/v1/images/edits")
    try:
        async with httpx.AsyncClient(timeout=240.0) as client:
            response = await client.post(
                upstream_url,
                data=form_data,
                files=files,
                headers={"Authorization": f"Bearer {cfg.api_key}"},
            )
    except httpx.RequestError as e:
        logger.warning("Upstream image edit connection failed base_url=%s error=%s", cfg.base_url, e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "upstream_api_connection_error",
                "message": "Cannot connect to upstream API.",
            },
        )

    if not response.is_success:
        _raise_upstream_http_exception(response, "images.edits")

    result = response.json()
    if isinstance(result, dict):
        result["data"] = await _persist_image_response_data(result.get("data"), current_user.id)

    _touch_legacy_key_usage(db, cfg.db_key_id)
    return result


# ---------------------------------------------------------------------------
# PAYG Proxy routes for image generation / editing
# These are called by the frontend when the user does NOT have a BYOK API key.
# Server-side credentials from the managed gateway are used.
# ---------------------------------------------------------------------------

@router.post("/proxy/v1/images/generations")
async def proxy_image_generations(
    request: V1ImageGenerationRequest,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_api_base_url: Optional[str] = Header(None, alias="X-API-Base-URL"),
    x_api_provider: Optional[str] = Header(None, alias="X-API-Provider"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """PAYG proxy: image generation using server-side credentials."""
    cfg = _resolve_upstream_config(
        current_user=current_user,
        db=db,
        # Do not forward any BYOK key from the request body; use server-side creds only.
        api_key=None,
        base_url=None,
        header_api_key=x_api_key,
        header_base_url=x_api_base_url,
        api_provider=x_api_provider,
    )

    prompt = request.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")

    if cfg.api_provider == "anthropic":
        _raise_unsupported_provider_capability("anthropic", "image_generation")

    if cfg.api_provider == "gemini":
        result = await _generate_image_via_gemini_native(cfg, request)
        if isinstance(result, dict):
            result["data"] = await _persist_image_response_data(result.get("data"), current_user.id)
        _touch_legacy_key_usage(db, cfg.db_key_id)
        return result

    if cfg.api_provider == "alibaba":
        result = await _generate_image_via_alibaba_native(cfg, request)
        if isinstance(result, dict):
            result["data"] = await _persist_image_response_data(result.get("data"), current_user.id)
        _touch_legacy_key_usage(db, cfg.db_key_id)
        return result

    payload: Dict[str, Any] = {
        "prompt": prompt,
        "response_format": request.response_format or "b64_json",
    }
    for key in ("model", "size", "quality", "style", "background", "user"):
        value = getattr(request, key)
        if value is not None and str(value).strip():
            payload[key] = value
    if request.n is not None:
        payload["n"] = request.n

    upstream_url = _build_upstream_url(cfg, "/v1/images/generations")
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                upstream_url,
                json=payload,
                headers=_build_upstream_auth_headers(cfg),
            )
    except httpx.RequestError as e:
        logger.warning("PAYG image generation connection failed provider=%s error=%s", cfg.api_provider, e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "api_connection_error",
                "message": f"Cannot connect to {cfg.api_provider} API.",
            },
        )

    if not response.is_success:
        _raise_upstream_http_exception(response, "proxy.images.generations")

    result = response.json()
    if isinstance(result, dict):
        result["data"] = await _persist_image_response_data(result.get("data"), current_user.id)

    _touch_legacy_key_usage(db, cfg.db_key_id)
    return result


@router.post("/proxy/v1/images/edits")
async def proxy_image_edits(
    request: V1ImageEditRequest,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_api_base_url: Optional[str] = Header(None, alias="X-API-Base-URL"),
    x_api_provider: Optional[str] = Header(None, alias="X-API-Provider"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """PAYG proxy: image editing using server-side credentials."""
    cfg = _resolve_upstream_config(
        current_user=current_user,
        db=db,
        api_key=None,
        base_url=None,
        header_api_key=x_api_key,
        header_base_url=x_api_base_url,
        api_provider=x_api_provider,
    )

    prompt = request.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")

    image_bytes, image_mime, image_filename = await _resolve_image_input_to_bytes(
        request.image,
        "image",
        current_user.id,
    )
    if cfg.api_provider == "anthropic":
        _raise_unsupported_provider_capability("anthropic", "image_edit")

    if cfg.api_provider == "gemini":
        result = await _edit_image_via_gemini_native(
            cfg,
            request,
            image_bytes=image_bytes,
            image_mime=image_mime,
        )
        if isinstance(result, dict):
            result["data"] = await _persist_image_response_data(result.get("data"), current_user.id)
        _touch_legacy_key_usage(db, cfg.db_key_id)
        return result

    if cfg.api_provider == "alibaba":
        image_data_url = _build_data_url(image_mime, image_bytes)
        mask_data_url: Optional[str] = None
        if request.mask:
            mask_bytes, mask_mime, _mask_filename = await _resolve_image_input_to_bytes(
                request.mask,
                "mask",
                current_user.id,
            )
            mask_data_url = _build_data_url(mask_mime, mask_bytes)
        result = await _edit_image_via_alibaba_native(
            cfg=cfg,
            request=request,
            image_data_url=image_data_url,
            mask_data_url=mask_data_url,
        )
        if isinstance(result, dict):
            result["data"] = await _persist_image_response_data(result.get("data"), current_user.id)
        _touch_legacy_key_usage(db, cfg.db_key_id)
        return result

    form_data: Dict[str, str] = {
        "prompt": prompt,
        "response_format": request.response_format or "b64_json",
    }
    for key in ("model", "size", "quality", "user"):
        value = getattr(request, key)
        if value is not None and str(value).strip():
            form_data[key] = str(value)
    if request.n is not None:
        form_data["n"] = str(request.n)

    files: Dict[str, tuple[str, bytes, str]] = {
        "image": (image_filename, image_bytes, image_mime),
    }
    if request.mask:
        mask_bytes, mask_mime, mask_filename = await _resolve_image_input_to_bytes(
            request.mask,
            "mask",
            current_user.id,
        )
        files["mask"] = (mask_filename, mask_bytes, mask_mime)

    upstream_url = _build_upstream_url(cfg, "/v1/images/edits")
    try:
        async with httpx.AsyncClient(timeout=240.0) as client:
            response = await client.post(
                upstream_url,
                data=form_data,
                files=files,
                headers={"Authorization": f"Bearer {cfg.api_key}"},
            )
    except httpx.RequestError as e:
        logger.warning("PAYG image edit connection failed error=%s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "upstream_api_connection_error",
                "message": "Cannot connect to upstream API.",
            },
        )

    if not response.is_success:
        _raise_upstream_http_exception(response, "proxy.images.edits")

    result = response.json()
    if isinstance(result, dict):
        result["data"] = await _persist_image_response_data(result.get("data"), current_user.id)

    _touch_legacy_key_usage(db, cfg.db_key_id)
    return result


@router.post("/v1/videos/generations")
async def v1_video_generations(
    request: V1VideoGenerationRequest,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_api_base_url: Optional[str] = Header(None, alias="X-API-Base-URL"),
    x_api_provider: Optional[str] = Header(None, alias="X-API-Provider"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cfg = _resolve_upstream_config(
        current_user=current_user,
        db=db,
        api_key=request.api_key,
        base_url=request.api_base_url,
        header_api_key=x_api_key,
        header_base_url=x_api_base_url,
        api_provider=x_api_provider,
    )
    request.model = _require_explicit_model_id(
        request.model,
        provider=cfg.api_provider,
        operation="video_generation",
    )

    prompt = _enhance_video_generation_prompt(request)
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")

    if cfg.api_provider in {"anthropic", "gemini"}:
        _raise_unsupported_provider_capability(cfg.api_provider, "video_generation")

    def _build_video_payload(*, numeric_duration: bool, include_duration: bool = True) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"prompt": prompt}
        for key in (
            "model",
            "size",
            "duration",
            "quality",
            "response_format",
            "style",
            "aspect_ratio",
            "user",
        ):
            value = getattr(request, key)
            if value is None or not str(value).strip():
                continue
            if key == "duration":
                if not include_duration:
                    continue
                trimmed_duration = str(value).strip()
                payload[key] = int(trimmed_duration) if numeric_duration and trimmed_duration.isdigit() else trimmed_duration
                continue
            payload[key] = value
        if request.n is not None:
            payload["n"] = request.n
        return payload

    payload_variants: list[Dict[str, Any]] = [_build_video_payload(numeric_duration=False)]
    raw_duration = (request.duration or "").strip()
    if raw_duration.isdigit():
        numeric_variant = _build_video_payload(numeric_duration=True)
        if numeric_variant != payload_variants[0]:
            payload_variants.append(numeric_variant)
        no_duration_variant = _build_video_payload(numeric_duration=False, include_duration=False)
        if no_duration_variant not in payload_variants:
            payload_variants.append(no_duration_variant)

    candidate_urls = _build_video_generation_candidate_urls(cfg)
    if not candidate_urls:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported_video_generation_endpoint",
                "message": "Could not resolve upstream video generation endpoint.",
            },
        )

    last_response: Optional[httpx.Response] = None
    last_connection_error: Optional[Exception] = None
    endpoint_not_supported = False

    headers = _build_upstream_auth_headers(cfg)
    headers["Content-Type"] = "application/json"

    async with httpx.AsyncClient(timeout=420.0) as client:
        for upstream_url in candidate_urls:
            for payload_index, payload in enumerate(payload_variants):
                try:
                    response = await client.post(upstream_url, json=payload, headers=headers)
                except httpx.RequestError as e:
                    last_connection_error = e
                    break

                last_response = response
                if response.status_code in {404, 405}:
                    endpoint_not_supported = True
                    break
                if not response.is_success:
                    error_text = ""
                    try:
                        error_text = response.text or ""
                    except Exception:
                        error_text = ""
                    should_retry_duration_variant = (
                        payload_index + 1 < len(payload_variants)
                        and "duration" in error_text.lower()
                        and "unmarshal" in error_text.lower()
                    )
                    if should_retry_duration_variant:
                        continue
                    _raise_upstream_http_exception(response, "videos.generations")

                _touch_legacy_key_usage(db, cfg.db_key_id)
                content_type = (response.headers.get("content-type") or "").lower()
                if "application/json" in content_type:
                    raw_payload = response.json()
                    normalized_payload = _normalize_video_generation_response_for_client(raw_payload, cfg)
                    # Cost control: do not auto-poll pending tasks server-side.
                    # The client can decide when/how often to poll task status.
                    return normalized_payload

                return Response(
                    content=response.content,
                    media_type=response.headers.get("content-type", "application/octet-stream"),
                )

    if last_response is None and last_connection_error is not None:
        logger.warning(
            "Video generation connection failed provider=%s base_url=%s error=%s",
            cfg.api_provider,
            cfg.base_url,
            last_connection_error,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "api_connection_error",
                "message": f"Cannot connect to {cfg.api_provider} API.",
            },
        )

    if endpoint_not_supported:
        attempted = _summarize_upstream_candidates(candidate_urls)
        capabilities = await _fetch_upstream_capabilities(cfg, headers)
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported_video_generation_endpoint",
                "message": (
                    f"Upstream base URL '{cfg.base_url}' did not expose a supported video generation endpoint. "
                    f"Tried: {attempted}."
                ),
                "base_url": cfg.base_url,
                "provider": cfg.api_provider,
                "tried_urls": candidate_urls,
                "capabilities": capabilities,
            },
        )

    if last_response is not None:
        _raise_upstream_http_exception(last_response, "videos.generations")

    raise HTTPException(
        status_code=502,
        detail={
            "error": "video_generation_unknown_error",
            "message": "Video generation failed without upstream response.",
        },
    )


@router.get("/v1/videos/generations/{task_id}")
async def get_v1_video_generation_task(
    task_id: str,
    api_key: Optional[str] = Query(None, description="Provider API key (BYOK)"),
    api_base_url: Optional[str] = Query(None, description="Provider API base URL (BYOK)"),
    api_provider: Optional[str] = Query(None, description="Provider ID (openai/anthropic/gemini/alibaba/auto)"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_api_base_url: Optional[str] = Header(None, alias="X-API-Base-URL"),
    x_api_provider: Optional[str] = Header(None, alias="X-API-Provider"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cfg = _resolve_upstream_config(
        current_user=current_user,
        db=db,
        api_key=api_key,
        base_url=api_base_url,
        header_api_key=x_api_key,
        header_base_url=x_api_base_url,
        api_provider=x_api_provider or api_provider,
    )

    clean_task_id = (task_id or "").strip()
    if not clean_task_id:
        raise HTTPException(status_code=400, detail="Task ID is required")

    if cfg.api_provider in {"anthropic", "gemini"}:
        _raise_unsupported_provider_capability(cfg.api_provider, "video_generation_task")

    headers = _build_upstream_auth_headers(cfg)
    async with httpx.AsyncClient(timeout=90.0) as client:
        result = await _fetch_video_generation_task_payload(
            client,
            cfg,
            clean_task_id,
            headers,
        )
        if isinstance(result, tuple):
            payload, endpoint_not_supported = result
            binary_content = None
            binary_media_type = None
        else:
            payload = result.payload
            endpoint_not_supported = result.endpoint_not_supported
            binary_content = result.binary_content
            binary_media_type = result.binary_media_type
        if binary_content is not None:
            _touch_legacy_key_usage(db, cfg.db_key_id)
            return Response(
                content=binary_content,
                media_type=binary_media_type or "application/octet-stream",
            )

    if endpoint_not_supported:
        candidate_urls = _build_video_task_candidate_urls(cfg, clean_task_id)
        logger.warning(
            "Video task lookup endpoint unresolved; returning pending provider=%s base_url=%s task_id=%s candidates=%s",
            cfg.api_provider,
            cfg.base_url,
            clean_task_id,
            candidate_urls,
        )
        pending_payload: dict[str, Any] = {
            "task_id": clean_task_id,
            "task_status": "PENDING",
            "data": [],
        }
        return _normalize_video_generation_response_for_client(pending_payload, cfg)
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=404,
            detail={
                "error": "video_task_not_found",
                "message": f"Could not load video task '{clean_task_id}'.",
            },
        )

    _touch_legacy_key_usage(db, cfg.db_key_id)
    return _normalize_video_generation_response_for_client(payload, cfg)


# =============================================================================
# Internal: Streaming & Non-Streaming Handlers
# =============================================================================

async def _handle_streaming(
    cfg: ResolvedUpstreamConfig,
    upstream_url: str,
    request_data: dict = None,
    headers: dict = None,
    request: V1ChatCompletionRequest = None,
    current_user: User = None,
    current_user_id: str = "",
    db: Session = None,
    coordinator: Optional[Any] = None,
    conversation_id: Optional[str] = None,
    lazy_prepare: Optional[Any] = None,
    search_context: Optional[SearchContext] = None,
    web_search_enabled: bool = False,
    memory_context_meta: Optional[Dict[str, Any]] = None,
    learning_context: Optional[Dict[str, Any]] = None,
    request_id: str = "",
    request_started_at: Optional[float] = None,
):
    """
    SSE pass-through proxy.

    Forwards raw SSE bytes from the upstream provider to the frontend.
    Performs lightweight content extraction during pass-through to
    accumulate the full response text for memory/auto-title.

    No re-parsing. No re-serialization. No model retry.
    The frontend and upstream provider handle SSE parsing, retries, and fallbacks.
    """
    resolved_search_context = search_context
    resolved_web_search_enabled = web_search_enabled
    resolved_memory_context_meta = memory_context_meta
    resolved_learning_context = learning_context

    def _extract_delta_content(raw_bytes: str) -> str:
        """Lightweight tap: pull text deltas from provider SSE for memory accumulation."""
        parts: list[str] = []
        for line in raw_bytes.split("\n"):
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]" or not data.startswith("{"):
                continue
            try:
                payload = json.loads(data)
                extracted = _extract_text_from_chat_payload(payload)
                if extracted:
                    parts.append(extracted)
            except (json.JSONDecodeError, IndexError, KeyError, TypeError):
                pass
        return "".join(parts)

    async def stream_generator():
        nonlocal cfg, upstream_url, request_data, headers, coordinator, conversation_id
        nonlocal resolved_search_context, resolved_web_search_enabled, resolved_memory_context_meta, resolved_learning_context
        stream_started_at = perf_counter()
        first_chunk_at: Optional[float] = None
        chunk_count = 0
        full_response = ""
        sse_buffer = ""
        stream_filter_state = StreamSanitizerState()
        model_used = request.model if request else "unknown"
        resolved_web_search_enabled = (
            request.use_web_search
            if request and request.use_web_search is not None
            else bool(settings.web_search_enabled_default)
        )

        if resolved_web_search_enabled:
            yield f"data: {json.dumps({'web_search': {'enabled': True, 'status': 'running'}})}\n\n"

        # ── Lazy preparation (inject memory + runtime prompt) ──
        if lazy_prepare:
            yield ": thinking\n\n"
            try:
                prep = await lazy_prepare()
                request_data = prep["request_data"]
                headers = prep["headers"]
                upstream_url = prep.get("upstream_url", upstream_url)
                coordinator = prep.get("coordinator")
                conversation_id = prep.get("conversation_id")
                resolved_search_context = prep.get("search_context")
                resolved_web_search_enabled = bool(
                    prep.get("web_search_enabled", resolved_web_search_enabled)
                )
                resolved_memory_context_meta = prep.get("memory_context_meta", resolved_memory_context_meta)
                resolved_learning_context = prep.get("learning_context", resolved_learning_context)
                if conversation_id:
                    # Emit conversation id as early metadata so frontend can
                    # bind the new thread immediately even when response header
                    # couldn't include it yet (lazy prepare creates it later).
                    yield f"data: {json.dumps({'conversation_id': conversation_id})}\n\n"
                if resolved_web_search_enabled:
                    web_search_meta = _serialize_web_search_meta(resolved_search_context, enabled=True)
                    payload: Dict[str, Any] = {"web_search": web_search_meta}
                    if resolved_search_context and resolved_search_context.citations:
                        payload["citations"] = resolved_search_context.citations
                    yield f"data: {json.dumps(payload)}\n\n"
                if isinstance(resolved_memory_context_meta, dict):
                    yield f"data: {json.dumps({'memory': resolved_memory_context_meta})}\n\n"
                if isinstance(resolved_learning_context, dict) and resolved_learning_context.get("enabled"):
                    learning_public_payload = dict(resolved_learning_context)
                    learning_public_payload.pop("system_prompt", None)
                    yield f"data: {json.dumps({'learning': learning_public_payload})}\n\n"
            except Exception as e:
                logger.error("Lazy prepare failed request_id=%s: %s", request_id, e)
                yield f'data: {{"error": {{"message": "Preparation failed: {e}", "type": "preparation_error"}}}}\n\n'
                return

        _release_db_connection(db, reason="stream_before_upstream", request_id=request_id)

        yield ": stream-open\n\n"

        # ── Stream pass-through ──
        try:
            client = await _get_chat_client()
            stream_timeout = httpx.Timeout(
                connect=8.0,
                read=STREAM_READ_TIMEOUT_SECONDS,
                write=30.0,
                pool=8.0,
            )

            payload_for_stream = request_data
            current_upstream_url = upstream_url
            current_headers = dict(headers)
            current_cfg = cfg
            for attempt in range(4):
                async with client.stream(
                    "POST",
                    current_upstream_url,
                    json=payload_for_stream,
                    headers=current_headers,
                    timeout=stream_timeout,
                ) as response:
                    if response.status_code != 200:
                        if attempt == 0 and _should_retry_texapi_partner_auth_error(response, current_cfg):
                            current_cfg = await _hydrate_texapi_partner_config(
                                current_cfg,
                                current_user,
                                force_refresh=True,
                            )
                            current_headers = _build_upstream_auth_headers(current_cfg)
                            current_upstream_url = _build_upstream_url(current_cfg, "/v1/chat/completions")
                            headers = current_headers
                            upstream_url = current_upstream_url
                            cfg = current_cfg
                            continue

                        error_body = await response.aread()
                        error_text = error_body.decode("utf-8", errors="replace")[:500]
                        should_retry_dashscope = (
                            attempt == 0
                            and _is_dashscope_message_shape_error(current_upstream_url, response.status_code, error_text)
                        )
                        retry_payload = (
                            _build_dashscope_message_shape_fallback_payload(payload_for_stream)
                            if should_retry_dashscope
                            else None
                        )
                        if retry_payload:
                            logger.info(
                                "Retrying stream with DashScope message-shape fallback request_id=%s",
                                request_id,
                            )
                            payload_for_stream = retry_payload
                            request_data = retry_payload
                            continue

                        native_fallback = (
                            _build_dashscope_native_chat_fallback(
                                upstream_url=current_upstream_url,
                                request_data=payload_for_stream,
                                stream=True,
                            )
                            if _is_dashscope_capability_error(response.status_code, error_text)
                            else None
                        )
                        if native_fallback:
                            native_url, native_payload, native_extra_headers = native_fallback
                            logger.info(
                                "Retrying stream with DashScope native fallback request_id=%s",
                                request_id,
                            )
                            payload_for_stream = native_payload
                            request_data = native_payload
                            current_upstream_url = native_url
                            current_headers = {**current_headers, **native_extra_headers}
                            current_headers["Content-Type"] = "application/json"
                            upstream_url = native_url
                            headers = current_headers
                            continue

                        logger.error(
                            "Upstream stream error status=%s request_id=%s body_preview=%s",
                            response.status_code, request_id, error_text,
                        )
                        error_chunk = {
                            "error": {
                                "message": f"Upstream API error (status {response.status_code})",
                                "type": "upstream_error",
                                "code": response.status_code,
                            },
                            "request_id": request_id,
                        }
                        yield f"data: {json.dumps(error_chunk)}\n\n"
                        return

                    # Pass-through: forward raw SSE bytes, extract content for memory
                    async for raw_chunk in response.aiter_bytes():
                        chunk_str = raw_chunk.decode("utf-8", errors="replace")
                        sse_buffer += chunk_str

                        while "\n\n" in sse_buffer:
                            event_text, sse_buffer = sse_buffer.split("\n\n", 1)
                            delta_text = _extract_delta_content(event_text)
                            if delta_text:
                                full_response += delta_text
                                chunk_count += 1
                                if first_chunk_at is None:
                                    first_chunk_at = perf_counter()

                            sanitized_event = sanitize_sse_event_block(event_text, stream_filter_state)
                            if sanitized_event:
                                yield sanitized_event

                    if sse_buffer.strip():
                        delta_text = _extract_delta_content(sse_buffer)
                        if delta_text:
                            full_response += delta_text
                            chunk_count += 1
                            if first_chunk_at is None:
                                first_chunk_at = perf_counter()
                        sanitized_tail = sanitize_sse_event_block(sse_buffer, stream_filter_state)
                        if sanitized_tail:
                            yield sanitized_tail
                        sse_buffer = ""

                    final_flush = flush_stream_sanitizer(stream_filter_state)
                    if final_flush:
                        yield final_flush
                    headers = current_headers
                    break

        except httpx.TimeoutException as e:
            logger.warning(
                "Upstream stream timeout request_id=%s model=%s error=%s",
                request_id, model_used, e,
            )
            error_chunk = {
                "error": {
                    "message": "Upstream API request timed out. Please retry.",
                    "type": "timeout_error",
                },
                "request_id": request_id,
            }
            yield f"data: {json.dumps(error_chunk)}\n\n"
            return
        except Exception:
            logger.exception(
                "Stream pipeline failed request_id=%s user_id=%s model=%s",
                request_id, current_user_id, model_used,
            )
            error_chunk = {
                "error": {
                    "message": "PigTex stream interrupted. Please retry.",
                    "type": "backend_error",
                },
                "request_id": request_id,
            }
            yield f"data: {json.dumps(error_chunk)}\n\n"
            return

        # ── Post-stream: log, save memory, auto-title ──
        duration_ms = int((perf_counter() - stream_started_at) * 1000)
        total_ms = int((perf_counter() - request_started_at) * 1000) if request_started_at else duration_ms
        ttft_ms = int((first_chunk_at - stream_started_at) * 1000) if first_chunk_at else -1
        logger.info(
            "v1_stream_complete request_id=%s user_id=%s model=%s conversation_id=%s chunks=%s ttft_ms=%s duration_ms=%s total_ms=%s chars=%s",
            request_id, current_user_id, model_used, conversation_id,
            chunk_count, ttft_ms, duration_ms, total_ms, len(full_response),
        )

        prompt_tokens = _estimate_messages_tokens((request_data or {}).get("messages", []))
        completion_tokens = int(len(full_response.split()) * 1.3)
        total_tokens = max(0, prompt_tokens + completion_tokens)
        cost_usd, _ = _estimate_usage_cost_usd(model_used, prompt_tokens, completion_tokens)
        usage_payload = {
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "cost_usd": cost_usd,
                "estimated": True,
            }
        }
        yield f"data: {json.dumps(usage_payload)}\n\n"

        # Non-critical background tasks: save to memory, auto-title
        if full_response:
            asyncio.create_task(
                _finalize_stream(
                    full_response,
                    model_used,
                    prompt_tokens,
                    completion_tokens,
                    cost_usd,
                )
            )

    async def _finalize_stream(
        full_response: str,
        model_used: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
    ) -> None:
        """Non-critical post-stream tasks: memory persistence + auto-title."""
        try:
            filtered_response = apply_output_filters(
                full_response,
                allow_internal_payload=True,
            )
            if coordinator and filtered_response and not _is_internal_agent_payload(filtered_response):
                try:
                    assistant_sources_payload = (
                        resolved_search_context.citations
                        if resolved_search_context and resolved_search_context.citations
                        else None
                    )
                    coordinator.add_message(
                        "assistant",
                        filtered_response,
                        model=model_used,
                        sources=assistant_sources_payload,
                    )
                    await _auto_title_conversation_if_needed(
                        coordinator=coordinator,
                        conversation_id=conversation_id,
                        request=request,
                        upstream_url=upstream_url,
                        headers=headers,
                        assistant_text=filtered_response,
                    )
                except Exception as e:
                    logger.warning("Memory save error (stream, non-fatal): %s", e)

            _track_usage(
                db, current_user_id, model_used,
                prompt_tokens, completion_tokens,
                "/v1/chat/completions", "success",
                cost=cost_usd,
            )
        except Exception as e:
            logger.warning("Stream finalize task failed (non-fatal): %s", e)

    response_headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
        "X-Request-ID": request_id,
    }
    if conversation_id:
        response_headers["X-Conversation-ID"] = conversation_id

    return StreamingResponse(stream_generator(), media_type="text/event-stream", headers=response_headers)


async def _handle_non_streaming(
    cfg: ResolvedUpstreamConfig,
    upstream_url: str,
    request_data: dict,
    headers: dict,
    request: V1ChatCompletionRequest,
    current_user: User,
    current_user_id: str,
    db: Session,
    coordinator: Optional[Any] = None,
    conversation_id: Optional[str] = None,
    search_context: Optional[SearchContext] = None,
    web_search_enabled: bool = False,
    memory_context_meta: Optional[Dict[str, Any]] = None,
    learning_context: Optional[Dict[str, Any]] = None,
    request_id: str = "",
    request_started_at: Optional[float] = None,
):
    """Handle non-streaming response from upstream provider API"""

    original_upstream_url = upstream_url
    original_request_data = request_data
    client = await _get_chat_client()
    current_cfg = cfg
    result: Optional[dict] = None
    response_obj: Optional[httpx.Response] = None
    direct_memory_reply = _try_resolve_direct_memory_reply(
        request=request,
        coordinator=coordinator,
        conversation_id=conversation_id,
    )
    if direct_memory_reply is not None:
        logger.info(
            "v1_nonstream_direct_memory_hit request_id=%s user_id=%s conversation_id=%s",
            request_id,
            current_user_id,
            conversation_id,
        )
        result = {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(datetime.now().timestamp()),
            "model": request.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": direct_memory_reply},
                    "finish_reason": "stop",
                }
            ],
        }
    else:
        _release_db_connection(db, reason="nonstream_before_upstream", request_id=request_id)
        response_obj = await client.post(
            upstream_url,
            json=request_data,
            headers=headers
        )

    if response_obj is not None and response_obj.status_code != 200:
        if _should_retry_texapi_partner_auth_error(response_obj, current_cfg):
            current_cfg = await _hydrate_texapi_partner_config(current_cfg, current_user, force_refresh=True)
            headers = _build_upstream_auth_headers(current_cfg)
            upstream_url = _build_upstream_url(current_cfg, "/v1/chat/completions")
            response_obj = await client.post(
                upstream_url,
                json=request_data,
                headers=headers,
            )

        if response_obj is not None and response_obj.status_code != 200:
            error_text = (response_obj.text or "")[:500]
            if _is_dashscope_message_shape_error(upstream_url, response_obj.status_code, error_text):
                retry_payload = _build_dashscope_message_shape_fallback_payload(request_data)
                if retry_payload:
                    logger.info(
                        "Retrying non-stream request with DashScope message-shape fallback request_id=%s",
                        request_id,
                    )
                    request_data = retry_payload
                    response_obj = await client.post(
                        upstream_url,
                        json=request_data,
                        headers=headers
                    )

            if response_obj.status_code != 200:
                error_text = (response_obj.text or "")[:500]
                model_id = str((request_data or {}).get("model") or "")

                if _is_dashscope_upstream_url(upstream_url) and _is_dashscope_stream_only_error(error_text):
                    stream_collected = await _collect_dashscope_stream_as_nonstream_response(
                        client=client,
                        upstream_url=upstream_url,
                        request_data=request_data,
                        headers=headers,
                        model_id=model_id,
                    )
                    if stream_collected:
                        logger.info(
                            "Recovered non-stream request via stream collection fallback request_id=%s model=%s",
                            request_id,
                            model_id,
                        )
                        result = stream_collected

                if (
                    result is None
                    and _is_dashscope_upstream_url(upstream_url)
                    and _is_dashscope_capability_error(response_obj.status_code, error_text)
                ):
                    native_fallback = _build_dashscope_native_chat_fallback(
                        upstream_url=upstream_url,
                        request_data=request_data,
                        stream=False,
                    )
                    if native_fallback:
                        native_url, native_payload, native_extra_headers = native_fallback
                        native_headers = dict(headers)
                        native_headers.update(native_extra_headers)
                        native_headers["Content-Type"] = "application/json"
                        logger.info(
                            "Retrying non-stream with DashScope native fallback request_id=%s model=%s",
                            request_id,
                            model_id,
                        )
                        native_response = await client.post(
                            native_url,
                            json=native_payload,
                            headers=native_headers,
                        )
                        if native_response.status_code == 200:
                            response_obj = native_response
                            upstream_url = native_url
                            request_data = native_payload
                        else:
                            response_obj = native_response
                            error_text = (response_obj.text or "")[:500]

                if result is None and response_obj.status_code != 200:
                    request_data = original_request_data
                    upstream_url = original_upstream_url
                    _raise_upstream_http_exception(response_obj, "chat.completions")

    if result is None and response_obj is not None:
        result = response_obj.json()

    # ── Self-Refinement Pipeline for weak models ──
    # Free tokens → we can afford a 2nd call to let the model review its own answer.
    # Only for non-streaming, high-complexity queries on weak models.
    try:
        from ..memory.prompt_injector import PromptInjector
        _refine_injector = PromptInjector(db)
        _latest_user_text = _message_content_to_text(
            (request_data.get("messages", [{}])[-1] or {}).get("content", "")
        ) if request_data.get("messages") else ""
        _refine_intent = _refine_injector.detect_intent(_latest_user_text)
        _refine_config = _refine_injector.get_self_refinement_config(
            model=request.model,
            user_message=_latest_user_text,
            detected_intent=_refine_intent,
        )

        if _refine_config and _refine_config.get("enabled"):
            draft_text = _extract_text_from_chat_payload(result)
            review_prompt = _refine_config.get("review_prompt", "")

            if draft_text and review_prompt and len(draft_text) > 60:
                refine_messages = list(request_data.get("messages", []))
                refine_messages.append({"role": "assistant", "content": draft_text})
                refine_messages.append({"role": "user", "content": review_prompt})

                refine_data = dict(request_data)
                refine_data["messages"] = refine_messages
                refine_data["max_tokens"] = _refine_config.get("max_tokens", 4096)

                refine_started = perf_counter()
                refine_response = await client.post(
                    upstream_url, json=refine_data, headers=headers
                )
                refine_ms = int((perf_counter() - refine_started) * 1000)

                if refine_response.status_code == 200:
                    refined_result = refine_response.json()
                    refined_text = _extract_text_from_chat_payload(refined_result)
                    if refined_text and len(refined_text) > 30:
                        # Use refined answer instead of draft
                        result = refined_result
                        logger.info(
                            "v1_self_refine_success request_id=%s model=%s draft_chars=%s refined_chars=%s refine_ms=%s",
                            request_id, request.model, len(draft_text), len(refined_text), refine_ms,
                        )
                    else:
                        logger.info(
                            "v1_self_refine_skip_empty request_id=%s model=%s refine_ms=%s",
                            request_id, request.model, refine_ms,
                        )
                else:
                    logger.info(
                        "v1_self_refine_failed request_id=%s model=%s status=%s refine_ms=%s",
                        request_id, request.model, refine_response.status_code, refine_ms,
                    )
    except Exception as _refine_err:
        logger.debug("Self-refinement skipped: %s", _refine_err)

    # Extract assistant content (supports provider-specific response shapes)
    assistant_content = _extract_text_from_chat_payload(result)
    if not assistant_content:
        assistant_content = _tool_calls_to_text(_extract_tool_calls(result))

    empty_retry_attempt = 0
    while not assistant_content and empty_retry_attempt < _CHAT_EMPTY_COMPLETION_RETRIES:
        empty_retry_attempt += 1
        logger.warning(
            "Upstream returned empty assistant content request_id=%s retry=%s model=%s",
            request_id,
            empty_retry_attempt,
            request.model,
        )
        retry_response = await client.post(
            original_upstream_url,
            json=original_request_data,
            headers=headers,
        )
        if retry_response.status_code != 200:
            logger.warning(
                "Empty-content retry failed request_id=%s retry=%s status=%s",
                request_id,
                empty_retry_attempt,
                retry_response.status_code,
            )
            break

        result = retry_response.json()
        upstream_url = original_upstream_url
        request_data = original_request_data
        assistant_content = _extract_text_from_chat_payload(result)
        if not assistant_content:
            assistant_content = _tool_calls_to_text(_extract_tool_calls(result))

    if response_obj is not None:
        assistant_content = _apply_style_preferences_output(assistant_content, coordinator)

    assistant_content = _maybe_expand_exact_memory_reply(
        assistant_content=assistant_content,
        request=request,
        coordinator=coordinator,
        conversation_id=conversation_id,
    )
    assistant_content = apply_output_filters(
        assistant_content,
        allow_internal_payload=True,
    )

    # Normalize content field for clients expecting string message.content
    if assistant_content:
        choices = result.get("choices")
        if isinstance(choices, list) and choices:
            first_choice = choices[0] if isinstance(choices[0], dict) else None
            if isinstance(first_choice, dict):
                message = first_choice.get("message")
                if not isinstance(message, dict):
                    message = {"role": "assistant"}
                    first_choice["message"] = message
                message["content"] = assistant_content
        elif "choices" not in result:
            result["choices"] = [{
                "index": 0,
                "message": {"role": "assistant", "content": assistant_content},
                "finish_reason": "stop",
            }]

    # Save assistant response in local memory
    if (
        assistant_content
        and coordinator
        and not _is_internal_agent_payload(assistant_content)
    ):
        try:
            assistant_sources_payload = (
                search_context.citations if search_context and search_context.citations else None
            )
            coordinator.add_message(
                "assistant",
                assistant_content,
                model=request.model,
                sources=assistant_sources_payload,
            )
            await _auto_title_conversation_if_needed(
                coordinator=coordinator,
                conversation_id=conversation_id,
                request=request,
                upstream_url=upstream_url,
                headers=headers,
                assistant_text=assistant_content,
            )
        except Exception as e:
            logger.warning("Memory save error (non-stream, non-fatal): %s", e)

    if conversation_id:
        result["conversation_id"] = conversation_id
    if request_id:
        result["request_id"] = request_id
    if web_search_enabled:
        result["web_search"] = _serialize_web_search_meta(search_context, enabled=True)
        if search_context and search_context.citations:
            result["citations"] = search_context.citations
    if isinstance(memory_context_meta, dict):
        result["memory"] = memory_context_meta
    if isinstance(learning_context, dict) and learning_context.get("enabled"):
        learning_public_payload = dict(learning_context)
        learning_public_payload.pop("system_prompt", None)
        result["learning"] = learning_public_payload

    # Track usage + expose normalized usage metadata.
    raw_usage = result.get("usage", {})
    usage = raw_usage if isinstance(raw_usage, dict) else {}

    upstream_prompt_tokens = usage.get("prompt_tokens")
    upstream_completion_tokens = usage.get("completion_tokens")
    upstream_total_tokens = usage.get("total_tokens")
    has_upstream_usage = (
        isinstance(upstream_prompt_tokens, (int, float))
        and isinstance(upstream_completion_tokens, (int, float))
    )

    prompt_tokens = int(upstream_prompt_tokens) if isinstance(upstream_prompt_tokens, (int, float)) else 0
    completion_tokens = int(upstream_completion_tokens) if isinstance(upstream_completion_tokens, (int, float)) else 0

    if prompt_tokens <= 0:
        prompt_tokens = _estimate_messages_tokens((request_data or {}).get("messages", []))
    if completion_tokens <= 0 and assistant_content:
        completion_tokens = int(len(assistant_content.split()) * 1.3)

    total_tokens = int(upstream_total_tokens) if isinstance(upstream_total_tokens, (int, float)) else 0
    if total_tokens <= 0:
        total_tokens = max(0, prompt_tokens + completion_tokens)

    cost_usd, is_rate_known = _estimate_usage_cost_usd(request.model, prompt_tokens, completion_tokens)
    usage_estimated = (not has_upstream_usage) or (not is_rate_known)
    result["usage"] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cost_usd": cost_usd,
        "estimated": usage_estimated,
    }

    _track_usage(
        db, current_user_id, request.model,
        prompt_tokens,
        completion_tokens,
        "/v1/chat/completions", "success",
        cost=cost_usd,
    )

    total_ms = int((perf_counter() - request_started_at) * 1000) if request_started_at else -1
    logger.info(
        "v1_nonstream_complete request_id=%s user_id=%s model=%s conversation_id=%s total_ms=%s chars=%s",
        request_id,
        current_user_id,
        request.model,
        conversation_id,
        total_ms,
        len(assistant_content or ""),
    )

    return result


# =============================================================================
# Memory API Endpoints
# =============================================================================

@router.get("/v1/memory/stats")
async def v1_memory_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get local-first memory statistics."""
    local_db = LocalDatabase(current_user.id)
    stats = local_db.get_storage_stats()

    schema_version = None
    try:
        with local_db._get_connection() as conn:
            row = conn.execute(
                "SELECT value FROM schema_info WHERE key = 'version'"
            ).fetchone()
            schema_version = int(row[0]) if row else None
    except Exception:
        schema_version = None

    return {
        "architecture": "local-first",
        "schema_version": schema_version,
        "storage": stats,
    }


@router.get("/v1/memory/info")
async def v1_memory_info(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get local-first memory system information."""
    local_db = LocalDatabase(current_user.id)
    return {
        "version": "3.0-local-first",
        "storage_root": local_db.storage_dir.name,
        "database_path": local_db.db_path.name,
        "layers": [
            "Layer 1: Working memory (request-scoped buffer)",
            "Layer 2: Conversation store (local SQLite)",
            "Layer 3: Unified memory (evidence -> assertion -> index)",
            "Layer 4: Prompt injection (runtime instruction + server metadata)",
        ],
        "features": [
            "Workspace-scoped retrieval",
            "Canonical key/value memory assertions",
            "Semantic search with FTS fallback",
            "Conversation persistence in local SQLite",
            "Facts extraction from user messages",
        ],
    }


@router.post("/v1/memory/reindex")
async def v1_memory_reindex(
    workspace_id: Optional[str] = Query(None, description="Optional workspace filter"),
    max_conversations: int = Query(120, ge=1, le=1000),
    max_messages_per_conversation: int = Query(100, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Re-scan existing local user messages and re-run fact/preference extraction.
    Useful when extraction rules changed or to backfill old conversations.
    """
    normalized_workspace_id = _normalize_optional_workspace_id(workspace_id)
    local_db = LocalDatabase(current_user.id)
    coordinator = get_memory_coordinator(db, current_user.id)

    conversations = local_db.get_conversations(
        workspace_id=normalized_workspace_id,
        limit=max_conversations,
    )

    processed_user_messages = 0
    skipped_messages = 0
    for conv in conversations:
        messages = local_db.get_messages(conv.id, limit=max_messages_per_conversation)
        for msg in messages:
            if (msg.role or "").strip().lower() != "user":
                continue
            content = (msg.content or "").strip()
            if not content or _is_internal_agent_payload(content):
                skipped_messages += 1
                continue
            try:
                coordinator._extract_facts_from_message(
                    content=content,
                    source_id=conv.id,
                    workspace_id=conv.workspace_id,
                )
                processed_user_messages += 1
            except Exception as e:
                logger.warning("Memory reindex skipped message_id=%s error=%s", msg.id, e)
                skipped_messages += 1

    stats = local_db.get_storage_stats()
    return {
        "ok": True,
        "scanned_conversations": len(conversations),
        "processed_user_messages": processed_user_messages,
        "skipped_messages": skipped_messages,
        "fact_count": int(stats.get("fact_count", 0) or 0),
        "preference_count": int(stats.get("preference_count", 0) or 0),
    }


@router.post("/v1/memory/remember")
async def v1_remember(
    content: str = Query(..., description="Nội dung cần nhớ"),
    category: str = Query("explicit_memory", description="Memory category"),
    subject: str = Query("User", description="Memory subject"),
    predicate: str = Query("remembers", description="Memory predicate"),
    workspace_id: Optional[str] = Query(None, description="Workspace ID (None = system memory)"),
    conversation_id: Optional[str] = Query(None, description="Source conversation ID"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Save an explicit memory as a canonical assertion.
    workspace_id=None -> user scope, workspace_id=<id> -> workspace scope.
    """
    local_db = LocalDatabase(current_user.id)
    normalized_workspace_id = _normalize_optional_workspace_id(workspace_id)
    coordinator = get_memory_coordinator(db, current_user.id)

    canonical_key = coordinator._canonicalize_memory_key(predicate or category or "memory_note")
    if not canonical_key:
        canonical_key = "memory_note"
    canonical_value = coordinator._canonicalize_memory_value(canonical_key, content) or content

    synthetic_fact = LocalFact(
        id=str(uuid.uuid4()),
        source_type="user_input",
        source_id=conversation_id,
        workspace_id=normalized_workspace_id,
        subject=subject or "User",
        predicate=canonical_key,
        object=canonical_value,
        category=category or "explicit_memory",
        confidence=1.0,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    coordinator._upsert_unified_memory(
        facts=[synthetic_fact],
        preferences=[],
        content=f"from now on {content}",
        source_id=conversation_id,
        workspace_id=normalized_workspace_id,
    )

    scope = "conversation" if (conversation_id and coordinator._is_temporary_candidate(content, canonical_value)) else (
        "workspace" if normalized_workspace_id else "user"
    )
    assertion = local_db.find_active_memory_assertion(
        key=canonical_key,
        scope=scope,
        workspace_id=normalized_workspace_id if scope == "workspace" else None,
        conversation_id=conversation_id if scope == "conversation" else None,
    )
    if assertion is None:
        candidates = local_db.get_memory_assertions(status="active", include_expired=False, limit=30)
        assertion = next(
            (
                item for item in candidates
                if (item.key or "").strip().lower() == canonical_key
                and (item.value or "").strip() == canonical_value
            ),
            None
        )
    if assertion is None:
        raise HTTPException(status_code=500, detail="Memory assertion was not persisted")
    return {"ok": True, "memory": _assertion_to_memory_dict(assertion)}


@router.get("/v1/memory/memories")
async def v1_list_memories(
    scope: str = Query("all", description="'system', 'workspace', or 'all'"),
    workspace_id: Optional[str] = Query(None, description="Required when scope='workspace'"),
    subject: Optional[str] = Query(None, description="Filter by subject"),
    category: Optional[str] = Query(None, description="Optional category filter"),
    limit: int = Query(500, ge=1, le=5000),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    List canonical memories from local storage.
    
    scope:
      - 'system'    -> only user-level memories
      - 'workspace' -> only memories for the given workspace_id
      - 'all'       -> all memories
    """
    local_db = LocalDatabase(current_user.id)
    normalized_workspace_id = _normalize_optional_workspace_id(workspace_id)
    normalized_scope = (scope or "all").strip().lower()
    if normalized_scope not in {"system", "workspace", "all"}:
        raise HTTPException(status_code=400, detail="scope must be one of: system, workspace, all")

    if subject and subject.strip().lower() not in {"user", "me", "toi", "tôi"}:
        return {"memories": [], "total": 0}

    if normalized_scope == "system":
        assertions = local_db.get_memory_assertions(
            scope="user",
            workspace_id=None,
            include_expired=False,
            status="active",
            limit=limit,
        )
    elif normalized_scope == "workspace":
        if not normalized_workspace_id:
            return {"memories": [], "total": 0}
        assertions = local_db.get_memory_assertions(
            scope="workspace",
            workspace_id=normalized_workspace_id,
            include_expired=False,
            status="active",
            limit=limit,
        )
    else:
        assertions = local_db.get_memory_assertions(
            scope=None,
            workspace_id=...,
            include_expired=False,
            status="active",
            limit=limit * 2,
        )
        assertions = [
            item for item in assertions
            if (item.scope or "").strip().lower() in {"user", "workspace"}
        ]
        if normalized_workspace_id:
            assertions = [
                item for item in assertions
                if (
                    (item.scope or "").strip().lower() == "user"
                    or item.workspace_id == normalized_workspace_id
                )
            ]

    if category:
        normalized_category = category.strip().lower()
        assertions = [
            item for item in assertions
            if (item.category or "").strip().lower() == normalized_category
        ]

    assertions = assertions[:limit]
    if assertions:
        return {
            "memories": [_assertion_to_memory_dict(item) for item in assertions],
            "total": len(assertions)
        }

    # Backward-compatible fallback for legacy fact-only users.
    if normalized_scope == "system":
        ws_filter = None
    elif normalized_scope == "workspace":
        ws_filter = normalized_workspace_id
    else:
        ws_filter = ...
    legacy_memories = local_db.get_facts(
        subject=subject,
        category=category,
        workspace_id=ws_filter,
        limit=limit,
    )
    return {
        "memories": [_fact_to_memory_dict(item) for item in legacy_memories],
        "total": len(legacy_memories)
    }


@router.patch("/v1/memory/memories/{memory_id}")
async def v1_update_memory(
    memory_id: str,
    subject: Optional[str] = None,
    predicate: Optional[str] = None,
    object: Optional[str] = None,
    category: Optional[str] = None,
    confidence: Optional[float] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a memory assertion by ID."""
    local_db = LocalDatabase(current_user.id)
    target_assertion = local_db.get_memory_assertion(memory_id)
    if target_assertion:
        coordinator = get_memory_coordinator(db, current_user.id)
        if subject is not None and subject.strip().lower() not in {"user", "me", "toi", "tôi"}:
            raise HTTPException(status_code=400, detail="Only user-subject memories can be updated")
        if predicate is not None:
            normalized_key = coordinator._canonicalize_memory_key(predicate)
            if normalized_key:
                target_assertion.key = normalized_key
        if object is not None:
            target_assertion.value = (
                coordinator._canonicalize_memory_value(target_assertion.key, object) or object
            )
        if category is not None:
            target_assertion.category = category
        if confidence is not None:
            target_assertion.confidence = max(0.0, min(1.0, float(confidence)))
        target_assertion.updated_at = datetime.now()
        local_db.save_memory_assertion(target_assertion)
        return {"ok": True, "memory": _assertion_to_memory_dict(target_assertion)}

    # Find existing fact
    facts = local_db.get_facts(limit=5000)
    target = next((f for f in facts if f.id == memory_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Memory not found")

    if subject is not None:
        target.subject = subject
    if predicate is not None:
        target.predicate = predicate
    if object is not None:
        target.object = object
    if category is not None:
        target.category = category
    if confidence is not None:
        target.confidence = confidence
    target.updated_at = datetime.now()

    local_db.save_fact(target)
    return {"ok": True, "memory": _fact_to_memory_dict(target)}


@router.delete("/v1/memory/memories/{memory_id}")
async def v1_delete_memory(
    memory_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a memory assertion by ID."""
    local_db = LocalDatabase(current_user.id)
    success = local_db.delete_memory_assertion(memory_id)
    if not success:
        success = local_db.delete_fact(memory_id)
    return {"ok": success}


@router.get("/v1/memory/search")
async def v1_search_memory(
    q: str = Query(..., description="Từ khóa tìm kiếm"),
    workspace_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Search across canonical memory, knowledge, conversations, and messages."""
    local_db = LocalDatabase(current_user.id)
    normalized_workspace_id = _normalize_optional_workspace_id(workspace_id)

    assertion_matches = local_db.search_memory_assertions(
        query=q,
        scope=None,
        workspace_id=...,
        conversation_id=...,
        types=None,
        include_expired=False,
        limit=30,
    )
    if normalized_workspace_id:
        assertion_matches = [
            item for item in assertion_matches
            if (
                (item.scope or "").strip().lower() == "user"
                or ((item.scope or "").strip().lower() == "workspace" and item.workspace_id == normalized_workspace_id)
            )
        ]
    if conversation_id:
        assertion_matches = [
            item for item in assertion_matches
            if not item.conversation_id or item.conversation_id == conversation_id
        ]

    legacy_facts = local_db.search_facts(q, limit=20)
    fact_payload = [_assertion_to_memory_dict(item) for item in assertion_matches]
    seen_ids = {str(item.get("id") or "") for item in fact_payload}
    for fact in legacy_facts:
        if fact.id in seen_ids:
            continue
        fact_payload.append(_fact_to_memory_dict(fact))

    knowledge = local_db.search_knowledge_fts(q, limit=20, workspace_id=normalized_workspace_id)
    conversations = local_db.search_conversations(
        q,
        workspace_id=normalized_workspace_id,
        limit=20
    )
    messages = local_db.search_messages_text(q, conversation_id=conversation_id, limit=50)

    return {
        "facts": fact_payload[:40],
        "knowledge": [
            {
                "id": k.id,
                "workspace_id": k.workspace_id,
                "title": k.title,
                "content_type": k.content_type,
                "updated_at": k.updated_at.isoformat() if k.updated_at else None,
            }
            for k in knowledge
        ],
        "conversations": [
            {
                "id": c.id,
                "title": c.title,
                "workspace_id": c.workspace_id,
                "total_messages": c.total_messages,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            }
            for c in conversations
        ],
        "messages": [
            {
                "id": m.id,
                "conversation_id": m.conversation_id,
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ],
        "total": {
            "facts": len(fact_payload[:40]),
            "knowledge": len(knowledge),
            "conversations": len(conversations),
            "messages": len(messages),
        }
    }


# =============================================================================
# Conversations API
# =============================================================================

class V1ConversationCreateRequest(BaseModel):
    title: Optional[str] = None
    workspace_id: Optional[str] = None


class V1ConversationMessageCreateRequest(BaseModel):
    role: str
    content: str
    model: Optional[str] = None


class V1ConversationMessageUpdateRequest(BaseModel):
    content: str
    model: Optional[str] = None


class V1ConversationLogCreateRequest(BaseModel):
    task_name: str
    content: str


class V1ConversationArtifactCreateRequest(BaseModel):
    filename: str
    content: str


def _serialize_v1_conversation(conv: LocalConversation) -> Dict[str, Any]:
    return {
        "id": conv.id,
        "title": conv.title,
        "summary": conv.summary,
        "workspace_id": conv.workspace_id,
        "total_messages": conv.total_messages,
        "total_tokens": conv.total_tokens,
        "is_archived": conv.is_archived,
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
        "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
    }


def _serialize_v1_conversation_message(message: LocalMessage) -> Dict[str, Any]:
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "model": message.model,
        "token_count": message.token_count or 0,
        "sources": message.sources if message.sources else None,
        "citations": message.citations if message.citations else None,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


@router.post("/v1/conversations")
async def v1_create_conversation(
    data: V1ConversationCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new conversation in local SQLite."""
    local_db = LocalDatabase(current_user.id)
    normalized_workspace_id = _normalize_optional_workspace_id(data.workspace_id)
    local_db.archive_empty_conversations(workspace_id=normalized_workspace_id)

    conv = LocalConversation(
        id=str(uuid.uuid4()),
        user_id=local_db.user_id,
        workspace_id=normalized_workspace_id,
        title=(data.title or "").strip() or "New Conversation",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    local_db.save_conversation(conv)
    return _serialize_v1_conversation(conv)


@router.get("/v1/conversations")
async def v1_list_conversations(
    limit: int = Query(50, description="Số lượng tối đa"),
    workspace_id: Optional[str] = None,
    include_archived: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List conversations from local SQLite."""
    local_db = LocalDatabase(current_user.id)
    local_db.archive_empty_conversations(workspace_id=workspace_id)

    convs = local_db.get_conversations(
        workspace_id=workspace_id,
        limit=limit,
        include_archived=include_archived
    )
    return {
        "conversations": [_serialize_v1_conversation(c) for c in convs],
        "total": len(convs)
    }


@router.get("/v1/conversations/{conv_id}")
async def v1_get_conversation(
    conv_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get conversation with messages"""
    local_db = LocalDatabase(current_user.id)

    conv = local_db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = local_db.get_messages(conv_id)

    return {
        **_serialize_v1_conversation(conv),
        "messages": [_serialize_v1_conversation_message(m) for m in messages],
    }


@router.delete("/v1/conversations/{conv_id}")
async def v1_delete_conversation(
    conv_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Archive a conversation instead of hard-deleting it."""
    local_db = LocalDatabase(current_user.id)
    conv = local_db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conv.is_archived = True
    conv.updated_at = datetime.now()
    local_db.save_conversation(conv)
    return {"ok": True}


@router.post("/v1/conversations/{conv_id}/messages")
async def v1_add_conversation_message(
    conv_id: str,
    data: V1ConversationMessageCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Add a message to a conversation in local SQLite."""
    local_db = LocalDatabase(current_user.id)
    if not local_db.get_conversation(conv_id):
        raise HTTPException(status_code=404, detail="Conversation not found")

    msg = LocalMessage(
        id=str(uuid.uuid4()),
        conversation_id=conv_id,
        role=(data.role or "").strip(),
        content=data.content,
        model=data.model,
        token_count=int(len((data.content or "").split()) * 1.3),
        created_at=datetime.now(),
    )
    local_db.save_message(msg)
    return _serialize_v1_conversation_message(msg)


@router.get("/v1/conversations/{conv_id}/messages")
async def v1_get_messages(
    conv_id: str,
    limit: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get messages for a conversation"""
    local_db = LocalDatabase(current_user.id)

    if not local_db.get_conversation(conv_id):
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = local_db.get_messages(conv_id, limit=limit)
    return {
        "messages": [_serialize_v1_conversation_message(m) for m in messages],
        "total": len(messages)
    }


@router.patch("/v1/conversations/{conv_id}/messages/{message_id}")
async def v1_update_conversation_message(
    conv_id: str,
    message_id: str,
    data: V1ConversationMessageUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a stored message while preserving stored source metadata."""
    local_db = LocalDatabase(current_user.id)
    if not local_db.get_conversation(conv_id):
        raise HTTPException(status_code=404, detail="Conversation not found")

    existing = local_db.get_message(message_id)
    if not existing or existing.conversation_id != conv_id:
        raise HTTPException(status_code=404, detail="Message not found")

    updated_content = (data.content or "").strip()
    updated_message = LocalMessage(
        id=existing.id,
        conversation_id=existing.conversation_id,
        role=existing.role,
        content=updated_content,
        model=data.model if data.model is not None else existing.model,
        token_count=int(len(updated_content.split()) * 1.3) if updated_content else 0,
        sources_json=existing.sources_json,
        embedding=existing.embedding,
        created_at=existing.created_at,
    )
    local_db.update_message(updated_message)
    return _serialize_v1_conversation_message(updated_message)


@router.post("/v1/conversations/{conv_id}/logs")
async def v1_write_conversation_log(
    conv_id: str,
    data: V1ConversationLogCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Write a task log file for a conversation."""
    local_db = LocalDatabase(current_user.id)
    if not local_db.get_conversation(conv_id):
        raise HTTPException(status_code=404, detail="Conversation not found")

    try:
        log_path = local_db.write_conversation_log(conv_id, data.task_name, data.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "path": str(log_path)}


@router.get("/v1/conversations/{conv_id}/logs")
async def v1_list_conversation_logs(
    conv_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List all log files for a conversation."""
    local_db = LocalDatabase(current_user.id)
    if not local_db.get_conversation(conv_id):
        raise HTTPException(status_code=404, detail="Conversation not found")

    conv_dir = local_db.brain_dir / "conversations" / conv_id / "logs"
    if not conv_dir.exists():
        return {"logs": []}
    return {"logs": [f.name for f in conv_dir.iterdir() if f.is_file()]}


@router.post("/v1/conversations/{conv_id}/artifacts")
async def v1_save_conversation_artifact(
    conv_id: str,
    data: V1ConversationArtifactCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Save an artifact file for a conversation."""
    local_db = LocalDatabase(current_user.id)
    if not local_db.get_conversation(conv_id):
        raise HTTPException(status_code=404, detail="Conversation not found")

    try:
        artifact_path = local_db.save_artifact(conv_id, data.filename, data.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "path": str(artifact_path)}


@router.get("/v1/conversations/{conv_id}/artifacts")
async def v1_list_conversation_artifacts(
    conv_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List all artifacts for a conversation."""
    local_db = LocalDatabase(current_user.id)
    if not local_db.get_conversation(conv_id):
        raise HTTPException(status_code=404, detail="Conversation not found")

    try:
        artifacts = local_db.list_conversation_artifacts(conv_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"artifacts": artifacts}


@router.get("/v1/conversations/{conv_id}/export")
async def v1_export_conversation(
    conv_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Export a conversation as markdown."""
    local_db = LocalDatabase(current_user.id)
    conv = local_db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    markdown = local_db.export_conversation_to_markdown(conv_id)
    return {"markdown": markdown, "title": conv.title}


# =============================================================================
# Rules API (PIGTEX.md)
# =============================================================================

@router.get("/v1/rules")
async def v1_get_rules(
    workspace_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get current local rules (PIGTEX.md) for user/workspace."""
    normalized_workspace_id = _normalize_optional_workspace_id(workspace_id)
    path = _rules_file_path(current_user.id, normalized_workspace_id)
    rules_text = path.read_text(encoding="utf-8") if path.exists() else ""
    return {
        "rules": rules_text,
        "path": str(path),
        "tokens": int(len(rules_text.split()) * 1.3),
    }


@router.put("/v1/rules")
async def v1_update_rules(
    content: str,
    workspace_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update local rules (PIGTEX.md) for user/workspace."""
    normalized_workspace_id = _normalize_optional_workspace_id(workspace_id)
    path = _rules_file_path(current_user.id, normalized_workspace_id)
    path.write_text(content or "", encoding="utf-8")
    return {
        "ok": True,
        "path": str(path)
    }


# =============================================================================
# Usage tracking helper
# =============================================================================

def _track_usage(
    db: Session,
    user_id: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    endpoint: str,
    track_status: str,
    cost: float = 0.0,
):
    """Track API usage in MySQL"""
    try:
        record = UsageRecord(
            id=str(uuid.uuid4()),
            user_id=user_id,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            cost=cost,
            endpoint=endpoint,
            status=track_status
        )
        db.add(record)
        db.commit()
    except Exception as e:
        logger.warning("Usage tracking error (non-fatal): %s", e)
        db.rollback()
