"""Intent Agent (Tier 2, section 6).

Classifies what the student is trying to do and, crucially, which of the two
routing tracks the turn belongs to:

* ``route == "direct"``   -> factual / definitional question. The workflow sends
  it straight to the RAG Orchestrator for a concise answer and **bypasses the
  Scaffolding Engine entirely**.
* ``route == "scaffold"`` -> conceptual / debugging / problem-solving / bypass.
  The workflow sends it to the Scaffolding Engine for Socratic guidance.

The LLM classifier is preferred, but a deterministic heuristic classifier
guarantees the pipeline still routes correctly when the inference tier is
unavailable. The heuristic is ordered so that concrete signals (a bypass demand,
a stack trace, a "how do I start") always win over the broad definition pattern.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

from ..config import settings
from ..inference import LLMError
from ..llm_router import LLMRouter, get_router
from ..prompts import INTENT_ROUTES, INTENT_SYSTEM, ROUTE_DIRECT, build_intent_prompt
from .few_shot_registry import FewShotExample, FewShotRegistry, get_few_shot_registry

logger = logging.getLogger(__name__)

#: Complete label vocabulary accepted from the LLM classifier.
_VALID_LABELS = frozenset(INTENT_ROUTES)

#: How many curriculum-grounded demonstrations to inject into the prompt.
_MAX_FEW_SHOTS = 12


@dataclass(slots=True)
class Intent:
    label: str
    confidence: float
    rationale: str = ""
    source: str = "heuristic"  # "heuristic" | "llm"
    route: str = ""  # "direct" | "scaffold"; derived from ``label`` if unset

    def __post_init__(self) -> None:
        # Keep ``route`` consistent with ``label`` no matter how it was built.
        if not self.route:
            self.route = INTENT_ROUTES.get(self.label, ROUTE_DIRECT)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "confidence": round(self.confidence, 2),
            "source": self.source,
            "route": self.route,
        }


_BYPASS_RE = re.compile(
    r"(?i)\b("
    r"write (my|the) (assignment|essay|report)|do my (homework|assignment)|"
    r"give me the (answer|code|solution)|just give me|"
    r"complete (solution|code)|full (solution|code)|final code|"
    r"write the (full|entire|whole) (program|code|solution)|"
    r"solve (this|it) for me|paste the answer"
    r")\b"
)
_DEBUG_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:error|exception|stack ?trace|traceback|compile|compiler|bug|broken|"
    r"throws|throw|crash|fails?|why is my|fix my)\b"
    r"|nullpointer|indexoutofrange"
    r"|doesn'?t (?:work|compile|run)"
    r"|not working"
    r"|never (?:ends|stops|terminates)|infinite loop|won'?t stop|doesn'?t stop"
    r"|keeps (?:printing|running|looping)"
    r")"
)
#: "How do I start / where do I begin" style requests (explicitly scaffolding).
_START_RE = re.compile(
    r"(?i)\b("
    r"how (?:do|can|should) i (?:start|begin|approach)|"
    r"where (?:do|should) i (?:start|begin)|"
    r"don'?t know (?:where|how) to start|"
    r"how to start|"
    r"no idea how to (?:start|begin|approach)"
    r")\b"
)
_PROBLEM_RE = re.compile(
    r"(?i)\b(how (do|can) i (solve|approach|calculate)|work through|prove|derive|calculate|"
    r"design a|come up with a)\b"
)
#: Definitional / acronym-expansion / syllabus-fact patterns -> direct track.
_FACTUAL_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:stand|stands) for\b"
    r"|\b(?:acronym|abbreviation)\b"
    r"|\b(?:define|definition of|definition for)\b"
    r"|\bdifference between\b"
    r"|\bwhat (?:is|are|was|were)\b"
    r"|\bwhat does\b.{0,40}\b(?:mean|stand for|do)\b"
    r"|\bexplain what\b"
    r"|\bwhat(?:'s|s) the (?:meaning|full form|definition)\b"
    r")"
)
#: Syllabus-fact patterns (dates, scope, marks) -> direct track.
_SYLLABUS_RE = re.compile(
    r"(?i)\b("
    r"when (?:is|are|'s) (?:the|our) (?:test|exam|assignment|test|due date)|"
    r"due date|deadline|"
    r"how many marks|pass mark|exam scope|test scope|"
    r"syllabus|study guide|"
    r"what chapters|which chapters|"
    r"what (?:is|are) the (?:scope|due date|deadline)"
    r")\b"
)
_CONCEPT_RE = re.compile(
    r"(?i)\b(what is|what are|explain|difference between|define|why does|why is|how does)\b"
)
#: Detects pasted code rather than prose mentioning coding keywords.
_CODE_SIGNAL_RE = re.compile(r"```|;\s*$|\{\s*$", re.MULTILINE)


class IntentAgent:
    """Classifies the student's request, preferring the LLM with a fallback."""

    def __init__(
        self,
        router: Optional[LLMRouter] = None,
        use_llm: Optional[bool] = None,
        registry: Optional[FewShotRegistry] = None,
    ) -> None:
        self.router = router or get_router()
        self.use_llm = settings.intent_use_llm if use_llm is None else use_llm
        if settings.intent_use_few_shots:
            self.registry = registry if registry is not None else get_few_shot_registry()
        else:
            self.registry = None

    def classify(
        self,
        message: str,
        module_name: Optional[str] = None,
        history: Optional[Iterable[Mapping[str, str]]] = None,
    ) -> Intent:
        if self.use_llm:
            try:
                llm_intent = self._classify_llm(message, module_name, history)
                if llm_intent is not None:
                    return llm_intent
            except LLMError as exc:
                logger.warning("Intent LLM unavailable, using heuristics: %s", exc)
        return self._heuristic(message)

    # -- LLM classification -------------------------------------------------
    def _few_shots_for(self, module_name: Optional[str]) -> list[dict[str, str]]:
        """Return a balanced slice of registry demonstrations for the prompt."""
        if self.registry is None or self.registry.is_empty():
            return []
        examples: list[FewShotExample] = self.registry.for_module(module_name) or self.registry.examples
        by_label: dict[str, list[FewShotExample]] = {}
        for example in examples:
            by_label.setdefault(example.intent, []).append(example)
        ordered: list[FewShotExample] = []
        while len(ordered) < _MAX_FEW_SHOTS and any(by_label.values()):
            for label in sorted(by_label):
                bucket = by_label[label]
                if bucket:
                    ordered.append(bucket.pop(0))
                    if len(ordered) >= _MAX_FEW_SHOTS:
                        break
        return [
            {"raw_student_input": example.raw_student_input, "intent": example.intent}
            for example in ordered
        ]

    def _classify_llm(
        self,
        message: str,
        module_name: Optional[str],
        history: Optional[Iterable[Mapping[str, str]]],
    ) -> Optional[Intent]:
        prompt = build_intent_prompt(
            message, module_name, history, few_shots=self._few_shots_for(module_name)
        )
        response = self.router.generate(
            [
                {"role": "system", "content": INTENT_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=120,
            json_mode=True,
        )
        data = _extract_json(response.text)
        if not data or "intent" not in data:
            return None
        label = str(data.get("intent", "")).strip().lower()
        if label not in _VALID_LABELS:
            return None
        try:
            confidence = float(data.get("confidence", 0.6))
        except (TypeError, ValueError):
            confidence = 0.6
        return Intent(
            label=label,
            confidence=max(0.0, min(1.0, confidence)),
            rationale=str(data.get("rationale", ""))[:280],
            source="llm",
            route=INTENT_ROUTES.get(label, ROUTE_DIRECT),
        )

    # -- Deterministic fallback --------------------------------------------
    @staticmethod
    def _heuristic(message: str) -> Intent:
        text = message or ""

        def make(label: str, confidence: float, rationale: str) -> Intent:
            return Intent(
                label=label,
                confidence=confidence,
                rationale=rationale,
                source="heuristic",
                route=INTENT_ROUTES.get(label, ROUTE_DIRECT),
            )

        # Order matters: concrete signals beat the broad definition pattern.
        if _BYPASS_RE.search(text):
            return make("bypass", 0.9, "Matched a bypass-style request.")
        if _DEBUG_RE.search(text) or _CODE_SIGNAL_RE.search(text):
            return make("debugging", 0.75, "Matched error/code signals.")
        if _START_RE.search(text) or _PROBLEM_RE.search(text):
            return make("problem_solving", 0.7, "Matched a how-to-start/approach request.")
        if _SYLLABUS_RE.search(text) or _FACTUAL_RE.search(text):
            return make("factual", 0.75, "Matched a definition/acronym/syllabus pattern.")
        if _CONCEPT_RE.search(text):
            return make("conceptual", 0.65, "Matched an explanation pattern.")
        return make("other", 0.4, "No strong signal.")


def _extract_json(text: str) -> Optional[dict[str, Any]]:
    """Leniently pull the first JSON object out of a model response."""
    if not text:
        return None
    candidates = [text.strip()]
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            continue
    return None
