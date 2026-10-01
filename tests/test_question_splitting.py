"""Tests for splitting merged exam blocks into atomic questions.

Every case here is a shape that actually occurs in the corpus. The splitter's
job is as much about what it must *not* cut - a wrapped single question - as
about what it must cut, so both are pinned.
"""

from __future__ import annotations

import pytest

from src.split_exam_questions import (
    _opens_question,
    iter_atomic,
    split_blob,
    split_question,
    split_statement_list,
    split_true_false,
)


# ---------------------------------------------------------------------------
# What opens a question
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "line",
    [
        "State and briefly describe one advantage of an interface.",
        "Explain why multithreading matters in distributed applications.",
        "Briefly explain the importance of standardization.",
        "Describe three ways biasness occurs during technology development.",
        "What is the main challenge with Integrative programming?",
        "Which model theory explains the hypothetical agreement?",
        # A quoted claim with its instruction at the end - the reason the plain
        # "starts with a verb" rule is not enough.
        '"Interfaces are preferred to concrete classes". Explain why.',
        "“Interoperability is improved”. Briefly explain.",
    ],
)
def test_lines_that_open_a_question(line):
    assert _opens_question(line)


@pytest.mark.parametrize(
    "line",
    [
        # The tail of a question that wrapped across lines.
        "of heterogeneous hardware and software resources. Briefly explain.",
        "an example.",
        "which extends Employee). The method toString() returns the",
        # Lower-case words are not imperatives.
        "state machine diagram for the parser.",
        "includes protection against SQL injection.",
    ],
)
def test_lines_that_continue_the_previous_question(line):
    assert not _opens_question(line)


# ---------------------------------------------------------------------------
# Splitting a merged block
# ---------------------------------------------------------------------------
def test_a_multi_part_block_becomes_several_questions():
    block = (
        "State and briefly describe one advantage of an interface over abstract class.\n"
        '"Interfaces or abstracts classes are preferred to concrete class". Explain why.\n'
        "Briefly explain the importance of standardization in data exchange."
    )
    parts = split_blob(block)
    assert len(parts) == 3
    assert parts[0].startswith("State and briefly describe")
    assert parts[1].startswith('"Interfaces')
    assert parts[2].startswith("Briefly explain")


def test_a_wrapped_single_question_is_not_shredded():
    """The whole point of continuation lines: OCR wraps long questions."""
    block = (
        "The figure below shows the package jspem contains classes Employee (abstract)\n"
        "and FullTime (which extends Employee). The method toString() returns the"
    )
    assert split_blob(block) == [block]


def test_a_single_line_block_is_returned_unchanged():
    block = "Explain why the diagram cannot be converted to a Java program?"
    assert split_blob(block) == [block]


def test_leading_furniture_before_the_first_question_is_discarded():
    """A fragment above the first instruction is a header, not a question."""
    block = (
        "MID YEAR MAIN EXAMINATION\n"
        "Explain why multithreading is important in distributed applications."
    )
    parts = split_blob(block)
    assert len(parts) == 1
    assert parts[0].startswith("Explain why")


def test_an_unsplittable_fragment_yields_nothing():
    """A half-sentence is worse in the bank than no question at all."""
    assert split_blob("an example.") == []
    assert split_blob("") == []


# ---------------------------------------------------------------------------
# True/false sections
# ---------------------------------------------------------------------------
def test_a_true_false_list_is_recovered_statement_by_statement():
    block = (
        "State whether the following is true or false about polymorphism\n"
        "In Integrative programming, at least one component to be integrated must exist.\n"
        "An abstract class can extend another abstract class.\n"
        "Dynamic binding is linked to method override."
    )
    parts = split_true_false(block)
    assert len(parts) == 3
    for part in parts:
        assert part.startswith('State whether the following is true or false: "')
    assert parts[0].endswith('must exist."')


def test_numbering_is_not_invented_for_true_false_statements():
    """OCR lost which number belonged to which statement, so none is asserted."""
    parts = split_true_false(
        "Select true or false\n"
        "An abstract class can extend another abstract class.\n"
        "Dynamic binding is linked to method override."
    )
    assert not any(part.lstrip().startswith(("1.", "2.", "3.")) for part in parts)


def test_a_true_false_statement_that_carries_its_own_instruction_is_left_alone():
    parts = split_true_false(
        "State whether the following is true or false\n"
        "Explain how an abstract class differs from an interface.\n"
        "Describe the role of dynamic binding in polymorphism."
    )
    assert parts[0].startswith("Explain how")
    assert parts[1].startswith("Describe the role")


def test_a_true_false_section_with_too_few_statements_is_not_invented():
    assert split_true_false("State whether the following is true or false.\nA claim.") == []


def test_split_question_dispatches_to_the_true_false_strategy():
    block = (
        "State whether the following is true or false about polymorphism\n"
        "An abstract class can extend another abstract class.\n"
        "Dynamic binding is linked to method override."
    )
    assert len(split_question(block)) == 2


# ---------------------------------------------------------------------------
# Statement lists whose instruction was lost
# ---------------------------------------------------------------------------
def test_a_bare_statement_list_is_recovered_even_with_the_instruction_missing():
    """OCR sometimes drops the instruction, leaving only the assertions.

    Left whole this is ten questions in one row, and a model answering it
    replies to whichever assertion it read first - a fluent answer to a
    question nobody asked.
    """
    block = (
        "In Integrative programming, at least one of the components to be integrated must exist.\n"
        "An abstract class can extend another abstract class.\n"
        "The main purpose of an abstract classes is code reuse.\n"
        "Dynamic binding is linked to method override."
    )
    parts = split_question(block)
    assert len(parts) == 4
    assert all(p.startswith('State whether the following is true or false: "') for p in parts)


def test_a_wrapped_assertion_is_not_cut_in_half():
    """One statement split across two lines stays one statement."""
    block = (
        "An abstract class can extend another abstract class.\n"
        "You must convert a class which contains variables and method implementations to an\n"
        "interface.\n"
        "Dynamic binding is linked to method override."
    )
    parts = split_statement_list(block)
    assert len(parts) == 3
    assert parts[1].endswith("to an interface.\"")


def test_wrapped_prose_is_not_mistaken_for_a_statement_list():
    """A figure description wraps across lines and has few sentence ends."""
    block = (
        "The figure below shows the package jspem contains classes Employee (abstract)\n"
        "and FullTime (which extends Employee). The method toString() returns the\n"
        "name and salary of the employee and the class is in a package called jspem."
    )
    assert split_statement_list(block) == []
    assert len(split_question(block)) == 1


def test_a_two_line_list_is_not_forced_into_statements():
    assert split_statement_list("A claim about systems integration here.\nAnother claim about design.") == []


# ---------------------------------------------------------------------------
# Provenance through the split
# ---------------------------------------------------------------------------
def test_provenance_gains_a_part_number_only_when_the_block_was_split():
    entries = [
        {
            "module_id": "IPRT301",
            "prompt": (
                "Explain why integration improves interoperability across systems.\n"
                "Describe the role an interface plays in that integration."
            ),
            "source_label": "2023 - Midyear Main - IPRT301 (p3) (OCR - verify)",
            "origin": "past_paper",
        },
        {
            "module_id": "SPRI301",
            "prompt": "Explain the digital divide and its effect on access to technology.",
            "source_label": "2024 - Midyear Main - SPRI301 (p4) (OCR - verify)",
            "origin": "past_paper",
        },
    ]
    out = list(iter_atomic(entries))
    assert len(out) == 3
    assert out[0]["source_label"].endswith("(OCR - verify) part 1")
    assert out[1]["source_label"].endswith("(OCR - verify) part 2")
    # An unsplit question keeps its label untouched.
    assert out[2]["source_label"] == "2024 - Midyear Main - SPRI301 (p4) (OCR - verify)"


def test_a_too_short_fragment_is_dropped_rather_than_banked():
    """Guard on the minimum: a stub is not a usable question."""
    entries = [
        {
            "module_id": "IPRT301",
            "prompt": (
                "Explain why integration improves interoperability across systems.\n"
                "Describe it."
            ),
            "source_label": "2023 - Midyear Main - IPRT301 (p3) (OCR - verify)",
            "origin": "past_paper",
        }
    ]
    out = list(iter_atomic(entries))
    assert len(out) == 1
    assert out[0]["prompt"].startswith("Explain why integration")


def test_the_split_keeps_the_fields_the_bank_needs():
    entries = [
        {
            "module_id": "IPRT301",
            "prompt": (
                "Explain why integration improves interoperability across systems.\n"
                "Describe the role an interface plays in that integration."
            ),
            "answer_notes": None,
            "difficulty": "medium",
            "origin": "past_paper",
            "source_label": "2023 - Midyear Main - IPRT301 (p3) (OCR - verify)",
        }
    ]
    for child in iter_atomic(entries):
        assert child["module_id"] == "IPRT301"
        assert child["origin"] == "past_paper"
        assert child["answer_notes"] is None
        assert child["difficulty"] == "medium"


def test_a_block_that_splits_to_nothing_is_dropped_not_banked():
    entries = [{"module_id": "IPRT301", "prompt": "an example.", "source_label": "x"}]
    assert list(iter_atomic(entries)) == []


# ---------------------------------------------------------------------------
# The real fixture
# ---------------------------------------------------------------------------
def test_splitting_the_committed_fixture_never_loses_a_question():
    """The invariant is safety, not growth.

    The committed fixture has already been split, so splitting it again is a
    no-op - an earlier version of this test asserted the count *grew*, which only
    held while the fixture held pre-split blocks and broke as soon as the split
    was committed.
    """
    import json

    from src.split_exam_questions import DEFAULT_FIXTURE

    if not DEFAULT_FIXTURE.exists():
        pytest.skip("exam_papers/question_bank.json is not present")

    payload = json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8"))
    original = payload["questions"]
    out = list(iter_atomic(original))

    assert len(out) >= len(original), "splitting must not drop questions"
    assert all(q["prompt"].strip() for q in out)
    assert all(len(q["prompt"].strip()) >= 24 for q in out), "no stubs may be banked"

    def by_module(rows):
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["module_id"]] = counts.get(row["module_id"], 0) + 1
        return counts

    after = by_module(out)
    for module_id, n in by_module(original).items():
        assert after.get(module_id, 0) >= n, module_id


def test_splitting_is_idempotent_on_the_committed_fixture():
    """Running the splitter twice must not keep re-cutting the same questions.

    This is what caught the real bug: a first pass turned 192 merged blocks into
    323 atomic questions, and a second pass had to leave them exactly as they
    were rather than fragmenting each one further.
    """
    import json

    from src.split_exam_questions import DEFAULT_FIXTURE, iter_atomic

    if not DEFAULT_FIXTURE.exists():
        pytest.skip("exam_papers/question_bank.json is not present")

    payload = json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8"))
    once = list(iter_atomic(payload["questions"]))
    twice = list(iter_atomic(once))

    assert [q["prompt"] for q in once] == [q["prompt"] for q in twice]
    assert len(once) == len(twice)