"""Deterministic, extension-only file-category classification and cheap
validation (Phase 36 requirements #6, #12-13).

This module NEVER inspects file content to decide what a file "is" --
that decision is always the caller's explicit `asset_type` (requirement
#5). What this module DOES do: (a) recognize a file's extension as one
of the four supported categories, so ingestion knows which cheap
sanity check applies, and (b) run that one cheap, deterministic check
(Pillow decode for images; a signature check for PDF; optional ffprobe
readability for audio/video when available). No OCR, no image
recognition, no semantic content inspection of any kind.
"""

from __future__ import annotations

import shutil
import subprocess
from enum import Enum
from pathlib import Path

from app.workspace.errors import (
    UnsupportedWorkspaceFileTypeError,
    WorkspaceImageCorruptError,
    WorkspaceMediaUnreadableError,
)


class FileCategory(str, Enum):
    DOCUMENT = "DOCUMENT"
    IMAGE = "IMAGE"
    AUDIO = "AUDIO"
    VIDEO = "VIDEO"


_DOCUMENT_EXTENSIONS = {"pdf", "txt", "md", "docx"}
_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
_AUDIO_EXTENSIONS = {"wav", "mp3", "m4a", "aac"}
_VIDEO_EXTENSIONS = {"mp4", "mov"}

_CATEGORY_BY_EXTENSION: dict[str, FileCategory] = {
    **{ext: FileCategory.DOCUMENT for ext in _DOCUMENT_EXTENSIONS},
    **{ext: FileCategory.IMAGE for ext in _IMAGE_EXTENSIONS},
    **{ext: FileCategory.AUDIO for ext in _AUDIO_EXTENSIONS},
    **{ext: FileCategory.VIDEO for ext in _VIDEO_EXTENSIONS},
}


def extension_of(path: Path) -> str:
    return path.suffix.lstrip(".").lower()


def classify_extension(extension: str) -> FileCategory:
    """Raises UnsupportedWorkspaceFileTypeError for anything outside the
    fixed, deterministic extension set -- never a best-effort guess."""
    try:
        return _CATEGORY_BY_EXTENSION[extension.lower()]
    except KeyError:
        supported = ", ".join(sorted(_CATEGORY_BY_EXTENSION))
        raise UnsupportedWorkspaceFileTypeError(
            f"Unsupported file extension {extension!r}. Supported: {supported}"
        ) from None


def validate_content(path: Path, category: FileCategory, extension: str) -> None:
    """Cheap, deterministic, non-semantic sanity checks only -- never OCR,
    never image/content recognition."""
    if category is FileCategory.IMAGE:
        _validate_image(path)
    elif category is FileCategory.DOCUMENT and extension == "pdf":
        _validate_pdf_signature(path)
    elif category in (FileCategory.AUDIO, FileCategory.VIDEO):
        _validate_media_readable_if_ffprobe_available(path)
    # .txt/.md/.docx: opaque storage is acceptable (requirement #12) --
    # existence/size/hash checks already ran before this is called.


def _validate_image(path: Path) -> None:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(path) as img:
            img.verify()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise WorkspaceImageCorruptError(f"{path} is not a valid/decodable image: {exc}") from exc


def _validate_pdf_signature(path: Path) -> None:
    header = path.open("rb").read(5)
    if header != b"%PDF-":
        raise WorkspaceMediaUnreadableError(f"{path} does not start with the %PDF- signature -- not a valid PDF")


def _validate_media_readable_if_ffprobe_available(path: Path) -> None:
    if shutil.which("ffprobe") is None:
        return  # opaque storage is acceptable when no prober is available (requirement #12/#13)
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise WorkspaceMediaUnreadableError(f"ffprobe could not read {path}: {result.stderr.strip()}")
