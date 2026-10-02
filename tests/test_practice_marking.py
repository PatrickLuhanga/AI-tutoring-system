"""Tests for how a practice attempt is marked.

The property under test is a safety one: **only a human-authored answer may be
marked against automatically.** Marking is exact match, so an answer the model
drafted is a trap - if it is subtly wrong, a student who answered correctly is
scored wrong, which penalises them for the bank's inaccuracy rather than for
their own work.
"""

from __future__ import annotations

import pytest

from src.db import session_scope
from src.models import Question
from src.practice import (
    PracticeError,
    add_question,
    draw_test,
    get_question,
    mark_attempt,
    update_question,
)

MODULE = "IPRT301"

#: Distinctive prompts: the real bank contains questions about interfaces and
#: abstracts, and a purge keyed on a plausible prompt would delete real rows.
P_A = "zt-mark-A: explain why an interface decouples a client from an implementation"
P_B = "zt-mark-B: describe the role of an abstract base class in a hierarchy"
P_C = "zt-mark-C: state how dynamic binding relates to method overriding here"


@pytest.fixture(autouse=True)
def _clean():
    def purge():
        with session_scope() as session:
            session.query(Question).filter(Question.prompt.in_((P_A, P_B, P_C))).delete(
                synchronize_session=False
            )
    purge()
    yield
    purge()


def _q(prompt: str, *, answer=None, answer_source=None, difficulty="medium") -> dict:
    return add_question(
        module_id=MODULE,
        prompt=prompt,
        created_by=None,
        answer_notes=answer,
        answer_source=answer_source,
        difficulty=difficulty,
        origin="authored",
    )


# ---------------------------------------------------------------------------
# The safety property
# ---------------------------------------------------------------------------
def test_an_authored_answer_is_marked_automatically():
    q = _q(P_A, answer="An interface decouples client from implementation.", answer_source="authored")
    result = mark_attempt(
        module_id=MODULE,
        question_ids=[q["question_id"]],
        answers={str(q["question_id"]): "An interface decouples client from implementation."},
        student_id=None,
        user_id=None,
    )
    assert result["score"] == 1
    assert result["max_score"] == 1
    assert result["breakdown"][0]["mode"] == "auto"


def test_a_generated_answer_is_shown_but_never_marked_against():
    """The whole point: a wrong draft must not be able to cost a student a mark."""
    q = _q(P_B, answer="An abstract base class provides shared implementation.", answer_source="generated")
    result = mark_attempt(
        module_id=MODULE,
        question_ids=[q["question_id"]],
        # Deliberately a *wrong* answer. If the draft were used as a key this
        # would still be wrong, but the failure that matters is the reverse:
        # assert the student is NOT told they are wrong by a machine draft.
        answers={str(q["question_id"]): "Something entirely different."},
        student_id=None,
        user_id=None,
    )
    assert result["score"] == 0
    assert result["max_score"] == 0, "a generated answer must not enter the denominator"
    assert result["breakdown"][0]["markable"] is False
    assert result["breakdown"][0]["mode"] == "reference"


def test_a_generated_answer_is_still_returned_as_a_reference():
    q = _q(P_B, answer="An abstract base class provides shared implementation.", answer_source="generated")
    result = mark_attempt(
        module_id=MODULE,
        question_ids=[q["question_id"]],
        answers={},
        student_id=None,
        user_id=None,
    )
    assert result["breakdown"][0]["answer_notes"] == "An abstract base class provides shared implementation."
    assert result["reference_only"] == 1


def test_an_unrecognised_answer_source_is_not_treated_as_authored():
    """An unknown label means nothing vouched for it, so it is not graded."""
    q = _q(P_C, answer="Dynamic binding links an override to the actual receiver.", answer_source="mystery")
    result = mark_attempt(
        module_id=MODULE,
        question_ids=[q["question_id"]],
        answers={str(q["question_id"]): "Dynamic binding links an override to the actual receiver."},
        student_id=None,
        user_id=None,
    )
    assert result["max_score"] == 0
    assert result["breakdown"][0]["mode"] == "reference"


def test_a_question_with_no_answer_at_all_is_reported_separately():
    _q(P_C)
    q = _q(P_C)
    result = mark_attempt(
        module_id=MODULE,
        question_ids=[q["question_id"]],
        answers={str(q["question_id"]): "anything"},
        student_id=None,
        user_id=None,
    )
    assert result["breakdown"][0]["mode"] == "none"
    assert result["no_answer"] == 1
    assert result["reference_only"] == 0
    assert result["unmarked"] == 1


# ---------------------------------------------------------------------------
# Approving a draft: the only way a seeded question ever starts being scored
# ---------------------------------------------------------------------------
def test_approving_a_draft_turns_it_into_a_marked_question():
    q = _q(P_A, answer="An interface decouples client from implementation.", answer_source="generated")

    before = mark_attempt(
        module_id=MODULE,
        question_ids=[q["question_id"]],
        answers={str(q["question_id"]): "An interface decouples client from implementation."},
        student_id=None, user_id=None,
    )
    assert before["max_score"] == 0

    update_question(q["question_id"], answer_source="authored")

    after = mark_attempt(
        module_id=MODULE,
        question_ids=[q["question_id"]],
        answers={str(q["question_id"]): "An interface decouples client from implementation."},
        student_id=None, user_id=None,
    )
    assert after["max_score"] == 1
    assert after["score"] == 1
    assert after["breakdown"][0]["mode"] == "auto"


def test_editing_an_answer_does_not_silently_promote_it():
    """Tidying a typo in a draft must not turn it into a marking key."""
    q = _q(P_B, answer="An abstract base class.", answer_source="generated")
    updated = update_question(q["question_id"], answer_notes="An abstract base class, revised.")
    assert updated["answer_source"] == "generated"


def test_clearing_the_answer_clears_its_source():
    """A source with no answer would imply a key exists for a question without one."""
    q = _q(P_C, answer="Dynamic binding links an override.", answer_source="authored")
    updated = update_question(q["question_id"], answer_notes=None, answer_source="authored")
    assert updated["answer_notes"] is None
    assert updated["answer_source"] is None


def test_an_update_touches_only_the_fields_supplied():
    q = _q(P_A, answer="An interface decouples.", answer_source="generated", difficulty="easy")
    updated = update_question(q["question_id"], difficulty="hard")
    assert updated["difficulty"] == "hard"
    assert updated["answer_notes"] == "An interface decouples."
    assert updated["prompt"] == P_A


def test_an_unknown_field_is_refused():
    q = _q(P_C, answer="An answer here.")
    with pytest.raises(PracticeError, match="Cannot update"):
        update_question(q["question_id"], nonsense="x")


def test_updating_a_missing_question_returns_none():
    assert update_question(999_999_999, difficulty="easy") is None
    assert get_question(999_999_999) is None


# ---------------------------------------------------------------------------
# Mixed sets
# ---------------------------------------------------------------------------
def test_a_mixed_set_scores_only_the_authored_questions():
    auto = _q(P_A, answer="An interface decouples client from implementation.", answer_source="authored")
    ref = _q(P_B, answer="An abstract base class provides shared implementation.", answer_source="generated")
    none_ = _q(P_C)

    result = mark_attempt(
        module_id=MODULE,
        question_ids=[auto["question_id"], ref["question_id"], none_["question_id"]],
        answers={
            str(auto["question_id"]): "An interface decouples client from implementation.",
            str(ref["question_id"]): "my own attempt",
            str(none_["question_id"]): "an attempt",
        },
        student_id=None,
        user_id=None,
    )
    assert result["score"] == 1
    assert result["max_score"] == 1
    assert result["reference_only"] == 1
    assert result["no_answer"] == 1
    modes = {b["question_id"]: b["mode"] for b in result["breakdown"]}
    assert modes[auto["question_id"]] == "auto"
    assert modes[ref["question_id"]] == "reference"
    assert modes[none_["question_id"]] == "none"


def test_an_attempt_is_recorded_even_when_nothing_could_be_marked():
    """The attempt row is what later progress features would read, so it is
    written even with a zero denominator rather than dropped."""
    ref = _q(P_B, answer="An abstract base class provides shared implementation.", answer_source="generated")
    result = mark_attempt(
        module_id=MODULE,
        question_ids=[ref["question_id"]],
        answers={str(ref["question_id"]): "attempt"},
        student_id=None,
        user_id=None,
    )
    assert result["attempt_id"]


# ---------------------------------------------------------------------------
# Existing contracts that must not regress
# ---------------------------------------------------------------------------
def test_an_unknown_question_id_is_rejected():
    with pytest.raises(PracticeError):
        mark_attempt(
            module_id=MODULE,
            question_ids=[999_999_999],
            answers={},
            student_id=None,
            user_id=None,
        )


def test_an_empty_attempt_is_rejected():
    with pytest.raises(PracticeError):
        mark_attempt(
            module_id=MODULE, question_ids=[], answers={}, student_id=None, user_id=None
        )


def test_drawn_questions_never_leak_their_answers():
    _q(P_A, answer="An interface decouples client from implementation.", answer_source="authored")
    drawn = draw_test(module_id=MODULE, count=5, seed=1)
    assert "answer_notes" not in drawn["questions"][0]


# ---------------------------------------------------------------------------
# The round trip through the fixture loader
# ---------------------------------------------------------------------------
def test_the_loader_carries_answer_source(tmp_path):
    """A generated answer must stay generated after a reload, or a restart would
    silently promote a draft into an auto-graded key."""
    import json

    from src.seed_question_bank import SCHEMA_VERSION, load_bank

    payload = {
        "schema_version": SCHEMA_VERSION,
        "questions": [
            {
                "module_id": MODULE,
                "prompt": P_C,
                "answer_notes": "A draft answer that must never be graded.",
                "answer_source": "generated",
                "difficulty": "medium",
                "origin": "past_paper",
                "source_label": "2024 - Midyear Main - IPRT301 (p3) (OCR - verify)",
            }
        ],
    }
    path = tmp_path / "bank.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    load_bank(path)

    with session_scope() as session:
        row = session.query(Question).filter(Question.prompt == P_C).one()
        assert row.answer_source == "generated"

    # And the loader must not have turned it into something gradeable.
    result = mark_attempt(
        module_id=MODULE,
        question_ids=[int(row.question_id)],
        answers={str(row.question_id): "A draft answer that must never be graded."},
        student_id=None,
        user_id=None,
    )
    assert result["max_score"] == 0