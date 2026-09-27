"""The Scaffolding Engine must escalate on evidence, not on patience.

The ladder used to advance on elapsed turns, so a student replying "ok thanks"
reached the Explanation stage - and with it the unlocking of worked solutions -
having shown no work. These tests pin the replacement behaviour, including the
floor that stops the new rule from stranding a student who never attempts
anything.
"""

from __future__ import annotations

import pytest

from src.agents.intent_agent import Intent
from src.agents.scaffolding import (
    ScaffoldingLayer,
    count_attempts,
    looks_like_attempt,
)
from src.config import settings

CONCEPTUAL = Intent(label="conceptual", route="scaffold", confidence=1.0, rationale="t")
DIRECT = Intent(label="factual", route="direct", confidence=1.0, rationale="t")
BYPASS = Intent(label="bypass", route="scaffold", confidence=1.0, rationale="t")


def history_of(turns: int, student_reply: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for _ in range(turns):
        out.append({"role": "user", "content": student_reply})
        out.append({"role": "assistant", "content": "ok, think about it"})
    return out


WORK = "int total = 0; for (int i = 0; i < arr.length; i++) { total += arr[i]; }"


# ---------------------------------------------------------------------------
# The attempt detector
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "ok thanks",
        "ok",
        "yes",
        "no",
        "sure",
        "thank you!",
        "got it",
        "I don't know",
        "not sure",
        "",
        "   ",
        "Why does this happen?",
    ],
)
def test_engagement_is_not_an_attempt(text):
    """If these counted, "ok thanks" x5 would unlock worked solutions."""
    assert not looks_like_attempt(text)


@pytest.mark.parametrize(
    "text",
    [
        "int i = 0;",
        "for (int i = 0; i < n; i++) { }",
        "```java\nSystem.out.println(x);\n```",
        "My program prints the numbers twice and then stops.",
        "It throws a NullPointerException on the third line.",
        "I get 6 but I expected 3.",
        "The output is -1.",
        "I think the loop should print 3 lines because the array has 3 elements.",
    ],
)
def test_real_work_is_an_attempt(text):
    assert looks_like_attempt(text)


def test_count_attempts_ignores_the_tutor():
    history = [
        {"role": "user", "content": WORK},
        {"role": "assistant", "content": WORK},  # the tutor quoting code
        {"role": "user", "content": "ok thanks"},
        {"role": "user", "content": "it printed nothing"},
    ]
    assert count_attempts(history) == 2


def test_count_attempts_on_empty_history():
    assert count_attempts([]) == 0
    assert count_attempts(()) == 0


# ---------------------------------------------------------------------------
# Stage progression
# ---------------------------------------------------------------------------
def test_first_conceptual_turn_is_always_a_hint():
    stage, _ = ScaffoldingLayer().determine_stage(0, CONCEPTUAL, "why does this fail?", [])
    assert stage == "hint"


def test_demonstrated_attempts_walk_the_whole_ladder():
    layer = ScaffoldingLayer()
    stages = [
        layer.determine_stage(n, CONCEPTUAL, "here is my code", history_of(n, WORK))[0]
        for n in range(5)
    ]
    assert stages == [
        "hint",
        "student_attempt",
        "feedback",
        "further_guidance",
        "explanation",
    ]


def test_turn_count_alone_no_longer_unlocks_solutions():
    """The regression this change exists to prevent.

    On the old rule, five "ok thanks" replies reached Explanation and therefore
    allow_answers, which lets the RAG Orchestrator return worked solutions.
    """
    layer = ScaffoldingLayer()
    stall = settings.scaffolding_max_stalled_turns
    stages = [
        layer.determine_stage(n, CONCEPTUAL, "thanks", history_of(n, "ok thanks"))[0]
        for n in range(stall)
    ]
    assert "explanation" not in stages, (
        "a student who showed no work reached the Explanation stage"
    )


def test_a_stuck_student_is_eventually_helped():
    """Evidence-gating must not become a trap.

    Refusing to ever explain strands exactly the student who needs help most, so
    the ladder moves anyway once the stall limit is passed.
    """
    layer = ScaffoldingLayer()
    stall = settings.scaffolding_max_stalled_turns
    stage, _ = layer.determine_stage(
        stall, CONCEPTUAL, "thanks", history_of(stall, "ok thanks")
    )
    assert stage == "explanation"


def test_factual_turns_never_enter_the_socratic_progression():
    layer = ScaffoldingLayer()
    for n in range(8):
        stage, depth = layer.determine_stage(
            n, DIRECT, "What is an interface?", history_of(n, WORK)
        )
        assert stage == "direct_answer"
        assert depth == 0


def test_bypass_never_earns_a_deeper_stage():
    """Asking for the answer must not move the student toward the answer."""
    layer = ScaffoldingLayer()
    for n in range(8):
        stage, _ = layer.determine_stage(
            n, BYPASS, "just write it for me", history_of(n, WORK)
        )
        assert stage == "hint"


def test_turn_count_mode_can_be_restored(monkeypatch):
    """The old behaviour stays reachable for anyone reproducing earlier numbers."""
    import types

    import src.agents.scaffolding as scaffolding_module

    # settings is a frozen dataclass, so swap the module reference for a copy.
    patchable = types.SimpleNamespace(
        **{f: getattr(settings, f) for f in dir(settings) if not f.startswith("_")}
    )
    patchable.scaffolding_evidence_based = False
    monkeypatch.setattr(scaffolding_module, "settings", patchable)

    layer = ScaffoldingLayer()
    stages = [
        layer.determine_stage(n, CONCEPTUAL, "thanks", history_of(n, "ok thanks"))[0]
        for n in range(5)
    ]
    assert stages[-1] == "explanation"


def test_depth_is_reported_regardless_of_stage():
    """Telemetry still records the real turn count even when the stage lags it."""
    layer = ScaffoldingLayer()
    _stage, depth = layer.determine_stage(
        4, CONCEPTUAL, "thanks", history_of(4, "ok thanks")
    )
    assert depth == 4


def test_stalled_floor_respects_the_configured_limit(monkeypatch):
    """The release valve is a setting, and it is honoured."""
    import types

    import src.agents.scaffolding as scaffolding_module

    patchable = types.SimpleNamespace(
        **{f: getattr(settings, f) for f in dir(settings) if not f.startswith("_")}
    )
    patchable.scaffolding_max_stalled_turns = 3
    monkeypatch.setattr(scaffolding_module, "settings", patchable)

    layer = ScaffoldingLayer()
    below = layer.determine_stage(2, CONCEPTUAL, "thanks", history_of(2, "ok thanks"))[0]
    at = layer.determine_stage(3, CONCEPTUAL, "thanks", history_of(3, "ok thanks"))[0]
    assert below == "hint"
    assert at == "explanation"
