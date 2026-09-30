"""The retrieval distance ceiling has to reject off-topic material.

Regression coverage for a real defect: ``RETRIEVAL_MAX_DISTANCE`` shipped at
0.75, which is loose enough that a PBDV301 question ("what is research") was
answered from a Flask textbook chapter at distance 0.67 and cited as if it were
the student's course material. Worse, the citation looked authoritative - the
student had no way to tell the notes had nothing to do with the question.

The corpus is threshold-sensitive, so these assertions are written as
"relevant questions are still answered" and "clearly unrelated ones are not"
rather than pinning exact distances, which drift as the corpus is re-ingested.
"""

from __future__ import annotations

import pytest

from src.config import settings
from src.retriever import RetrievalPolicy, Retriever


@pytest.fixture()
def own_material_only():
    """A policy using the configured ceiling, with textbooks excluded."""
    return RetrievalPolicy(
        allow_answers=True,
        include_third_party=False,
        max_distance=settings.retrieval_max_distance,
    )


def _retrieve(retriever, policy, module_id, query):
    return retriever.retrieve(query, module_id, include_patterns=False, policy=policy)


def test_ceiling_is_tight_enough_to_reject_off_topic_material():
    """The whole point: 0.75 was nearly double the justified ceiling."""
    assert settings.retrieval_max_distance <= 0.45, (
        "RETRIEVAL_MAX_DISTANCE is loose enough for unrelated textbook "
        "chapters to be cited as course material"
    )


@pytest.mark.parametrize(
    ("module_id", "query"),
    [
        ("IPRT301", "how do I use a singleton pattern in Java"),
        ("PBDV301", "how do I create a Flask route"),
        ("RESK301", "what is research methodology"),
    ],
)
def test_on_topic_questions_are_still_grounded(
    corpus_loaded, retriever, own_material_only, module_id, query
):
    """Tightening the ceiling must not throw away genuine matches."""
    result = _retrieve(retriever, own_material_only, module_id, query)
    assert result.chunks, f"{module_id}: {query!r} lost its material at the new ceiling"
    assert result.citations()


@pytest.mark.parametrize(
    ("module_id", "query", "why"),
    [
        ("PBDV301", "what is research", "a Research Skills topic in a Flask module"),
        ("PBDV301", "explain inheritance and polymorphism", "an OOP topic, not web dev"),
        ("SPRI301", "what is professional ethics", "no close match in the corpus"),
    ],
)
def test_off_topic_questions_return_no_material(
    corpus_loaded, retriever, own_material_only, module_id, query, why
):
    """An unanswerable question must be queued for a human, not invented.

    Returning nothing is the correct outcome: the workflow queues the question
    (``no_context`` / ``below_threshold``) and a tutor answers it, which is what
    the help queue exists for.
    """
    result = _retrieve(retriever, own_material_only, module_id, query)
    assert not result.chunks, (
        f"{module_id}: {query!r} matched {len(result.chunks)} chunk(s) "
        f"({why}); the first was {result.chunks[0].source_file!r} at "
        f"distance {result.chunks[0].distance:.3f}"
    )
    assert result.is_empty


def test_no_citation_is_ever_invented_without_a_chunk(corpus_loaded, retriever, own_material_only):
    """An empty context must not produce a citation that points nowhere."""
    result = _retrieve(retriever, own_material_only, "PBDV301", "what is research")
    assert result.citations() == []


def test_citations_always_belong_to_the_requested_module(
    corpus_loaded, retriever, own_material_only
):
    """The metadata filter must hold whatever the ceiling is set to."""
    for module_id in ("IPRT301", "PBDV301", "RESK301", "SPRI301"):
        result = _retrieve(
            retriever, own_material_only, module_id, "explain the main idea of this module"
        )
        for chunk in result.chunks:
            assert chunk.module_id == module_id


def test_third_party_is_disclosed_whenever_it_is_served(corpus_loaded, retriever):
    """Textbook material must never be presented as the module's own notes."""
    policy = RetrievalPolicy(
        allow_answers=True,
        include_third_party=True,
        max_distance=settings.retrieval_max_distance,
    )
    result = _retrieve(retriever, policy, "PBDV301", "how do I create a Flask route")
    if any(c.source_category == "books" for c in result.chunks):
        assert result.third_party_fallback is True
