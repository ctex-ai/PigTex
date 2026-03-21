import os
import uuid
from pathlib import Path

from .request_scope import get_request_device_scope_id


MACHINE_SCOPE_PREFIX = "machine-"
MACHINE_SCOPE_FILE = ".machine_scope_id"


def get_storage_root() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home()

    root = base / ".pigtex"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _normalize_scope_id(value: str | None) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    return candidate


def get_machine_scope_id() -> str:
    root = get_storage_root()
    scope_file = root / MACHINE_SCOPE_FILE

    if scope_file.exists():
        existing = _normalize_scope_id(scope_file.read_text(encoding="utf-8", errors="ignore"))
        if existing:
            return existing

    scope_id = f"{MACHINE_SCOPE_PREFIX}{uuid.uuid4()}"
    scope_file.write_text(scope_id, encoding="utf-8")
    return scope_id


def resolve_local_owner_id(user_id: str | None = None) -> str:
    request_device_scope_id = get_request_device_scope_id()
    if request_device_scope_id:
        return request_device_scope_id

    candidate = _normalize_scope_id(user_id)
    if candidate:
        return candidate

    return get_machine_scope_id()


def iter_legacy_storage_dirs(current_scope_id: str, allowed_owner_ids: set[str] | None = None):
    root = get_storage_root()
    for child in root.iterdir():
        if not child.is_dir():
            continue
        if child.name == current_scope_id:
            continue
        if child.name.startswith("."):
            continue
        if allowed_owner_ids is not None and child.name not in allowed_owner_ids:
            continue
        if (child / "local.db").exists() or (child / "brain").exists():
            yield child
