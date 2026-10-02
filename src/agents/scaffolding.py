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

import logging
import re
from typing import Iterable, Mapping, Sequence

from ..config import settings
from ..prompts import (
    ROUTE_DIRECT,
    build_direct_system_prompt,
    build_tutor_system_prompt,
)
from ..retriever import RetrievalResult
from .intent_agent import Intent

logger = logging.getLogger(__name__)

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


#: Replies that are engagement with the tutor's question, not evidence of thinking.
#: Counting these as attempts would let "ok thanks" unlock the Explanation stage.
_NON_ATTEMPT_RE = re.compile(
    r"^(?:ok(?:ay)?|k|yes|no|yep|nope|sure|thanks?|thank you|ta|got it|i see|"
    r"hmm+|idk|i don'?t know|don'?t know|not sure|maybe|right|cool|nice|"
    r"oh|ah|wait|what|why|how)\b[\s.!?]*$",
    re.IGNORECASE,
)

#: A line that reads as code the student wrote or tried.
_CODE_LINE_RE = re.compile(
    r"(?:\b(?:public|private|protected|static|final|void|int|double|float|char|"
    r"boolean|long|class|interface|return|new|import|package|if|else|for|while|"
    r"try|catch|throw|throws|extends|implements)\b)"
    r"|[;{}]\s*$"
    r"|\w+\s*\([^)]*\)\s*\{"
    r"|=>|::",
)

#: A reported observation. Running the code and describing what happened is
#: exactly the work the Feedback stage is built on, even when the sentence is
#: short: "it prints twice then stops" is a far stronger signal than "ok thanks".
_SYMPTOM_RE = re.compile(
    # A reported observation. The subject is deliberately unconstrained: "it
    # prints", "my program printed" and "I get" are all the student describing
    # what they saw when they ran the code, which is the work being looked for.
    r"\b(?:print(?:s|ed|ing)?|output(?:s|ted|ting)?|show(?:s|ed|ing)?|"
    r"throw(?:s|ing)?|crash(?:es|ed|ing)?|fail(?:s|ed|ing)?|"
    r"loop(?:s|ed|ing)?|repeat(?:s|ed|ing)?|stop(?:s|ped|ping)?|hang(?:s|ed|ing)?|"
    r"i (?:get|got|see|think|thought|expected)"
    r"|the (?:output|result|error|exception|answer|console|terminal) (?:is|was|shows?)"
    r"|i(?:'m| am)? seeing|should (?:print|output|be|return))\b",
    re.IGNORECASE,
)

#: Enough prose to be an actual explanation rather than a one-word reply. Kept
#: low deliberately: a student stating a concrete prediction in a short sentence
#: has still attempted something.
_MIN_ATTEMPT_CHARS = 60


def looks_like_attempt(text: str) -> bool:
    """True when a student turn shows work rather than mere engagement.

    This is the signal that decides whether the Scaffolding Engine has earned the
    right to escalate. It is a heuristic, deliberately: it recognises code, a
    reported observation, a concrete prediction and substantive prose, and it
    deliberately does not try to judge correctness - a wrong attempt is still an
    attempt, and diagnosing a wrong attempt is exactly what the Feedback stage is
    for.
    """
    body = (text or "").strip()
    if not body:
        return False
    if _NON_ATTEMPT_RE.match(body):
        return False
    if "```" in body:
        return True
    for line in body.splitlines():
        if _CODE_LINE_RE.search(line):
            return True
    if _SYMPTOM_RE.search(body):
        return True
    return len(body) >= _MIN_ATTEMPT_CHARS


def count_attempts(history: Iterable[Mapping[str, str]]) -> int:
    """How many of the student's own turns in ``history`` show work.

    Counts the same history the tutor model is given, so the stage reflects what
    the tutor can actually see rather than a separate server-side guess.
    """
    total = 0
    for entry in history or ():
        if (entry.get("role") or "").casefold() != "user":
            continue
        if looks_like_attempt(entry.get("content", "")):
            total += 1
    return total


class ScaffoldingLayer:
    """Turns session history into the Socratic instructions handed to the tutor."""

    def determine_stage(
        self,
        prior_turns: int,
        intent: Intent,
        message: str,
        history: Sequence[Mapping[str, str]] = (),
    ) -> tuple[str, int]:
        """Return ``(stage, hint_sequence_depth)``.

        The first request always starts at the Hint stage (section 7.1). Later
        turns advance through the progression. A bypass attempt is pinned to a
        gentle refusal at the Hint stage so the depth does not reward it. A
        factual / definitional turn bypasses scaffolding entirely.

        Advancement is driven by demonstrated attempts rather than elapsed turns
        (:func:`looks_like_attempt`). On a pure turn count a student who replies
        "ok thanks" five times reaches the Explanation stage - and with it the
        unlocking of worked solutions - having shown no work at all, which
        inverts the purpose of scaffolding.

        ``SCAFFOLDING_MAX_STALLED_TURNS`` stops that becoming a trap: a student
        who never attempts anything still reaches the explanation after that many
        turns, because refusing to ever explain is its own failure mode and would
        strand exactly the student who needs help most.
        """
        depth = max(0, int(prior_turns))
        if intent.route == ROUTE_DIRECT:
            return DIRECT_STAGE, 0
        if intent.label == "bypass":
            return "hint", depth

        last = len(_STAGE_SEQUENCE) - 1
        if not settings.scaffolding_evidence_based:
            return _STAGE_SEQUENCE[min(depth, last)], depth

        index = min(count_attempts(history), last)
        if index < last and depth >= settings.scaffolding_max_stalled_turns:
            logger.info(
                "Advancing to %s after %d turns with no attempt detected "
                "(stalled floor, not earned).",
                _STAGE_SEQUENCE[last],
                depth,
            )
            index = last
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
