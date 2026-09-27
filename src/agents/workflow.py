"""Tutoring workflow - coordinates the agents for one student turn (Tier 2).

The pipeline is a chain of single-responsibility agents, each in its own module
(section 21: "no single component should be responsible for everything"):

    Intent Agent  ->  classify what the student wants        (section 6)
    Scaffolding   ->  decide how directly to guide           (section 7)
    Retriever     ->  fetch module-scoped course context     (section 8)
    Tutor Agent   ->  draft the Socratic response            (sections 7-9)
    Guardrail     ->  block leaked solutions / audit         (section 11)

Fault isolation
---------------
A crash in the **Data Tier** (PostgreSQL / pgvector) must not take the API down.
The two DB-dependent steps - counting prior turns and RAG retrieval - are wrapped
so that a database outage degrades the reply (no history depth, no retrieved
context) instead of raising. Telemetry writes were already best-effort. A crash
in the **Inference Tier** (Ollama) is raised as an :class:`LLMError`, which the
route converts into the clean JSON payload the UI expects.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import func, select

from ..config import settings
from ..db import session_scope
from ..llm_router import LLMError, LLMResponse, LLMRouter, get_router
from ..models import CurriculumChunk, Module, TelemetryLog
from ..query_expansion import (
    QueryExpander,
    is_weak_result,
    reciprocal_rank_fusion,
)
from ..retriever import (
    RetrievalPolicy,
    RetrievalResult,
    RetrievedChunk,
    Retriever,
    get_retriever,
)
from ..prompts import ROUTE_DIRECT
from ..unanswered import classify_reason, record_ungrounded
from .guardrail import GuardrailAgent, GuardrailResult
from .intent_agent import _CODE_SIGNAL_RE, Intent, IntentAgent
from .scaffolding import ScaffoldingLayer
from .tutor_agent import TutorAgent

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ChatRequest:
    message: str
    module_id: str
    session_id: str
    student_id: Optional[int] = None
    student_email: Optional[str] = None
    history: list[dict[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class WorkflowResult:
    session_id: str
    message_id: str
    reply: str
    intent: dict[str, Any]
    scaffolding: dict[str, Any]
    guardrail: dict[str, Any]
    retrieval: dict[str, Any]
    llm: dict[str, Any]
    telemetry_log_id: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "message_id": self.message_id,
            "reply": self.reply,
            "intent": self.intent,
            "scaffolding": self.scaffolding,
            "guardrail": self.guardrail,
            "retrieval": self.retrieval,
            "llm": self.llm,
            "telemetry_log_id": self.telemetry_log_id,
        }


class TutoringWorkflow:
    """Coordinates the agents for one student turn and logs telemetry."""

    def __init__(
        self,
        retriever: Optional[Retriever] = None,
        router: Optional[LLMRouter] = None,
        intent_agent: Optional[IntentAgent] = None,
        scaffolding: Optional[ScaffoldingLayer] = None,
        tutor: Optional[TutorAgent] = None,
        guardrail: Optional[GuardrailAgent] = None,
    ) -> None:
        self.router = router or get_router()
        self.retriever = retriever or get_retriever()
        self.intent_agent = intent_agent or IntentAgent(self.router)
        self.scaffolding = scaffolding or ScaffoldingLayer()
        self.tutor = tutor or TutorAgent(self.router)
        self.guardrail = guardrail or GuardrailAgent()
        self.expander = QueryExpander(self.router)

    def handle(self, request: ChatRequest) -> WorkflowResult:
        started = time.perf_counter()
        module_name = self._module_name(request.module_id)
        message_id = uuid.uuid4().hex

        # Data Tier: degrade to zero history if PostgreSQL is unreachable.
        prior_turns = self._count_session_turns(request.session_id)
        intent = self.intent_agent.classify(request.message, module_name, request.history)
        # ``determine_stage`` returns the neutral ``direct_answer`` stage for the
        # factual track, so telemetry stays consistent without a Socratic stage.
        stage, depth = self.scaffolding.determine_stage(prior_turns, intent, request.message)
        direct = intent.route == ROUTE_DIRECT

        # Data Tier: degrade to empty context if the vector store is unreachable.
        retrieval = self._retrieve(request, intent, stage, direct)

        # Inference Tier: may raise LLMError -> handled as a clean 502 upstream.
        if direct:
            # Factual / definitional: answer from RAG, bypass scaffolding.
            draft: LLMResponse = self.tutor.answer_directly(
                request.message,
                module_name,
                intent,
                retrieval,
                request.history,
            )
        else:
            # Conceptual / debugging: Socratic scaffolding.
            draft = self.tutor.draft(
                request.message,
                module_name,
                stage,
                intent,
                retrieval,
                request.history,
            )

        audit = self.guardrail.audit(
            draft.text,
            retrieval.context_text(),
            request.message,
            allow_direct=direct,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)

        # Human Adjustment Cycle (section 17): if RAG could not ground the
        # question, queue it for a tutor instead of letting the model answer from
        # its parametric memory. A weak guardrail flag also queues it.
        self._queue_if_ungrounded(
            request=request,
            intent=intent,
            retrieval=retrieval,
            audit=audit,
            message_id=message_id,
        )

        log_id = self._log_telemetry(
            request=request,
            message_id=message_id,
            intent=intent,
            stage=stage,
            depth=depth,
            audit=audit,
            retrieval=retrieval,
            latency_ms=latency_ms,
        )

        return WorkflowResult(
            session_id=request.session_id,
            message_id=message_id,
            reply=audit.approved_text,
            intent=intent.to_dict(),
            scaffolding={"stage": stage, "hint_sequence_depth": depth, "route": intent.route},
            guardrail=audit.to_dict(),
            retrieval={
                "chunks": [c.to_dict() for c in retrieval.chunks],
                "patterns": [p.to_dict() for p in retrieval.patterns],
                "citations": retrieval.citations(),
                "grounding_categories": retrieval.grounding_categories,
                "third_party_fallback": retrieval.third_party_fallback,
                "below_threshold": retrieval.below_threshold,
                "context_empty": retrieval.is_empty,
            },
            llm=draft.to_dict(),
            telemetry_log_id=log_id,
        )

    # -- Helpers ------------------------------------------------------------
    @staticmethod
    def _module_name(module_id: str) -> str:
        for module in settings.modules.values():
            if module["module_id"] == module_id:
                return module["module_name"]
        return module_id

    def _retrieve(
        self,
        request: ChatRequest,
        intent: Intent,
        stage: str,
        direct: bool,
    ) -> RetrievalResult:
        """Retrieve module-scoped context, degrading gracefully on DB failure.

        Worked solutions are withheld until the session reaches the Explanation
        stage (or is a factual turn, where answering directly is the intent), so
        retrieval cannot collapse the Socratic scaffolding (section 7.4).

        A first pass that comes back weak triggers one adaptive second hop: the
        question is rewritten into alternative phrasings, grounded in the
        module's own section titles, and the runs are fused by reciprocal rank.
        This is what lets an abstract question like "why does code reuse matter?"
        find the inheritance slides instead of falling back to memory.
        """
        include_patterns = intent.label in {"debugging", "problem_solving"} or bool(
            _CODE_SIGNAL_RE.search(request.message)
        )
        policy = RetrievalPolicy(
            allow_answers=bool(direct) or stage == "explanation",
            include_third_party=settings.retrieval_include_third_party,
            max_distance=(settings.retrieval_max_distance or None),
        )
        try:
            first = self.retriever.retrieve(
                request.message,
                request.module_id,
                include_patterns=include_patterns,
                policy=policy,
            )
            if not (
                settings.retrieval_expand_on_weak
                and is_weak_result(first.chunks, weak_distance=settings.retrieval_weak_distance)
            ):
                return first
            return self._retrieve_expanded(
                request, policy, include_patterns, first
            )
        except Exception as exc:  # noqa: BLE001 - Data Tier must not break the turn
            logger.warning(
                "Retrieval failed for module %s; continuing without course context: %s",
                request.module_id,
                exc,
            )
            return RetrievalResult(query=request.message, module_id=request.module_id)

    def _retrieve_expanded(
        self,
        request: ChatRequest,
        policy: RetrievalPolicy,
        include_patterns: bool,
        first: RetrievalResult,
    ) -> RetrievalResult:
        """Second retrieval hop over rewrites of a question that retrieved weakly."""
        try:
            titles = self._module_section_titles(request.module_id)
            rewrites = self.expander.expand(
                request.message, self._module_name(request.module_id), titles
            )
            if not rewrites:
                logger.info(
                    "Weak retrieval for %s but no usable rewrite; keeping the first pass.",
                    request.module_id,
                )
                return first

            runs: list[RetrievalResult] = [first]
            for rewrite in rewrites:
                runs.append(
                    self.retriever.retrieve(
                        rewrite,
                        request.module_id,
                        include_patterns=False,
                        policy=policy,
                    )
                )

            merged = self._fuse(first, runs, request, policy, include_patterns)
            if merged is not None:
                logger.info(
                    "Expanded %r into %d rewrite(s); grounding improved "
                    "(best distance %.3f -> %.3f)",
                    request.message[:50],
                    len(rewrites),
                    first.chunks[0].distance if first.chunks else float("nan"),
                    merged.chunks[0].distance if merged.chunks else float("nan"),
                )
                return merged
            return first
        except Exception as exc:  # noqa: BLE001 - enhancement must not break the turn
            logger.warning("Query expansion failed, using the first pass: %s", exc)
            return first

    def _module_section_titles(self, module_id: str) -> list[str]:
        """Section headings for a module, used to ground the rewrite."""
        try:
            with session_scope() as session:
                return list(
                    session.execute(
                        select(CurriculumChunk.section_title)
                        .where(
                            CurriculumChunk.module_id == module_id,
                            CurriculumChunk.section_title.isnot(None),
                        )
                        .distinct()
                        .limit(60)
                    ).scalars()
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not list section titles for %s: %s", module_id, exc)
            return []

    def _fuse(
        self,
        first: RetrievalResult,
        runs: list[RetrievalResult],
        request: ChatRequest,
        policy: RetrievalPolicy,
        include_patterns: bool,
    ) -> Optional[RetrievalResult]:
        """Merge several runs into one result, keeping the original query string.

        Citation keys are re-assigned after fusion so ``[C1]`` in a reply still
        matches the source list the student sees.
        """
        by_id: dict[int, RetrievedChunk] = {}
        for run in runs:
            for chunk in run.chunks:
                existing = by_id.get(chunk.chunk_id)
                if existing is None or chunk.distance < existing.distance:
                    by_id[chunk.chunk_id] = chunk
        if not by_id:
            return None

        ordered_ids = reciprocal_rank_fusion(
            [[c.chunk_id for c in run.chunks] for run in runs if run.chunks]
        )
        pool = {c.chunk_id: c for c in first.chunks}
        for run in runs[1:]:
            pool.update({c.chunk_id: c for c in run.chunks})

        merged = RetrievalResult(
            query=first.query,
            module_id=first.module_id,
            policy=first.policy,
        )
        limit = getattr(self.retriever, "top_k", 3) or 3
        # RRF decides which chunks survive; distance decides the order they are
        # shown in, so the closest match leads the prompt rather than whatever
        # several runs happened to agree on.
        survivors = sorted(
            (by_id[cid] for cid in ordered_ids[: limit * 2] if cid in by_id),
            key=lambda c: c.distance,
        )
        for chunk in survivors:
            # Skip a chunk from a document already in context, so the reply reads
            # as one passage instead of two fragments of the same slide deck.
            if merged.chunks and merged.chunks[-1].source_file == chunk.source_file:
                continue
            chunk.cite_key = f"C{len(merged.chunks) + 1}"
            merged.chunks.append(chunk)
            if len(merged.chunks) >= limit:
                break
        if not merged.chunks:
            return None
        if include_patterns:
            merged.patterns = first.patterns
        return merged

    @staticmethod
    def _queue_if_ungrounded(
        *,
        request: ChatRequest,
        intent: Intent,
        retrieval: RetrievalResult,
        audit: GuardrailResult,
        message_id: str,
    ) -> None:
        """Send a question to the tutor queue when it was not grounded in material.

        Deliberately conservative: only genuinely ungrounded turns are queued, so
        the queue stays a short list of real curriculum gaps rather than every
        question a student ever asked.
        """
        try:
            no_grounding = retrieval.is_empty
            weak_guardrail = bool(audit.flagged)
            if not (no_grounding or weak_guardrail):
                return
            if intent.label == "bypass":
                # A bypass attempt is a pedagogical event, not a curriculum gap.
                return
            reason = (
                "student_flagged"
                if weak_guardrail and not no_grounding
                else classify_reason(
                    chunk_count=len(retrieval.chunks),
                    below_threshold=retrieval.below_threshold,
                    third_party_fallback=retrieval.third_party_fallback,
                    grounded_categories=retrieval.grounding_categories,
                )
            )
            best = min((c.distance for c in retrieval.chunks), default=None)
            record_ungrounded(
                question_text=request.message,
                module_id=request.module_id,
                reason=reason,
                session_id=request.session_id,
                message_id=message_id,
                student_id=request.student_id,
                intent=intent.label,
                best_distance=best,
            )
        except Exception as exc:  # noqa: BLE001 - never break a reply
            logger.warning("Could not evaluate question for the tutor queue: %s", exc)

    @staticmethod
    def _count_session_turns(session_id: str) -> int:
        """Return how many turns this session has had (0 if the DB is down)."""
        try:
            with session_scope() as session:
                return int(
                    session.execute(
                        select(func.count())
                        .select_from(TelemetryLog)
                        .where(TelemetryLog.session_id == session_id)
                    ).scalar_one()
                )
        except Exception as exc:  # noqa: BLE001 - Data Tier must not break the turn
            logger.warning("Could not read session history for %s: %s", session_id, exc)
            return 0

    @staticmethod
    def _module_exists(module_id: str) -> bool:
        with session_scope() as session:
            return (
                session.execute(
                    select(Module.module_id).where(Module.module_id == module_id).limit(1)
                ).scalar_one_or_none()
                is not None
            )

    def _log_telemetry(
        self,
        *,
        request: ChatRequest,
        message_id: str,
        intent: Intent,
        stage: str,
        depth: int,
        audit: GuardrailResult,
        retrieval: RetrievalResult,
        latency_ms: int,
    ) -> Optional[int]:
        try:
            module_id = request.module_id if self._module_exists(request.module_id) else None
            with session_scope() as session:
                log = TelemetryLog(
                    session_id=request.session_id,
                    message_id=message_id,
                    student_id=request.student_id,
                    module_id=module_id,
                    intent=intent.label,
                    scaffolding_stage=stage,
                    hint_sequence_depth=depth,
                    guardrail_flagged=audit.flagged,
                    guardrail_failure_flags=audit.flags,
                    retrieved_curriculum_chunk_ids=retrieval.chunk_ids,
                    retrieved_code_pattern_ids=retrieval.pattern_ids,
                    embedding_model=settings.embedding_model_name,
                    response_latency_ms=latency_ms,
                    retrieval_categories=retrieval.grounding_categories,
                    third_party_fallback=retrieval.third_party_fallback,
                )
                session.add(log)
                session.flush()
                return int(log.log_id)
        except Exception as exc:  # noqa: BLE001 - telemetry must never break a reply
            logger.error("Failed to write telemetry for session %s: %s", request.session_id, exc)
            return None
