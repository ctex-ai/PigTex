import logging
import time
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

_LEGACY_COMPATIBLE_ALEMBIC_REVISIONS: dict[str, str] = {
    "20260319_0010": "20260314_0009",
    "20260321_0011": "20260314_0009",
    "20260321_0012": "20260314_0009",
}

def _build_database_url() -> str | URL:
    raw_url = (settings.database_url or "").strip()
    if raw_url:
        # Allow sharing a plain mysql:// URL across services.
        if raw_url.startswith("mysql://"):
            return raw_url.replace("mysql://", "mysql+pymysql://", 1)
        return raw_url

    # Build URL safely to handle special characters in password.
    return URL.create(
        drivername="mysql+pymysql",
        username=settings.db_user,
        password=settings.db_password,
        host=settings.db_host,
        port=settings.db_port,
        database=settings.db_name,
    )


database_url = _build_database_url()

engine = create_engine(
    database_url,
    pool_pre_ping=True,
    pool_recycle=3600,
    pool_size=max(1, int(settings.db_pool_size)),
    max_overflow=max(0, int(settings.db_pool_max_overflow)),
    pool_timeout=max(1.0, float(settings.db_pool_timeout_seconds)),
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_database_url_string() -> str:
    if isinstance(database_url, URL):
        return database_url.render_as_string(hide_password=False)
    return str(database_url)


def check_database_connection() -> None:
    """Raise if database connection is not available."""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


def wait_for_database(
    max_retries: int | None = None,
    retry_interval_seconds: float | None = None,
) -> None:
    """
    Wait for database readiness with bounded retries.
    Raises RuntimeError if DB is still unreachable after retries.
    """
    retries = max_retries if max_retries is not None else settings.db_startup_max_retries
    interval = (
        retry_interval_seconds
        if retry_interval_seconds is not None
        else settings.db_startup_retry_interval_seconds
    )
    retries = max(1, int(retries))
    interval = max(0.1, float(interval))

    for attempt in range(1, retries + 1):
        try:
            check_database_connection()
            if attempt > 1:
                logger.info("Database became ready after %s attempt(s)", attempt)
            return
        except Exception as exc:
            if attempt >= retries:
                raise RuntimeError(
                    f"Database is unavailable after {attempt} attempt(s): {exc}"
                ) from exc
            logger.warning(
                "Database not ready (attempt %s/%s): %s. Retrying in %.1fs",
                attempt,
                retries,
                exc,
                interval,
            )
            time.sleep(interval)


def _alembic_config_path() -> Path:
    return Path(__file__).resolve().parent.parent / "alembic.ini"


def get_alembic_head_revisions() -> list[str]:
    """Return alembic head revisions from local migration scripts."""
    cfg_path = _alembic_config_path()
    if not cfg_path.exists():
        logger.warning("Alembic config not found at %s", cfg_path)
        return []

    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config(str(cfg_path))
        script = ScriptDirectory.from_config(cfg)
        return list(script.get_heads())
    except Exception as exc:
        logger.warning("Failed to load alembic heads: %s", exc)
        return []


def get_database_alembic_revisions() -> list[str]:
    """Return current alembic revisions recorded in DB."""
    with engine.connect() as conn:
        inspector = inspect(conn)
        if not inspector.has_table("alembic_version"):
            return []

        rows = conn.execute(text("SELECT version_num FROM alembic_version")).fetchall()
        revisions = [str(row[0]) for row in rows if row and row[0]]
        return revisions


def _canonicalize_alembic_revisions(revisions: list[str]) -> list[str]:
    canonical: list[str] = []
    for revision in revisions:
        normalized = _LEGACY_COMPATIBLE_ALEMBIC_REVISIONS.get(revision, revision)
        if normalized and normalized not in canonical:
            canonical.append(normalized)
    return canonical


def ensure_database_schema_is_current(require_head: bool) -> None:
    """
    Ensure DB schema is at alembic head.
    - If require_head is True: raise RuntimeError on mismatch.
    - Else: log warning and continue.
    """
    head_revisions = get_alembic_head_revisions()
    if not head_revisions:
        message = "No alembic head revision found; cannot verify schema migration state"
        if require_head:
            raise RuntimeError(message)
        logger.warning(message)
        return

    db_revisions = get_database_alembic_revisions()
    if not db_revisions:
        message = (
            "Database has no alembic_version record. Run migrations before starting service."
        )
        if require_head:
            raise RuntimeError(message)
        logger.warning(message)
        return

    canonical_head_revisions = _canonicalize_alembic_revisions(head_revisions)
    canonical_db_revisions = _canonicalize_alembic_revisions(db_revisions)

    if set(canonical_db_revisions) != set(canonical_head_revisions):
        message = (
            f"Database revision mismatch. current={db_revisions}, expected_head={head_revisions}"
        )
        if require_head:
            raise RuntimeError(message)
        logger.warning(message)
        return

    if canonical_db_revisions != db_revisions:
        logger.info(
            "Database is using legacy-compatible alembic revisions: raw=%s canonical=%s",
            db_revisions,
            canonical_db_revisions,
        )

    logger.info("Database schema revision is at head: %s", db_revisions)


def get_db():
    """Dependency to get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
