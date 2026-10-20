"""Upload validation: which file types each kind accepts, and content sniffing.

The extension decides the stored MIME type; the client's Content-Type is ignored,
and the first bytes must match the claimed format.
"""

import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

from app.db.models.enums import FileKind

# extension -> MIME type
MIME_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".json": "application/json",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".log": "text/plain",
    ".tex": "application/x-tex",
    ".bib": "application/x-bibtex",
    ".py": "text/x-python",
    ".ipynb": "application/x-ipynb+json",
    ".r": "text/x-r",
    ".m": "text/x-matlab",
    ".js": "text/javascript",
    ".ts": "text/plain",
    ".java": "text/x-java",
    ".c": "text/x-c",
    ".cpp": "text/x-c++",
    ".h": "text/x-c",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".zip": "application/zip",
}

ALLOWED_EXTENSIONS: dict[FileKind, frozenset[str]] = {
    FileKind.PAPER_PDF: frozenset({".pdf"}),
    FileKind.DATASET: frozenset({".csv", ".tsv", ".json", ".xlsx", ".txt"}),
    FileKind.DIAGRAM: frozenset({".png", ".jpg", ".jpeg", ".webp", ".svg", ".pdf"}),
    FileKind.CODE: frozenset({".py", ".ipynb", ".r", ".m", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".txt", ".zip"}),
    FileKind.NOTES: frozenset({".txt", ".md", ".pdf", ".docx", ".tex", ".bib"}),
    FileKind.RESULT: frozenset({".csv", ".tsv", ".json", ".xlsx", ".txt", ".log", ".png", ".jpg", ".jpeg", ".pdf"}),
}

# Binary formats identified by their leading bytes
_MAGIC: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF-",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".xlsx": (b"PK\x03\x04",),
    ".docx": (b"PK\x03\x04",),
    ".zip": (b"PK\x03\x04", b"PK\x05\x06"),
}
SNIFF_BYTES = 8192


class UploadRejectedError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class ValidatedUpload:
    filename: str
    extension: str
    mime: str


def safe_filename(raw: str | None) -> str:
    """Basename only, no control/odd characters, max 200 chars (keeps the extension)."""
    name = PureWindowsPath(PurePosixPath(raw or "").name).name  # strip any directory part
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r"[\x00-\x1f\x7f/\\:*?\"<>|]", "_", name).strip(" .")
    if not name:
        return "file"
    stem, dot, ext = name.rpartition(".")
    if dot and len(ext) <= 10:
        return f"{stem[: 200 - len(ext) - 1]}.{ext}"
    return name[:200]


def validate_upload(kind: FileKind, filename: str | None, head: bytes) -> ValidatedUpload:
    """Check the extension is allowed for `kind` and the content looks like that format."""
    name = safe_filename(filename)
    ext = PurePosixPath(name).suffix.lower()
    allowed = ALLOWED_EXTENSIONS[kind]
    if ext not in allowed:
        raise UploadRejectedError(
            415,
            f"'{ext or 'no extension'}' files are not allowed for {kind.value}; allowed: {', '.join(sorted(allowed))}",
        )
    if not head:
        raise UploadRejectedError(422, "File is empty")
    if not _content_matches(ext, head):
        raise UploadRejectedError(422, f"File content does not look like a {ext} file")
    return ValidatedUpload(filename=name, extension=ext, mime=MIME_TYPES[ext])


def _content_matches(ext: str, head: bytes) -> bool:
    if ext in _MAGIC:
        return head.startswith(_MAGIC[ext])
    if ext == ".webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if ext == ".ipynb":
        return _is_text(head) and head.lstrip().startswith(b"{")
    # Everything else (csv, json, txt, svg, code, ...) must be text
    return _is_text(head)


def _is_text(head: bytes) -> bool:
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        # A multi-byte character may be cut off at the end of the sniffed chunk
        return exc.start >= len(head) - 3
    return True
