"""
File Upload & Extraction API Routes
===================================
Handles document uploads for chat context enrichment.

Supported formats (phase 1):
- .txt / .md
- .pdf
- .docx
"""

from __future__ import annotations

import io
import logging
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Callable, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel

from ..models import User
from .auth_utils import get_current_user

router = APIRouter(prefix="/files", tags=["Files"])
logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB
MAX_FILES_PER_UPLOAD = 5
MAX_EXTRACTED_CHARS = 40_000

TEXT_MIME_TYPES = {"text/plain", "text/markdown"}
PDF_MIME_TYPES = {"application/pdf"}
DOCX_MIME_TYPES = {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown", ".pdf", ".docx"}


class FileUploadResponse(BaseModel):
    id: str
    filename: str
    mime_type: str
    size: int
    extracted_text: str
    text_chars: int
    truncated: bool


class MultiFileUploadResponse(BaseModel):
    files: List[FileUploadResponse]
    count: int


def _normalize_text(text: str) -> str:
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars].rstrip(), True


def _extract_text_from_plain(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _extract_text_from_pdf_with_pdf_oxide(data: bytes) -> str:
    from pdf_oxide import PdfDocument

    temp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".pdf", delete=False) as temp_pdf:
            temp_pdf.write(data)
            temp_path = temp_pdf.name

        doc = PdfDocument(temp_path)
        parts: list[str] = []
        for page_index in range(doc.page_count()):
            parts.append(doc.extract_text(page_index) or "")
        return "\n\n".join(parts)
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                logger.warning("Failed to remove temporary PDF file: %s", temp_path)


def _extract_text_from_pdf(data: bytes) -> str:
    size = len(data)
    started_at = time.perf_counter()
    try:
        extracted = _extract_text_from_pdf_with_pdf_oxide(data)
        logger.info(
            "PDF extracted parser=%s size=%d chars=%d duration_ms=%.2f",
            "pdf_oxide",
            size,
            len(extracted),
            (time.perf_counter() - started_at) * 1000,
        )
        return extracted
    except Exception as exc:
        logger.warning(
            "PDF extraction failed parser=%s size=%d duration_ms=%.2f error=%s; falling back to pypdf",
            "pdf_oxide",
            size,
            (time.perf_counter() - started_at) * 1000,
            exc,
        )

    started_at = time.perf_counter()
    try:
        from pypdf import PdfReader
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="PDF parser is not available on server.",
        ) from exc

    reader = PdfReader(io.BytesIO(data))
    parts: list[str] = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    extracted = "\n\n".join(parts)
    logger.info(
        "PDF extracted parser=%s size=%d chars=%d duration_ms=%.2f",
        "pypdf",
        size,
        len(extracted),
        (time.perf_counter() - started_at) * 1000,
    )
    return extracted


def _extract_text_from_docx(data: bytes) -> str:
    try:
        from docx import Document
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="DOCX parser is not available on server.",
        ) from exc

    doc = Document(io.BytesIO(data))
    parts: list[str] = []

    for paragraph in doc.paragraphs:
        if paragraph.text:
            parts.append(paragraph.text)

    for table in doc.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text and cell.text.strip()]
            if cells:
                parts.append(" | ".join(cells))

    return "\n".join(parts)


def _resolve_extractor(content_type: str, filename: str) -> Optional[Callable[[bytes], str]]:
    normalized_mime = (content_type or "").split(";", 1)[0].strip().lower()
    extension = Path(filename or "").suffix.lower()

    if normalized_mime in TEXT_MIME_TYPES or extension in {".txt", ".md", ".markdown"}:
        return _extract_text_from_plain
    if normalized_mime in PDF_MIME_TYPES or extension == ".pdf":
        return _extract_text_from_pdf
    if normalized_mime in DOCX_MIME_TYPES or extension == ".docx":
        return _extract_text_from_docx
    return None


@router.post("/upload", response_model=MultiFileUploadResponse)
async def upload_files(
    files: List[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
):
    """Upload and extract text from document files for chat context."""
    if len(files) > MAX_FILES_PER_UPLOAD:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum {MAX_FILES_PER_UPLOAD} files per upload.",
        )

    results: list[FileUploadResponse] = []

    for upload_file in files:
        filename = (upload_file.filename or "").strip() or f"file_{uuid.uuid4().hex[:8]}"
        extension = Path(filename).suffix.lower()
        content_type = (upload_file.content_type or "").split(";", 1)[0].strip().lower()

        extractor = _resolve_extractor(content_type, filename)
        if extractor is None or (extension and extension not in SUPPORTED_EXTENSIONS):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported file type: {filename}",
            )

        data = await upload_file.read()
        size = len(data)

        if size == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Empty file: {filename}",
            )
        if size > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File too large: {filename}. Maximum {MAX_FILE_SIZE} bytes.",
            )

        try:
            raw_text = extractor(data)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to extract text from file: {filename}",
            ) from exc
        normalized_text = _normalize_text(raw_text)
        extracted_text, truncated = _truncate_text(normalized_text, MAX_EXTRACTED_CHARS)
        text_chars = len(normalized_text)

        if not extracted_text:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"No extractable text found in file: {filename}",
            )

        results.append(
            FileUploadResponse(
                id=f"file_{uuid.uuid4().hex[:12]}",
                filename=os.path.basename(filename),
                mime_type=content_type or "application/octet-stream",
                size=size,
                extracted_text=extracted_text,
                text_chars=text_chars,
                truncated=truncated,
            )
        )

        logger.info(
            "File extracted user_id=%s filename=%s mime=%s size=%d chars=%d truncated=%s",
            current_user.id,
            filename,
            content_type or "application/octet-stream",
            size,
            text_chars,
            truncated,
        )

    return MultiFileUploadResponse(files=results, count=len(results))
