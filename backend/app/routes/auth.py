from datetime import datetime, timedelta, timezone
from html import escape
import logging
import secrets
import time
from typing import Any, Literal
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import OAuthAccount, User
from ..oauth_state import delete_oauth_state, get_oauth_state, set_oauth_state
from ..rate_limit import auth_login_rate_limit, auth_register_rate_limit
from ..schemas import Token, UserCreate, UserLogin, UserResponse
from ..services.texapi_partner_service import TexApiPartnerService
from .auth_utils import (
    ROLE_ADMIN,
    ROLE_SUPER_ADMIN,
    create_access_token,
    get_current_user,
    get_user_permissions,
    get_password_hash,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])
settings = get_settings()
logger = logging.getLogger(__name__)

OAuthProvider = Literal["google", "github"]

_OAUTH_SESSION_CLEANUP_GRACE_SECONDS = 300


def _oauth_state_pending_ttl_seconds() -> int:
    return max(120, int(settings.oauth_state_ttl_seconds))


def _oauth_state_result_ttl_seconds() -> int:
    return max(60, _OAUTH_SESSION_CLEANUP_GRACE_SECONDS)


def _store_oauth_state(state: str, payload: dict[str, Any], ttl_seconds: int) -> None:
    set_oauth_state(state, payload, ttl_seconds)


def _complete_oauth_state(
    state: str,
    payload: dict[str, Any],
    *,
    status_value: Literal["success", "error"],
    access_token: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    updated_payload = dict(payload)
    updated_payload["status"] = status_value
    updated_payload["completed_at"] = time.time()
    if status_value == "success":
        updated_payload["access_token"] = access_token or ""
        updated_payload.pop("error", None)
    else:
        updated_payload["error"] = error_message or "OAuth login failed"
        updated_payload.pop("access_token", None)
    _store_oauth_state(state, updated_payload, _oauth_state_result_ttl_seconds())
    return updated_payload


def _get_provider_credentials(provider: OAuthProvider) -> dict[str, str] | None:
    if provider == "google":
        client_id = settings.google_client_id.strip()
        client_secret = settings.google_client_secret.strip()
        if client_id and client_secret:
            return {"client_id": client_id, "client_secret": client_secret}
        return None

    client_id = settings.github_client_id.strip()
    client_secret = settings.github_client_secret.strip()
    if client_id and client_secret:
        return {"client_id": client_id, "client_secret": client_secret}
    return None


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


async def _maybe_upsert_texapi_partner_customer(user: User) -> None:
    service = TexApiPartnerService()
    if not service.is_enabled():
        return
    try:
        await service.upsert_customer(user)
    except Exception:
        logger.warning(
            "Best-effort TexAPI partner customer upsert failed user_id=%s",
            str(getattr(user, "id", "")),
            exc_info=True,
        )


def _normalize_username_seed(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return "user"
    normalized_chars: list[str] = []
    for ch in raw:
        if ch.isalnum():
            normalized_chars.append(ch)
        else:
            normalized_chars.append("_")
    normalized = "".join(normalized_chars).strip("_")
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized[:80] or "user"


def _build_unique_username(db: Session, seed: str) -> str:
    base = _normalize_username_seed(seed)
    for attempt in range(100):
        suffix = "" if attempt == 0 else f"_{attempt}"
        candidate = f"{base}{suffix}"[:100]
        exists = db.query(User).filter(User.username == candidate).first()
        if not exists:
            return candidate
    return f"user_{int(time.time())}"[:100]


def _render_oauth_result_page(success: bool, message: str) -> HTMLResponse:
    title = "PigTex OAuth Success" if success else "PigTex OAuth Failed"
    headline = "Đăng nhập thành công" if success else "Đăng nhập thất bại"
    safe_message = escape(message)
    accent_color = "#111118" if success else "#dc2626"
    status_code = status.HTTP_200_OK if success else status.HTTP_400_BAD_REQUEST

    icon_html = '<div class="icon-success">✓</div>' if success else '<div class="icon-error">✕</div>'

    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title}</title>
    <style>
      :root {{
        --background: #fafafa;
        --foreground: #111118;
        --surface: #ffffff;
        --border-clr: rgba(0, 0, 0, 0.08);
        --muted: #71717a;
        --gradient-primary: linear-gradient(135deg, #8b5cf6 0%, #06b6d4 100%);
        --radius: 24px;
      }}
      body {{
        margin: 0;
        min-height: 100vh;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        background: var(--background);
        color: var(--foreground);
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        padding: 24px;
        box-sizing: border-box;
      }}
      body::before {{
        content: "";
        position: fixed;
        inset: 0;
        z-index: -1;
        pointer-events: none;
        opacity: 0.03;
        background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
      }}
      .wrapper {{
        position: relative;
        width: min(100%, 420px);
      }}
      .glow-behind {{
        position: absolute;
        inset: -2px;
        background: var(--gradient-primary);
        filter: blur(24px);
        opacity: 0.4;
        border-radius: var(--radius);
        z-index: 0;
        animation: pulse 4s cubic-bezier(0.4, 0, 0.6, 1) infinite;
      }}
      @keyframes pulse {{
        0%, 100% {{ opacity: 0.3; }}
        50% {{ opacity: 0.5; }}
      }}
      .card {{
        position: relative;
        z-index: 1;
        background: var(--surface);
        border: 1px solid var(--border-clr);
        border-radius: var(--radius);
        padding: 40px 32px;
        text-align: center;
        box-shadow: 0 20px 40px rgba(0, 0, 0, 0.04);
      }}
      .icon-success {{
        width: 64px;
        height: 64px;
        margin: 0 auto 24px auto;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 32px;
        font-weight: bold;
        background: rgba(16, 185, 129, 0.1);
        color: #10b981;
        border: 1px solid rgba(16, 185, 129, 0.2);
      }}
      .icon-error {{
        width: 64px;
        height: 64px;
        margin: 0 auto 24px auto;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 32px;
        font-weight: bold;
        background: rgba(220, 38, 38, 0.1);
        color: #dc2626;
        border: 1px solid rgba(220, 38, 38, 0.2);
      }}
      h1 {{
        margin: 0 0 12px;
        font-size: 1.5rem;
        font-weight: 600;
        letter-spacing: -0.02em;
        color: {accent_color};
      }}
      p {{
        margin: 0 0 24px;
        line-height: 1.6;
        color: var(--muted);
        font-size: 1rem;
      }}
      .btn {{
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding: 12px 24px;
        border-radius: 9999px;
        font-weight: 500;
        font-size: 0.95rem;
        color: #fff;
        background: var(--foreground);
        text-decoration: none;
        transition: all 0.2s ease;
        border: none;
        cursor: pointer;
        width: 100%;
        box-sizing: border-box;
      }}
      .btn:hover {{
        transform: translateY(-2px);
        box-shadow: 0 8px 16px rgba(0,0,0,0.1);
        background: #27272a;
      }}
    </style>
  </head>
  <body>
    <div class="wrapper">
      <div class="glow-behind"></div>
      <main class="card">
        {icon_html}
        <h1>{headline}</h1>
        <p>{safe_message}</p>
        <button class="btn" onclick="window.close()">Đóng tab này</button>
      </main>
    </div>
  </body>
</html>"""
    return HTMLResponse(content=html, status_code=status_code)


def _build_redirect_uri(request: Request, provider: OAuthProvider) -> str:
    return str(request.url_for("oauth_callback", provider=provider))


def _build_authorization_url(
    provider: OAuthProvider,
    client_id: str,
    redirect_uri: str,
    state: str,
) -> str:
    if provider == "google":
        query = urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": "openid email profile",
                "state": state,
                "access_type": "offline",
                "prompt": "consent",
            }
        )
        return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"

    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "read:user user:email",
            "state": state,
        }
    )
    return f"https://github.com/login/oauth/authorize?{query}"


async def _exchange_google_code_for_profile(
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
) -> dict[str, str]:
    timeout = httpx.Timeout(15.0, connect=8.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        token_response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
        )
        token_payload = token_response.json() if token_response.content else {}
        access_token = token_payload.get("access_token")
        if token_response.status_code >= 400 or not access_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Google OAuth token exchange failed",
            )

        profile_response = await client.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        profile_payload = profile_response.json() if profile_response.content else {}
        if profile_response.status_code >= 400:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to fetch Google profile",
            )

    email = _normalize_email(str(profile_payload.get("email") or ""))
    email_verified = bool(profile_payload.get("email_verified"))
    provider_account_id = str(profile_payload.get("sub") or "").strip()
    display_name = str(profile_payload.get("name") or "").strip()
    avatar_url = str(profile_payload.get("picture") or "").strip()

    if not provider_account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google profile is missing provider account ID",
        )
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google account is missing an email address",
        )
    if not email_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google email must be verified before signing in",
        )

    return {
        "provider_account_id": provider_account_id,
        "email": email,
        "name": display_name or email.split("@")[0],
        "avatar_url": avatar_url,
    }


async def _exchange_github_code_for_profile(
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
) -> dict[str, str]:
    timeout = httpx.Timeout(15.0, connect=8.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        token_response = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
        token_payload = token_response.json() if token_response.content else {}
        access_token = token_payload.get("access_token")
        if token_response.status_code >= 400 or not access_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="GitHub OAuth token exchange failed",
            )

        api_headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        profile_response = await client.get("https://api.github.com/user", headers=api_headers)
        profile_payload = profile_response.json() if profile_response.content else {}
        if profile_response.status_code >= 400:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to fetch GitHub profile",
            )

        email = _normalize_email(str(profile_payload.get("email") or ""))
        if not email:
            emails_response = await client.get("https://api.github.com/user/emails", headers=api_headers)
            emails_payload = emails_response.json() if emails_response.content else []
            if emails_response.status_code < 400 and isinstance(emails_payload, list):
                preferred = None
                for item in emails_payload:
                    if isinstance(item, dict) and item.get("verified") and item.get("primary"):
                        preferred = item
                        break
                if preferred is None:
                    for item in emails_payload:
                        if isinstance(item, dict) and item.get("verified"):
                            preferred = item
                            break
                if preferred is None and emails_payload:
                    first_item = emails_payload[0]
                    if isinstance(first_item, dict):
                        preferred = first_item
                if isinstance(preferred, dict):
                    email = _normalize_email(str(preferred.get("email") or ""))

    provider_account_id = str(
        profile_payload.get("id")
        or profile_payload.get("node_id")
        or ""
    ).strip()
    display_name = str(
        profile_payload.get("name")
        or profile_payload.get("login")
        or ""
    ).strip()
    avatar_url = str(profile_payload.get("avatar_url") or "").strip()

    if not provider_account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="GitHub profile is missing provider account ID",
        )
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="GitHub account is missing an email address",
        )

    return {
        "provider_account_id": provider_account_id,
        "email": email,
        "name": display_name or email.split("@")[0],
        "avatar_url": avatar_url,
    }


async def _fetch_oauth_profile(
    provider: OAuthProvider,
    code: str,
    redirect_uri: str,
    credentials: dict[str, str],
) -> dict[str, str]:
    if provider == "google":
        return await _exchange_google_code_for_profile(
            code=code,
            redirect_uri=redirect_uri,
            client_id=credentials["client_id"],
            client_secret=credentials["client_secret"],
        )

    return await _exchange_github_code_for_profile(
        code=code,
        redirect_uri=redirect_uri,
        client_id=credentials["client_id"],
        client_secret=credentials["client_secret"],
    )


def _resolve_user_from_oauth(
    db: Session,
    provider: OAuthProvider,
    provider_account_id: str,
    email: str,
    display_name: str,
    avatar_url: str = "",
) -> User:
    linked_account = db.query(OAuthAccount).filter(
        OAuthAccount.provider == provider,
        OAuthAccount.provider_account_id == provider_account_id,
    ).first()

    if linked_account and linked_account.user:
        user = linked_account.user
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Inactive account",
            )
        linked_account.email = email
        linked_account.avatar_url = avatar_url or None
        if display_name:
            user.name = display_name
        user.last_login = datetime.now(timezone.utc)
        return user

    user = db.query(User).filter(User.email == email).first()
    if user and not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive account",
        )

    if not user:
        username = _build_unique_username(db, display_name or email.split("@")[0])
        user = User(
            email=email,
            username=username,
            name=display_name or username,
            hashed_password=None,
            is_active=True,
        )
        db.add(user)
        db.flush()
    elif display_name and user.name in ("", user.username, "PigTex User"):
        user.name = display_name

    oauth_link = OAuthAccount(
        user_id=user.id,
        provider=provider,
        provider_account_id=provider_account_id,
        email=email,
        avatar_url=avatar_url or None,
    )
    db.add(oauth_link)
    user.last_login = datetime.now(timezone.utc)
    return user


def _build_user_response_payload(
    current_user: User,
    oauth_account: OAuthAccount | None,
) -> dict[str, Any]:
    oauth_provider: str | None = None
    avatar_url: str | None = None

    if oauth_account:
        provider = (oauth_account.provider or "").strip().lower()
        oauth_provider = provider or None
        avatar_candidate = (oauth_account.avatar_url or "").strip()
        if avatar_candidate:
            avatar_url = avatar_candidate
        elif provider == "github":
            provider_account_id = (oauth_account.provider_account_id or "").strip()
            if provider_account_id.isdigit():
                avatar_url = f"https://avatars.githubusercontent.com/u/{provider_account_id}?v=4"

    return {
        "id": current_user.id,
        "email": current_user.email,
        "username": current_user.username,
        "plan": current_user.plan,
        "role": current_user.role or "user",
        "is_admin": (current_user.role or "user") in {ROLE_ADMIN, ROLE_SUPER_ADMIN},
        "permissions": get_user_permissions(current_user),
        "is_active": current_user.is_active,
        "created_at": current_user.created_at,
        "last_login": current_user.last_login,
        "has_password": bool((current_user.hashed_password or "").strip()),
        "oauth_provider": oauth_provider,
        "avatar_url": avatar_url,
    }


def _issue_user_access_token(user: User) -> str:
    return create_access_token(
        data={"sub": user.id},
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
    )


@router.get("/oauth/providers")
async def get_oauth_providers() -> dict[str, bool]:
    return {
        "google": _get_provider_credentials("google") is not None,
        "github": _get_provider_credentials("github") is not None,
    }


@router.api_route("/oauth/{provider}/start", methods=["GET", "POST"])
async def start_oauth(
    provider: OAuthProvider,
    request: Request,
) -> dict[str, Any]:
    credentials = _get_provider_credentials(provider)
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{provider.capitalize()} OAuth is not configured",
        )

    state = secrets.token_urlsafe(32)
    now_ts = time.time()
    ttl_seconds = _oauth_state_pending_ttl_seconds()
    expires_at = now_ts + ttl_seconds
    redirect_uri = _build_redirect_uri(request, provider)
    auth_url = _build_authorization_url(
        provider=provider,
        client_id=credentials["client_id"],
        redirect_uri=redirect_uri,
        state=state,
    )

    _store_oauth_state(state, {
        "provider": provider,
        "status": "pending",
        "created_at": now_ts,
        "expires_at": expires_at,
        "completed_at": 0.0,
    }, ttl_seconds)

    return {
        "provider": provider,
        "auth_url": auth_url,
        "state": state,
        "expires_in": ttl_seconds,
    }


@router.get("/oauth/{provider}/status")
async def oauth_status(
    provider: OAuthProvider,
    state: str = Query(..., min_length=20, max_length=256),
) -> dict[str, Any]:
    payload = get_oauth_state(state)
    if not payload or payload.get("provider") != provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OAuth session not found or expired",
        )

    state_status = payload.get("status")
    if state_status == "pending":
        return {"status": "pending"}

    if state_status == "success":
        access_token = payload.get("access_token")
        if not access_token:
            delete_oauth_state(state)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="OAuth session completed without token",
            )
        delete_oauth_state(state)
        return {
            "status": "success",
            "access_token": access_token,
            "token_type": "bearer",
        }

    error_message = str(payload.get("error") or "OAuth login failed")
    delete_oauth_state(state)
    return {
        "status": "error",
        "error": error_message,
    }


@router.get(
    "/oauth/{provider}/callback",
    response_class=HTMLResponse,
    name="oauth_callback",
)
async def oauth_callback(
    provider: OAuthProvider,
    request: Request,
    db: Session = Depends(get_db),
    state: str | None = Query(default=None),
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
):
    if not state:
        return _render_oauth_result_page(False, "Missing OAuth state.")

    payload = get_oauth_state(state)
    if not payload or payload.get("provider") != provider:
        return _render_oauth_result_page(
            False,
            "OAuth session expired. Please return to PigTex and try again.",
        )

    if payload.get("status") != "pending":
        return _render_oauth_result_page(
            False,
            "OAuth session was already handled. Start a new login from PigTex.",
        )

    if error:
        provider_error = error_description or error.replace("_", " ")
        completed_payload = _complete_oauth_state(
            state,
            payload,
            status_value="error",
            error_message=f"{provider.capitalize()} OAuth failed: {provider_error}",
        )
        return _render_oauth_result_page(False, str(completed_payload["error"]))

    if not code:
        completed_payload = _complete_oauth_state(
            state,
            payload,
            status_value="error",
            error_message="OAuth callback is missing authorization code.",
        )
        return _render_oauth_result_page(False, str(completed_payload["error"]))

    credentials = _get_provider_credentials(provider)
    if not credentials:
        completed_payload = _complete_oauth_state(
            state,
            payload,
            status_value="error",
            error_message=f"{provider.capitalize()} OAuth is not configured",
        )
        return _render_oauth_result_page(False, str(completed_payload["error"]))

    redirect_uri = _build_redirect_uri(request, provider)

    try:
        profile = await _fetch_oauth_profile(
            provider=provider,
            code=code,
            redirect_uri=redirect_uri,
            credentials=credentials,
        )
        user = _resolve_user_from_oauth(
            db=db,
            provider=provider,
            provider_account_id=profile["provider_account_id"],
            email=profile["email"],
            display_name=profile["name"],
            avatar_url=profile.get("avatar_url", ""),
        )
        db.commit()
        db.refresh(user)
    except HTTPException as exc:
        db.rollback()
        _complete_oauth_state(
            state,
            payload,
            status_value="error",
            error_message=str(exc.detail),
        )
        return _render_oauth_result_page(False, str(exc.detail))
    except IntegrityError:
        db.rollback()
        logger.exception("Integrity error while handling OAuth callback")
        completed_payload = _complete_oauth_state(
            state,
            payload,
            status_value="error",
            error_message="OAuth login failed due to account linking conflict.",
        )
        return _render_oauth_result_page(False, str(completed_payload["error"]))
    except Exception:
        db.rollback()
        logger.exception("Unexpected OAuth callback error")
        completed_payload = _complete_oauth_state(
            state,
            payload,
            status_value="error",
            error_message="Unexpected OAuth error. Please try again.",
        )
        return _render_oauth_result_page(False, str(completed_payload["error"]))

    await _maybe_upsert_texapi_partner_customer(user)
    access_token = _issue_user_access_token(user)
    _complete_oauth_state(
        state,
        payload,
        status_value="success",
        access_token=access_token,
    )
    return _render_oauth_result_page(
        True,
        f"{provider.capitalize()} login completed. You can return to PigTex now.",
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    user_data: UserCreate,
    db: Session = Depends(get_db),
    _: None = Depends(auth_register_rate_limit),
):
    """Register a new user"""
    if db.query(User).filter(User.email == user_data.email).first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    if db.query(User).filter(User.username == user_data.username).first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already taken",
        )

    hashed_password = get_password_hash(user_data.password)
    new_user = User(
        email=user_data.email,
        username=user_data.username,
        name=user_data.username.strip() or user_data.email.split("@")[0],
        hashed_password=hashed_password,
    )

    try:
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
        await _maybe_upsert_texapi_partner_customer(new_user)
        return new_user
    except IntegrityError as exc:
        db.rollback()
        error_text = str(getattr(exc, "orig", exc)).lower()
        if "email" in error_text:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered",
            ) from exc
        if "username" in error_text:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already taken",
            ) from exc

        logger.exception("Unexpected integrity error on auth register")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create account due to database constraint mismatch",
        ) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to synchronize account with PigTex database",
        ) from exc


@router.post("/login", response_model=Token)
async def login(
    user_data: UserLogin,
    db: Session = Depends(get_db),
    _: None = Depends(auth_login_rate_limit),
):
    """Login and get access token"""
    user = db.query(User).filter(User.email == user_data.email).first()

    if not user or not user.hashed_password or not verify_password(user_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive account",
        )

    user.last_login = datetime.now(timezone.utc)
    db.commit()

    access_token = _issue_user_access_token(user)
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get current user info"""
    oauth_account = (
        db.query(OAuthAccount)
        .filter(OAuthAccount.user_id == current_user.id)
        .order_by(OAuthAccount.updated_at.desc(), OAuthAccount.created_at.desc())
        .first()
    )
    return _build_user_response_payload(current_user, oauth_account)
