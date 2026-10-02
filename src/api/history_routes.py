"""Chat-history routes for the student sidebar.

* ``GET    /api/sessions``               - list the caller's sessions
* ``GET    /api/sessions/<session_id>``  - full message history for one session
* ``DELETE /api/sessions/<session_id>``  - delete a session and its data
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from ..auth import AuthError, resolve_identity
from ..history import delete_session, get_session, list_sessions

logger = logging.getLogger(__name__)

history_bp = Blueprint("history", __name__, url_prefix="/api")


def _identity(request):
    identity = resolve_identity(request)
    if not identity.authenticated or (identity.student_id is None and not identity.email):
        raise AuthError("Sign in to view your chat history.", status_code=401)
    return identity


@history_bp.get("/sessions")
def sessions():
    """The caller's chat sessions, newest activity first."""
    identity = _identity(request)
    module_id = request.args.get("module_id") or None
    return (
        jsonify(
            {
                "sessions": list_sessions(
                    student_id=identity.student_id,
                    email=identity.email,
                    module_id=module_id,
                )
            }
        ),
        200,
    )


@history_bp.get("/sessions/<session_id>")
def session_detail(session_id: str):
    """One owned session and its full message history."""
    identity = _identity(request)
    payload = get_session(
        student_id=identity.student_id, email=identity.email, session_id=session_id
    )
    if payload is None:
        return jsonify({"error": "Session not found."}), 404
    return jsonify(payload), 200


@history_bp.delete("/sessions/<session_id>")
def session_delete(session_id: str):
    """Delete an owned session, its messages, telemetry and feedback."""
    identity = _identity(request)
    try:
        deleted = delete_session(
            student_id=identity.student_id, email=identity.email, session_id=session_id
        )
    except Exception as exc:  # noqa: BLE001 - Data Tier must not take the API down
        logger.error("Could not delete session %s: %s", session_id, exc)
        return jsonify({"error": "The session store is temporarily unavailable."}), 503
    if not deleted:
        return jsonify({"error": "Session not found."}), 404
    return jsonify({"status": "deleted", "session_id": session_id}), 200
