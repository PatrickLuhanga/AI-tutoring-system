"""Retrieval behaviour the paper's numbers depend on.

The distance floor, the third-party fallback and the answer-withholding rule are
what keep the reported recall figures honest: without them the tutor retrieves
whatever is nearest and answers confidently from it. These were verified by hand
at the time; they are pinned here now.
"""

from __future__ import annotations

import pytest

from src.config import settings
from src.loaders import THIRD_PARTY_CATEGORIES
from src.retriever import RetrievalPolicy, RetrievedChunk


def make_chunk(
    chunk_id: int = 1,
    module_id: str = "IPRT301",
    distance: float = 0.2,
    category: str = "slides",
    is_answer: bool = False,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        module_id=module_id,
        source_name="s",
        source_file=f"{module_id}/slides/x.md",
        section_title="s",
        text="t",
        distance=distance,
        source_category=category,
        is_answer=is_answer,
    )


# ---------------------------------------------------------------------------
# Citation metadata
# ---------------------------------------------------------------------------
def test_a_hand_built_chunk_has_no_label_until_search_assigns_one():
    """``cite_key`` is positional, so it is assigned during search in order.

    A chunk built outside the retriever therefore has none, and the dataclass
    default is empty rather than a misleading "C1".
    """
    assert make_chunk().cite_key == ""


def test_to_dict_exposes_what_the_frontend_needs():
    payload = make_chunk().to_dict()
    for key in (
        "chunk_id", "cite_key", "module_id", "source_file", "section_title",
        "distance", "source_category", "is_answer", "url", "anchor", "preview",
    ):
        assert key in payload, key
    # source_file was added with citations; the frontend reads it.
    assert payload["source_file"].endswith(".md")


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------
def test_the_distance_floor_is_configured_and_reachable():
    assert settings.retrieval_max_distance, "no floor means no way to return nothing"
    assert 0.0 < settings.retrieval_max_distance < 1.5


def test_policy_defaults_withhold_worked_solutions():
    policy = RetrievalPolicy.default()
    assert policy.allow_answers is False, (
        "worked solutions must be withheld until the Explanation stage"
    )


def test_policy_can_permit_answers_for_the_explanation_stage():
    assert RetrievalPolicy(allow_answers=True).allow_answers is True


def test_policy_defaults_exclude_third_party_material():
    assert RetrievalPolicy.default().include_third_party is False


def test_books_are_a_third_party_category():
    """The books are commercial text, not course material, and must be labelled."""
    assert "books" in THIRD_PARTY_CATEGORIES


# ---------------------------------------------------------------------------
# Live retrieval: the floor and the answer rule
# ---------------------------------------------------------------------------
@pytest.mark.needs_corpus
def test_a_well_grounded_question_returns_material(corpus_loaded):
    from src.retriever import get_retriever

    result = get_retriever().retrieve(
        "What is inheritance and why does it help with code reuse?",
        "IPRT301",
        include_patterns=False,
        policy=RetrievalPolicy.default(),
    )
    assert result.chunks, "a textbook-grounded question returned nothing"
    assert not result.is_empty
    assert result.chunks[0].distance < settings.retrieval_max_distance


@pytest.mark.needs_corpus
def test_an_off_topic_question_is_refused_rather_than_answered(corpus_loaded):
    """The floor is what makes "I found nothing" possible.

    Without it the tutor retrieves the least-bad chunk for any question and
    answers confidently, which is the failure the ungrounded-question loop exists
    to catch after the fact.
    """
    from src.retriever import get_retriever

    result = get_retriever().retrieve(
        "What is the optimal yeast hydration for a 72-hour cold ferment?",
        "IPRT301",
        include_patterns=False,
        policy=RetrievalPolicy.default(),
    )
    best = result.chunks[0].distance if result.chunks else None
    assert best is None or best > settings.retrieval_max_distance, (
        f"an off-topic question retrieved d={best:.3f}, inside the floor of "
        f"{settings.retrieval_max_distance}"
    )


@pytest.mark.needs_corpus
def test_no_worked_solution_leaks_before_the_explanation_stage(corpus_loaded):
    from src.retriever import get_retriever

    result = get_retriever().retrieve(
        "Show me the solution to the RMI square of numbers example",
        "IPRT301",
        include_patterns=False,
        policy=RetrievalPolicy.default(),
    )
    assert not any(c.is_answer for c in result.chunks), (
        "a worked solution was retrieved while answers were withheld"
    )


@pytest.mark.needs_corpus
def test_worked_solutions_become_available_at_the_explanation_stage(corpus_loaded):
    """The Exercise stage is only meaningful if withholding actually withholds.

    The corpus has a handful of tagged answer chunks (IPRT301 examples and
    exercises), so this asserts the flag changes what retrieval returns rather
    than that the flag exists.
    """
    from src.retriever import get_retriever

    retriever = get_retriever()
    query = "Show me the solution to the RMI square of numbers example"
    before = retriever.retrieve(
        query, "IPRT301", include_patterns=False, policy=RetrievalPolicy.default()
    )
    after = retriever.retrieve(
        query, "IPRT301", include_patterns=False, policy=RetrievalPolicy(allow_answers=True)
    )
    assert any(c.is_answer for c in after.chunks), (
        "the answer flag never unlocks anything; the Exercise stage is inert"
    )
    assert not any(c.is_answer for c in before.chunks), (
        "answers were visible before the Explanation stage"
    )


@pytest.mark.needs_corpus
def test_every_retrieved_chunk_carries_a_resolvable_citation(corpus_loaded):
    from src.retriever import get_retriever

    result = get_retriever().retrieve(
        "What is the difference between an interface and an abstract class?",
        "IPRT301",
        include_patterns=False,
        policy=RetrievalPolicy.default(),
    )
    citations = result.citations()
    assert citations, "retrieval produced no citations"
    assert [c["cite_key"] for c in citations] == [
        f"C{i}" for i in range(1, len(citations) + 1)
    ], "citation labels are not sequential from C1"


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------
def test_context_text_labels_every_block_so_the_model_can_cite_it():
    from src.retriever import RetrievalResult

    result = RetrievalResult(
        query="q",
        module_id="IPRT301",
        chunks=[make_chunk(1), make_chunk(2)],
    )
    text = result.context_text()
    assert "[C1]" in text and "[C2]" in text
    assert "x.md" in text, "the source file must be visible for the Sources panel"


def test_context_text_says_so_when_nothing_was_retrieved():
    from src.retriever import RetrievalResult

    result = RetrievalResult(query="q", module_id="IPRT301")
    assert result.context_text() == ""
    assert result.is_empty
