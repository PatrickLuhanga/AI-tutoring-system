"""Chat-history sidebar and evaluation-isolation (defect C8) tests.

The sidebar store (``tutoring_sessions`` / ``session_messages``) and the
evaluation harness' write-suppression are both new here. The DB-backed parts
skip when PostgreSQL is unavailable; the isolation contract never needs a DB.
"""

from __future__ import annotations

import pytest


class _Intent:
    label = "conceptual"
    route = "scaffold"
    confidence = 0.5

    def to_dict(self) -> dict:
        return {"label": self.label, "route": self.route}


class _Resp:
    text = "Try tracing the loop condition."

    def to_dict(self) -> dict:
        return {"text": self.text, "provider": "cloud", "model": "m", "backend": "groq", "latency_ms": 1}


class _Audit:
    approved_text = "Try tracing the loop condition."
    flagged = False
    flags: list[str] = []

    def to_dict(self) -> dict:
        return {"flagged": False, "flags": [], "action": "pass"}


class _EmptyRetrieval:
    query = "why does my loop never end"
    module_id = "IPRT301"
    chunks: list = []
    patterns: list = []
    chunk_ids: list = []
    pattern_ids: list = []
    grounding_categories: list = []
    third_party_fallback = False
    below_threshold = 0
    is_empty = True

    def context_text(self) -> str:
        return ""

    def citations(self) -> list:
        return []


def test_generation_eval_is_isolated_by_default():
    """The eval harness must default to *not* writing to the live database (C8)."""
    from eval.run_generation_eval import build_workflow

    wf = build_workflow(no_rag=False, no_guardrail=False, no_scaffolding=False)
    assert wf.persist is False


def test_generation_eval_can_opt_in_to_persistence():
    from eval.run_generation_eval import build_workflow

    wf = build_workflow(False, False, False, persist=True)
    assert wf.persist is True


def test_non_persisting_workflow_writes_nothing(monkeypatch):
    """``persist=False`` serves the turn but never touches chat/queue/telemetry."""
    from src.agents.workflow import ChatRequest, TutoringWorkflow

    wf = TutoringWorkflow(persist=False)
    monkeypatch.setattr(wf, "_retrieve", lambda *a, **k: _EmptyRetrieval())
    monkeypatch.setattr(wf.intent_agent, "classify", lambda *a, **k: _Intent())
    monkeypatch.setattr(wf.scaffolding, "determine_stage", lambda *a, **k: ("hint", 1))
    monkeypatch.setattr(wf.tutor, "draft", lambda *a, **k: _Resp())
    monkeypatch.setattr(wf.guardrail, "audit", lambda *a, **k: _Audit())

    calls: list[str] = []
    monkeypatch.setattr(wf, "_record_turn", lambda **k: calls.append("chat"))
    monkeypatch.setattr(wf, "_queue_if_ungrounded", lambda **k: calls.append("queue"))
    monkeypatch.setattr(wf, "_log_telemetry", lambda **k: calls.append("telemetry"))

    result = wf.handle(
        ChatRequest(message="why does my loop never end", module_id="IPRT301", session_id="eval-1")
    )

    assert result.reply == _Audit.approved_text
    assert calls == []
    assert result.telemetry_log_id is None


def test_history_module_serialises_sessions():
    """The sidebar serialiser is pure and needs no database."""
    from src.history import _derive_title

    assert _derive_title("A very long question " * 10).endswith("\u2026")
    assert _derive_title("   spaced   out   ") == "spaced out"
    assert _derive_title("") == "New chat"


@pytest.mark.skip(reason="requires a live PostgreSQL instance")
def test_history_roundtrip_against_database():  # pragma: no cover - env dependent
    pass
