"""Scaffolding Engine (Tier 2, sections 7.1-7.4).

The pedagogical brain for the **scaffold track**. It tracks the session's
progression through the Socratic stages and builds the hidden system prompt that
tells the model *how* to guide rather than answer. This is the hard constraint
that prevents scaffolding collapse when a student repeatedly asks for the final
answer.

Factual / definitional turns never reach the Socratic progression: the Intent
Agent marks them ``route="direct"`` and the workflow answers them from RAG
instead. ``determine_stage`` returns the neutral ``direct_answer`` stage for
those turns so telemetry stays consistent.
"""

from __future__ import annotations

from ..prompts import (
    ROUTE_DIRECT,
    build_direct_system_prompt,
    build_tutor_system_prompt,
)
from ..retriever import RetrievalResult
from .intent_agent import Intent

#: Hint progression (section 7.1); index 0 is used on the first request.
_STAGE_SEQUENCE = (
    "hint",
    "student_attempt",
    "feedback",
    "further_guidance",
    "explanation",
)

#: Stage recorded for factual / definitional turns that bypass scaffolding.
DIRECT_STAGE = "direct_answer"


class ScaffoldingLayer:
    """Turns session history into the Socratic instructions handed to the tutor."""

    def determine_stage(
        self,
        prior_turns: int,
        intent: Intent,
        message: str,
    ) -> tuple[str, int]:
        """Return ``(stage, hint_sequence_depth)``.

        The first request always starts at the Hint stage (section 7.1); later
        turns advance through the progression. A bypass attempt is pinned to a
        gentle refusal at the Hint stage so the depth does not reward it. A
        factual / definitional turn bypasses scaffolding entirely.
        """
        depth = max(0, int(prior_turns))
        if intent.route == ROUTE_DIRECT:
            return DIRECT_STAGE, 0
        if intent.label == "bypass":
            return "hint", depth
        index = min(depth, len(_STAGE_SEQUENCE) - 1)
        return _STAGE_SEQUENCE[index], depth

    @staticmethod
    def build_system_prompt(
        module_name: str,
        stage: str,
        intent: Intent,
        retrieval: RetrievalResult,
    ) -> str:
        """Build the hidden system prompt for the current turn.

        Routes to the direct-answer prompt for factual turns and to the Socratic
        prompt for everything else.
        """
        if intent.route == ROUTE_DIRECT:
            return build_direct_system_prompt(
                module_name, intent.label, retrieval.context_text()
            )
        base = build_tutor_system_prompt(module_name, stage, intent.label, retrieval.context_text())
        if intent.label == "bypass":
            base += (
                "\n\nIMPORTANT: The student has asked you to do the work for them. "
                "Do not comply. Warmly decline, restate that you will guide them, and "
                "ask one small question to get them started."
            )
        return base
