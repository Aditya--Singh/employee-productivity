"""Upload validation shared by all tabs: file type, content signature and size."""

import io
import os
import zipfile
from dataclasses import dataclass

from fastapi import HTTPException, UploadFile

from app.core.config import get_settings

DEFAULT_ALLOWED = frozenset({".pdf", ".docx", ".txt"})
CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class ValidatedFile:
    filename: str
    ext: str
    content: bytes


def _safe_name(name: str | None) -> str:
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    return base or "upload"


def _content_matches(ext: str, content: bytes) -> bool:
    if ext == ".pdf":
        return content.startswith(b"%PDF-")
    if ext == ".docx":
        if not content.startswith(b"PK\x03\x04"):
            return False
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                return "word/document.xml" in zf.namelist()
        except zipfile.BadZipFile:
            return False
    if ext == ".txt":
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            return False
        return True
    return False


async def validate_upload(
    file: UploadFile,
    allowed: set[str] | frozenset[str] = DEFAULT_ALLOWED,
    max_bytes: int | None = None,
) -> ValidatedFile:
    """Read and validate an upload. Raises HTTPException 400/413/415 on failure."""
    limit = max_bytes if max_bytes is not None else get_settings().max_upload_bytes
    filename = _safe_name(file.filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in allowed:
        raise HTTPException(
            status_code=415,
            detail=f"File type '{ext or 'none'}' is not allowed. Allowed: {', '.join(sorted(allowed))}",
        )

    buf = bytearray()
    while chunk := await file.read(CHUNK_SIZE):
        buf.extend(chunk)
        if len(buf) > limit:
            raise HTTPException(
                status_code=413,
                detail=f"File is larger than the {limit // (1024 * 1024)} MB limit.",
            )
    content = bytes(buf)
    if not content:
        raise HTTPException(status_code=400, detail="File is empty.")
    if not _content_matches(ext, content):
        raise HTTPException(
            status_code=415,
            detail=f"File content does not match its '{ext}' extension.",
        )
    return ValidatedFile(filename=filename, ext=ext, content=content)
