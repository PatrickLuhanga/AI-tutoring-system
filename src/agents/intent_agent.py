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
    # "write the full DAO implementation" / "write me a complete answer": the
    # adjective plus any noun is a demand for the finished artefact.
    r"write (?:me |us )?(?:the |a |an )?(?:full|entire|whole|complete)\b|"
    r"implement (?:this|it|the whole) for me|"
    r"solve (this|it) for me|paste the answer"
    r")\b"
)
_DEBUG_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:error|errors|exception|exceptions|stack ?trace|traceback|compile|compiler|"
    r"bug|bugs|broken|throws|throw|crash|fails?|why is my|fix my)\b"
    # CamelCase exception/error class names, e.g. NullPointerException. Anchored
    # to the end of the token so it cannot fire on an unrelated substring.
    r"|\w*exception\b|\w*error\b"
    r"|null\s*pointer|index\s*out\s*of\s*range"
    r"|doesn'?t (?:work|compile|run)"
    r"|\bnot working\b"
    r"|\bnever (?:ends|stops|terminates)\b|\binfinite loop\b|\bwon'?t stop\b|\bdoesn'?t stop\b"
    r"|\bkeeps (?:printing|running|looping)\b"
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
#: Deliberately narrow: a question is only "factual" when recalling a definition is
#: the whole point. Anything that also asks for reasoning is caught earlier by
#: :data:`_REASONING_RE`. ``difference between`` used to appear here as well as in
#: :data:`_CONCEPT_RE`; because the factual test runs first that sent every
#: comparison question straight to the direct track and silently bypassed the
#: Scaffolding Engine.
_FACTUAL_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:stand|stands) for\b"
    r"|\b(?:acronym|abbreviation)\b"
    r"|\b(?:define|definition of|definition for)\b"
    r"|\bwhat (?:is|are|was|were)\b"
    r"|\bwhat does\b.{0,40}\b(?:mean|stand for)\b"
    r"|\bwhat(?:'s|s) the (?:meaning|full form|definition)\b"
    r")"
)
#: Asks the model to *reason* rather than recall a definition -> conceptual track.
#: Tested before the definitional pattern, so "What is inheritance and why does it
#: help with code reuse?" scaffolds instead of dumping the answer on turn one.
_REASONING_RE = re.compile(
    r"(?i)\b(?:"
    r"why (?:does|do|did|is|are|was|were|would|should|can|could|might)|"
    r"how (?:does|do|did|is|are|was|were|can|could|would|should)|"
    r"explain (?:how|why)|"
    r"when (?:should|would|do|to use)|"
    r"what (?:makes|causes|leads)|"
    r"how come|"
    # A comparison, a trade-off, a challenge or a purpose all require reasoning
    # rather than recall, even when the sentence opens with "what is".
    r"difference between|"
    r"trade-?offs?|"
    r"challenges? (?:of|with|in|when|that)|"
    r"how (?:it|this|that) (?:works|is used)"
    # NB: a bare "used for" is deliberately NOT here. "What are XML schemas used
    # for?" is a definitional question and should take the direct track; treating
    # it as reasoning regressed two factual rows to fix one conceptual row.
    r")\b"
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


def _has_error_signal(text: str) -> bool:
    """True only when the message carries a genuine error/code signal.

    A "debugging" label is only ever justified by an error token (``error``,
    ``traceback``, ...), a stack trace or actual pasted code. This is the guard
    that keeps a plain definitional question - "What is research?" - off the
    debugging track when the LLM classifier over-triggers on a topic keyword.
    Matching is word-boundary anchored, so a token like ``search`` can never be
    found inside ``research`` and ``bug`` never inside ``debugging``.
    """
    return bool(_DEBUG_RE.search(text or "") or _CODE_SIGNAL_RE.search(text or ""))


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
                    return self._reconcile(llm_intent, message)
            except LLMError as exc:
                logger.warning("Intent LLM unavailable, using heuristics: %s", exc)
        return self._heuristic(message)

    @staticmethod
    def _reconcile(intent: Intent, message: str) -> Intent:
        """Veto an LLM "debugging" label that has no error/code evidence.

        Small models over-trigger on topic words; a definitional question such
        as "What is research?" must never be routed as debugging. When the label
        is unsupported by any error/code signal, fall back to the deterministic
        heuristic, which is word-boundary based and routes it to the direct or
        conceptual track instead.
        """
        if intent.label == "debugging" and not _has_error_signal(message):
            heuristic = IntentAgent._heuristic(message)
            logger.info(
                "LLM labelled %r debugging but no error/code signal is present; "
                "using heuristic label %s instead.",
                (message or "")[:80],
                heuristic.label,
            )
            return heuristic
        return intent

    # -- LLM classification -------------------------------------------------
    def _few_shots_for(self, module_name: Optional[str]) -> list[dict[str, str]]:
        """Return a balanced slice of registry demonstrations for the prompt.

        Balanced rather than "first N": examples are drawn round-robin across
        labels so a heavily-represented class (debugging dominates the IPRT
        registry) cannot crowd out the rare ones (bypass attempts).
        """
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
        # A definition request that also asks *why*/*how* is a conceptual question.
        if _REASONING_RE.search(text):
            return make("conceptual", 0.7, "Matched an explanation/reasoning request.")
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
