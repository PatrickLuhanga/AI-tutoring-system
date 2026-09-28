"""Loader for the generated few-shot intent registry.

``scripts/generate_few_shots.py`` analyses the ingested curriculum and writes
``src/agents/few_shot_registry.json``. This module is the seam the Intent Agent
uses to consume that file at runtime instead of relying solely on hand-written
regexes.

The registry is authored in a four-label vocabulary
(``conceptual``, ``debugging``, ``procedural``, ``bypass_attempt``) which is
translated here onto the classifier's internal label set (see
``src.models.INTENT_VALUES``) so the rest of the pipeline keeps its existing
routing contract.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ..config import PROJECT_ROOT

logger = logging.getLogger(__name__)

#: Default location of the generated registry.
DEFAULT_REGISTRY_PATH: Path = PROJECT_ROOT / "src" / "agents" / "few_shot_registry.json"

#: Registry label -> Intent Agent internal label.
REGISTRY_TO_INTERNAL: dict[str, str] = {
    "conceptual": "conceptual",
    "debugging": "debugging",
    # "procedural" (how to approach/start a task) is the internal "problem_solving".
    "procedural": "problem_solving",
    # "bypass_attempt" (demands the answer/code) is the internal "bypass".
    "bypass_attempt": "bypass",
}

#: Reverse mapping, used when reporting or exporting.
INTERNAL_TO_REGISTRY: dict[str, str] = {v: k for k, v in REGISTRY_TO_INTERNAL.items()}


@dataclass(frozen=True, slots=True)
class FewShotExample:
    """One curriculum-grounded demonstration of an intent classification."""

    raw_student_input: str
    registry_intent: str
    intent: str  # translated to the classifier's internal label set
    optimized_search_query: str
    reasoning_hint: str
    module_id: str


class FewShotRegistry:
    """In-memory view of ``few_shot_registry.json``.

    Loading is best-effort: a missing or malformed registry leaves the Intent
    Agent on its heuristic fallback rather than raising.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_REGISTRY_PATH
        self._by_module: dict[str, list[FewShotExample]] = {}
        self._names: dict[str, str] = {}
        self._all: list[FewShotExample] = []
        self._loaded = False

    # -- Loading ------------------------------------------------------------
    def load(self, *, force: bool = False) -> "FewShotRegistry":
        if self._loaded and not force:
            return self
        self._by_module = {}
        self._names = {}
        self._all = []
        self._loaded = True

        if not self.path.is_file():
            logger.info("Few-shot registry not found at %s; using heuristics only.", self.path)
            return self
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read few-shot registry %s: %s", self.path, exc)
            return self

        modules = data.get("modules") if isinstance(data, dict) else None
        if not isinstance(modules, dict):
            logger.warning("Few-shot registry %s has no 'modules' mapping.", self.path)
            return self

        for module_id, entry in modules.items():
            if not isinstance(entry, dict):
                continue
            self._names[module_id.lower()] = module_id
            for key in ("module_name", "module_code"):
                name = entry.get(key)
                if name:
                    self._names[str(name).strip().lower()] = module_id
            for raw in entry.get("examples") or []:
                example = _build_example(raw, module_id)
                if example is not None:
                    self._by_module.setdefault(module_id, []).append(example)
                    self._all.append(example)

        logger.info(
            "Loaded %d few-shot intent example(s) across %d module(s) from %s.",
            len(self._all),
            len(self._by_module),
            self.path.name,
        )
        return self

    # -- Access -------------------------------------------------------------
    @property
    def examples(self) -> list[FewShotExample]:
        return list(self._all)

    def for_module(self, module_key: Optional[str]) -> list[FewShotExample]:
        """Return examples for a module id, module code or module name."""
        if not module_key:
            return []
        key = str(module_key).strip().lower()
        module_id = self._names.get(key, module_key if module_key in self._by_module else None)
        if module_id is None:
            return []
        return list(self._by_module.get(module_id, []))

    def is_empty(self) -> bool:
        return not self._all


def _build_example(raw: Any, module_id: str) -> Optional[FewShotExample]:
    if not isinstance(raw, dict):
        return None
    message = str(raw.get("raw_student_input") or "").strip()
    registry_intent = str(raw.get("true_intent") or "").strip().lower()
    internal_intent = REGISTRY_TO_INTERNAL.get(registry_intent)
    if not message or internal_intent is None:
        return None
    return FewShotExample(
        raw_student_input=message,
        registry_intent=registry_intent,
        intent=internal_intent,
        optimized_search_query=str(raw.get("optimized_search_query") or "").strip(),
        reasoning_hint=str(raw.get("reasoning_hint") or "").strip(),
        module_id=module_id,
    )


_REGISTRY: Optional[FewShotRegistry] = None


def get_few_shot_registry(path: Optional[Path] = None) -> FewShotRegistry:
    """Return the process-wide registry singleton (lazy-loaded)."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = FewShotRegistry(path).load()
    return _REGISTRY
