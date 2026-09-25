"""RAG retrieval (Tier 2, section 8 of the architecture document).

Given a student's query the retriever:

1. embeds it with the local ``all-MiniLM-L6-v2`` model,
2. applies the module metadata filter **before** the similarity search, and
3. returns the top-k curriculum chunks plus, where relevant, module-scoped
   code-repair patterns.

The module filter is what stops material from one subject leaking into another
(the same word - "class" - means different things in different modules).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import or_, select

from .config import settings
from .db import session_scope
from .embeddings import get_embedder
from .models import CodeRepairPattern, CurriculumChunk

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RetrievedChunk:
    chunk_id: int
    module_id: str
    source_name: str
    source_file: str
    section_title: Optional[str]
    text: str
    distance: float

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "module_id": self.module_id,
            "source_name": self.source_name,
            "section_title": self.section_title,
            "distance": round(self.distance, 4),
            "preview": (self.text or "")[:240],
        }


@dataclass(slots=True)
class RetrievedPattern:
    pattern_id: int
    module_id: Optional[str]
    error_title: str
    exception_thrown: Optional[str]
    conceptual_tutor_hint: str
    broken_code: str
    distance: float

    def to_dict(self) -> dict:
        return {
            "pattern_id": self.pattern_id,
            "module_id": self.module_id,
            "error_title": self.error_title,
            "exception_thrown": self.exception_thrown,
            "distance": round(self.distance, 4),
            "hint": self.conceptual_tutor_hint,
        }


@dataclass(slots=True)
class RetrievalResult:
    query: str
    module_id: str
    chunks: list[RetrievedChunk] = field(default_factory=list)
    patterns: list[RetrievedPattern] = field(default_factory=list)

    @property
    def chunk_ids(self) -> list[int]:
        return [chunk.chunk_id for chunk in self.chunks]

    @property
    def pattern_ids(self) -> list[int]:
        return [pattern.pattern_id for pattern in self.patterns]

    def context_text(self) -> str:
        """Flatten everything retrieved into one prompt-ready block."""
        blocks: list[str] = []
        for index, chunk in enumerate(self.chunks, start=1):
            title = chunk.section_title or chunk.source_name
            blocks.append(f"[Curriculum {index}] {title}\n{chunk.text}")
        for index, pattern in enumerate(self.patterns, start=1):
            blocks.append(
                f"[Code pattern {index}] {pattern.error_title} "
                f"({pattern.exception_thrown or 'n/a'})\n"
                f"Conceptual hint: {pattern.conceptual_tutor_hint}"
            )
        return "\n\n".join(blocks)


class Retriever:
    """Module-scoped semantic search over the pgvector store."""

    def __init__(self, top_k: Optional[int] = None, code_top_k: Optional[int] = None) -> None:
        self.top_k = top_k or settings.retrieval_top_k
        self.code_top_k = code_top_k or settings.retrieval_code_top_k

    def retrieve(
        self,
        query: str,
        module_id: str,
        *,
        include_patterns: bool = True,
    ) -> RetrievalResult:
        """Return the top-k chunks (and patterns) for ``query`` in ``module_id``."""
        result = RetrievalResult(query=query, module_id=module_id)
        if not query or not query.strip():
            return result

        vector = get_embedder().encode_one(query)
        with session_scope() as session:
            result.chunks = self._search_curriculum(session, vector, module_id)
            if include_patterns:
                result.patterns = self._search_patterns(session, vector, module_id)

        logger.info(
            "Retrieved %d chunks and %d patterns for module %s",
            len(result.chunks),
            len(result.patterns),
            module_id,
        )
        return result

    # -- Internals ----------------------------------------------------------
    def _search_curriculum(self, session, vector: list[float], module_id: str) -> list[RetrievedChunk]:
        distance = CurriculumChunk.embedding.cosine_distance(vector).label("distance")
        rows = (
            session.execute(
                select(CurriculumChunk, distance)
                .where(CurriculumChunk.module_id == module_id)
                .order_by(distance)
                .limit(self.top_k)
            )
            .all()
        )
        return [
            RetrievedChunk(
                chunk_id=chunk.chunk_id,
                module_id=chunk.module_id,
                source_name=chunk.source_name,
                source_file=chunk.source_file,
                section_title=chunk.section_title,
                text=chunk.chunk_text,
                distance=float(dist),
            )
            for chunk, dist in rows
        ]

    def _search_patterns(self, session, vector: list[float], module_id: str) -> list[RetrievedPattern]:
        distance = CodeRepairPattern.embedding.cosine_distance(vector).label("distance")
        # Patterns tagged for this module, plus "general" patterns with no tag.
        rows = (
            session.execute(
                select(CodeRepairPattern, distance)
                .where(
                    or_(
                        CodeRepairPattern.module_id == module_id,
                        CodeRepairPattern.module_id.is_(None),
                    )
                )
                .order_by(distance)
                .limit(self.code_top_k)
            )
            .all()
        )
        return [
            RetrievedPattern(
                pattern_id=pattern.pattern_id,
                module_id=pattern.module_id,
                error_title=pattern.error_title,
                exception_thrown=pattern.exception_thrown,
                conceptual_tutor_hint=pattern.conceptual_tutor_hint,
                broken_code=pattern.broken_code,
                distance=float(dist),
            )
            for pattern, dist in rows
        ]


_RETRIEVER: Optional[Retriever] = None


def get_retriever() -> Retriever:
    """Return the process-wide retriever singleton."""
    global _RETRIEVER
    if _RETRIEVER is None:
        _RETRIEVER = Retriever()
    return _RETRIEVER
