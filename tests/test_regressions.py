"""Regression tests for the four defects found in the RESK301 test turn.

Covers: intent false-positive on "What is research?", strict module isolation in
the retriever, and the Groq config reconciliation. The frontend fixes (badge,
sidebar refresh, mock opt-in) are verified by the typecheck/build.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Bug 1 - intent classification false positive
# ---------------------------------------------------------------------------
def test_definitional_question_never_debugging():
    from src.agents.intent_agent import IntentAgent

    for text in (
        "What is research?",
        "what is research",
        "Explain research methods",
        "What is a literature review?",
    ):
        assert IntentAgent._heuristic(text).label != "debugging"


def test_reconcile_vetoes_llm_debugging_without_evidence():
    from src.agents.intent_agent import Intent, IntentAgent

    over_eager = Intent(label="debugging", confidence=0.99, source="llm")
    vetoed = IntentAgent._reconcile(over_eager, "What is research?")
    assert vetoed.label != "debugging"

    # A genuine error is still allowed through.
    genuine = Intent(label="debugging", confidence=0.9, source="llm")
    kept = IntentAgent._reconcile(genuine, "NullPointerException in ServerTester at line 12")
    assert kept.label == "debugging"


def test_no_naive_substring_matching():
    from src.agents.intent_agent import _has_error_signal

    # "search" inside "research" must not count as an error/code signal.
    assert _has_error_signal("What is research and how do I search the literature?") is False
    # "bug" inside "debugging" must not either.
    assert _has_error_signal("explain debugging techniques") is False


# ---------------------------------------------------------------------------
# Bug 2 - strict module isolation
# ---------------------------------------------------------------------------
def test_is_coding_module():
    from src.config import settings

    assert settings.is_coding_module("IPRT301") is True
    assert settings.is_coding_module("PBDV301") is True
    assert settings.is_coding_module("RESK301") is False
    assert settings.is_coding_module("SPRI301") is False
    assert settings.is_coding_module("NOPE999") is False


# ---------------------------------------------------------------------------
# Bug 3 - Groq config reconciliation
# ---------------------------------------------------------------------------
def _stale_local_config(updated_by: str):
    from src.models import LLMConfig

    return LLMConfig(
        config_id=1,
        name="default",
        is_active=True,
        provider="local",
        ollama_base_url="http://localhost:11434",
        local_model="qwen3:4b",
        cloud_provider="openai",
        cloud_base_url="https://api.openai.com/v1",
        cloud_model="gpt-4o-mini",
        temperature=0.4,
        max_tokens=1024,
        top_p=0.9,
        extra={},
        updated_by=updated_by,
    )


def test_stale_seeded_local_config_is_reconciled_when_env_is_groq():
    from src.config import settings
    from src.llm_router import LLMConfigService

    if settings.default_llm_provider != "cloud":
        return  # environment does not ask for Groq; nothing to reconcile

    # A row seeded before the migration must be refreshed to the env defaults.
    assert LLMConfigService._should_reconcile(_stale_local_config("system-bootstrap")) is True
    assert LLMConfigService._should_reconcile(_stale_local_config("setup_database")) is True
    # ...but an admin's deliberate choice is preserved.
    assert LLMConfigService._should_reconcile(_stale_local_config("admin@example.edu")) is False


class _EmptyResult:
    def all(self):
        return []


class _CapturingSession:
    def __init__(self) -> None:
        self.statements: list = []

    def execute(self, stmt):
        self.statements.append(stmt)
        return _EmptyResult()


def test_pattern_search_is_module_scoped_for_theory_modules():
    from src.retriever import Retriever

    retriever = Retriever()

    theory = _CapturingSession()
    retriever._search_patterns(theory, [0.0] * 8, "RESK301", allow_general=False)
    theory_sql = str(theory.statements[-1]).upper()
    # The query is still module-scoped (bound parameter) and, crucially, never
    # widens to the untagged "general" patterns for a theory module.
    assert "MODULE_ID" in theory_sql
    assert " IS NULL" not in theory_sql

    coding = _CapturingSession()
    retriever._search_patterns(coding, [0.0] * 8, "IPRT301", allow_general=True)
    assert " IS NULL" in str(coding.statements[-1]).upper()
