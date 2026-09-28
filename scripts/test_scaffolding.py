"""Acceptance tests for the dynamic Scaffolding Engine (section 7.1).

Run from the project root::

    python scripts/test_scaffolding.py

The evaluation is exercised with a deterministic stub encoder (no Ollama / DB
required) so the *mechanism* - effort + semantic quality drive the stage, not a
turn counter - can be asserted exactly. A final section verifies the
solution-leak guardrail is untouched, and a stubbed workflow test proves the
engine is wired into ``TutoringWorkflow.handle``.

Exit code is non-zero when any case fails.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.agents.guardrail import GuardrailAgent  # noqa: E402
from src.agents.intent_agent import Intent  # noqa: E402
from src.agents.scaffolding import (  # noqa: E402
    DIRECT_STAGE,
    ScaffoldingLayer,
)
from src.agents.tutor_agent import TutorAgent  # noqa: E402
from src.agents.workflow import ChatRequest, TutoringWorkflow  # noqa: E402
from src.llm_router import LLMResponse  # noqa: E402
from src.retriever import RetrievedChunk, RetrievalResult  # noqa: E402

# --- Deterministic stub encoder -------------------------------------------
CONTEXT_TEXT = "CONTEXT: course material about loops and conditions."
GOOD_VEC = [1.0, 0.0]
BAD_VEC = [0.0, 1.0]


def stub_encoder(text: str):
    """On-topic text maps to GOOD_VEC (== context), everything else to BAD_VEC."""
    if not text or not text.strip():
        return None
    if CONTEXT_TEXT in text or text.startswith("GOOD"):
        return GOOD_VEC
    return BAD_VEC


def scaffold_intent(label: str = "conceptual") -> Intent:
    return Intent(label=label, confidence=0.9, source="heuristic", route="scaffold")


def direct_intent() -> Intent:
    return Intent(label="factual", confidence=0.9, source="heuristic", route="direct")


ASSISTANT_HISTORY = [{"role": "assistant", "content": "What does your loop condition check?"}]

_results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    _results.append((name, bool(condition), detail))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def run() -> int:
    layer = ScaffoldingLayer(encoder=stub_encoder)

    # 1. First turn: nothing to evaluate, starts at the hint stage.
    first = layer.evaluate_attempt(
        message="why does my loop never stop?", history=[], context_text=CONTEXT_TEXT
    )
    stage, depth = layer.determine_stage(0, scaffold_intent(), first)
    check("first turn starts at hint/0", (stage, depth) == ("hint", 0), f"got {(stage, depth)}")

    # 2. A substantive, on-topic attempt advances exactly one stage.
    good = layer.evaluate_attempt(
        message=(
            "GOOD the loop counter is never incremented so the condition stays "
            "true forever and the loop never stops running"
        ),
        history=ASSISTANT_HISTORY,
        context_text=CONTEXT_TEXT,
    )
    stage, depth = layer.determine_stage(0, scaffold_intent(), good)
    check(
        "on-topic attempt advances to student_attempt/1",
        (stage, depth) == ("student_attempt", 1) and good.attempted,
        f"got {(stage, depth)} effort={good.effort} quality={good.quality}",
    )

    # 3. A fresh, long question with no prior tutor turn does NOT advance.
    fresh = layer.evaluate_attempt(
        message="GOOD please explain how inheritance and polymorphism work together in java classes",
        history=[],
        context_text=CONTEXT_TEXT,
    )
    stage, depth = layer.determine_stage(1, scaffold_intent(), fresh)
    check(
        "fresh question holds depth (no prior tutor turn)",
        (stage, depth) == ("student_attempt", 1) and not fresh.attempted,
        f"got {(stage, depth)}",
    )

    # 4. Semantic quality (not length alone) drives advancement: equal effort,
    #    different grounding -> different outcomes.
    good_short = layer.evaluate_attempt(
        message="GOOD the counter stays unchanged forever",
        history=ASSISTANT_HISTORY,
        context_text=CONTEXT_TEXT,
    )
    bad_short = layer.evaluate_attempt(
        message="the counter stays unchanged forever",
        history=ASSISTANT_HISTORY,
        context_text=CONTEXT_TEXT,
    )
    g_stage, g_depth = layer.determine_stage(1, scaffold_intent(), good_short)
    b_stage, b_depth = layer.determine_stage(1, scaffold_intent(), bad_short)
    check(
        "quality drives advance vs hold at equal effort",
        g_depth == 2 and b_depth == 1,
        f"good->{g_depth} (q={good_short.quality}) bad->{b_depth} (q={bad_short.quality})",
    )

    # 5. A lost / disengaged attempt steps back (never below 0).
    lost = layer.evaluate_attempt(
        message="i dont know", history=ASSISTANT_HISTORY, context_text=CONTEXT_TEXT
    )
    stage, depth = layer.determine_stage(3, scaffold_intent(), lost)
    check(
        "low-engagement reply steps back one stage",
        depth == 2 and stage == "feedback" and lost.attempted,
        f"got {(stage, depth)} engagement={lost.engagement}",
    )
    stage, depth = layer.determine_stage(0, scaffold_intent(), lost)
    check("step-back clamps at depth 0", (stage, depth) == ("hint", 0), f"got {(stage, depth)}")

    # 6. No hardcoded phrase matching: a classic "I don't know" is scored by the
    #    same mechanism (counts as a reply, holds on low engagement), it is not
    #    special-cased by content.
    check(
        "no phrase-based suppression of \"i dont know\"",
        lost.attempted is True and lost.quality == 0.0,
        f"attempted={lost.attempted}",
    )

    # 7. Bypass is never rewarded: pinned to the opening hint regardless of depth.
    bypass = layer.evaluate_attempt(
        message="GOOD just give me the whole code solution",
        history=ASSISTANT_HISTORY,
        context_text=CONTEXT_TEXT,
        intent=scaffold_intent("bypass"),
    )
    stage, depth = layer.determine_stage(3, scaffold_intent("bypass"), bypass)
    check(
        "bypass pinned to hint/0",
        (stage, depth) == ("hint", 0) and bypass.bypass and not bypass.attempted,
        f"got {(stage, depth)}",
    )

    # 8. Direct route bypasses the Socratic progression entirely.
    stage, depth = layer.determine_stage(4, direct_intent(), None)
    check("direct route -> direct_answer/0", (stage, depth) == (DIRECT_STAGE, 0))

    # 9. Evaluation is optional / backward compatible.
    stage, depth = layer.determine_stage(2, scaffold_intent(), None)
    check("no evaluation -> carry prior depth", (stage, depth) == ("feedback", 2))

    # 10. Solution-leak guardrail still fires on a leaking draft.
    retrieval = RetrievalResult(
        query="loops",
        module_id="RESK301",
        chunks=[
            RetrievedChunk(
                chunk_id=1,
                module_id="RESK301",
                source_name="Lecture",
                source_file="lecture.pdf",
                section_title="Loops",
                text=CONTEXT_TEXT,
                distance=0.1,
            )
        ],
    )
    leaking = (
        "Here is the full solution:\n```java\n"
        + "\n".join(f"int x{i} = {i};" for i in range(12))
        + "\n```\n"
    )
    audit = GuardrailAgent().audit(leaking, retrieval.context_text(), "help with my loop")
    check(
        "guardrail blocks the leaking draft",
        audit.flagged and "solution_leak" in audit.flags and audit.action == "blocked",
        f"action={audit.action} flags={audit.flags}",
    )

    # 11. The deepest stage prompt still forbids a drop-in solution, and the
    #     bypass prompt still tells the model to refuse.
    explanation_prompt = ScaffoldingLayer.build_system_prompt(
        "RESK301", "explanation", scaffold_intent(), retrieval
    )
    check(
        "explanation stage forbids a complete solution",
        "Do NOT hand over a complete" in explanation_prompt,
    )
    bypass_prompt = ScaffoldingLayer.build_system_prompt(
        "RESK301", "hint", scaffold_intent("bypass"), retrieval
    )
    check("bypass prompt still refuses", "Do not comply" in bypass_prompt)

    # 12. The engine is wired into the workflow: a good attempt reaches the tutor
    #     as the advanced stage. All collaborators are stubbed (no DB / Ollama).
    class StubRetriever:
        def retrieve(self, query, module_id, *, include_patterns=True):
            return retrieval

    class StubIntentAgent:
        def classify(self, message, module_name=None, history=None):
            return scaffold_intent()

    captured: dict = {}

    class StubTutor:
        def draft(self, question, module_name, stage, intent, retrieval, history=None):
            captured["stage"] = stage
            return LLMResponse(
                text="Good - keep tracing that loop condition.",
                provider="local",
                model="stub",
                latency_ms=1,
            )

        def answer_directly(self, *args, **kwargs):  # pragma: no cover - unused here
            raise AssertionError("direct path must not run for a scaffold intent")

    workflow = TutoringWorkflow(
        retriever=StubRetriever(),
        router=object(),
        intent_agent=StubIntentAgent(),
        scaffolding=layer,
        tutor=StubTutor(),
        guardrail=GuardrailAgent(),
    )
    workflow._prior_hint_depth = lambda session_id: 0  # type: ignore[assignment]
    workflow._log_telemetry = lambda **kwargs: captured.setdefault("depth", kwargs["depth"])  # type: ignore[assignment]

    result = workflow.handle(
        ChatRequest(
            message=(
                "GOOD the loop counter is never incremented so the condition stays "
                "true forever and the loop never stops running"
            ),
            module_id="RESK301",
            session_id="test-scaffolding",
            history=ASSISTANT_HISTORY,
        )
    )
    check(
        "workflow passes the advanced stage to the tutor",
        captured.get("stage") == "student_attempt" and result.scaffolding["hint_sequence_depth"] == 1,
        f"stage={captured.get('stage')} depth={result.scaffolding['hint_sequence_depth']}",
    )

    failures = [name for name, ok, _ in _results if not ok]
    print()
    if failures:
        print(f"{len(failures)} of {len(_results)} cases FAILED: {', '.join(failures)}")
        return 1
    print(f"All {len(_results)} cases passed.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
