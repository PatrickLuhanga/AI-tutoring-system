"""Notifications: what a signed-in account can read, and marking them read.

Routes
------
``GET  /api/notifications``        the caller's notifications, newest first
``POST /api/notifications/read``   mark a set as read
``POST /api/notifications``        send one (lecturer or admin)

The read path derives the caller's role and module scope from their identity, so
a client cannot widen its own audience. Sending is restricted to lecturers and
admins: a tutor answers questions, a lecturer sets them.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from ..auth import AuthError, require_student
from ..notify import NotificationError, list_for_user, mark_read, send, unread_count

logger = logging.getLogger(__name__)

notification_bp = Blueprint("notifications", __name__, url_prefix="/api/notifications")

#: Who may send. A tutor works the queue; announcements are a lecturer's job.
SEND_ROLES = {"lecturer", "admin"}


@notification_bp.get("")
@notification_bp.get("/")
def get_notifications():
    """The caller's notifications, plus an unread count for the header badge."""
    identity = require_student(request)
    items = list_for_user(
        identity.user_id or 0,
        role=identity.role,
        module_ids=identity.module_ids or (),
    )
    return jsonify(
        {
            "count": len(items),
            "unread": sum(1 for n in items if not n["is_read"]),
            "notifications": items,
        }
    ), 200


@notification_bp.post("/read")
def post_read():
    """Body:: ``{"notification_ids": [1, 2]}``"""
    identity = require_student(request)
    if identity.user_id is None:
        raise AuthError("This identity has no account to record reads against.", status_code=403)
    payload = request.get_json(silent=True) or {}
    ids = payload.get("notification_ids") or []
    if not isinstance(ids, list):
        return jsonify({"error": "notification_ids must be a list."}), 400
    added = mark_read(user_id=identity.user_id, notification_ids=ids)
    return jsonify({"marked": added}), 200


@notification_bp.post("")
@notification_bp.post("/")
def post_notification():
    """Send one. Body::

        {"title": "...", "body": "...", "audience": "all|module|role|user",
         "module_id": "IPRT301", "role": "student", "user_id": 12}
    """
    identity = require_student(request)
    if identity.role not in SEND_ROLES:
        raise AuthError("Only a lecturer or admin can send notifications.", status_code=403)

    payload = request.get_json(silent=True) or {}
    audience = str(payload.get("audience") or "all").strip().lower()
    module_id = payload.get("module_id")
    role = payload.get("role")
    user_id = payload.get("user_id")

    # A lecturer can only broadcast to modules they teach; an admin can reach
    # any. Checked after the payload shape, so a missing module_id reports the
    # real problem instead of looking like a permissions failure.
    if identity.role != "admin" and audience == "module" and module_id:
        allowed = {str(m).strip().upper() for m in (identity.module_ids or ())}
        if allowed and str(module_id).strip().upper() not in allowed:
            raise AuthError(f"You are not assigned to {module_id}.", status_code=403)

    try:
        row = send(
            title=payload.get("title"),
            body=payload.get("body"),
            audience=audience,
            role=role,
            module_id=module_id,
            user_id=user_id,
            created_by=identity.user_id,
        )
    except NotificationError as exc:
        return jsonify({"error": exc.message}), exc.status_code
    return jsonify({"notification": row}), 201
