"""Adaptive query expansion for abstract questions (section 8.3).

A high-level question often names a concept the corpus *does* teach but in
different words. "Why does code reuse matter?" is answerable from the IPRT
inheritance slides, yet it shares few terms with them, so the embedding lands
outside the distance floor and the tutor either answers from memory or says it
found nothing. Neither is right: the material is there.

The fix is a second retrieval hop, taken only when the first was weak. The
expansion is grounded in the module's own section titles rather than invented by
the model, so a rewrite is pushed towards the vocabulary the notes actually use
instead of drifting somewhere the corpus never mentions.

This costs one extra generation, and only on the turns that need it, which is why
it lives behind a trigger rather than running on every query.
"""

from __future__ import annotations

import logging
import re
from typing import Optional, Sequence

from .config import settings
from .inference import LLMError

logger = logging.getLogger(__name__)

_EXPANSION_SYSTEM = """\
You rewrite a student's question into short search queries for a course-notes
search engine. You are NOT answering the question.

Rules:
- Output one query per line, 3 to 6 words each, no numbering, no punctuation
  beyond spaces, no explanation.
- Each query must reuse vocabulary from the SECTION TITLES provided, because
  those are the only notes that exist.
- Cover different angles: the definition, the mechanism, the benefit or purpose.
- The student's question is often abstract and phrased in words the notes never
  use. Generalise to the concept the notes actually teach, and always give your
  best two grounded guesses even when the match is not obvious. Do not output
  nothing: a wrong guess costs one extra search, whereas refusing costs the
  student an answer the material does contain.
- Never name a topic that is absent from the section titles.
"""

_LINE_RE = re.compile(r"^\s*(?:\d+[.)]\s*)?([A-Za-z0-9][A-Za-z0-9 \-]{2,48}?)\s*$")


class QueryExpander:
    """Produces alternative search phrasings for a question that retrieved weakly."""

    def __init__(self, router=None) -> None:
        self._router = router

    def _router_or_default(self):
        if self._router is not None:
            return self._router
        from .llm_router import get_router

        self._router = get_router()
        return self._router

    def expand(
        self,
        question: str,
        module_name: str,
        section_titles: Sequence[str],
        *,
        max_queries: int = 3,
    ) -> list[str]:
        """Return up to ``max_queries`` alternative phrasings.

        Returns ``[]`` on any failure: expansion is an enhancement, so a model
        error must degrade to the original single-shot behaviour rather than fail
        the turn.
        """
        titles = [t for t in dict.fromkeys(section_titles) if t][:60]
        if not titles or not (question or "").strip():
            return []

        listing = "\n".join(f"- {t}" for t in titles)
        prompt = (
            f"MODULE: {module_name}\n\n"
            f"SECTION TITLES AVAILABLE:\n{listing}\n\n"
            f"STUDENT QUESTION: {question.strip()}\n\n"
            "Search queries (one per line):"
        )
        try:
            response = self._router_or_default().generate(
                [
                    {"role": "system", "content": _EXPANSION_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=80,
            )
        except LLMError as exc:
            logger.info("Query expansion unavailable, keeping the original query: %s", exc)
            return []

        return self._parse(response.text, max_queries)

    @staticmethod
    def _parse(text: Optional[str], max_queries: int) -> list[str]:
        out: list[str] = []
        for raw in (text or "").splitlines():
            match = _LINE_RE.match(raw)
            if not match:
                continue
            candidate = match.group(1).strip()
            if len(candidate.split()) < 3:
                continue
            low = candidate.casefold()
            # Reject degenerate echoes. The model sometimes copies a section title
            # verbatim ("MODULE Platform Based Development"), which is a label
            # rather than a search query and retrieves nothing useful.
            if low.startswith(("module ", "chapter ", "slide ", "topic ")):
                continue
            if not any(ch.isalpha() for ch in candidate):
                continue
            if any(low == seen.casefold() for seen in out):
                continue
            out.append(candidate)
            if len(out) >= max_queries:
                break
        return out


def is_weak_result(chunks, *, weak_distance: float) -> bool:
    """True when a retrieval pass was too unsure to answer from.

    Two ways to be weak: nothing at all came back, or everything that came back
    is far enough away that the closest match is barely about the question.
    """
    if not chunks:
        return True
    return chunks[0].distance > weak_distance


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[int]],
    *,
    k: int = 60,
    limit: Optional[int] = None,
) -> list[int]:
    """Decide *which* chunks survive several runs, by reciprocal rank.

    RRF is used for selection rather than for ordering. Rank is the only signal
    comparable across runs when runs disagree about magnitude - a short rewrite
    legitimately scores closer than the long original question, so raw distances
    from different queries are not directly comparable either. The caller
    re-sorts the survivors by distance before showing them, so the strongest
    evidence still leads.
    """
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for position, identifier in enumerate(ranked):
            scores[identifier] = scores.get(identifier, 0.0) + 1.0 / (k + position + 1)
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    out = [identifier for identifier, _ in ordered]
    return out[:limit] if limit else out
