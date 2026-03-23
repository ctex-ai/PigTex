from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from .config import get_settings


@dataclass(frozen=True)
class DirectProviderSpec:
    public_id: str
    upstream_mode: str
    label: str
    default_base_url: str
    docs_url: str
    auth_style: str
    aliases: tuple[str, ...] = ()
    first_party_hosts: tuple[str, ...] = ()
    first_party_path_hints: tuple[str, ...] = ()


DIRECT_PROVIDER_SPECS: tuple[DirectProviderSpec, ...] = (
    DirectProviderSpec(
        public_id="openai",
        upstream_mode="openai",
        label="OpenAI",
        default_base_url="https://api.openai.com",
        docs_url="https://platform.openai.com/api-keys",
        auth_style="bearer",
        aliases=(
            "openai-compatible",
            "openai_compatible",
            "openai-compat",
            "openai_compat",
        ),
        first_party_hosts=("api.openai.com",),
    ),
    DirectProviderSpec(
        public_id="google",
        upstream_mode="gemini",
        label="Google",
        default_base_url="https://generativelanguage.googleapis.com",
        docs_url="https://aistudio.google.com/apikey",
        auth_style="x-goog-api-key",
        aliases=("gemini", "gemini-native"),
        first_party_hosts=("generativelanguage.googleapis.com",),
    ),
    DirectProviderSpec(
        public_id="anthropic",
        upstream_mode="anthropic",
        label="Anthropic",
        default_base_url="https://api.anthropic.com",
        docs_url="https://console.anthropic.com/settings/keys",
        auth_style="x-api-key",
        aliases=("anthropic-native",),
        first_party_hosts=("api.anthropic.com",),
    ),
    DirectProviderSpec(
        public_id="alibaba",
        upstream_mode="alibaba",
        label="Alibaba",
        default_base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        docs_url="https://www.alibabacloud.com/help/en/model-studio/",
        auth_style="bearer",
        aliases=(
            "alibaba-native",
            "dashscope",
            "dashscope-compatible",
            "dashscope_compatible",
            "aliyun",
        ),
        first_party_hosts=("dashscope-intl.aliyuncs.com", "dashscope.aliyuncs.com"),
        first_party_path_hints=("/compatible-mode/",),
    ),
)

DEFAULT_PROVIDER_BASE_URLS: dict[str, str] = {
    spec.upstream_mode: spec.default_base_url
    for spec in DIRECT_PROVIDER_SPECS
}

SUPPORTED_API_PROVIDERS = frozenset({"auto", *DEFAULT_PROVIDER_BASE_URLS.keys()})

PROVIDER_ALIASES: dict[str, str] = {
    "google": "gemini",
    "texapi": "openai",
    "tex-api": "openai",
}
for _spec in DIRECT_PROVIDER_SPECS:
    PROVIDER_ALIASES[_spec.public_id] = _spec.upstream_mode
    for _alias in _spec.aliases:
        PROVIDER_ALIASES[_alias] = _spec.upstream_mode


def normalize_api_provider(raw: Optional[str]) -> str:
    if not raw:
        return "auto"
    normalized = raw.strip().lower()
    normalized = PROVIDER_ALIASES.get(normalized, normalized)
    return normalized if normalized in SUPPORTED_API_PROVIDERS else "auto"


def _parse_host_and_path(base_url: Optional[str]) -> tuple[str, str]:
    normalized = (base_url or "").strip().lower()
    if not normalized:
        return "", ""

    parsed = urlparse(normalized)
    host = (parsed.netloc or parsed.path or "").lower().split("@")[-1]
    host = host.split(":", 1)[0]
    path = (parsed.path or "").lower()
    return host, path


def infer_provider_from_base_url(base_url: Optional[str]) -> str:
    host, path = _parse_host_and_path(base_url)
    if not host and not path:
        return "openai"

    for spec in DIRECT_PROVIDER_SPECS:
        if host in spec.first_party_hosts:
            return spec.upstream_mode
        if any(hint in path for hint in spec.first_party_path_hints):
            return spec.upstream_mode

    return "openai"


def infer_provider_from_api_key(api_key: Optional[str]) -> Optional[str]:
    key = (api_key or "").strip()
    if not key:
        return None
    if key.startswith("sk-ant-"):
        return "anthropic"
    if key.startswith("AIza"):
        return "gemini"
    if key.startswith("dashscope_") or key.startswith("ali-"):
        return "alibaba"
    return None


def is_first_party_provider_url(base_url: Optional[str], provider: Optional[str] = None) -> bool:
    host, path = _parse_host_and_path(base_url)
    if not host and not path:
        return False

    checks: dict[str, bool] = {}
    for spec in DIRECT_PROVIDER_SPECS:
        checks[spec.upstream_mode] = host in spec.first_party_hosts or any(
            hint in path for hint in spec.first_party_path_hints
        )

    if provider:
        normalized_provider = normalize_api_provider(provider)
        return checks.get(normalized_provider, False)
    return any(checks.values())


def build_public_provider_catalog() -> list[dict[str, object]]:
    settings = get_settings()
    return [
        {
            "id": "texapi",
            "label": "TexAPI",
            "kind": "gateway",
            "upstream_mode": "openai",
            "request_api_provider": "openai",
            "default_base_url": settings.texapi_partner_gateway_base_url.strip(),
            "docs_url": "",
            "auth_style": "bearer",
            "supports_byok": False,
            "managed_by_server": True,
            "aliases": ["texapi", "tex-api"],
        },
        *[
            {
                "id": spec.public_id,
                "label": spec.label,
                "kind": "direct",
                "upstream_mode": spec.upstream_mode,
                "request_api_provider": spec.upstream_mode,
                "default_base_url": spec.default_base_url,
                "docs_url": spec.docs_url,
                "auth_style": spec.auth_style,
                "supports_byok": True,
                "managed_by_server": False,
                "aliases": [spec.public_id, *spec.aliases],
            }
            for spec in DIRECT_PROVIDER_SPECS
        ],
    ]
