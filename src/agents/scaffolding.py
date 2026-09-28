"""Scaffolding Engine (Tier 2, sections 7.1-7.4).

The pedagogical brain for the **scaffold track**. It decides how directly the
tutor should guide and builds the hidden system prompt that tells the model
*how* to guide rather than answer. This is the hard constraint that prevents
scaffolding collapse when a student repeatedly asks for the final answer.

Dynamic progression
-------------------
The engine no longer advances on a raw turn counter. Each turn it *evaluates the
student's latest attempt* - how much effort it shows and how semantically
grounded it is in the retrieved course material - and moves the
``hint_sequence_depth``:

* a substantive, on-topic attempt advances one stage;
* a neutral attempt holds the current stage;
* a lost / disengaged attempt steps back one stage (never below the first);
* a bypass request ("just give me the code") is pinned to the opening hint so it
  is never rewarded with a more revealing stage.

The evaluation uses embeddings (semantic quality) plus structural effort; it does
**not** pattern-match hardcoded phrases in the student's message.

Factual / definitional turns never reach the Socratic progression: the Intent
Agent marks them ``route="direct"`` and the workflow answers them from RAG
instead. :meth:`ScaffoldingLayer.determine_stage` returns the neutral
``direct_answer`` stage for those turns so telemetry stays consistent.

Solution-leak safety
--------------------
This engine chooses only *how* to guide - it never relaxes the leak constraints.
Every stage still carries the Socratic principles (no complete / copy-pasteable
solutions), the deepest ``explanation`` stage explicitly forbids a drop-in
solution, a bypass is pinned to the hint stage, and the Guardrail Agent remains
the final gate on the drafted text.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Optional

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
STAGE_SEQUENCE: tuple[str, ...] = (
    "hint",
    "student_attempt",
    "feedback",
    "further_guidance",
    "explanation",
)

#: Stage recorded for factual / definitional turns that bypass scaffolding.
DIRECT_STAGE = "direct_answer"

#: Words for the structural effort signal (letters then word characters).
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_']*")

#: Text -> embedding vector, or ``None`` when the embedder is unavailable.
Encoder = Callable[[str], Optional[list[float]]]


def _cosine(left: Optional[list[float]], right: Optional[list[float]]) -> float:
    """Cosine similarity clamped to ``[0, 1]``; 0.0 when either vector is empty."""
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(x * y for x, y in zip(left, right))
    left_norm = math.sqrt(sum(x * x for x in left))
    right_norm = math.sqrt(sum(y * y for y in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (left_norm * right_norm)))


def _last_assistant_message(
    history: Optional[Iterable[Mapping[str, str]]],
) -> Optional[str]:
    """Return the tutor's most recent turn, if any (structural, not content-based)."""
    for item in reversed(list(history or [])):
        if str(item.get("role", "")).lower() == "assistant":
            content = str(item.get("content", "")).strip()
            if content:
                return content
    return None


def _default_encoder(text: str) -> Optional[list[float]]:
    """Embed ``text`` via the shared embedder; evaluation must never break a turn."""
    if not text or not text.strip():
        return None
    try:
        from ..embeddings import get_embedder

        return get_embedder().encode_one(text)
    except Exception as exc:  # noqa: BLE001 - degrade to effort-only scoring
        logger.warning("Scaffolding evaluation embedder unavailable: %s", exc)
        return None


@dataclass(slots=True)
class AttemptEvaluation:
    """How much the student's latest turn looks like a genuine attempt."""

    #: Structural effort, 0..1, from the length/substance of the message.
    effort: float
    #: Semantic overlap between the attempt and the retrieved course material.
    quality: float
    #: True when this is a reply to the tutor that clears the effort floor.
    attempted: bool
    #: True when the Intent Agent classified the turn as a bypass request.
    bypass: bool
    #: Whether :attr:`quality` was actually measured (embedder + context present).
    has_quality: bool = False
    #: Human-readable signals, logged for observability.
    reasons: list[str] = field(default_factory=list)

    @property
    def engagement(self) -> float:
        """Blend of effort and semantic quality (effort-only when unmeasured)."""
        if self.has_quality:
            return round(0.5 * self.effort + 0.5 * self.quality, 4)
        return round(self.effort, 4)


class ScaffoldingLayer:
    """Evaluates the student's attempt and picks the Socratic stage for the turn."""

    def __init__(
        self,
        encoder: Optional[Encoder] = None,
        *,
        advance_threshold: Optional[float] = None,
        retreat_threshold: Optional[float] = None,
        min_attempt_words: Optional[int] = None,
    ) -> None:
        self._encode: Encoder = encoder or _default_encoder
        self.advance_threshold = (
            settings.scaffolding_advance_threshold
            if advance_threshold is None
            else advance_threshold
        )
        self.retreat_threshold = (
            settings.scaffolding_retreat_threshold
            if retreat_threshold is None
            else retreat_threshold
        )
        self.min_attempt_words = (
            settings.scaffolding_min_attempt_words
            if min_attempt_words is None
            else min_attempt_words
        )

    # -- Evaluation ---------------------------------------------------------
    def evaluate_attempt(
        self,
        *,
        message: str,
        history: Optional[Iterable[Mapping[str, str]]] = None,
        context_text: str = "",
        intent: Optional[Intent] = None,
    ) -> AttemptEvaluation:
        """Score the student's latest message as an attempt.

        ``attempted`` is only true when the message replies to a prior tutor turn
        and clears the effort floor, so a fresh (possibly long) question never
        advances the depth. A bypass request never counts as an attempt.
        """
        attempt = (message or "").strip()
        words = _WORD_RE.findall(attempt)
        previous_tutor_turn = _last_assistant_message(history)
        effort = self._effort_score(len(words))
        reasons = [f"effort={effort:.2f} ({len(words)} words)"]

        quality = 0.0
        has_quality = False
        if previous_tutor_turn and context_text.strip():
            try:
                attempt_vector = self._encode(attempt)
                context_vector = self._encode(
                    context_text[: settings.scaffolding_context_char_budget]
                )
            except Exception as exc:  # noqa: BLE001 - custom encoders must not break a turn
                logger.warning("Scaffolding attempt encoding failed: %s", exc)
                attempt_vector = context_vector = None
            if attempt_vector and context_vector:
                quality = _cosine(attempt_vector, context_vector)
                has_quality = True
                reasons.append(f"quality={quality:.2f}")

        bypass = bool(intent is not None and intent.label == "bypass")
        attempted = bool(previous_tutor_turn) and len(words) >= self.min_attempt_words and not bypass
        if not previous_tutor_turn:
            reasons.append("no prior tutor turn")
        if bypass:
            reasons.append("bypass request")
        if not has_quality:
            reasons.append("no semantic reference available")

        return AttemptEvaluation(
            effort=effort,
            quality=quality,
            attempted=attempted,
            bypass=bypass,
            has_quality=has_quality,
            reasons=reasons,
        )

    def _effort_score(self, word_count: int) -> float:
        """Effort from message length, with diminishing returns (0..1)."""
        full = max(1, settings.scaffolding_effort_full_words)
        return round(min(1.0, math.sqrt(max(0, word_count) / full)), 4)

    # -- Stage selection ----------------------------------------------------
    def determine_stage(
        self,
        prior_depth: int,
        intent: Intent,
        evaluation: Optional[AttemptEvaluation] = None,
    ) -> tuple[str, int]:
        """Return ``(stage, hint_sequence_depth)`` for the current turn.

        ``prior_depth`` is the depth carried forward from the session's previous
        turn; the evaluated engagement of the student's latest attempt decides
        whether it advances, holds or steps back.
        """
        if intent.route == ROUTE_DIRECT:
            return DIRECT_STAGE, 0

        depth = max(0, min(int(prior_depth), len(STAGE_SEQUENCE) - 1))

        if evaluation is not None:
            if evaluation.bypass:
                # Never reward a "just do it for me" request with a deeper stage.
                depth = 0
            elif evaluation.attempted:
                engagement = evaluation.engagement
                if engagement >= self.advance_threshold:
                    depth = min(depth + 1, len(STAGE_SEQUENCE) - 1)
                elif engagement <= self.retreat_threshold and depth > 0:
                    depth -= 1
            # A neutral or non-attempt turn holds the current depth.
            logger.info(
                "Scaffolding stage=%s depth=%d engagement=%.2f attempted=%s bypass=%s",
                STAGE_SEQUENCE[depth],
                depth,
                evaluation.engagement,
                evaluation.attempted,
                evaluation.bypass,
            )

        return STAGE_SEQUENCE[depth], depth

    @staticmethod
    def build_system_prompt(
        module_name: str,
        stage: str,
        intent: Intent,
        retrieval: RetrievalResult,
    ) -> str:
        """Build the hidden system prompt for the current turn.

        Routes to the direct-answer prompt for factual turns and to the Socratic
        prompt for everything else. The bypass instruction is retained so the
        model still refuses to hand over the work.
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
