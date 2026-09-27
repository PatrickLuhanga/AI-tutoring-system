"""Curriculum ingestion pipeline.

Walks the ``academic content`` folder, extracts Markdown from every supported
document, chunks it on the document's own heading hierarchy with LangChain's
``MarkdownHeaderTextSplitter``, generates local embeddings and upserts the result
into ``curriculum_chunks``.

Chunking is heading-aware: a chunk never spans two topics, and each chunk's
embedded text is prefixed with a contextual breadcrumb
(``module › topic › heading path``) so the vector carries the context that the
surrounding document would otherwise provide.

Every chunk is still tagged with the ``module_id`` of its top-level folder so the
RAG Orchestrator can apply a metadata filter before the similarity search.

Usage:
    python -m src.ingest_curriculum --dry-run
    python -m src.ingest_curriculum
    python -m src.ingest_curriculum --module IPRT
    python -m src.ingest_curriculum --reset
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple, Optional

from sqlalchemy import delete, func, insert, select
from tqdm import tqdm

from .config import settings
from .db import session_scope
from .embeddings import get_embedder
from .loaders import ContentFile, Section, count_skipped, extract_sections, iter_content_files
from .models import CurriculumChunk

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
logger = logging.getLogger("ingest_curriculum")


@dataclass
class IngestionStats:
    files_seen: int = 0
    files_ingested: int = 0
    files_empty: int = 0
    chunks_written: int = 0
    sections_seen: int = 0
    errors: int = 0
    per_module: dict[str, int] = field(default_factory=dict)

    def record_module(self, module_id: str, chunks: int) -> None:
        self.per_module[module_id] = self.per_module.get(module_id, 0) + chunks


HEADING_KEYS = ("h1", "h2", "h3", "h4", "h5", "h6")

#: ``MarkdownHeaderTextSplitter`` config: heading marker -> metadata key.
_MARKDOWN_HEADERS = [("#" * level, key) for level, key in enumerate(HEADING_KEYS, start=1)]

#: Separator used inside the contextual breadcrumb (module > topic > headings).
_BREADCRUMB_SEP = " › "


class ChunkSplitters(NamedTuple):
    """The two splitters that make up the chunking strategy."""

    markdown: "MarkdownHeaderTextSplitter"
    fallback: "RecursiveCharacterTextSplitter"


class _ChunkFragment(NamedTuple):
    """Minimal stand-in for a LangChain ``Document`` on the fallback path."""

    page_content: str
    metadata: dict


def build_splitters() -> ChunkSplitters:
    """Build the heading-aware splitter plus a size fallback.

    ``MarkdownHeaderTextSplitter`` cuts each document on its own heading
    hierarchy so chunks stay on one topic; the fallback recursive splitter only
    breaks up a single section that is still larger than ``CHUNK_SIZE``.
    """
    try:
        from langchain_text_splitters import (
            MarkdownHeaderTextSplitter,
            RecursiveCharacterTextSplitter,
        )
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "langchain-text-splitters is required. Run `pip install -r requirements.txt`."
        ) from exc

    markdown = MarkdownHeaderTextSplitter(
        headers_to_split_on=_MARKDOWN_HEADERS,
        strip_headers=True,
    )
    fallback = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        length_function=len,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    return ChunkSplitters(markdown=markdown, fallback=fallback)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _breadcrumb(content_file: ContentFile, heading_path: list[str]) -> str:
    """Compose the ``module › topic › heading path`` contextual prefix."""
    parts = [content_file.module_id, content_file.topic, *heading_path]
    return _BREADCRUMB_SEP.join(part for part in parts if part)


def _split_section(section: Section, splitters: ChunkSplitters) -> list:
    """Split one section's Markdown into heading-scoped fragments."""
    try:
        fragments = splitters.markdown.split_text(section.text)
    except Exception as exc:  # noqa: BLE001 - never lose a section to a splitter bug
        logger.warning("Markdown split failed (%s); using the section as-is", exc)
        fragments = []
    if fragments:
        return fragments
    return [_ChunkFragment(page_content=section.text, metadata={})]


def build_chunk_rows(
    content_file: ContentFile, sections: list[Section], splitters: ChunkSplitters
) -> list[dict]:
    """Turn extracted sections into DB-ready rows (without embeddings).

    The heading path is prepended to the embedded text as a breadcrumb so the
    vector itself carries the context the surrounding document would provide.
    """
    rows: list[dict] = []
    chunk_index = 0
    for section in sections:
        for fragment in _split_section(section, splitters):
            body = (fragment.page_content or "").strip()
            if not body:
                continue

            heading_path = [
                fragment.metadata[key] for key in HEADING_KEYS if fragment.metadata.get(key)
            ]
            # Documents without detected headings (tables, CSVs, plain text)
            # still get a meaningful breadcrumb: the section title stands in
            # for the heading path so the prefix always names the source.
            breadcrumb = _breadcrumb(content_file, heading_path or ([section.title] if section.title else []))
            section_title = (heading_path[-1] if heading_path else section.title)[:512]

            body_parts = (
                splitters.fallback.split_text(body)
                if len(body) > settings.chunk_size
                else [body]
            )
            strategy = "markdown-header" if len(body_parts) == 1 else "markdown-header+recursive"

            for part in body_parts:
                text = part.strip()
                if not text:
                    continue
                chunk_text = f"{breadcrumb}\n\n{text}" if breadcrumb else text
                rows.append(
                    {
                        "module_id": content_file.module_id,
                        "source_file": content_file.rel_path,
                        "source_name": content_file.path.name,
                        "source_type": content_file.source_type,
                        "topic": content_file.topic,
                        "section_title": section_title,
                        "breadcrumb": breadcrumb or None,
                        "heading_path": heading_path,
                        "chunk_strategy": strategy,
                        "chunk_index": chunk_index,
                        "chunk_text": chunk_text,
                        "token_count": len(chunk_text.split()),
                        "content_hash": _hash(chunk_text),
                        "doc_metadata": {
                            "module_folder": content_file.module_folder,
                            "module_id": content_file.module_id,
                            "rel_path": content_file.rel_path,
                            "topic": content_file.topic,
                            "section_title": section_title,
                            "source_type": content_file.source_type,
                            "heading_path": heading_path,
                            "breadcrumb": breadcrumb,
                            "chunk_strategy": strategy,
                        },
                        "embedding_model": settings.embedding_model_name,
                    }
                )
                chunk_index += 1
    return rows


def _embed_rows(rows: list[dict], embedder) -> None:
    texts = [row["chunk_text"] for row in rows]
    vectors = embedder.encode(texts)
    for row, vector in zip(rows, vectors):
        row["embedding"] = [float(value) for value in vector]


def ingest_file(content_file: ContentFile, splitters: ChunkSplitters, embedder, dry_run: bool) -> tuple[int, bool]:
    """Ingest a single file. Returns ``(chunks_written, was_empty)``."""
    sections = extract_sections(content_file.path)
    if not sections:
        return 0, True

    rows = build_chunk_rows(content_file, sections, splitters)
    if not rows:
        return 0, True
    if dry_run:
        return len(rows), False

    _embed_rows(rows, embedder)

    with session_scope() as session:
        # Idempotent per source file: replace any previous chunks for this file.
        session.execute(
            delete(CurriculumChunk).where(CurriculumChunk.source_file == content_file.rel_path)
        )
        session.execute(insert(CurriculumChunk), rows)
    return len(rows), False


def reset_table() -> None:
    with session_scope() as session:
        session.execute(delete(CurriculumChunk))
    logger.warning("Cleared all rows from curriculum_chunks (--reset)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest curriculum material into pgvector.")
    parser.add_argument("--content-dir", type=Path, default=None, help="Override ACADEMIC_CONTENT_DIR.")
    parser.add_argument("--module", default=None, help="Only ingest one module folder, e.g. IPRT.")
    parser.add_argument("--max-files", type=int, default=None, help="Stop after N files (debug).")
    parser.add_argument("--dry-run", action="store_true", help="Chunk only; do not touch the database.")
    parser.add_argument("--reset", action="store_true", help="Delete all existing rows first.")
    args = parser.parse_args(argv)

    content_dir = args.content_dir or settings.content_dir
    logger.info("Content directory: %s", content_dir)
    logger.info("Embedding model:   %s (%d dims, backend=%s)",
                settings.embedding_model_name, settings.embedding_dim, settings.embedding_backend)

    if not args.dry_run and args.reset:
        reset_table()

    splitters = build_splitters()
    embedder = None if args.dry_run else get_embedder()

    files = list(
        iter_content_files(
            content_dir=content_dir,
            module_filter=args.module,
            max_files=args.max_files,
        )
    )
    if not files:
        logger.warning("No supported files found under %s", content_dir)
        return 1

    skipped = count_skipped(content_dir)
    if skipped:
        summary = ", ".join(f"{ext}:{count}" for ext, count in sorted(skipped.items()))
        logger.info("Ignoring non-ingestable files: %s", summary)

    stats = IngestionStats(files_seen=len(files))
    for content_file in tqdm(files, desc="Ingesting", unit="file"):
        try:
            written, empty = ingest_file(content_file, splitters, embedder, args.dry_run)
        except Exception as exc:  # noqa: BLE001 - continue on individual failures
            stats.errors += 1
            logger.error("Failed %s: %s", content_file.rel_path, exc)
            continue

        if empty:
            stats.files_empty += 1
            logger.debug("No extractable text: %s", content_file.rel_path)
            continue

        stats.files_ingested += 1
        stats.chunks_written += written
        stats.record_module(content_file.module_id, written)

    logger.info("-" * 60)
    logger.info("Files discovered : %d", stats.files_seen)
    logger.info("Files ingested   : %d", stats.files_ingested)
    logger.info("Files with no text: %d", stats.files_empty)
    logger.info("Errors           : %d", stats.errors)
    logger.info("Chunks %-8s : %d", "counted" if args.dry_run else "written", stats.chunks_written)
    for module_id, count in sorted(stats.per_module.items()):
        logger.info("  %-10s %6d chunks", module_id, count)

    if not args.dry_run:
        with session_scope() as session:
            total = session.execute(select(func.count()).select_from(CurriculumChunk)).scalar_one()
        logger.info("curriculum_chunks total rows: %d", total)

    return 0


if __name__ == "__main__":
    sys.exit(main())
