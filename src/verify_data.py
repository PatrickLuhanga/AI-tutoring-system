"""Post-ingestion sanity check.

Prints row counts for every table and runs two module-filtered semantic searches
(curriculum + code-repair patterns) to prove the vector store is queryable.

Usage:
    python -m src.verify_data
    python -m src.verify_data --module IPRT --query "NullPointerException null String"
"""

from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import func, select

from .config import settings
from .db import session_scope
from .embeddings import get_embedder
from .models import (
    CodeRepairPattern,
    CurriculumChunk,
    Enrollment,
    HintFeedback,
    Module,
    Student,
    TelemetryLog,
    TutorAssignment,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
logger = logging.getLogger("verify_data")

TABLES = [
    Student,
    Module,
    Enrollment,
    TutorAssignment,
    TelemetryLog,
    HintFeedback,
    CurriculumChunk,
    CodeRepairPattern,
]


def print_counts() -> None:
    logger.info("Row counts:")
    with session_scope() as session:
        for model in TABLES:
            count = session.execute(select(func.count()).select_from(model)).scalar_one()
            logger.info("  %-22s %8d", model.__tablename__, count)


def search_curriculum(query: str, module_id: str, limit: int) -> None:
    embedder = get_embedder()
    vector = embedder.encode_one(query)
    with session_scope() as session:
        distance = CurriculumChunk.embedding.cosine_distance(vector).label("distance")
        rows = session.execute(
            select(CurriculumChunk.section_title, CurriculumChunk.source_file, distance)
            .where(CurriculumChunk.module_id == module_id)
            .order_by(distance)
            .limit(limit)
        ).all()
    logger.info("Curriculum search (module=%s, q=%r):", module_id, query)
    for title, source, dist in rows:
        logger.info("  d=%.4f  %-45s  %s", dist, (title or "")[:45], source)
    if not rows:
        logger.warning("  no matches - is the module id correct and the table populated?")


def search_code_patterns(query: str, module_id: str | None, limit: int) -> None:
    embedder = get_embedder()
    vector = embedder.encode_one(query)
    with session_scope() as session:
        distance = CodeRepairPattern.embedding.cosine_distance(vector).label("distance")
        stmt = select(
            CodeRepairPattern.error_title,
            CodeRepairPattern.exception_thrown,
            distance,
        ).order_by(distance)
        if module_id:
            stmt = stmt.where(CodeRepairPattern.module_id == module_id)
        rows = session.execute(stmt.limit(limit)).all()
    logger.info("Code-pattern search (q=%r):", query)
    for title, exception, dist in rows:
        logger.info("  d=%.4f  %-50s  %s", dist, title[:50], exception)
    if not rows:
        logger.warning("  no matches - is the table populated?")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the Hybrid Data Tier contents.")
    parser.add_argument("--module", default="IPRT301", help="Module id used for the sample search.")
    parser.add_argument("--query", default="NullPointerException when calling a method on a null string")
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args(argv)

    print_counts()
    search_curriculum(args.query, args.module, args.limit)
    search_code_patterns(args.query, args.module, args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
