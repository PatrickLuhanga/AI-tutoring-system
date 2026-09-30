"""Uploading past papers, exercises and notes into a module.

Uploads are an input surface, so the two things that matter are handled
defensively here rather than at the route:

* **Path traversal.** A filename is attacker-controlled. It is reduced to its
  basename, every separator and drive letter is stripped, and the result is
  re-checked against the resolved target path, so ``..\\..\\..\\.env`` cannot
  escape the content root.
* **Extension allowlist.** Only the extensions in ``UPLOAD_ALLOWED_EXTENSIONS``
  are accepted, and the size is capped by ``UPLOAD_MAX_MB``. A ``.py`` or
  ``.exe`` in a notes folder would eventually be picked up by something that
  executes it.

Files are written under the module's folder in the same content root the
ingestion pipeline walks, so an uploaded note is one ``ingest_curriculum`` run
away from being retrievable.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from .config import settings
from .db import session_scope
from .models import UPLOAD_CATEGORIES, Module, UploadedDocument

logger = logging.getLogger(__name__)

#: Anything that is not a word, a dot, a dash or an underscore becomes an
#: underscore. This is deliberately stricter than the filesystem needs: a
#: filename is a display label and a lookup key, not a place to express intent.
_SAFE_STEM = re.compile(r"[^A-Za-z0-9._-]+")


class UploadError(Exception):
    """An upload that cannot be accepted."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def safe_filename(raw: object) -> str:
    """Reduce an attacker-supplied filename to something safe to join onto a path."""
    name = unicodedata.normalize("NFKD", str(raw or "")).strip()
    # Take the basename under both separators, so a Windows client sending
    # "C:\\fakepath\\notes.md" does not smuggle a path through.
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = _SAFE_STEM.sub("_", name).lstrip(".")
    if not name or name in {".", ".."}:
        raise UploadError("That file has no usable name.")
    return name[:120]


def content_root() -> Path:
    """The content root the ingestion pipeline also walks.

    ``settings.content_dir`` is already resolved to an absolute path against the
    project root, so uploads land in the same tree ``ingest_curriculum`` reads.
    """
    return Path(settings.content_dir)


def module_folder(module_id: str) -> Path:
    """The directory uploads for a module land in."""
    folder = settings.folder_for_module_id(str(module_id).strip().upper())
    if not folder:
        raise UploadError(
            f"{module_id} is not a configured module, so there is nowhere to put this file.",
            status_code=404,
        )
    return content_root() / folder


def _assert_within(root: Path, candidate: Path) -> None:
    """Refuse anything that resolves outside ``root``."""
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        raise UploadError("That filename points outside the module folder.") from None


def store_upload(
    *,
    module_id: str,
    filename: object,
    data: bytes,
    category: object = "notes",
    uploaded_by: Optional[int] = None,
    content_type: Optional[str] = None,
) -> dict:
    """Write an uploaded file into the module's folder and index it."""
    module = str(module_id).strip().upper()
    kind = str(category or "notes").strip().lower()
    if kind not in UPLOAD_CATEGORIES:
        raise UploadError(f"Category must be one of {list(UPLOAD_CATEGORIES)}.")

    if not data:
        raise UploadError("The uploaded file was empty.")
    limit = settings.upload_max_mb * 1024 * 1024
    if len(data) > limit:
        raise UploadError(
            f"That file is larger than the {settings.upload_max_mb} MB limit.", status_code=413
        )

    name = safe_filename(filename)
    stem, dot, ext = name.rpartition(".")
    ext = ext.lower()
    if not dot or ext not in settings.upload_allowed_extensions:
        raise UploadError(
            f"'{ext or name}' files are not accepted. "
            f"Allowed: {', '.join(settings.upload_allowed_extensions)}."
        )
    if not stem:
        raise UploadError("That file has no name before its extension.")

    folder = module_folder(module)
    _assert_within(content_root(), folder)

    target = folder / name
    _assert_within(folder, target)
    folder.mkdir(parents=True, exist_ok=True)
    # Never silently overwrite: a second upload of the same name gets a suffix,
    # so an earlier past paper is not destroyed by a later one.
    if target.exists():
        target = folder / f"{stem}_{_short_suffix()}{dot}{ext}"
        _assert_within(folder, target)

    target.write_bytes(data)

    # source_file is stored relative to the content root, which is what the
    # ingestion pipeline and the resource routes both expect.
    stored_rel = target.relative_to(content_root()).as_posix()

    with session_scope() as session:
        row = UploadedDocument(
            module_id=module,
            uploaded_by=uploaded_by,
            category=kind,
            original_name=name,
            stored_path=stored_rel,
            content_type=(content_type or None),
            size_bytes=len(data),
            # A past paper is a question source, not something to embed.
            ingest_status="pending" if kind != "past_paper" else "pending",
        )
        session.add(row)
        session.flush()
        result = _to_dict(row)

    logger.info(
        "Stored %s upload %s for %s by %s (%d bytes)",
        kind,
        stored_rel,
        module,
        uploaded_by,
        len(data),
    )
    return result


def _short_suffix() -> str:
    import uuid

    return uuid.uuid4().hex[:8]


def list_uploads(module_id: Optional[str] = None) -> list[dict]:
    """Index rows, newest first. Optionally for one module."""
    with session_scope() as session:
        stmt = select(UploadedDocument)
        if module_id:
            stmt = stmt.where(UploadedDocument.module_id == str(module_id).strip().upper())
        rows = session.execute(
            stmt.order_by(UploadedDocument.created_at.desc()).limit(500)
        ).scalars()
        return [_to_dict(r) for r in rows]


def delete_upload(document_id: int, *, remove_file: bool = False) -> bool:
    """Drop the index row, and optionally the file.

    The file is kept by default: a past paper may have already had questions
    imported from it, and deleting the source would leave those questions
    unattributable.
    """
    with session_scope() as session:
        row = session.get(UploadedDocument, int(document_id))
        if row is None:
            return False
        target = content_root() / row.stored_path
        if remove_file:
            _assert_within(content_root(), target)
            try:
                target.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Could not remove %s: %s", row.stored_path, exc)
        session.delete(row)
    return True


def known_modules() -> list[str]:
    with session_scope() as session:
        return sorted(
            str(r)
            for r in session.execute(select(Module.module_id)).scalars()
        )


def _to_dict(row: UploadedDocument) -> dict:
    return {
        "document_id": int(row.document_id),
        "module_id": row.module_id,
        "category": row.category,
        "original_name": row.original_name,
        "stored_path": row.stored_path,
        "size_bytes": int(row.size_bytes or 0),
        "ingest_status": row.ingest_status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
