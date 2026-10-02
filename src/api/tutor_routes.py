"""Tutor-facing routes for the ungrounded-question queue (sections 4.2, 16.1, 17).

A tutor sees only the modules they are assigned to, and that filter is applied in
the SQL query via ``tutor_assignments`` - never in the frontend. Admins may pass
``?scope=all`` for system-wide oversight.

Routes
------
``GET  /api/tutor/questions``              the queue, most-demanded first
``GET  /api/tutor/questions/summary``      counts for the dashboard header
``GET  /api/tutor/conversations``          recent student exchanges, any grounding
``POST /api/tutor/questions/<id>/answer``  answer it, optionally promoting to corpus
``POST /api/tutor/questions/<id>/dismiss`` close it without an answer
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from ..auth import AuthError, resolve_identity
from ..chatlog import list_turns, turn_counts
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


def _scope() -> tuple[object, TutorScope]:
    identity = resolve_identity(request)
    return identity, resolve_tutor_scope(identity)


def _require_tutor(identity) -> None:
    if identity.role not in QUEUE_ROLES:
        raise AuthError("Tutor, lecturer or admin role required.", status_code=403)


@tutor_bp.get("/questions")
def get_questions():
    """List queued questions the caller is allowed to see."""
    identity, scope = _scope()
    _require_tutor(identity)
    status = (request.args.get("status") or "open").lower()
    module_id = request.args.get("module_id")
    try:
        limit = int(request.args.get("limit", 100))
    except ValueError:
        limit = 100
    rows = list_questions(scope, status=status, module_id=module_id, limit=limit)
    return (
        jsonify(
            {
                "scope": {
                    "role": identity.role,
                    "is_admin": scope.is_admin,
                    "modules": sorted(scope.module_ids),
                },
                "status": status,
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
