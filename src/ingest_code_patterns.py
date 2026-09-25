"""Code-repair corpus ingestion.

Loads the synthetic Java error corpus, builds a retrieval-friendly embedding
text for each pattern, embeds it locally and upserts the rows into
``code_repair_patterns``.

Usage:
    python -m src.ingest_code_patterns --dry-run
    python -m src.ingest_code_patterns
    python -m src.ingest_code_patterns --reset
"""

from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .config import settings
from .corpora.java_error_corpus import DEFAULT_MODULE_ID, JAVA_ERROR_CORPUS, validate_corpus
from .db import session_scope
from .embeddings import get_embedder
from .models import CodeRepairPattern

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
logger = logging.getLogger("ingest_code_patterns")


def build_embedding_text(pattern: dict) -> str:
    """Compose the text that represents a pattern in vector space.

    Mirrors how a student query looks (an error name plus the failing code) so
    semantic search matches real questions rather than only the hint prose.
    """
    return (
        f"Java error: {pattern['error_title']}\n"
        f"Exception: {pattern.get('exception_thrown', '')}\n"
        f"Problematic code:\n{pattern['broken_code']}\n"
        f"Common cause: {pattern.get('common_cause', '')}"
    )


def build_rows() -> list[dict]:
    rows: list[dict] = []
    for pattern in JAVA_ERROR_CORPUS:
        rows.append(
            {
                "module_id": pattern.get("module_id", DEFAULT_MODULE_ID),
                "language": pattern.get("language", "Java"),
                "error_title": pattern["error_title"],
                "error_category": pattern.get("error_category"),
                "exception_thrown": pattern.get("exception_thrown"),
                "broken_code": pattern["broken_code"],
                "conceptual_tutor_hint": pattern["conceptual_tutor_hint"],
                "common_cause": pattern.get("common_cause"),
                "tags": pattern.get("tags", []),
                "difficulty": pattern.get("difficulty"),
                "embedding_model": settings.embedding_model_name,
            }
        )
    return rows


def ingest(dry_run: bool = False, reset: bool = False) -> int:
    validate_corpus()
    rows = build_rows()
    logger.info("Corpus contains %d patterns", len(rows))

    if dry_run:
        by_category: dict[str, int] = {}
        for row in rows:
            key = row.get("error_category") or "uncategorised"
            by_category[key] = by_category.get(key, 0) + 1
        for key, count in sorted(by_category.items()):
            logger.info("  %-24s %2d", key, count)
        logger.info("Dry run complete - no database writes performed.")
        return 0

    if reset:
        with session_scope() as session:
            session.execute(delete(CodeRepairPattern))
        logger.warning("Cleared all rows from code_repair_patterns (--reset)")

    embedder = get_embedder()
    texts = [build_embedding_text(pattern) for pattern in JAVA_ERROR_CORPUS]
    vectors = embedder.encode(texts)
    for row, vector in zip(rows, vectors):
        row["embedding"] = [float(value) for value in vector]

    with session_scope() as session:
        stmt = pg_insert(CodeRepairPattern.__table__).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[CodeRepairPattern.__table__.c.error_title],
            set_={
                "module_id": stmt.excluded.module_id,
                "language": stmt.excluded.language,
                "error_category": stmt.excluded.error_category,
                "exception_thrown": stmt.excluded.exception_thrown,
                "broken_code": stmt.excluded.broken_code,
                "conceptual_tutor_hint": stmt.excluded.conceptual_tutor_hint,
                "common_cause": stmt.excluded.common_cause,
                "tags": stmt.excluded.tags,
                "difficulty": stmt.excluded.difficulty,
                "embedding_model": stmt.excluded.embedding_model,
                "embedding": stmt.excluded.embedding,
            },
        )
        session.execute(stmt)
        total = session.execute(select(func.count()).select_from(CodeRepairPattern)).scalar_one()

    logger.info("Upserted %d patterns; code_repair_patterns total rows: %d", len(rows), total)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest the synthetic Java error corpus.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and summarise; no writes.")
    parser.add_argument("--reset", action="store_true", help="Delete all existing patterns first.")
    args = parser.parse_args(argv)
    return ingest(dry_run=args.dry_run, reset=args.reset)


if __name__ == "__main__":
    sys.exit(main())
