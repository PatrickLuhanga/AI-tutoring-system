"""Curriculum ingestion pipeline.

Walks the ``academic content`` folder, extracts text from every supported
document, chunks it with LangChain's ``RecursiveCharacterTextSplitter``,
generates local embeddings with ``all-MiniLM-L6-v2`` and upserts the result into
``curriculum_chunks``.

Every chunk is tagged with the ``module_id`` of its top-level folder so the RAG
Orchestrator can apply a metadata filter before the similarity search.

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
from typing import Optional

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


def build_splitter():
    """Return a LangChain recursive character splitter honouring .env sizes."""
    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "langchain-text-splitters is required. Run `pip install -r requirements.txt`."
        ) from exc

    return RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        length_function=len,
        separators=["\n\n", "\n", ". ", " ", ""],
    )


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_chunk_rows(content_file: ContentFile, sections: list[Section], splitter) -> list[dict]:
    """Turn extracted sections into DB-ready rows (without embeddings)."""
    rows: list[dict] = []
    chunk_index = 0
    for section in sections:
        for chunk_text in splitter.split_text(section.text):
            text = chunk_text.strip()
            if not text:
                continue
            rows.append(
                {
                    "module_id": content_file.module_id,
                    "source_file": content_file.rel_path,
                    "source_name": content_file.path.name,
                    "source_type": content_file.source_type,
                    "topic": content_file.topic,
                    "section_title": section.title[:512],
                    "chunk_index": chunk_index,
                    "chunk_text": text,
                    "token_count": len(text.split()),
                    "content_hash": _hash(text),
                    "doc_metadata": {
                        "module_folder": content_file.module_folder,
                        "module_id": content_file.module_id,
                        "rel_path": content_file.rel_path,
                        "topic": content_file.topic,
                        "section_title": section.title,
                        "source_type": content_file.source_type,
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


def ingest_file(content_file: ContentFile, splitter, embedder, dry_run: bool) -> tuple[int, bool]:
    """Ingest a single file. Returns ``(chunks_written, was_empty)``."""
    sections = extract_sections(content_file.path)
    if not sections:
        return 0, True

    rows = build_chunk_rows(content_file, sections, splitter)
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

    splitter = build_splitter()
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
            written, empty = ingest_file(content_file, splitter, embedder, args.dry_run)
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
