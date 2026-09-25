"""Tutor Agent (Tier 2, sections 7-9).

Drafts the response from the scaffolding system prompt, few-shot examples,
retrieved course context and the current question. It never talks to Ollama
directly - it goes through the Dynamic LLM Router, which selects the active
backend (local Ollama or a cloud API).

Two drafting paths mirror the Intent Agent's two routing tracks:

* :meth:`draft`           - Socratic guidance for conceptual/debugging turns.
* :meth:`answer_directly` - a concise factual/definitional answer from RAG.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Optional

from ..llm_router import LLMResponse, LLMRouter, get_router
from ..prompts import (
    DIRECT_FEW_SHOT_EXAMPLES,
    SCAFFOLD_FEW_SHOT_EXAMPLES,
    build_tutor_user_prompt,
)
from ..retriever import RetrievalResult
from .intent_agent import Intent
from .scaffolding import ScaffoldingLayer


class TutorAgent:
    """Drafts the response using the scaffolding instructions and retrieved context."""

    def __init__(self, router: Optional[LLMRouter] = None) -> None:
        self.router = router or get_router()

    def draft(
        self,
        question: str,
        module_name: str,
        stage: str,
        intent: Intent,
        retrieval: RetrievalResult,
        history: Optional[Iterable[Mapping[str, str]]] = None,
    ) -> LLMResponse:
        """Socratic scaffold turn (conceptual / debugging / problem-solving)."""
        system_prompt = ScaffoldingLayer.build_system_prompt(module_name, stage, intent, retrieval)
        user_prompt = build_tutor_user_prompt(question, history)
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            *[dict(item) for item in SCAFFOLD_FEW_SHOT_EXAMPLES],
            {"role": "user", "content": user_prompt},
        ]
        return self.router.generate(messages)

    def answer_directly(
        self,
        question: str,
        module_name: str,
        intent: Intent,
        retrieval: RetrievalResult,
        history: Optional[Iterable[Mapping[str, str]]] = None,
    ) -> LLMResponse:
        """Direct factual / definitional turn - no Socratic counter-questioning."""
        system_prompt = ScaffoldingLayer.build_system_prompt(
            module_name, "direct_answer", intent, retrieval
        )
        user_prompt = build_tutor_user_prompt(question, history)
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            *[dict(item) for item in DIRECT_FEW_SHOT_EXAMPLES],
            {"role": "user", "content": user_prompt},
        ]
        return self.router.generate(messages, temperature=0.2)
