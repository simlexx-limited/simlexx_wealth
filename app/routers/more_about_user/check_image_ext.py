import os
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, UploadFile

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
DOCUMENT_EXTENSIONS = IMAGE_EXTENSIONS | {".pdf"}
DEFAULT_MAX_SIZE_MB = 5


def detect_file_kind(header: bytes) -> Optional[str]:
    """Identify a file from its first bytes, ignoring whatever its name claims."""
    if header.startswith(b"\xff\xd8\xff"):
        return "image"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image"
    if header[:6] in (b"GIF87a", b"GIF89a"):
        return "image"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image"
    if header.startswith(b"%PDF-"):
        return "pdf"
    return None


def get_file_size(file: UploadFile) -> int:
    """File size in bytes; falls back to measuring it if the parser didn't set it."""
    if file.size is not None:
        return file.size
    file.file.seek(0, os.SEEK_END)
    size = file.file.tell()
    file.file.seek(0)
    return size


async def check_file_size(file: UploadFile, max_size_mb: int = DEFAULT_MAX_SIZE_MB) -> None:
    """Reject empty files and files larger than max_size_mb megabytes."""
    size = get_file_size(file)
    if size == 0:
        raise HTTPException(status_code=400, detail="The uploaded file is empty")
    if size > max_size_mb * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"File is too large. Maximum allowed size is {max_size_mb} MB",
        )


async def check_image_extension(
    file: UploadFile,
    max_size_mb: int = DEFAULT_MAX_SIZE_MB,
    allow_pdf: bool = False,
) -> None:
    """
    Validate an upload: extension, real content, and size.

    The extension is only a first filter - the first bytes must also match an
    allowed format, so a renamed .txt or .exe is rejected. Images only by
    default; pass allow_pdf=True for documents such as an ID scan.
    """
    allowed_extensions = DOCUMENT_EXTENSIONS if allow_pdf else IMAGE_EXTENSIONS
    allowed_kinds = {"image", "pdf"} if allow_pdf else {"image"}

    extension = Path(file.filename or "").suffix.lower()
    if extension not in allowed_extensions:
        raise HTTPException(
            status_code=415,
            detail=f"File type not supported. Allowed: {', '.join(sorted(allowed_extensions))}",
        )

    await check_file_size(file, max_size_mb)

    header = await file.read(12)
    await file.seek(0)  # leave the stream at the start for whoever reads it next
    if detect_file_kind(header) not in allowed_kinds:
        raise HTTPException(
            status_code=415,
            detail="File content does not match an allowed file type",
        )
