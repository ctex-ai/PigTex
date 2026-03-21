"""
Image Upload & Serve API Routes
=================================
Handles image uploads for multimodal chat.
Persists images to local disk for conversation history.

POST   /api/images/upload   - Upload image(s), returns base64 + persisted URLs
GET    /api/images/serve/{image_id}  - Serve a saved image
"""

import base64
import logging
import os
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..models import User
from ..local_storage.scope import resolve_local_owner_id
from .auth_utils import get_current_user

router = APIRouter(prefix="/images", tags=["Images"])
logger = logging.getLogger(__name__)

# Max 10MB per image
MAX_IMAGE_SIZE = 10 * 1024 * 1024
# Max 5 images per upload
MAX_IMAGES_PER_UPLOAD = 5

ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/bmp",
}

MIME_TO_EXT = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/bmp": "bmp",
}

EXT_TO_MIME = {
    "jpg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
}

# Storage directory (relative to backend root)
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "uploads", "images")


def _sanitize_path_component(value: str, field_name: str) -> str:
    candidate = os.path.basename((value or "").strip())
    if not candidate or candidate in {".", ".."}:
        raise ValueError(f"Invalid {field_name}")
    return candidate


def _get_user_upload_dir(user_id: str) -> str:
    safe_user_id = _sanitize_path_component(resolve_local_owner_id(user_id), "user id")
    return os.path.join(UPLOAD_DIR, safe_user_id)


def _ensure_upload_dir(user_id: Optional[str] = None):
    """Create upload directory if it doesn't exist."""
    target_dir = _get_user_upload_dir(user_id) if user_id else UPLOAD_DIR
    os.makedirs(target_dir, exist_ok=True)


def _normalize_declared_mime_type(content_type: str) -> str:
    normalized = (content_type or "").strip().lower()
    if normalized == "image/jpg":
        return "image/jpeg"
    return normalized


def detect_image_mime_type(data: bytes) -> Optional[str]:
    """Best-effort signature sniffing for supported raster image types."""
    try:
        if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if len(data) >= 3 and data[:3] == b"GIF":
            return "image/gif"
        if len(data) >= 2 and data[:2] == b"\xff\xd8":
            return "image/jpeg"
        if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "image/webp"
        if len(data) >= 2 and data[:2] == b"BM":
            return "image/bmp"
    except Exception:
        return None
    return None


def build_image_serve_url(user_id: str, filename: str) -> str:
    safe_user_id = _sanitize_path_component(resolve_local_owner_id(user_id), "user id")
    safe_filename = _sanitize_path_component(filename, "filename")
    return f"/api/images/serve/{safe_user_id}/{safe_filename}"


def resolve_owned_image_path(user_id: str, image_path: str) -> Optional[Path]:
    """Resolve a serve path to a machine-scoped local image."""
    try:
        safe_user_id = _sanitize_path_component(resolve_local_owner_id(user_id), "user id")
    except ValueError:
        return None

    raw_parts = [part for part in Path((image_path or "").strip()).parts if part not in {"", "."}]
    if not raw_parts or any(part == ".." for part in raw_parts):
        return None

    candidate_paths: list[Path] = []

    if len(raw_parts) == 1:
        try:
            safe_filename = _sanitize_path_component(raw_parts[0], "filename")
        except ValueError:
            return None
        candidate_paths.append(Path(_get_user_upload_dir(safe_user_id)) / safe_filename)
        # Legacy fallback for images saved before machine-scoped local storage.
        candidate_paths.append(Path(UPLOAD_DIR) / safe_filename)
        for legacy_dir in Path(UPLOAD_DIR).iterdir() if Path(UPLOAD_DIR).exists() else []:
            if not legacy_dir.is_dir():
                continue
            if legacy_dir.name == safe_user_id:
                continue
            candidate_paths.append(legacy_dir / safe_filename)
    elif len(raw_parts) == 2:
        try:
            path_user_id = _sanitize_path_component(raw_parts[0], "user id")
            safe_filename = _sanitize_path_component(raw_parts[1], "filename")
        except ValueError:
            return None
        candidate_paths.append(Path(UPLOAD_DIR) / path_user_id / safe_filename)
        candidate_paths.append(Path(_get_user_upload_dir(safe_user_id)) / safe_filename)
    else:
        return None

    for candidate in candidate_paths:
        if candidate.is_file():
            return candidate

    return None


def load_owned_image_from_serve_path(user_id: str, image_path: str) -> Optional[tuple[bytes, str, str]]:
    resolved_path = resolve_owned_image_path(user_id, image_path)
    if resolved_path is None:
        return None

    try:
        content = resolved_path.read_bytes()
    except OSError:
        return None

    ext = resolved_path.suffix.lower().lstrip(".")
    media_type = EXT_TO_MIME.get(ext) or detect_image_mime_type(content) or "image/png"
    return content, media_type, resolved_path.name


class ImageUploadResponse(BaseModel):
    id: str
    filename: str
    mime_type: str
    size: int
    base64_data: str       # data:image/png;base64,... (for immediate use)
    serve_url: str         # /api/images/serve/{id}.ext (for persistence)
    width: Optional[int] = None
    height: Optional[int] = None


class MultiImageUploadResponse(BaseModel):
    images: List[ImageUploadResponse]
    count: int


def _get_image_dimensions(data: bytes, mime_type: str) -> tuple[Optional[int], Optional[int]]:
    """Try to extract image dimensions without heavy dependencies."""
    try:
        if mime_type == "image/png" and len(data) >= 24:
            if data[:8] == b'\x89PNG\r\n\x1a\n':
                w = int.from_bytes(data[16:20], 'big')
                h = int.from_bytes(data[20:24], 'big')
                return w, h

        if mime_type in ("image/jpeg", "image/jpg") and len(data) >= 2:
            if data[0:2] == b'\xff\xd8':
                i = 2
                while i < len(data) - 1:
                    if data[i] != 0xFF:
                        break
                    marker = data[i + 1]
                    if marker in (0xC0, 0xC1, 0xC2):
                        if i + 9 < len(data):
                            h = int.from_bytes(data[i + 5:i + 7], 'big')
                            w = int.from_bytes(data[i + 7:i + 9], 'big')
                            return w, h
                        break
                    if i + 3 < len(data):
                        seg_len = int.from_bytes(data[i + 2:i + 4], 'big')
                        i += 2 + seg_len
                    else:
                        break

        if mime_type == "image/gif" and len(data) >= 10:
            if data[:3] == b'GIF':
                w = int.from_bytes(data[6:8], 'little')
                h = int.from_bytes(data[8:10], 'little')
                return w, h

        if mime_type == "image/webp" and len(data) >= 30:
            if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
                if data[12:16] == b'VP8 ' and len(data) >= 30:
                    w = int.from_bytes(data[26:28], 'little') & 0x3FFF
                    h = int.from_bytes(data[28:30], 'little') & 0x3FFF
                    return w, h
    except Exception:
        pass
    return None, None


def save_image_to_disk(image_id: str, data: bytes, ext: str) -> str:
    """Save image bytes to disk. Returns the filename."""
    _ensure_upload_dir()
    filename = f"{image_id}.{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(data)
    return filename


def save_image_to_user_disk(image_id: str, data: bytes, ext: str, user_id: str) -> str:
    """Save image bytes to the current user's private image directory."""
    user_dir = _get_user_upload_dir(user_id)
    _ensure_upload_dir(user_id)
    filename = f"{image_id}.{ext}"
    filepath = os.path.join(user_dir, filename)
    with open(filepath, "wb") as f:
        f.write(data)
    return filename


def save_base64_image_to_disk(image_id: str, base64_data_url: str, user_id: str) -> Optional[str]:
    """
    Save a base64 data URL to disk.
    Returns the serve URL path, or None on failure.
    """
    try:
        # Parse data:image/png;base64,... format
        if not base64_data_url.startswith("data:"):
            return None
        header, b64_str = base64_data_url.split(",", 1)
        # Extract mime from header: data:image/png;base64
        mime_part = _normalize_declared_mime_type(header.split(":")[1].split(";")[0])
        data = base64.b64decode(b64_str)
        detected_mime = detect_image_mime_type(data)
        if detected_mime not in {"image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp"}:
            return None
        if mime_part and mime_part != detected_mime:
            return None

        ext = MIME_TO_EXT.get(detected_mime, "png")
        filename = save_image_to_user_disk(image_id, data, ext, user_id)
        return build_image_serve_url(user_id, filename)
    except Exception as e:
        logger.warning("Failed to save base64 image %s: %s", image_id, e)
        return None


@router.post("/upload", response_model=MultiImageUploadResponse)
async def upload_images(
    files: List[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
):
    """
    Upload one or more images for use in multimodal chat.
    Images are saved to local disk AND returned as base64 for immediate use.
    """
    if len(files) > MAX_IMAGES_PER_UPLOAD:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum {MAX_IMAGES_PER_UPLOAD} images per upload.",
        )

    results: list[ImageUploadResponse] = []

    for upload_file in files:
        data = await upload_file.read()
        if len(data) > MAX_IMAGE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Image too large: {len(data)} bytes. Maximum: {MAX_IMAGE_SIZE} bytes.",
            )

        if len(data) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Empty image file.",
            )

        declared_content_type = _normalize_declared_mime_type(upload_file.content_type or "")
        detected_content_type = detect_image_mime_type(data)
        if detected_content_type not in {"image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unsupported image content.",
            )
        if declared_content_type and declared_content_type not in ALLOWED_MIME_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported image type: {declared_content_type}.",
            )
        if declared_content_type and declared_content_type != detected_content_type:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file content does not match the declared image type.",
            )

        # Generate ID and save to disk
        image_id = str(uuid.uuid4())
        ext = MIME_TO_EXT.get(detected_content_type, "png")
        saved_filename = save_image_to_user_disk(image_id, data, ext, current_user.id)
        serve_url = build_image_serve_url(current_user.id, saved_filename)

        # Also base64 encode for immediate use
        b64 = base64.b64encode(data).decode("ascii")
        data_url = f"data:{detected_content_type};base64,{b64}"

        width, height = _get_image_dimensions(data, detected_content_type)
        filename = upload_file.filename or f"image_{image_id[:8]}.{ext}"

        results.append(ImageUploadResponse(
            id=image_id,
            filename=filename,
            mime_type=detected_content_type,
            size=len(data),
            base64_data=data_url,
            serve_url=serve_url,
            width=width,
            height=height,
        ))

        logger.info(
            "Image uploaded user_id=%s filename=%s size=%d saved=%s",
            current_user.id, filename, len(data), serve_url,
        )

    return MultiImageUploadResponse(images=results, count=len(results))


@router.get("/serve/{image_path:path}")
async def serve_image(
    image_path: str,
    current_user: User = Depends(get_current_user),
):
    """Serve a saved image owned by the current authenticated user."""
    resolved_path = resolve_owned_image_path(current_user.id, image_path)
    if resolved_path is None:
        raise HTTPException(status_code=404, detail="Image not found")

    # Determine MIME type from extension
    ext = resolved_path.suffix.lower().lstrip(".")
    media_type = EXT_TO_MIME.get(ext, "image/png")

    return FileResponse(
        str(resolved_path),
        media_type=media_type,
        headers={
            "Cache-Control": "private, no-store, max-age=0",
            "X-Content-Type-Options": "nosniff",
        }
    )
