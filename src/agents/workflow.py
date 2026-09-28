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
in the **Inference Tier** (Ollama) is raised as an :class:`LLMError`; before it
propagates, the workflow still records a failed-turn telemetry row (carrying the
:data:`INFERENCE_FAILURE_FLAG`) so abandoned sessions are observable, and the
route then converts the error into the clean JSON payload the UI expects.
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
from ..models import Module, TelemetryLog
from ..retriever import RetrievalResult, Retriever, get_retriever
from ..prompts import ROUTE_DIRECT
from .guardrail import GuardrailAgent, GuardrailResult
from .intent_agent import _CODE_SIGNAL_RE, Intent, IntentAgent
from .scaffolding import ScaffoldingLayer
from .tutor_agent import TutorAgent

logger = logging.getLogger(__name__)

#: Failure flag recorded on a telemetry row when the inference tier fails a turn.
#: Abandoned-session analytics select rows carrying this flag.
INFERENCE_FAILURE_FLAG = "inference_error"


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
        retrieval = self._retrieve(request, intent)

        # Inference Tier: may raise LLMError -> handled as a clean 502 upstream.
        draft: LLMResponse
        try:
            if direct:
                # Factual / definitional: answer from RAG, bypass scaffolding.
                draft = self.tutor.answer_directly(
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
        except LLMError:
            # The turn is abandoned, but analytics still need to see it: record a
            # failed-turn row (with the context gathered so far) before the route
            # turns the error into a 502. The guardrail never ran, so this is not
            # a content flag - only the failure-flag list is populated.
            self._log_telemetry(
                request=request,
                message_id=message_id,
                intent=intent,
                stage=stage,
                depth=depth,
                retrieval=retrieval,
                latency_ms=int((time.perf_counter() - started) * 1000),
                failure_flags=[INFERENCE_FAILURE_FLAG],
            )
            raise

        audit = self.guardrail.audit(
            draft.text,
            retrieval.context_text(),
            request.message,
            allow_direct=direct,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)

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
                "query": retrieval.query,
                "module_id": retrieval.module_id,
                "chunks": [c.to_dict() for c in retrieval.chunks],
                "patterns": [p.to_dict() for p in retrieval.patterns],
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

    def _retrieve(self, request: ChatRequest, intent: Intent) -> RetrievalResult:
        """Retrieve module-scoped context, degrading gracefully on DB failure."""
        include_patterns = intent.label in {"debugging", "problem_solving"} or bool(
            _CODE_SIGNAL_RE.search(request.message)
        )
        try:
            return self.retriever.retrieve(
                request.message, request.module_id, include_patterns=include_patterns
            )
        except Exception as exc:  # noqa: BLE001 - Data Tier must not break the turn
            logger.warning(
                "Retrieval failed for module %s; continuing without course context: %s",
                request.module_id,
                exc,
            )
            return RetrievalResult(query=request.message, module_id=request.module_id)

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
        retrieval: RetrievalResult,
        latency_ms: int,
        audit: Optional[GuardrailResult] = None,
        failure_flags: Optional[list[str]] = None,
    ) -> Optional[int]:
        """Best-effort telemetry write.

        A completed turn passes ``audit``. A turn abandoned by an inference
        failure passes ``failure_flags`` instead: the guardrail never ran, so
        ``guardrail_flagged`` stays false and only the failure-flag list carries
        the marker (``inference_error``) used to compute abandoned sessions.
        """
        if failure_flags is not None:
            flagged = False
            flags = list(failure_flags)
        elif audit is not None:
            flagged = audit.flagged
            flags = list(audit.flags)
        else:
            flagged = False
            flags = []

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
                    guardrail_flagged=flagged,
                    guardrail_failure_flags=flags,
                    retrieved_curriculum_chunk_ids=retrieval.chunk_ids,
                    retrieved_code_pattern_ids=retrieval.pattern_ids,
                    embedding_model=settings.embedding_model_name,
                    response_latency_ms=latency_ms,
                )
                session.add(log)
                session.flush()
                return int(log.log_id)
        except Exception as exc:  # noqa: BLE001 - telemetry must never break a reply
            logger.error("Failed to write telemetry for session %s: %s", request.session_id, exc)
            return None
