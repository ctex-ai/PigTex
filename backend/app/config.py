from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from pydantic import model_validator
from pathlib import Path
from urllib.parse import urlparse

BACKEND_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(BACKEND_ENV_PATH), ".env"),
        extra="ignore",
        protected_namespaces=("settings_",),
    )

    # Environment
    app_env: str = "development"  # development | production

    # Database - preferred single URL for cross-service consistency
    database_url: str = ""

    # Database fallback fields (used when DATABASE_URL is empty)
    db_host: str = "localhost"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = "password"
    db_name: str = "pigtex"
    db_pool_size: int = 20
    db_pool_max_overflow: int = 40
    db_pool_timeout_seconds: float = 30.0

    # JWT
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 10080  # 7 days for local dev

    # OAuth providers (desktop browser-based flow)
    google_client_id: str = ""
    google_client_secret: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    oauth_state_ttl_seconds: int = 600
    oauth_state_backend: str = "auto"  # auto | memory | redis
    oauth_state_key_prefix: str = "pigtex:oauthstate"
    oauth_state_redis_socket_timeout_seconds: float = 1.0

    # API governance
    model_admin_api_key: str = ""  # Required for model catalog write operations
    admin_bootstrap_emails: str = ""  # Comma-separated emails elevated to admin on login/auth

    # Web search pipeline (Tavily + Jina Reader)
    web_search_enabled_default: bool = False
    web_search_tavily_api_key: str = ""
    web_search_tavily_endpoint: str = "https://api.tavily.com/search"
    web_search_provider_order: str = "tavily,duckduckgo"
    web_search_duckduckgo_enabled: bool = True
    web_search_duckduckgo_region: str = "us-en"
    web_search_duckduckgo_safesearch: str = "moderate"
    web_search_duckduckgo_backend: str = "html"
    web_search_github_enabled: bool = True
    web_search_github_api_endpoint: str = "https://api.github.com"
    web_search_github_token: str = ""
    web_search_github_max_selected_files: int = 4
    web_search_github_max_file_chars: int = 1800
    web_search_github_max_render_chars: int = 7200
    web_search_jina_endpoint: str = "https://r.jina.ai/"
    web_search_timeout_seconds: float = 12.0
    web_search_max_results: int = 5
    web_search_url_read_max_snippet_chars: int = 4200
    web_search_cache_ttl_seconds: int = 600
    web_search_rate_limit_per_minute: int = 30
    web_search_max_deep_reads: int = 2
    web_search_verify_max_claims: int = 4
    web_search_verify_min_sources_per_claim: int = 2
    web_search_verify_max_queries: int = 8
    web_search_cache_key_prefix: str = "pigtex:websearch:cache"
    web_search_rate_limit_key_prefix: str = "pigtex:websearch:rate"

    # Runtime behavior
    auto_create_db_schema: bool = False
    cors_origins: str = "https://app.example.com,null,http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173"
    db_startup_max_retries: int = 30
    db_startup_retry_interval_seconds: float = 2.0
    require_db_migration_head_on_startup: bool = False
    enable_rate_limit: bool = True
    rate_limit_backend: str = "memory"  # memory | redis
    redis_url: str = ""
    rate_limit_key_prefix: str = "pigtex:ratelimit"
    rate_limit_redis_socket_timeout_seconds: float = 1.0
    rate_limit_auth_login_per_minute: int = 10
    rate_limit_auth_register_per_minute: int = 5
    rate_limit_v1_per_minute: int = 180
    rate_limit_proxy_per_minute: int = 120
    enable_idempotency: bool = True
    idempotency_backend: str = "memory"  # memory | redis
    idempotency_key_prefix: str = "pigtex:idempotency"
    idempotency_ttl_seconds: int = 86400
    idempotency_lock_ttl_seconds: int = 30
    idempotency_max_cached_response_bytes: int = 262144

    # Cloud backup / DigitalOcean Spaces
    cloud_backup_enabled: bool = False
    spaces_region: str = ""
    spaces_endpoint_url: str = ""
    spaces_bucket_backups: str = ""
    spaces_bucket_shared: str = ""
    spaces_access_key_id: str = ""
    spaces_secret_access_key: str = ""
    spaces_cdn_url: str = ""
    spaces_addressing_style: str = "virtual"
    spaces_signed_url_ttl_seconds: int = 900
    cloud_backup_default_quota_bytes: int = 10 * 1024 * 1024 * 1024  # 10 GiB
    cloud_backup_default_retention_days: int = 30
    cloud_backup_default_max_devices: int = 5
    cloud_backup_default_max_snapshots: int = 30
    sync_billing_enabled: bool = False
    sync_billing_provider: str = "payos"  # mock | payos | stripe
    sync_freeze_grace_days: int = 14
    sync_checkout_success_url: str = "https://app.example.com/billing/sync/success"
    sync_checkout_cancel_url: str = "https://app.example.com/billing/sync/cancel"
    sync_portal_return_url: str = "https://app.example.com/settings/billing"
    sync_plan_quota_bytes: int = 10 * 1024 * 1024 * 1024  # 10 GiB
    sync_plan_retention_days: int = 30
    sync_plan_max_devices: int = 5
    sync_plan_max_snapshots: int = 64
    sync_plus_quota_bytes: int = 100 * 1024 * 1024 * 1024  # 100 GiB
    sync_plus_retention_days: int = 180
    sync_plus_max_devices: int = 10
    sync_plus_max_snapshots: int = 256
    sync_monthly_price_vnd: int = 79000
    sync_annual_price_vnd: int = 790000
    sync_plus_monthly_price_vnd: int = 149000
    sync_plus_annual_price_vnd: int = 1490000
    sync_payos_client_id: str = ""
    sync_payos_api_key: str = ""
    sync_payos_checksum_key: str = ""
    sync_payos_partner_code: str = ""
    sync_payos_payment_link_expiry_minutes: int = 30
    sync_stripe_secret_key: str = ""
    sync_stripe_webhook_secret: str = ""
    sync_stripe_price_sync_monthly: str = ""
    sync_stripe_price_sync_annual: str = ""
    sync_stripe_price_sync_plus_monthly: str = ""
    sync_stripe_price_sync_plus_annual: str = ""

    # Server
    # Bind all interfaces intentionally for container/network deployments.
    host: str = "0.0.0.0"  # nosec B104
    port: int = 3001

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() == "production"

    def get_cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def get_admin_bootstrap_emails(self) -> set[str]:
        return {
            email.strip().lower()
            for email in self.admin_bootstrap_emails.split(",")
            if email.strip()
        }

    @property
    def should_require_db_migration_head(self) -> bool:
        # Production should enforce schema at alembic head by default.
        return (
            self.is_production
            or self.require_db_migration_head_on_startup
            or self.cloud_backup_enabled
        )

    @model_validator(mode="after")
    def validate_security_settings(self) -> "Settings":
        normalized_spaces_region = self.spaces_region.strip().lower()
        normalized_spaces_endpoint = self.spaces_endpoint_url.strip()

        if not normalized_spaces_endpoint and normalized_spaces_region:
            normalized_spaces_endpoint = (
                f"https://{normalized_spaces_region}.digitaloceanspaces.com"
            )

        if not normalized_spaces_region and normalized_spaces_endpoint:
            parsed_endpoint = urlparse(normalized_spaces_endpoint)
            hostname = (parsed_endpoint.hostname or "").strip().lower()
            suffix = ".digitaloceanspaces.com"
            if hostname.endswith(suffix):
                normalized_spaces_region = hostname[: -len(suffix)].strip(".")

        self.spaces_region = normalized_spaces_region
        self.spaces_endpoint_url = normalized_spaces_endpoint
        self.spaces_addressing_style = (
            self.spaces_addressing_style.strip().lower() or "virtual"
        )
        self.sync_billing_provider = (
            self.sync_billing_provider.strip().lower() or "mock"
        )
        if self.sync_billing_provider not in {"mock", "payos", "stripe"}:
            raise ValueError("SYNC_BILLING_PROVIDER must be one of mock, payos, or stripe")
        self.oauth_state_backend = (
            self.oauth_state_backend.strip().lower() or "auto"
        )
        if self.oauth_state_backend not in {"auto", "memory", "redis"}:
            raise ValueError("OAUTH_STATE_BACKEND must be one of auto, memory, or redis")

        normalized_jwt_secret = self.jwt_secret_key.strip()
        insecure_defaults = {
            "",
            "your-super-secret-key-change-in-production",
            "pigtex-super-secret-key-2024-change-me",
            "change-this-in-real-deploy",
        }
        normalized_redis_url = self.redis_url.strip()
        parsed_redis_url = urlparse(normalized_redis_url) if normalized_redis_url else None
        oauth_is_configured = any(
            (
                self.google_client_id.strip() and self.google_client_secret.strip(),
                self.github_client_id.strip() and self.github_client_secret.strip(),
            )
        )
        if self.is_production:
            if normalized_jwt_secret in insecure_defaults or len(normalized_jwt_secret) < 32:
                raise ValueError("JWT_SECRET_KEY must be set to a strong non-default secret in production")

            requires_redis = (
                self.rate_limit_backend.strip().lower() == "redis"
                or self.idempotency_backend.strip().lower() == "redis"
            )
            resolved_oauth_backend = self.oauth_state_backend
            if resolved_oauth_backend == "auto":
                resolved_oauth_backend = "redis" if normalized_redis_url else "memory"
            if resolved_oauth_backend == "redis":
                requires_redis = True
            if oauth_is_configured and resolved_oauth_backend != "redis":
                raise ValueError(
                    "OAuth login in production requires Redis-backed OAuth state storage"
                )
            if requires_redis:
                if not normalized_redis_url:
                    raise ValueError("REDIS_URL must be set when Redis-backed features are enabled in production")
                assert parsed_redis_url is not None
                if parsed_redis_url.scheme not in {"redis", "rediss"}:
                    raise ValueError("REDIS_URL must use redis:// or rediss:// in production")
                if not parsed_redis_url.password:
                    raise ValueError("REDIS_URL must include a password in production")

            if self.sync_billing_enabled and self.sync_billing_provider == "stripe":
                if not self.sync_stripe_secret_key.strip():
                    raise ValueError("SYNC_STRIPE_SECRET_KEY must be set when Stripe billing is enabled")
                if not self.sync_stripe_webhook_secret.strip():
                    raise ValueError("SYNC_STRIPE_WEBHOOK_SECRET must be set when Stripe billing is enabled")
            if self.sync_billing_enabled and self.sync_billing_provider == "payos":
                if not self.sync_payos_client_id.strip():
                    raise ValueError("SYNC_PAYOS_CLIENT_ID must be set when PayOS billing is enabled")
                if not self.sync_payos_api_key.strip():
                    raise ValueError("SYNC_PAYOS_API_KEY must be set when PayOS billing is enabled")
                if not self.sync_payos_checksum_key.strip():
                    raise ValueError("SYNC_PAYOS_CHECKSUM_KEY must be set when PayOS billing is enabled")
            if self.sync_billing_enabled and not self.cloud_backup_enabled:
                raise ValueError("CLOUD_BACKUP_ENABLED must be true when PigTex Sync billing is enabled in production")
            if self.cloud_backup_enabled:
                if not self.spaces_access_key_id.strip():
                    raise ValueError("SPACES_ACCESS_KEY_ID must be set when cloud backup is enabled in production")
                if not self.spaces_secret_access_key.strip():
                    raise ValueError("SPACES_SECRET_ACCESS_KEY must be set when cloud backup is enabled in production")
                if not self.spaces_bucket_backups.strip():
                    raise ValueError("SPACES_BUCKET_BACKUPS must be set when cloud backup is enabled in production")
                if not (self.spaces_endpoint_url.strip() or self.spaces_region.strip()):
                    raise ValueError("SPACES_ENDPOINT_URL or SPACES_REGION must be set when cloud backup is enabled in production")
        return self

@lru_cache()
def get_settings() -> Settings:
    return Settings()
