"""Student-facing routes: the tutoring chat and feedback.

Feedback is routed straight to the relational store (section 3: "Route feedback
directly to telemetry rather than sending it through the AI agents").
"""

from __future__ import annotations

import logging
import uuid

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..agents import ChatRequest, TutoringWorkflow
from ..auth import check_module_access, resolve_identity
from ..db import session_scope
from ..history import append_message
from ..inference import INFERENCE_UNAVAILABLE_MESSAGE
from ..llm_router import LLMError
from ..models import FEEDBACK_REASON_TAGS, HintFeedback, Module, TelemetryLog
from ..sessions import record_session_turn

logger = logging.getLogger(__name__)

chat_bp = Blueprint("chat", __name__, url_prefix="/api")


def _workflow() -> TutoringWorkflow:
    return current_app.extensions["tutoring_workflow"]


def _discard_telemetry(log_id: int | None) -> None:
    """Best-effort removal of a telemetry row whose transcript write failed.

    Keeping telemetry only when its ``tutoring_sessions`` / ``session_messages``
    rows exist prevents the orphan rows the admin dashboards cannot join.
    """
    if log_id is None:
        return
    try:
        with session_scope() as session:
            session.execute(delete(TelemetryLog).where(TelemetryLog.log_id == log_id))
    except Exception as exc:  # noqa: BLE001 - never break the served reply
        logger.error("Could not roll back telemetry row %s: %s", log_id, exc)


def _clean_history(raw) -> list[dict[str, str]]:
    """Keep only well-formed ``{role, content}`` history entries."""
    if not isinstance(raw, list):
        return []
    cleaned: list[dict[str, str]] = []
    for item in raw[-12:]:
        if isinstance(item, dict):
            role = str(item.get("role", "user"))
            content = item.get("content")
            if role in {"user", "assistant"} and isinstance(content, str):
                cleaned.append({"role": role, "content": content})
    return cleaned


@chat_bp.post("/chat")
def chat():
    """Receive a student question, run the agent workflow, return the audit."""
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message") or "").strip()
    module_id = str(payload.get("module_id") or "").strip()

    if not message:
        return jsonify({"error": "`message` is required."}), 400
    if not module_id:
        return jsonify({"error": "`module_id` is required."}), 400

    identity = resolve_identity(request)
    access = check_module_access(identity, module_id)
    if not access.allowed:
        status = 404 if not access.exists else 403
        return jsonify({"error": access.reason, "module_id": module_id}), status

    session_id = str(payload.get("session_id") or uuid.uuid4().hex)
    # Persist the student's turn as a *hard requirement* before any inference.
    # ``append_message`` upserts the parent ``tutoring_sessions`` row and inserts
    # the ``session_messages`` row in one transaction; if that fails we deliberately
    # stop here rather than run the workflow, whose telemetry row would otherwise
    # be orphaned with no session or message to join back to.
    user_message_id = uuid.uuid4().hex
    try:
        # Refresh the relational session heartbeat: last activity + turn counter.
        record_session_turn(
            session_id=session_id,
            student_id=identity.student_id,
            email=identity.email,
            module_id=module_id,
        )
        append_message(
            session_id=session_id,
            role="user",
            content=message,
            module_id=module_id,
            student_id=identity.student_id,
            email=identity.email,
            message_id=user_message_id,
        )
    except Exception:  # noqa: BLE001 - fail before telemetry rather than orphan it
        logger.exception("Could not persist the student turn for session %s", session_id)
        return (
            jsonify(
                {
                    "error": "The chat store is temporarily unavailable. "
                    "Your message was not saved."
                }
            ),
            503,
        )

    request_obj = ChatRequest(
        message=message,
        module_id=module_id,
        session_id=session_id,
        student_id=identity.student_id,
        student_email=identity.email,
        history=_clean_history(payload.get("history")),
    )

    try:
        result = _workflow().handle(request_obj)
    except LLMError as exc:
        # Inference tier is down/timed out. Return the stable, user-safe payload
        # so the React client can show a friendly banner instead of crashing.
        logger.error("Inference error: %s", exc)
        return jsonify({"error": INFERENCE_UNAVAILABLE_MESSAGE}), 502

    body = result.to_dict()
    body["identity"] = identity.to_dict()
    body["module_access"] = access.to_dict()
    body["user_message_id"] = user_message_id
    # Persist the tutor's reply together with its audit trail.
    try:
        append_message(
            session_id=session_id,
            role="assistant",
            content=result.reply,
            module_id=module_id,
            student_id=identity.student_id,
            email=identity.email,
            message_id=result.message_id,
            audit={
                "intent": result.intent,
                "scaffolding": result.scaffolding,
                "guardrail": result.guardrail,
                "retrieval": result.retrieval,
                "llm": result.llm,
                "telemetry_log_id": result.telemetry_log_id,
            },
        )
    except Exception:  # noqa: BLE001 - never break the reply, but keep integrity
        logger.exception("Could not persist the tutor turn for session %s", session_id)
        # The reply reached the student but its transcript row did not, so drop
        # the telemetry row rather than leave it orphaned from the message it
        # describes (and stop advertising a log id that no longer exists).
        _discard_telemetry(result.telemetry_log_id)
        body["telemetry_log_id"] = None
    return jsonify(body), 200


@chat_bp.post("/feedback")
def feedback():
    """Record a thumbs up/down for a delivered hint (section 12)."""
    payload = request.get_json(silent=True) or {}
    session_id = str(payload.get("session_id") or "").strip()
    message_id = str(payload.get("message_id") or "").strip()
    rating = payload.get("rating")
    reason_tag = payload.get("reason_tag")

    if not session_id or not message_id:
        return jsonify({"error": "`session_id` and `message_id` are required."}), 400
    if rating not in (1, -1):
        return jsonify({"error": "`rating` must be +1 or -1."}), 400
    if reason_tag is not None and reason_tag not in FEEDBACK_REASON_TAGS:
        return jsonify({"error": f"`reason_tag` must be one of {list(FEEDBACK_REASON_TAGS)}."}), 400

    identity = resolve_identity(request)
    module_id = payload.get("module_id")
    values = {
        "session_id": session_id,
        "message_id": message_id,
        "student_id": identity.student_id,
        "module_id": None,
        "rating": rating,
        "reason_tag": reason_tag,
        "comment": payload.get("comment"),
    }
    try:
        with session_scope() as session:
            if module_id is not None:
                if session.execute(
                    select(Module.module_id).where(Module.module_id == module_id).limit(1)
                ).scalar_one_or_none() is not None:
                    values["module_id"] = module_id

            stmt = pg_insert(HintFeedback).values(**values)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_hint_feedback_message",
                set_={"rating": rating, "reason_tag": reason_tag, "comment": payload.get("comment")},
            )
            session.execute(stmt)
    except Exception as exc:  # noqa: BLE001 - Data Tier must not take the API down
        logger.error("Feedback could not be recorded (data tier unavailable): %s", exc)
        return (
            jsonify({"error": "The feedback store is temporarily unavailable. Your rating was not saved."}),
            503,
        )

    return jsonify({"status": "recorded", "session_id": session_id, "message_id": message_id}), 201
