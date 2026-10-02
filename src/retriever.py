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
import re
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import and_, or_, select, text

from .config import settings
from .corpus_render import citation_url
from .db import session_scope
from .embeddings import get_embedder
from .loaders import THIRD_PARTY_CATEGORIES
from .models import CodeRepairPattern, CurriculumChunk

logger = logging.getLogger(__name__)

#: Tags the ingested textbooks leave inside heading text, e.g. the stored title
#: ``<u>PART I</u> Introduction to Flask``. Stripped for display only - the
#: citation anchor is still derived from the raw title so it keeps matching the
#: id the page renderer emits for that heading.
_MARKUP = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


def plain_title(title: Optional[str]) -> Optional[str]:
    """Return ``title`` with inline markup removed, for display and prompting.

    507 of the 4,080 ingested chunks carry HTML from the source textbooks, which
    otherwise reaches the student as literal ``<u>CHAPTER 16</u>`` in the sources
    panel and as noise in the tutor's prompt.
    """
    if not title:
        return title
    cleaned = _WHITESPACE.sub(" ", _MARKUP.sub("", title)).strip()
    return cleaned or title



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
    #: Citation label shown to the model and rendered as a link for the student,
    #: e.g. ``C1``.
    cite_key: str = ""
    #: Deep link into the rendered corpus page, anchored on this chunk's section.
    url: str = ""
    anchor: str = ""

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "cite_key": self.cite_key,
            "module_id": self.module_id,
            "source_name": self.source_name,
            "source_file": self.source_file,
            "section_title": self.section_title,
            "distance": round(self.distance, 4),
            "source_category": self.source_category,
            "is_answer": self.is_answer,
            "url": self.url,
            "anchor": self.anchor,
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

    def sync_third_party_flag(self) -> None:
        """Re-derive the third-party disclosure from the chunks actually served.

        ``third_party_fallback`` is a disclosure the UI renders as "none of your
        module material matched", so it has to describe the context the student is
        shown rather than the route that produced it. Both retrieval passes that
        can hand back textbook chunks have to call this: the widening branch in
        :meth:`Retriever.retrieve`, and the reciprocal-rank merge in
        ``TutoringWorkflow._fuse`` - which builds a fresh result and would
        otherwise silently reset the flag to its ``False`` default.
        """
        if any(c.source_category in THIRD_PARTY_CATEGORIES for c in self.chunks):
            self.third_party_fallback = True

    def context_text(self) -> str:
        """Flatten everything retrieved into one prompt-ready block.

        Each curriculum block carries a citation label, its source document and
        its section heading, because the prompt asks the tutor to attribute each
        claim. A student can then follow the label to the exact notes.
        """
        blocks: list[str] = []
        for chunk in self.chunks:
            label = chunk.cite_key or f"C{self.chunks.index(chunk) + 1}"
            title = plain_title(chunk.section_title) or chunk.source_name
            blocks.append(
                f"[{label}] {chunk.source_file} :: \"{title}\"\n{chunk.text}"
            )
        for index, pattern in enumerate(self.patterns, start=1):
            blocks.append(
                f"[Code pattern {index}] {pattern.error_title} "
                f"({pattern.exception_thrown or 'n/a'})\n"
                f"Conceptual hint: {pattern.conceptual_tutor_hint}"
            )
        return "\n\n".join(blocks)

    def citations(self) -> list[dict]:
        """Citation list for the API response, in the order the model saw them."""
        return [
            {
                "cite_key": chunk.cite_key,
                "module_id": chunk.module_id,
                "source_file": chunk.source_file,
                "section_title": plain_title(chunk.section_title),
                "url": chunk.url,
                "anchor": chunk.anchor,
                "distance": round(chunk.distance, 4),
                "source_category": chunk.source_category,
                "is_answer": chunk.is_answer,
            }
            for chunk in self.chunks
            if chunk.cite_key
        ]


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
                # Widening exists so a thin module result does not leave the tutor
                # with no context, but a textbook chapter about an unrelated topic
                # is worse than no context: the tutor cites it, so the student is
                # shown a confident answer attributed to their course material that
                # never said it. Require the widened hits to be clearly closer than
                # the module's own weakest hit before substituting them.
                floor = result.chunks[-1].distance if result.chunks else None
                ceiling = policy.max_distance
                if floor is not None:
                    ceiling = min(ceiling, floor) if ceiling is not None else floor
                widened = RetrievalPolicy(
                    allow_answers=policy.allow_answers,
                    include_third_party=True,
                    max_distance=ceiling,
                )
                module_hits = len(result.chunks)
                module_below = result.below_threshold
                chunks, below = self._search_curriculum(session, vector, module_id, widened)
                if len(chunks) > module_hits:
                    result.chunks = chunks
                    result.below_threshold = below
                    result.third_party_fallback = True
                    result.policy = widened
                    logger.info(
                        "Only %d module chunk(s) for %r in %s; widened to %d third-party chunk(s) "
                        "at distance <= %.4f",
                        module_hits,
                        query[:60],
                        module_id,
                        len(chunks) - module_hits,
                        ceiling if ceiling is not None else float("nan"),
                    )
                else:
                    logger.info(
                        "Only %d module chunk(s) for %r in %s; no third-party content was close "
                        "enough to substitute",
                        module_hits,
                        query[:60],
                        module_id,
                    )
                    result.below_threshold = module_below
            if include_patterns:
                result.patterns = self._search_patterns(session, vector, module_id)

        # Disclosure must describe the chunks that were actually returned, not
        # just the branch that produced them - see sync_third_party_flag.
        result.sync_third_party_flag()

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
            url, anchor = citation_url(
                chunk.module_id, chunk.source_file, chunk.section_title
            )
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
                    cite_key=f"C{len(chunks) + 1}",
                    url=url,
                    anchor=anchor,
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
