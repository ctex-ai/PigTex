from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import logging
import uuid
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import get_settings
from .database import (
    engine,
    Base,
    check_database_connection,
    wait_for_database,
    ensure_database_schema_is_current,
)
from .local_storage import (
    LocalDatabaseEncryptionUnavailableError,
    LocalDatabaseLockedError,
)
from .local_storage.request_scope import (
    LOCAL_DEVICE_SCOPE_HEADER,
    LOCAL_LEGACY_ACCOUNTS_HEADER,
    bind_request_local_scope,
    parse_legacy_account_ids_header,
    reset_request_local_scope,
)
from .oauth_state import (
    OAuthStateStoreUnavailableError,
    ensure_oauth_state_ready,
    get_oauth_state_backend_status,
)
from .routes import auth, user, memory
from .routes import v1_api
from .routes import cloud_backup
from .routes import sync_billing
from .routes import texapi_partner
from .routes import images as images_routes
from .routes import files as files_routes
from .routes import skill_foundry as skill_foundry_routes
from .routes import learning as learning_routes
from .idempotency import IdempotencyMiddleware

settings = get_settings()
logger = logging.getLogger("pigtex.api")

# Avoid implicit schema mutation in production by default.
if settings.auto_create_db_schema:
    Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="PigTex API",
    description="AI-powered assistant backend with Super Context Memory",
    version="0.2.0"
)

# Idempotency middleware (for write requests carrying Idempotency-Key).
app.add_middleware(IdempotencyMiddleware)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Conversation-ID", "X-Request-ID"],  # Allow frontend to read custom headers
)


@app.middleware("http")
async def local_scope_middleware(request: Request, call_next):
    tokens = bind_request_local_scope(
        request.headers.get(LOCAL_DEVICE_SCOPE_HEADER),
        parse_legacy_account_ids_header(request.headers.get(LOCAL_LEGACY_ACCOUNTS_HEADER)),
    )
    try:
        return await call_next(request)
    finally:
        reset_request_local_scope(tokens)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response

# Include routers
app.include_router(auth.router, prefix="/api")
app.include_router(user.router, prefix="/api")
app.include_router(memory.router, prefix="/api")
app.include_router(cloud_backup.router, prefix="/api")
app.include_router(sync_billing.router, prefix="/api")
app.include_router(texapi_partner.router, prefix="/api")

# V1 API - OpenAI-compatible endpoints (provider-agnostic BYOK)
app.include_router(v1_api.router, prefix="/api")

# Image Upload API - Multimodal chat support
app.include_router(images_routes.router, prefix="/api")

# File Upload API - Document extraction for chat context
app.include_router(files_routes.router, prefix="/api")

# Skill Foundry API - offline prompt skill ingestion and runtime inspection
app.include_router(skill_foundry_routes.router, prefix="/api")

# Guided Learning API
app.include_router(learning_routes.router, prefix="/api")




@app.get("/")
async def root():
    return {
        "service": "PigTex Backend",
        "version": "0.1.0",
        "status": "running"
    }


@app.get("/api/health")
async def health():
    payload = {
        "status": "ok",
        "db": "ok",
        "oauth_state": get_oauth_state_backend_status(),
    }
    try:
        check_database_connection()
    except Exception:
        payload["status"] = "degraded"
        payload["db"] = "unavailable"

    oauth_required = bool(payload["oauth_state"].get("required"))
    oauth_healthy = bool(payload["oauth_state"].get("healthy"))
    if oauth_required and not oauth_healthy:
        payload["status"] = "degraded"

    if payload["status"] != "ok":
        return JSONResponse(status_code=503, content=payload)
    return payload


@app.on_event("startup")
async def startup_database_checks():
    wait_for_database()
    ensure_database_schema_is_current(settings.should_require_db_migration_head)
    ensure_oauth_state_ready()
    await v1_api.warmup_chat_client()


@app.on_event("shutdown")
async def shutdown_http_clients():
    await v1_api.close_chat_client()


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
    detail = exc.detail
    if isinstance(detail, dict):
        payload = {"detail": {**detail, "request_id": request_id}}
    else:
        payload = {
            "detail": detail,
            "request_id": request_id,
        }
    return JSONResponse(
        status_code=exc.status_code,
        content=payload,
        headers=exc.headers,
    )


@app.exception_handler(LocalDatabaseLockedError)
async def local_database_locked_handler(request: Request, exc: LocalDatabaseLockedError):
    request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
    return JSONResponse(
        status_code=423,
        content={
            "detail": str(exc),
            "request_id": request_id,
        },
    )


@app.exception_handler(LocalDatabaseEncryptionUnavailableError)
async def local_database_encryption_unavailable_handler(
    request: Request,
    exc: LocalDatabaseEncryptionUnavailableError,
):
    request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
    return JSONResponse(
        status_code=503,
        content={
            "detail": str(exc),
            "request_id": request_id,
        },
    )


@app.exception_handler(OAuthStateStoreUnavailableError)
async def oauth_state_store_unavailable_handler(
    request: Request,
    exc: OAuthStateStoreUnavailableError,
):
    request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
    return JSONResponse(
        status_code=503,
        content={
            "detail": str(exc),
            "request_id": request_id,
        },
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
    logger.exception(
        "Unhandled exception request_id=%s method=%s url=%s",
        request_id,
        request.method,
        request.url,
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "request_id": request_id,
        }
    )
