"""Tutor-facing routes for the web-fallback question queue (sections 4.2, 16.1, 17).

A tutor sees only the modules they are assigned to, and that filter is applied in
the SQL query via ``tutor_assignments`` - never in the frontend. Admins may pass
``?scope=all`` for system-wide oversight.

Routes
------
``GET  /api/tutor/questions``              fallback queries, most-demanded first
``GET  /api/tutor/questions/summary``      counts for the dashboard header
``GET  /api/tutor/conversations``          recent student exchanges, any grounding
``GET  /api/tutor/struggles``              high hint-depth turns, by module/intent
``POST /api/tutor/questions/<id>/answer``  answer it, optionally promoting to corpus
``POST /api/tutor/questions/<id>/dismiss`` close it without an answer
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request
from sqlalchemy import func, select

from ..auth import AuthError, resolve_identity
from ..chatlog import list_turns, turn_counts
from ..db import session_scope
from ..models import TelemetryLog
from ..unanswered import (
    TutorScope,
    answer_question,
    dismiss_question,
    list_questions,
    resolve_tutor_scope,
    summary,
)

logger = logging.getLogger(__name__)

tutor_bp = Blueprint("tutor", __name__, url_prefix="/api/tutor")

#: Roles that may work the help queue. A ``lecturer`` is a superset of a tutor,
#: so the queue is not tutor-only. Kept in step with the 403 contract in
#: tests/test_auth.py: presenting the wrong credential is Forbidden, not
#: Unauthorized.
QUEUE_ROLES = {"tutor", "lecturer", "admin"}

#: Failure reasons that mean the module's own material could not answer the
#: question and the retriever fell back (to third-party content or to nothing).
#: This is the queue the tutor's "Fallback Queries" view shows.
FALLBACK_REASONS = ("no_context", "below_threshold", "third_party_only")

#: A turn that needed at least this many hint rounds before the student was
#: unstuck is counted as "friction" in the telemetry dashboard.
STRUGGLE_THRESHOLD = 2


def _scope() -> tuple[object, TutorScope]:
    identity = resolve_identity(request)
    return identity, resolve_tutor_scope(identity)


def _require_tutor(identity) -> None:
    if identity.role not in QUEUE_ROLES:
        raise AuthError("Tutor, lecturer or admin role required.", status_code=403)


@tutor_bp.get("/questions")
def get_questions():
    """List queued questions the caller is allowed to see.

    By default (``fallback=true``) this returns only questions that missed the
    module's own material - the "Fallback Queries" view. Pass
    ``fallback=false`` to see the whole queue, including weak-grounding
    escalations.
    """
    identity, scope = _scope()
    _require_tutor(identity)
    status = (request.args.get("status") or "open").lower()
    module_id = request.args.get("module_id")
    fallback = (request.args.get("fallback") or "true").lower() not in {"0", "false", "no"}
    try:
        limit = int(request.args.get("limit", 100))
    except ValueError:
        limit = 100
    rows = list_questions(
        scope,
        status=status,
        module_id=module_id,
        reasons=FALLBACK_REASONS if fallback else None,
        limit=limit,
    )
    return (
        jsonify(
            {
                "scope": {
                    "role": identity.role,
                    "is_admin": scope.is_admin,
                    "modules": sorted(scope.module_ids),
                },
                "status": status,
                "fallback": fallback,
                "count": len(rows),
                "questions": rows,
            }
        ),
        200,
    )


@tutor_bp.get("/questions/summary")
def get_summary():
    """Queue counts for the tutor dashboard."""
    identity, scope = _scope()
    _require_tutor(identity)
    return jsonify(summary(scope)), 200


@tutor_bp.get("/struggles")
def get_struggles():
    """High hint-depth turns for the tutor telemetry dashboard.

    Aggregates ``telemetry_logs`` where ``hint_sequence_depth >= threshold``,
    grouped by module and intent, so a tutor can see which topics are costing
    students the most back-and-forth. Scoped to the caller's modules in SQL.
    """
    identity, scope = _scope()
    _require_tutor(identity)

    module_id = (request.args.get("module_id") or "").strip().upper() or None
    try:
        threshold = int(request.args.get("threshold", STRUGGLE_THRESHOLD))
    except ValueError:
        threshold = STRUGGLE_THRESHOLD
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        limit = 50

    payload = _build_struggles(
        scope,
        module_id=module_id,
        threshold=max(1, threshold),
        limit=limit,
    )
    payload["scope"] = {
        "role": identity.role,
        "is_admin": scope.is_admin,
        "modules": sorted(scope.module_ids),
    }
    payload["module_id"] = module_id
    return jsonify(payload), 200


@tutor_bp.get("/conversations")
def get_conversations():
    """Recent student exchanges in the caller's modules, whatever the grounding.

    The queue above only holds questions RAG could not answer. This holds every
    recorded turn, so a tutor can also see the exchanges that *were* grounded but
    where the student still came away confused - the other half of what the
    Human Adjustment Cycle is meant to surface.

    Scope comes from ``resolve_tutor_scope`` and never from the query string, so
    a tutor cannot widen it by asking for another module.
    """
    identity, scope = _require_tutor_scope(request)
    if scope.is_admin and (request.args.get("scope") or "").lower() == "all":
        modules = [m.module_id for m in _all_modules()]
    else:
        modules = list(scope.module_ids)

    grounding = (request.args.get("grounding") or "").strip().lower() or None
    search = (request.args.get("q") or "").strip() or None
    try:
        limit = int(request.args.get("limit", 100))
    except ValueError:
        limit = 100

    rows = list_turns(module_ids=modules, grounding=grounding, search=search, limit=limit)
    return (
        jsonify(
            {
                "scope": {
                    "role": identity.role,
                    "is_admin": scope.is_admin,
                    "modules": sorted(modules),
                },
                "count": len(rows),
                "counts": turn_counts(module_ids=modules),
                "conversations": rows,
            }
        ),
        200,
    )


def _require_tutor_scope(req):
    """Resolve identity and scope together, refusing a non-staff caller."""
    identity, scope = _scope()
    _require_tutor(identity)
    return identity, scope


def _all_modules():
    from ..models import Module
    from sqlalchemy import select

    from ..db import session_scope

    with session_scope() as session:
        return session.execute(select(Module)).scalars().all()


def _build_struggles(
    scope: TutorScope,
    *,
    module_id: Optional[str] = None,
    threshold: int = STRUGGLE_THRESHOLD,
    limit: int = 50,
) -> dict:
    """Aggregate high hint-depth turns, scoped to the caller's modules.

    Groups by module and intent rather than by question text: ``telemetry_logs``
    is deliberately lean (no text), so intent plus module is the topic signal it
    carries. Ordered by how many turns each needed deep scaffolding.
    """
    empty = {"threshold": threshold, "total_turns": 0, "struggles": []}
    if not scope.is_admin and not scope.module_ids:
        return empty
    if module_id and not scope.allows(module_id):
        return empty

    with session_scope() as session:
        stmt = (
            select(
                TelemetryLog.module_id,
                TelemetryLog.intent,
                func.count().label("turns"),
                func.max(TelemetryLog.hint_sequence_depth).label("max_depth"),
                func.avg(TelemetryLog.hint_sequence_depth).label("avg_depth"),
                func.max(TelemetryLog.created_at).label("latest_at"),
            )
            .where(TelemetryLog.hint_sequence_depth >= threshold)
            .group_by(TelemetryLog.module_id, TelemetryLog.intent)
            .order_by(func.count().desc())
            .limit(max(1, min(limit, 200)))
        )
        if not scope.is_admin:
            stmt = stmt.where(TelemetryLog.module_id.in_(scope.module_ids))
        if module_id:
            stmt = stmt.where(TelemetryLog.module_id == module_id)
        rows = session.execute(stmt).all()

    struggles = [
        {
            "module_id": row.module_id,
            "intent": row.intent,
            "turns": int(row.turns),
            "max_hint_depth": int(row.max_depth or 0),
            "avg_hint_depth": round(float(row.avg_depth or 0), 2),
            "latest_at": row.latest_at.isoformat() if row.latest_at else None,
        }
        for row in rows
    ]
    return {
        "threshold": threshold,
        "total_turns": sum(item["turns"] for item in struggles),
        "struggles": struggles,
    }


@tutor_bp.post("/questions/<int:question_id>/answer")
def post_answer(question_id: int):
    """Answer a queued question.

    Body::

        {"answer_text": "...", "promote": true}

    ``promote`` writes the answer into ``curriculum_chunks`` so later students
    asking something similar are grounded in it.
    """
    identity, scope = _scope()
    _require_tutor(identity)
    payload = request.get_json(silent=True) or {}
    answer_text = (payload.get("answer_text") or "").strip()
    if not answer_text:
        return jsonify({"error": "answer_text is required."}), 400
    promote = bool(payload.get("promote", False))

    try:
        row = answer_question(
            question_id,
            scope,
            answer_text=answer_text,
            answered_by=identity.student_id,
            promote=promote,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if row is None:
        return jsonify({"error": "Question not found or outside your module scope."}), 404
    logger.info(
        "Tutor %s answered question %s (promote=%s, chunk=%s)",
        identity.email,
        question_id,
        promote,
        row.get("promoted_chunk_id"),
    )
    return jsonify({"question": row, "promoted": bool(row.get("promoted_chunk_id"))}), 200


@tutor_bp.post("/questions/<int:question_id>/dismiss")
def post_dismiss(question_id: int):
    """Close a question without answering it (duplicate, out of scope, ...)."""
    identity, scope = _scope()
    _require_tutor(identity)
    row = dismiss_question(question_id, scope)
    if row is None:
        return jsonify({"error": "Question not found or outside your module scope."}), 404
    return jsonify({"question": row}), 200
