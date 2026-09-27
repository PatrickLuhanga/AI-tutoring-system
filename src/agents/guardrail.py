"""Guardrail Agent (Tier 2, section 11).

The final gate between the model and the student. It audits the draft for
complete-solution leakage (large copy-pasteable code blocks or direct-answer
phrasing) and for material that falls outside the retrieved course context, then
blocks, truncates or passes it, recording failure flags for telemetry.

Note: this module audits *content*. The resilient HTTP call to Ollama lives in
:mod:`src.inference.ollama_client`, keeping "check the output" separate from
"talk to the model" (architecture section 5.1).

The numeric thresholds are implementation/evaluation decisions (section 11.1) and
therefore live in ``.env``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ..config import settings

_CODE_FENCE_RE = re.compile(r"```[^\n]*\n?(.*?)```", re.DOTALL)
_CODE_LINE_RE = re.compile(
    r"(?m)^\s*(?:public|private|protected|static|class\s+\w|interface\s+\w|enum\s+\w|"
    r"def\s+\w+\s*\(|function\s+\w+\s*\(|import\s+[\w.]|package\s+[\w.]|"
    r"System\.out|console\.log|print\s*\(|for\s*\(|while\s*\(|if\s*\(|"
    r"[\w<>\[\]]+\s+\w+\s*=\s*new\b|back\s*=\s)"
)
#: Phrasings that hand over a finished artefact.
#:
#: The adjective run is ``{0,2}`` rather than a single word because "the complete
#: working solution" and "the final working code" are ordinary English and both
#: slipped past a single-adjective pattern - verified with a live audit, where
#: "Here's the complete working solution:" followed by a code block passed
#: unflagged.
_DIRECT_ANSWER_RE = re.compile(
    r"(?i)\b("
    r"(?:here(?:'| i)?s|this is|that is|below is|below are)\s+the\s+"
    r"(?:(?:full|complete|final|finished|working|ready|correct|polished)\s+){0,2}"
    r"(?:code|solution|answer|program|implementation|solution)"
    r"|the\s+(?:(?:full|complete|final|finished|working|ready|correct)\s+){0,2}"
    r"(?:code|solution|answer|program|implementation)\s+(?:is|below|here)"
    r"|copy (?:and )?(?:paste )?this|just (?:copy|paste|use) this|"
    r"solve(?:d)? it for you"
    r")\b"
)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")

_BLOCKING_FLAGS = {"code_leak", "direct_answer"}

_SOCRATIC_FALLBACK = (
    "I'm going to keep us working through this together rather than hand over a "
    "finished solution - that's how the understanding sticks. Let's shrink the "
    "problem: what is the smallest part you can already do, and where exactly does "
    "it stop making sense?"
)


@dataclass(slots=True)
class GuardrailResult:
    approved_text: str
    flagged: bool
    flags: list[str] = field(default_factory=list)
    action: str = "pass"  # pass | blocked | truncated
    original_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "flagged": self.flagged,
            "flags": self.flags,
            "action": self.action,
        }


class GuardrailAgent:
    """Final gate: blocks complete-solution leakage and audits the draft."""

    def __init__(
        self,
        max_code_lines: Optional[int] = None,
        max_words: Optional[int] = None,
        min_context_overlap: Optional[float] = None,
        action: Optional[str] = None,
    ) -> None:
        self.max_code_lines = max_code_lines or settings.guardrail_max_code_lines
        self.max_words = max_words or settings.guardrail_max_words
        self.min_context_overlap = (
            settings.guardrail_min_context_overlap
            if min_context_overlap is None
            else min_context_overlap
        )
        self.action = (action or settings.guardrail_action).lower()

    def audit(
        self,
        draft: str,
        context: str,
        question: str = "",
        *,
        allow_direct: bool = False,
    ) -> GuardrailResult:
        """Audit a draft response.

        ``allow_direct`` is set for the factual / definitional track: those
        answers *should* state facts directly, so the solution-leakage checks
        (``code_leak``, ``direct_answer``) are disabled. Advisory checks
        (``too_long``, ``out_of_scope``) still apply.
        """
        original = draft or ""
        if not settings.guardrail_enabled:
            return GuardrailResult(approved_text=original, flagged=False, original_text=original)

        flags: list[str] = []
        if not allow_direct:
            code_lines = self._count_code_lines(original)
            if code_lines > self.max_code_lines:
                flags.append("code_leak")
            if _DIRECT_ANSWER_RE.search(original):
                flags.append("direct_answer")
        if len(_WORD_RE.findall(original)) > self.max_words:
            flags.append("too_long")
        if context.strip() and self._overlap_ratio(original, context) < self.min_context_overlap:
            flags.append("out_of_scope")

        if not flags:
            return GuardrailResult(approved_text=original, flagged=False, original_text=original)

        approved = original
        blocking = _BLOCKING_FLAGS.intersection(flags)

        if blocking and self.action == "truncate":
            stripped = self._strip_leaks(original)
            # If stripping was not enough (or removed everything), block outright.
            if stripped is None or self._count_code_lines(stripped) > self.max_code_lines:
                approved = _SOCRATIC_FALLBACK
                action = "blocked"
            else:
                approved = stripped
                action = "truncated"
        elif blocking:
            approved = _SOCRATIC_FALLBACK
            action = "blocked"
        else:
            # Only advisory flags (too_long / out_of_scope): keep the response.
            action = "flagged"

        return GuardrailResult(
            approved_text=approved,
            flagged=True,
            flags=flags,
            action=action,
            original_text=original,
        )

    # -- Internals ----------------------------------------------------------
    @staticmethod
    def _count_code_lines(text: str) -> int:
        fenced = sum(len(block.splitlines()) for block in _CODE_FENCE_RE.findall(text))
        unwrapped = len(_CODE_LINE_RE.findall(text))
        return max(fenced, unwrapped)

    @staticmethod
    def _strip_leaks(text: str) -> Optional[str]:
        """Remove fenced code blocks and direct-answer sentences.

        Returns ``None`` when nothing salvageable remains.
        """
        text = _CODE_FENCE_RE.sub("[code removed by the tutor guardrail]", text)
        sentences = re.split(r"(?<=[.!?])\s+", text)
        kept = [s for s in sentences if not _DIRECT_ANSWER_RE.search(s)]
        cleaned = " ".join(kept).strip()
        return cleaned or None

    @staticmethod
    def _overlap_ratio(response: str, context: str) -> float:
        response_tokens = {t.lower() for t in _WORD_RE.findall(response)}
        if not response_tokens:
            return 1.0
        context_tokens = {t.lower() for t in _WORD_RE.findall(context)}
        return len(response_tokens & context_tokens) / len(response_tokens)
