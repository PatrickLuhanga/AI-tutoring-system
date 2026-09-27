"""Corpus resource routes: the browsable, linkable course-material library.

A citation in a tutor reply points here, so a student can land on the exact
slide or section a claim came from instead of hunting through a PDF.

Routes
------
``GET /api/resources``                     every module with its documents
``GET /api/resources/<module_id>``         one module's document list
``GET /api/resources/<module_id>/doc/<path>``  one document as JSON (sections + html)
``GET /resources/<module_id>/<path>``      one document as a styled HTML page

Documents are read from ``ACADEMIC_CONTENT_DIR`` at request time rather than from
the vector store, so the page shows the original authoring rather than the
chunked text, and always reflects the current corpus.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from flask import Blueprint, jsonify, request

from ..config import settings
from ..corpus_render import (
    RENDERABLE_SUFFIXES,
    content_root,
    render_markdown_document,
    render_resource_page,
    resolve_doc_path,
)
from ..loaders import SOURCE_CATEGORIES

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
