"""Corpus resource routes: the browsable, linkable course-material library.

A citation in a tutor reply points here, so a student can land on the exact
slide or section a claim came from instead of hunting through a PDF.

Routes
------
``GET /resources``                         styled library root, every module
``GET /resources/<module_id>``             one module's documents, grouped
``GET /resources/<module_id>/<path>``      one document as a styled HTML page
``GET /api/resources``                     same index as JSON
``GET /api/resources/<module_id>``         one module's document list as JSON
``GET /api/resources/<module_id>/doc/<path>``  one document as JSON (sections + html)

Documents are read from ``ACADEMIC_CONTENT_DIR`` at request time rather than from
the vector store, so the page shows the original authoring rather than the
chunked text, and always reflects the current corpus.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from flask import Blueprint, jsonify, request
from sqlalchemy import func, select

from ..config import settings
from ..corpus_render import (
    RENDERABLE_SUFFIXES,
    content_root,
    render_library_root,
    render_markdown_document,
    render_module_index,
    render_resource_page,
    resolve_doc_path,
)
from ..db import session_scope
from ..loaders import SOURCE_CATEGORIES
from ..models import CurriculumChunk

logger = logging.getLogger(__name__)

resource_bp = Blueprint("resources", __name__)


def _resolve(module_id: str) -> Optional[tuple[str, dict]]:
    """Accept either a registry key (``IPRT``) or a module id (``IPRT301``)."""
    return settings.resolve_any(module_id)


def _module_dir(module_id: str) -> Optional[Path]:
    resolved = _resolve(module_id)
    if resolved is None:
        return None
    folder, _record = resolved
    return content_root() / folder


_HEADING_RE = re.compile(r"^#{1,3}\s+(.+?)\s*#*\s*$")


def _document_title(path: Path) -> str | None:
    """First heading in a document, without opening the whole corpus.

    Only the first few kilobytes are read: a title always sits at the top, and
    some of these files are whole textbook chapters.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for _ in range(40):
                line = handle.readline()
                if not line:
                    return None
                match = _HEADING_RE.match(line.strip())
                if match:
                    return re.sub(r"[*_`~]+", "", match.group(1)).strip() or None
    except OSError:
        return None
    return None


def _list_documents(module_id: str) -> list[dict]:
    """Every renderable document in a module, ordered by category then name."""
    directory = _module_dir(module_id)
    if directory is None or not directory.is_dir():
        return []
    docs: list[dict] = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in RENDERABLE_SUFFIXES:
            continue
        rel = path.relative_to(directory).as_posix()
        parts = rel.split("/")
        category = (
            SOURCE_CATEGORIES.get(parts[0].lower(), "notes") if len(parts) > 1 else "notes"
        )
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        docs.append(
            {
                "path": rel,
                "name": path.name,
                # The document's own heading, so a library listing reads as
                # "Stages of the research process" rather than
                # "01_Stages_of_Research.md". Falls back to the filename for a
                # document with no heading.
                "title": _document_title(path) or path.name,
                "group": parts[0] if len(parts) > 1 else "",
                "source_category": category,
                "size_bytes": size,
                "url": f"/resources/{module_id}/{rel}",
                "api_url": f"/api/resources/{module_id}/doc/{rel}",
            }
        )
    order = {"slides": 0, "notes": 1, "lecture_notes": 1, "exercises": 2,
             "examples": 3, "tutorials": 4, "books": 5}
    docs.sort(key=lambda d: (order.get(d["source_category"], 9), d["path"]))
    return docs


@resource_bp.get("/api/resources")
def list_all_resources():
    """Index of every module and the documents it contains."""
    modules = []
    for record in settings.modules.values():
        mid = record["module_id"]
        docs = _list_documents(mid)
        modules.append(
            {
                "module_id": mid,
                "module_name": record["module_name"],
                "language": record.get("language"),
                "document_count": len(docs),
                "total_bytes": sum(d["size_bytes"] for d in docs),
                "url": f"/resources/{mid}",
                "documents": docs,
            }
        )
    return jsonify({"modules": modules, "count": len(modules)}), 200


@resource_bp.get("/api/resources/<module_id>")
def list_module_resources(module_id: str):
    resolved = _resolve(module_id)
    if resolved is None:
        return jsonify({"error": "Unknown module."}), 404
    _folder, record = resolved
    docs = _list_documents(module_id)
    return (
        jsonify(
            {
                "module_id": record["module_id"],
                "module_name": record["module_name"],
                "count": len(docs),
                "documents": docs,
            }
        ),
        200,
    )


@resource_bp.get("/api/materials/<module_id>")
def list_module_materials(module_id: str):
    """The ingested knowledge base for one module, straight from the vector store.

    ``/api/resources/<module_id>`` lists *renderable files on disk* (Markdown),
    which is empty for a corpus ingested from raw PDF/PPTX. This endpoint instead
    reads ``curriculum_chunks`` - the actual text the tutor retrieves - so a
    student or lecturer can audit exactly what the RAG system knows.

    Query parameters:
        ``limit``  chunks returned per document (default 5, max 50)
        ``doc``    restrict to one ``source_file``
        ``q``      case-insensitive substring filter over chunk text

    The response groups chunks by source document and reports each document's
    type, topic, provenance category and total chunk count, so the caller can see
    coverage as well as content.
    """
    resolved = _resolve(module_id)
    if resolved is None:
        return jsonify({"error": "Unknown module."}), 404
    _folder, record = resolved
    mid = record["module_id"]

    try:
        per_doc = int(request.args.get("limit", 5))
    except (TypeError, ValueError):
        per_doc = 5
    per_doc = max(1, min(per_doc, 50))
    doc_filter = (request.args.get("doc") or "").strip()
    search = (request.args.get("q") or "").strip()

    try:
        with session_scope() as session:
            stmt = select(CurriculumChunk).where(CurriculumChunk.module_id == mid)
            if doc_filter:
                stmt = stmt.where(CurriculumChunk.source_file == doc_filter)
            if search:
                stmt = stmt.where(CurriculumChunk.chunk_text.ilike(f"%{search}%"))
            stmt = stmt.order_by(
                CurriculumChunk.source_file,
                CurriculumChunk.chunk_index,
                CurriculumChunk.chunk_id,
            )
            rows = session.execute(stmt).scalars().all()
    except Exception as exc:  # noqa: BLE001 - Data Tier must not 500 the page
        logger.error("Could not load materials for %s: %s", mid, exc)
        return jsonify({"error": "The material store is temporarily unavailable."}), 503

    # Group into documents, preserving the query's ordering.
    documents: dict[str, dict] = {}
    for chunk in rows:
        doc = documents.get(chunk.source_file)
        if doc is None:
            doc = {
                "source_file": chunk.source_file,
                "source_name": chunk.source_name,
                "source_type": chunk.source_type,
                "source_category": chunk.source_category,
                "topic": chunk.topic,
                "section_title": chunk.section_title,
                "chunk_count": 0,
                "returned": 0,
                "chunks": [],
            }
            documents[chunk.source_file] = doc
        doc["chunk_count"] += 1
        if doc["returned"] < per_doc:
            doc["chunks"].append(
                {
                    "chunk_id": int(chunk.chunk_id),
                    "chunk_index": int(chunk.chunk_index),
                    "section_title": chunk.section_title,
                    "token_count": chunk.token_count,
                    "is_answer": bool(chunk.is_answer),
                    "text": chunk.chunk_text,
                }
            )
            doc["returned"] += 1

    # Reading order: faculty material before third-party textbooks, then by path.
    category_order = {"slides": 0, "lecture_notes": 1, "notes": 1, "examples": 2,
                      "exercises": 3, "books": 9}
    docs = sorted(
        documents.values(),
        key=lambda d: (category_order.get(d["source_category"], 5), d["source_file"]),
    )

    return (
        jsonify(
            {
                "module_id": mid,
                "module_name": record["module_name"],
                "language": record.get("language"),
                "total_chunks": len(rows),
                "document_count": len(docs),
                "returned_chunks": sum(d["returned"] for d in docs),
                "limit_per_document": per_doc,
                "documents": docs,
            }
        ),
        200,
    )


@resource_bp.get("/api/resources/<module_id>/doc/<path:rel_path>")
def get_document_json(module_id: str, rel_path: str):
    """One document as JSON: rendered HTML plus its anchor index."""
    resolved = _resolve(module_id)
    if resolved is None:
        return jsonify({"error": "Unknown module."}), 404
    _folder, record = resolved
    path = resolve_doc_path(module_id, rel_path)
    if path is None:
        return jsonify({"error": "Document not found."}), 404

    text = _read(path)
    if text is None:
        return jsonify({"error": "Document could not be read."}), 500

    parts = rel_path.split("/")
    category = SOURCE_CATEGORIES.get(parts[0].lower(), "notes") if len(parts) > 1 else "notes"
    doc = render_markdown_document(
        module_id, rel_path, text, source_name=path.name, category=category
    )
    return (
        jsonify(
            {
                "module_id": module_id,
                "module_name": record["module_name"],
                "path": rel_path,
                "title": doc.title,
                "source_category": category,
                "html": doc.html,
                "headings": [
                    {"level": h.level, "title": h.title, "anchor": h.anchor, "slide": h.slide}
                    for h in doc.headings
                ],
                "url": f"/resources/{module_id}/{rel_path}",
            }
        ),
        200,
    )


@resource_bp.get("/resources")
@resource_bp.get("/resources/")
def get_library_root():
    """Styled entry point: every module, linking to its document list."""
    modules = []
    for record in settings.modules.values():
        mid = record["module_id"]
        docs = _list_documents(mid)
        by_category: dict[str, int] = {}
        for doc in docs:
            by_category[doc["source_category"]] = by_category.get(doc["source_category"], 0) + 1
        modules.append(
            {
                "module_id": mid,
                "module_name": record["module_name"],
                "document_count": len(docs),
                "by_category": by_category,
                "url": f"/resources/{mid}",
            }
        )
    page = render_library_root(modules)
    return page, 200, {"Content-Type": "text/html; charset=utf-8"}


@resource_bp.get("/resources/<module_id>")
@resource_bp.get("/resources/<module_id>/")
def get_module_index(module_id: str):
    """Styled index of a module's documents, grouped by provenance."""
    resolved = _resolve(module_id)
    if resolved is None:
        return _not_found_page("Unknown module."), 404
    _folder, record = resolved
    page = render_module_index(record["module_id"], record["module_name"], _list_documents(module_id))
    return page, 200, {"Content-Type": "text/html; charset=utf-8"}


@resource_bp.get("/resources/<module_id>/<path:rel_path>")
def get_document_page(module_id: str, rel_path: str):
    """One document as a styled HTML page, optionally scrolled to a cited anchor."""
    resolved = _resolve(module_id)
    if resolved is None:
        return _not_found_page("Unknown module."), 404
    _folder, record = resolved
    path = resolve_doc_path(module_id, rel_path)
    if path is None:
        return _not_found_page("That document does not exist."), 404

    text = _read(path)
    if text is None:
        return _not_found_page("That document could not be read."), 500

    parts = rel_path.split("/")
    category = SOURCE_CATEGORIES.get(parts[0].lower(), "notes") if len(parts) > 1 else "notes"
    doc = render_markdown_document(
        module_id, rel_path, text, source_name=path.name, category=category
    )

    docs = _list_documents(module_id)
    try:
        index = next(i for i, d in enumerate(docs) if d["path"] == rel_path)
    except StopIteration:
        index = -1
    prev_doc = docs[index - 1] if 0 < index <= len(docs) - 1 else (docs[-1] if index == 0 else None)
    next_doc = docs[index + 1] if 0 <= index < len(docs) - 1 else None

    page = render_resource_page(
        doc,
        module_name=record["module_name"],
        prev_doc=prev_doc,
        next_doc=next_doc,
        cited_anchor=request.args.get("at"),
    )
    return page, 200, {"Content-Type": "text/html; charset=utf-8"}


def _read(path: Path) -> Optional[str]:
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            logger.warning("Could not read corpus document %s: %s", path, exc)
            return None
    return path.read_text(encoding="utf-8", errors="ignore")


def _not_found_page(message: str) -> str:
    from ..corpus_render import _CSS, module_nav

    from html import escape

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Not found</title><style>{_CSS}</style></head><body>
<header class="site"><span class="brand">AI Tutor</span><span class="spacer"></span>
{module_nav("")}</header>
<main><h1 class="doc">Not found</h1><p>{escape(message)}</p>
<p><a href="/">Back to the tutor</a></p></main></body></html>"""
