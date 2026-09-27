"""RAG retrieval (Tier 2, section 8 of the architecture document).

Given a student's query the retriever:

1. embeds it with the local ``all-MiniLM-L6-v2`` model,
2. applies the module metadata filter **before** the similarity search, and
3. returns the top-k curriculum chunks plus, where relevant, module-scoped
   code-repair patterns.

The module filter is what stops material from one subject leaking into another
(the same word - "class" - means different things in different modules).

Two further guards keep retrieval honest:

``is_answer`` chunks (worked solutions and model answers) are withheld until the
Scaffolding Engine reaches the Explanation stage, so retrieval cannot hand the
model a finished solution to copy (section 7.4).

Third-party material (commercial textbooks) is excluded by default and only
included on request, or as a fallback when the module's own material yields
nothing - so a student is never left with an empty context, but the tutor
preferentially cites the faculty-approved course material.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import and_, or_, select, text

from .config import settings
from .db import session_scope
from .embeddings import get_embedder
from .loaders import THIRD_PARTY_CATEGORIES
from .models import CodeRepairPattern, CurriculumChunk

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievalPolicy:
    """Per-turn retrieval constraints derived from the Scaffolding Engine.

    ``allow_answers`` must only be true once the session has earned an
    explanation, otherwise a single retrieval can collapse the scaffolding.
    """

    allow_answers: bool = False
    include_third_party: bool = False
    max_distance: Optional[float] = None

    @classmethod
    def default(cls) -> "RetrievalPolicy":
        return cls(
            allow_answers=False,
            include_third_party=settings.retrieval_include_third_party,
            max_distance=(settings.retrieval_max_distance or None),
        )


@dataclass(slots=True)
class RetrievedChunk:
    chunk_id: int
    module_id: str
    source_name: str
    source_file: str
    section_title: Optional[str]
    text: str
    distance: float
    source_category: str = "notes"
    is_answer: bool = False

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "module_id": self.module_id,
            "source_name": self.source_name,
            "section_title": self.section_title,
            "distance": round(self.distance, 4),
            "source_category": self.source_category,
            "is_answer": self.is_answer,
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
    #: Diagnostics for telemetry and evaluation: how the policy shaped the result.
    policy: Optional[RetrievalPolicy] = None
    third_party_fallback: bool = False
    relaxed_answer_filter: bool = False
    below_threshold: int = 0

    @property
    def chunk_ids(self) -> list[int]:
        return [chunk.chunk_id for chunk in self.chunks]

    @property
    def pattern_ids(self) -> list[int]:
        return [pattern.pattern_id for pattern in self.patterns]

    @property
    def is_empty(self) -> bool:
        return not self.chunks and not self.patterns

    @property
    def grounding_categories(self) -> list[str]:
        """Provenance classes actually present in the prompt context."""
        return sorted({c.source_category for c in self.chunks})

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
        policy: Optional[RetrievalPolicy] = None,
    ) -> RetrievalResult:
        """Return the top-k chunks (and patterns) for ``query`` in ``module_id``."""
        policy = policy or RetrievalPolicy.default()
        result = RetrievalResult(query=query, module_id=module_id, policy=policy)
        if not query or not query.strip():
            return result

        vector = get_embedder().encode_one(query)
        with session_scope() as session:
            self._tune_ann(session)
            result.chunks, result.below_threshold = self._search_curriculum(
                session, vector, module_id, policy
            )
            # If the module's own material did not satisfy the request, widen the
            # net rather than hand the model an empty or near-empty context. One
            # weak hit (a README line, say) is not a usable answer, so the test is
            # "fewer than top_k", not "none".
            if (
                len(result.chunks) < self.top_k
                and not policy.include_third_party
                and settings.retrieval_third_party_fallback
            ):
                widened = RetrievalPolicy(
                    allow_answers=policy.allow_answers,
                    include_third_party=True,
                    max_distance=policy.max_distance,
                )
                chunks, below = self._search_curriculum(session, vector, module_id, widened)
                if len(chunks) > len(result.chunks):
                    result.chunks = chunks
                    result.below_threshold = below
                    result.third_party_fallback = True
                    result.policy = widened
                    logger.info(
                        "Only %d module chunk(s) for %r in %s; widened to third-party content",
                        len(chunks) - len(result.chunks),
                        query[:60],
                        module_id,
                    )
            if include_patterns:
                result.patterns = self._search_patterns(session, vector, module_id)

        logger.info(
            "Retrieved %d chunks and %d patterns for module %s "
            "(answers_allowed=%s, third_party_fallback=%s, below_threshold=%d)",
            len(result.chunks),
            len(result.patterns),
            module_id,
            policy.allow_answers,
            result.third_party_fallback,
            result.below_threshold,
        )
        return result

    # -- Internals ----------------------------------------------------------
    @staticmethod
    def _tune_ann(session) -> None:
        """Apply HNSW tuning for this transaction.

        With a metadata filter, pgvector's HNSW index filters *after* the ANN scan,
        so a selective ``module_id`` filter can return fewer rows than ``top_k`` (or
        none) even when matching rows exist. ``hnsw.iterative_scan`` (pgvector >= 0.8)
        keeps scanning until enough filtered rows are found. Both settings are
        transaction-local so they never leak into pooled connections.
        """
        mode = (settings.hnsw_iterative_scan or "off").lower()
        if mode in {"off", ""}:
            return
        try:
            session.execute(
                text("SET LOCAL hnsw.iterative_scan = :mode"), {"mode": mode}
            )
            session.execute(
                text("SET LOCAL hnsw.ef_search = :ef"), {"ef": int(settings.hnsw_ef_search)}
            )
        except Exception as exc:  # noqa: BLE001 - older pgvector lacks the GUC
            logger.warning("Could not apply hnsw.iterative_scan=%r: %s", mode, exc)

    def _search_curriculum(
        self, session, vector: list[float], module_id: str, policy: RetrievalPolicy
    ) -> tuple[list[RetrievedChunk], int]:
        distance = CurriculumChunk.embedding.cosine_distance(vector).label("distance")

        conditions = [CurriculumChunk.module_id == module_id]
        if not policy.allow_answers:
            conditions.append(CurriculumChunk.is_answer.is_(False))
        if not policy.include_third_party:
            conditions.append(
                CurriculumChunk.source_category.notin_(sorted(THIRD_PARTY_CATEGORIES))
            )

        # Over-fetch so the distance floor can discard weak matches without
        # returning fewer than top_k good ones.
        fetch = self.top_k * 3
        rows = (
            session.execute(
                select(CurriculumChunk, distance)
                .where(and_(*conditions))
                .order_by(distance)
                .limit(fetch)
            )
            .all()
        )

        chunks: list[RetrievedChunk] = []
        dropped = 0
        ceiling = policy.max_distance
        for chunk, dist in rows:
            if ceiling is not None and float(dist) > ceiling:
                dropped += 1
                continue
            chunks.append(
                RetrievedChunk(
                    chunk_id=chunk.chunk_id,
                    module_id=chunk.module_id,
                    source_name=chunk.source_name,
                    source_file=chunk.source_file,
                    section_title=chunk.section_title,
                    text=chunk.chunk_text,
                    distance=float(dist),
                    source_category=chunk.source_category,
                    is_answer=bool(chunk.is_answer),
                )
            )
            if len(chunks) >= self.top_k:
                break
        return chunks, dropped

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
