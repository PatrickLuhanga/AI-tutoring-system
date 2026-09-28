"""RAG retrieval (Tier 2, section 8 of the architecture document).

Hybrid search ported from the StatSSA Rafiki RAG design
(https://github.com/voltedge-africa/statssa-rafiki):

1. Tokenise the query, drop stopwords and keep only *discriminative* terms -
   terms that occur in the corpus but in at most ``RETRIEVAL_MAX_DF_RATIO`` of
   its source documents (so boilerplate words like "java" or "module" cannot
   match the whole corpus).
2. Run two module-filtered branches in parallel:
   * keyword search - ``ts_rank_cd`` over the generated ``content_tsv`` column
     (PostgreSQL full-text, ``simple`` config), OR-ing the quoted terms;
   * semantic search - pgvector cosine distance, gated by a minimum similarity
     that tightens when the query has no corpus-grounded terms at all.
3. Fuse the two rankings with Reciprocal Rank Fusion
   (``score = weight / (RRF_K + rank)`` summed across branches) and return the
   top-k chunks.

The ``RetrievedChunk`` / ``RetrievedPattern`` dataclasses and the ``module_id``
metadata filter are unchanged from the semantic-only implementation.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import func, or_, select

from .config import settings
from .db import session_scope
from .embeddings import get_embedder
from .models import CodeRepairPattern, CurriculumChunk

logger = logging.getLogger(__name__)

#: Words that carry no discriminative signal in a student query (subset of the
#: StatSSA Rafiki stopword list).
STOPWORDS = frozenset(
    {
        "a", "about", "an", "and", "are", "as", "at", "based", "be", "been", "by",
        "can", "could", "describe", "did", "do", "does", "explain", "for", "from",
        "give", "had", "has", "have", "he", "her", "his", "how", "i", "in", "into",
        "is", "it", "its", "like", "list", "me", "my", "need", "of", "on",
        "opinion", "or", "our", "over", "provide", "regarding", "she", "should",
        "show", "so", "support", "tell", "than", "that", "the", "their", "them",
        "then", "there", "these", "they", "this", "those", "to", "us", "use",
        "used", "using", "want", "was", "we", "were", "what", "when", "where",
        "which", "who", "why", "will", "with", "would", "you", "your",
    }
)

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _tokenize(query: str) -> list[str]:
    """Lowercase alphanumeric terms, deduped, without stopwords or 1-char tokens."""
    terms = _TOKEN_RE.findall(query.lower())
    return [term for term in dict.fromkeys(terms) if len(term) > 1 and term not in STOPWORDS]


def _ts_query(terms: list[str]) -> str:
    """OR the terms into a ``tsquery`` literal; terms are already alphanumeric."""
    return " | ".join(f"'{term}'" for term in terms)


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


# ---------------------------------------------------------------------------
# Fusion bookkeeping
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class _FusedHit:
    chunk_id: int
    module_id: str
    source_name: str
    source_file: str
    section_title: Optional[str]
    text: str
    distance: float
    score: float = 0.0
    keyword_rank: Optional[int] = None
    vector_rank: Optional[int] = None


def _rrf_score(rank: int, weight: float) -> float:
    return weight / (settings.retrieval_rrf_k + rank)


def _fuse(keyword: list[_FusedHit], semantic: list[_FusedHit]) -> list[_FusedHit]:
    """Reciprocal Rank Fusion of the two ranked lists (score = w/(RRF_K + rank))."""
    merged: dict[int, _FusedHit] = {}
    keyword_weight = settings.retrieval_keyword_weight
    vector_weight = settings.retrieval_vector_weight

    for index, hit in enumerate(keyword):
        hit.keyword_rank = index + 1
        hit.score += _rrf_score(index + 1, keyword_weight)
        merged[hit.chunk_id] = hit

    for index, hit in enumerate(semantic):
        contribution = _rrf_score(index + 1, vector_weight)
        existing = merged.get(hit.chunk_id)
        if existing is not None:
            existing.score += contribution
            existing.vector_rank = index + 1
            existing.distance = hit.distance
        else:
            hit.vector_rank = index + 1
            hit.score += contribution
            merged[hit.chunk_id] = hit

    return sorted(merged.values(), key=lambda hit: hit.score, reverse=True)


class Retriever:
    """Hybrid module-scoped retrieval (tsvector keyword + pgvector semantic + RRF)."""

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
            result.chunks = self._search_curriculum(session, query, vector, module_id)
            if include_patterns:
                result.patterns = self._search_patterns(session, query, vector, module_id)

        logger.info(
            "Retrieved %d chunks and %d patterns for module %s",
            len(result.chunks),
            len(result.patterns),
            module_id,
        )
        return result

    # -- Curriculum chunks ---------------------------------------------------
    def _search_curriculum(
        self, session, query: str, vector: list[float], module_id: str
    ) -> list[RetrievedChunk]:
        if not settings.retrieval_hybrid_enabled:
            return self._semantic_chunks_only(session, vector, module_id, self.top_k)

        pool = max(self.top_k * settings.retrieval_hybrid_pool_factor, settings.retrieval_hybrid_min_pool)
        try:
            terms = self._discriminative_terms(session, CurriculumChunk, query, module_id)
        except Exception as exc:  # noqa: BLE001 - keyword branch must not kill the turn
            logger.warning("Discriminative-term scan failed; disabling keyword branch: %s", exc)
            terms = []

        min_similarity = (
            settings.retrieval_min_similarity if terms else settings.retrieval_strict_similarity
        )
        semantic = self._vector_hits(session, CurriculumChunk, vector, module_id, pool, min_similarity)

        keyword: list[_FusedHit] = []
        if terms:
            try:
                keyword = self._keyword_hits(session, CurriculumChunk, terms, module_id, pool)
            except Exception as exc:  # noqa: BLE001 - degrade to semantic-only
                logger.warning("Keyword branch failed; using semantic ranking only: %s", exc)

        fused = _fuse(keyword, semantic) if keyword else semantic
        return [
            RetrievedChunk(
                chunk_id=hit.chunk_id,
                module_id=hit.module_id,
                source_name=hit.source_name,
                source_file=hit.source_file,
                section_title=hit.section_title,
                text=hit.text,
                distance=hit.distance,
            )
            for hit in fused[: self.top_k]
        ]

    def _semantic_chunks_only(self, session, vector: list[float], module_id: str, limit: int) -> list[RetrievedChunk]:
        """Pure-cosine fallback used when hybrid retrieval is disabled."""
        distance = CurriculumChunk.embedding.cosine_distance(vector).label("distance")
        rows = (
            session.execute(
                select(CurriculumChunk, distance)
                .where(CurriculumChunk.module_id == module_id)
                .order_by(distance)
                .limit(limit)
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

    # -- Code-repair patterns ------------------------------------------------
    def _search_patterns(
        self, session, query: str, vector: list[float], module_id: str
    ) -> list[RetrievedPattern]:
        # Patterns tagged for this module, plus "general" patterns with no tag.
        module_clause = or_(
            CodeRepairPattern.module_id == module_id,
            CodeRepairPattern.module_id.is_(None),
        )

        if not settings.retrieval_hybrid_enabled:
            rows = self._semantic_patterns_only(session, vector, module_clause, self.code_top_k)
            return rows

        pool = max(self.code_top_k * settings.retrieval_hybrid_pool_factor, settings.retrieval_hybrid_min_pool)
        try:
            terms = self._discriminative_terms(session, CodeRepairPattern, query, module_id, module_clause)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Pattern discriminative-term scan failed; disabling keyword branch: %s", exc)
            terms = []

        min_similarity = (
            settings.retrieval_min_similarity if terms else settings.retrieval_strict_similarity
        )
        semantic = self._vector_hits(session, CodeRepairPattern, vector, module_id, pool, min_similarity, module_clause)

        keyword: list[_FusedHit] = []
        if terms:
            try:
                keyword = self._keyword_hits(session, CodeRepairPattern, terms, module_id, pool, module_clause)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Pattern keyword branch failed; using semantic ranking only: %s", exc)

        # The pattern corpus is tiny and semantically strong, so a keyword-only
        # match (no semantic grounding) is almost always a false positive (e.g.
        # the word "java" in an unrelated snippet). Only fuse keyword hits that
        # the gated semantic branch also surfaced.
        semantic_ids = {hit.chunk_id for hit in semantic}
        keyword = [hit for hit in keyword if hit.chunk_id in semantic_ids]

        fused = _fuse(keyword, semantic) if keyword else semantic
        rows = session.execute(
            select(CodeRepairPattern).where(CodeRepairPattern.pattern_id.in_([h.chunk_id for h in fused[: self.code_top_k]]))
        ).scalars().all() if fused else []
        pattern_by_id = {pattern.pattern_id: pattern for pattern in rows}
        return [
            RetrievedPattern(
                pattern_id=hit.chunk_id,
                module_id=pattern_by_id[hit.chunk_id].module_id,
                error_title=pattern_by_id[hit.chunk_id].error_title,
                exception_thrown=pattern_by_id[hit.chunk_id].exception_thrown,
                conceptual_tutor_hint=pattern_by_id[hit.chunk_id].conceptual_tutor_hint,
                broken_code=pattern_by_id[hit.chunk_id].broken_code,
                distance=hit.distance,
            )
            for hit in fused[: self.code_top_k]
        ]

    def _semantic_patterns_only(self, session, vector: list[float], module_clause, limit: int) -> list[RetrievedPattern]:
        distance = CodeRepairPattern.embedding.cosine_distance(vector).label("distance")
        rows = (
            session.execute(
                select(CodeRepairPattern, distance)
                .where(module_clause)
                .order_by(distance)
                .limit(limit)
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

    # -- Branch primitives ---------------------------------------------------
    @staticmethod
    def _doc_id_column(model):
        """The 'document' identity column used for document-frequency pruning."""
        return CurriculumChunk.source_file if model is CurriculumChunk else CodeRepairPattern.pattern_id

    def _discriminative_terms(self, session, model, query: str, module_id: str, module_clause=None) -> list[str]:
        """Terms occurring in >0 and <= MAX_DF_RATIO of the *module's* documents.

        Unlike the StatSSA port (global document frequency), the scope here is
        the module the query is filtered by anyway: a term shared by most of a
        module's documents (e.g. "java" inside IPRT301, "research" inside
        RESK301) carries no discriminative signal within that module.
        """
        doc_column = self._doc_id_column(model)
        scope = model.module_id == module_id if module_clause is None else module_clause
        total = session.execute(
            select(func.count(func.distinct(doc_column))).select_from(model).where(scope)
        ).scalar_one()
        if not total:
            return []
        max_df = max(1, math.floor(total * settings.retrieval_max_df_ratio))

        kept: list[str] = []
        for term in _tokenize(query):
            df = session.execute(
                select(func.count(func.distinct(doc_column)))
                .select_from(model)
                .where(scope, model.content_tsv.op("@@")(func.to_tsquery("simple", f"'{term}'")))
            ).scalar_one()
            if 0 < df <= max_df:
                kept.append(term)
        return kept

    def _term_idf(self, session, model, terms: list[str], module_id: str, module_clause=None) -> dict[str, float]:
        """IDF weight per term: ln(N / df) + 1, N = module document count."""
        doc_column = self._doc_id_column(model)
        scope = model.module_id == module_id if module_clause is None else module_clause
        total = session.execute(
            select(func.count(func.distinct(doc_column))).select_from(model).where(scope)
        ).scalar_one() or 1
        idf: dict[str, float] = {}
        for term in terms:
            df = session.execute(
                select(func.count(func.distinct(doc_column)))
                .select_from(model)
                .where(scope, model.content_tsv.op("@@")(func.to_tsquery("simple", f"'{term}'")))
            ).scalar_one() or 0
            idf[term] = math.log(total / max(df, 1)) + 1.0
        return idf

    @staticmethod
    def _keyword_score(text: str, terms: list[str], idf: dict[str, float]) -> float:
        """IDF-weighted term coverage over a chunk's text.

        A term counts when it appears as a standalone lexeme *or* as a substring
        (camelCase identifiers like ``ChangeListener`` inside
        ``addChangeListener``), which tsvector's exact lexeme matching misses.
        """
        lowered = (text or "").lower()
        score = 0.0
        for term in terms:
            if f" {term} " in f" {lowered} " or term in lowered:
                score += idf.get(term, 1.0)
        return score

    @staticmethod
    def _lexical_text(model, row) -> str:
        """Full searchable text of a row, for coverage scoring."""
        if model is CurriculumChunk:
            return row.chunk_text
        return f"{row.error_title} {row.broken_code} {row.conceptual_tutor_hint}"

    def _keyword_hits(
        self,
        session,
        model,
        terms: list[str],
        module_id: str,
        limit: int,
        module_clause=None,
    ) -> list[_FusedHit]:
        """Full-text keyword branch.

        Candidates come from two PostgreSQL lookups: the generated ``content_tsv``
        column (GIN-indexed tsvector match) and a pg_trgm substring sweep for
        camelCase identifiers that tsvector's exact lexemes miss. Both are then
        ranked with an IDF-weighted term-coverage score (plus a ts_rank_cd
        component), so a chunk mentioning a rare query term outranks one that
        merely repeats a common one.
        """
        query_literal = _ts_query(terms)
        ts_match = model.content_tsv.op("@@")(func.to_tsquery("simple", query_literal))
        rank_cd = func.ts_rank_cd(model.content_tsv, func.to_tsquery("simple", query_literal))
        clause = model.module_id == module_id if module_clause is None else module_clause
        idf = self._term_idf(session, model, terms, module_id, module_clause)

        # Bound the tsvector branch *before* materialising rows: on a large
        # module a broad tsquery can match thousands of chunks, so keep only the
        # best-ranked candidates in the same pool size the substring sweep uses.
        ts_rows = (
            session.execute(
                select(model, rank_cd.label("rank_cd"))
                .where(clause, ts_match)
                .order_by(rank_cd.desc())
                .limit(limit * 4)
            )
            .all()
        )

        lexical = func.lower(self._lexical_text_column(model))
        substring_any = or_(*[lexical.like(f"%{term}%") for term in terms])
        sub_rows = session.execute(
            select(model).where(clause, ~ts_match, substring_any).limit(limit * 4)
        ).all()

        scored_by_id: dict[int, tuple[float, model, float]] = {}
        for row, row_rank in ts_rows:
            text = self._lexical_text(model, row)
            score = self._keyword_score(text, terms, idf) + 0.5 * float(row_rank or 0.0)
            scored_by_id[getattr(row, "chunk_id" if model is CurriculumChunk else "pattern_id")] = (score, row, float(row_rank or 0.0))
        for (row,) in sub_rows:
            row_id = getattr(row, "chunk_id" if model is CurriculumChunk else "pattern_id")
            text = self._lexical_text(model, row)
            score = self._keyword_score(text, terms, idf)
            if row_id not in scored_by_id or score > scored_by_id[row_id][0]:
                scored_by_id[row_id] = (score, row, 0.0)
        scored = sorted(scored_by_id.values(), key=lambda item: item[0], reverse=True)[:limit]

        hits: list[_FusedHit] = []
        for _score, row, _rank in scored:
            if model is CurriculumChunk:
                hits.append(
                    _FusedHit(
                        chunk_id=row.chunk_id,
                        module_id=row.module_id,
                        source_name=row.source_name,
                        source_file=row.source_file,
                        section_title=row.section_title,
                        text=row.chunk_text,
                        distance=1.0,
                    )
                )
            else:
                hits.append(
                    _FusedHit(
                        chunk_id=row.pattern_id,
                        module_id=row.module_id or "",
                        source_name=row.error_title,
                        source_file="",
                        section_title=None,
                        text=row.conceptual_tutor_hint,
                        distance=1.0,
                    )
                )
        return hits

    @staticmethod
    def _lexical_text_column(model):
        if model is CurriculumChunk:
            return CurriculumChunk.chunk_text
        return func.concat(
            CodeRepairPattern.error_title, " ",
            CodeRepairPattern.broken_code, " ",
            CodeRepairPattern.conceptual_tutor_hint,
        )

    def _vector_hits(
        self,
        session,
        model,
        vector: list[float],
        module_id: str,
        limit: int,
        min_similarity: float,
        module_clause=None,
    ) -> list[_FusedHit]:
        cosine_distance = model.embedding.cosine_distance(vector)
        clause = model.module_id == module_id if module_clause is None else module_clause
        rows = (
            session.execute(
                select(model, cosine_distance.label("distance"))
                .where(clause, model.embedding.is_not(None), (1 - cosine_distance) >= min_similarity)
                .order_by(cosine_distance)
                .limit(limit)
            )
            .all()
        )
        hits: list[_FusedHit] = []
        for row, distance in rows:
            if model is CurriculumChunk:
                hits.append(
                    _FusedHit(
                        chunk_id=row.chunk_id,
                        module_id=row.module_id,
                        source_name=row.source_name,
                        source_file=row.source_file,
                        section_title=row.section_title,
                        text=row.chunk_text,
                        distance=float(distance),
                    )
                )
            else:
                hits.append(
                    _FusedHit(
                        chunk_id=row.pattern_id,
                        module_id=row.module_id or "",
                        source_name=row.error_title,
                        source_file="",
                        section_title=None,
                        text=row.conceptual_tutor_hint,
                        distance=float(distance),
                    )
                )
        return hits


_RETRIEVER: Optional[Retriever] = None


def get_retriever() -> Retriever:
    """Return the process-wide retriever singleton."""
    global _RETRIEVER
    if _RETRIEVER is None:
        _RETRIEVER = Retriever()
    return _RETRIEVER
