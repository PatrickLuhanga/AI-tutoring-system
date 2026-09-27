"""The Guardrail Agent is the last thing between a student and a leaked answer.

It is also the component most likely to be silently weakened: every prompt change
upstream makes replies longer and more answer-shaped, and a guardrail that stops
catching them fails open. These tests pin the behaviour that had previously been
verified only by throwaway scripts.

Two weaknesses are documented here rather than papered over, because both were
found by running these very inputs against the live agent:

* the grounding check is a token-overlap heuristic, and a wholly fabricated
  sentence can pass it (see ``test_token_overlap_grounding_is_only_a_heuristic``);
* ``allow_direct`` does not exempt a reply from the scope check, so a factual
  answer about a topic absent from the retrieved context is flagged.
"""

from __future__ import annotations

import pytest

from src.agents.guardrail import GuardrailAgent, _DIRECT_ANSWER_RE

INHERITANCE_CONTEXT = (
    '[C1] IPRT/slides/01_Inheritance.md :: "2. Inheritance"\n'
    "class PartTime extends Employee. The pay calculation is written once in "
    "Employee and reused by subclasses.\n"
)

INTERFACE_CONTEXT = (
    '[C1] IPRT/slides/02_Interfaces.md :: "2. What is an interface?"\n'
    "An interface is a reference type that defines a set of methods a class may "
    "implement. It contains method signatures only, with no implementation.\n"
)


@pytest.fixture(scope="module")
def guardrail():
    return GuardrailAgent()


# ---------------------------------------------------------------------------
# What must pass
# ---------------------------------------------------------------------------
def test_a_socratic_hint_passes(guardrail):
    result = guardrail.audit(
        "Nice - you're looking right at it. Because PartTime extends Employee, the "
        "pay calculation lives in one place. Which fields would you move up?",
        INHERITANCE_CONTEXT,
        "Why does inheritance help here?",
    )
    assert result.action == "pass"
    assert not result.flagged


def test_a_grounded_factual_answer_passes(guardrail):
    result = guardrail.audit(
        "An interface is a reference type that defines methods a class may implement, "
        "with no implementation of its own [C1].",
        INTERFACE_CONTEXT,
        "What is an interface?",
        allow_direct=True,
    )
    assert result.action == "pass", f"a grounded direct answer was flagged: {result.flags}"
    assert not result.flagged


# ---------------------------------------------------------------------------
# What must be caught
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "reply",
    [
        "Here is the full code for you.",
        "The final answer is below.",
        "Copy and paste this into your file.",
        "Let me solve it for you.",
        # The two that previously slipped through a single-adjective pattern.
        "Here's the complete working solution:",
        "This is the final working code.",
        "Below is the full finished program.",
    ],
)
def test_answer_offer_phrasings_are_caught(guardrail, reply):
    result = guardrail.audit(reply, INHERITANCE_CONTEXT, "do it for me")
    assert result.flagged, f"not caught: {reply!r}"


def test_a_short_code_block_behind_a_solution_offer_is_blocked(guardrail):
    """The regression: a solution phrase plus code used to pass unflagged."""
    reply = (
        "Here's the complete working solution:\n"
        "```java\nclass PartTime extends Employee { private int hours; }\n```"
    )
    result = guardrail.audit(reply, INHERITANCE_CONTEXT, "just write it for me")
    assert result.flagged, "a solution phrase plus a code block passed the guardrail"


def test_a_long_code_block_is_not_returned_intact(guardrail):
    reply = (
        "Try this:\n```java\n"
        + "\n".join(f"        int x{i} = {i};" for i in range(80))
        + "\n```"
    )
    result = guardrail.audit(reply, INHERITANCE_CONTEXT, "write the whole program")
    assert result.action in {"blocked", "truncated"}
    assert "x79" not in result.approved_text, "the guardrail returned the whole block"


def test_the_direct_answer_pattern_matches_multi_adjective_phrasings():
    """A direct unit test of the fix, independent of the rest of the pipeline."""
    for phrase in (
        "here's the complete working solution:",
        "here is the full code",
        "this is the final working code.",
        "the finished program is below",
    ):
        assert _DIRECT_ANSWER_RE.search(phrase), f"pattern misses {phrase!r}"


# ---------------------------------------------------------------------------
# Auditability
# ---------------------------------------------------------------------------
def test_the_original_text_survives_for_auditing(guardrail):
    """The paper's leak measurements read ``original_text``.

    Losing it would quietly make the reported numbers unverifiable, so the
    redacted text must never be the only thing retained.
    """
    original = "Here is the full code for you."
    result = guardrail.audit(original, INHERITANCE_CONTEXT, "do it for me")
    assert result.original_text == original
    assert result.approved_text != original, "a blocked reply was returned unchanged"


def test_flags_name_the_reason(guardrail):
    result = guardrail.audit(
        "Here is the full code for you.", INHERITANCE_CONTEXT, "do it for me"
    )
    assert result.flags, "a flagged reply must say why"
    assert "direct_answer" in result.flags


def test_audit_is_deterministic(guardrail):
    reply = "Which fields would you move into Employee?"
    first = guardrail.audit(reply, INHERITANCE_CONTEXT, "why?")
    second = guardrail.audit(reply, INHERITANCE_CONTEXT, "why?")
    assert first.action == second.action
    assert first.flagged == second.flagged


# ---------------------------------------------------------------------------
# Known limitations, pinned so they cannot change unnoticed
# ---------------------------------------------------------------------------
def test_token_overlap_grounding_is_only_a_heuristic(guardrail):
    """A fabricated sentence can satisfy the scope check.

    ``min_context_overlap`` is a word-overlap threshold, so a reply built from
    ordinary function words can clear it even when nothing in it is supported.
    This is a real weakness, not a passing test: it means ``out_of_scope`` should
    not be reported as a hallucination rate, because it is not one.
    """
    fabricated = (
        "In 1987 the department mandated that all PartTime staff be reclassified "
        "under the 2019 leave policy."
    )
    result = guardrail.audit(fabricated, INHERITANCE_CONTEXT, "what is the leave policy?")
    assert not result.flagged, (
        "token overlap now catches this; the caveat in the module docstring and the "
        "paper's limitations section can be tightened"
    )


def test_allow_direct_does_not_exempt_the_scope_check(guardrail):
    """``allow_direct`` relaxes answer-length rules, not grounding.

    A factual reply about a topic absent from the retrieved context is still
    flagged, which is why a grounded context must be supplied in the test above
    for a direct answer to pass.
    """
    result = guardrail.audit(
        "An interface is a reference type that declares methods without providing "
        "their implementation [C1].",
        INHERITANCE_CONTEXT,
        "What is an interface?",
        allow_direct=True,
    )
    assert result.flagged
    assert "out_of_scope" in result.flags
