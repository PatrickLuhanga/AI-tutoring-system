"""Session tracking and dashboard analytics routes.

* ``POST /api/session``            - record a student's login + module-chat open
* ``GET  /api/tutor/analytics``    - analytics scoped to the tutor's modules
* ``GET  /api/admin/analytics``    - system-wide telemetry aggregates
* ``GET  /api/admin/overview``     - users, roles and guardrail flag totals
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from ..analytics import admin_analytics, admin_overview, resolve_scope, tutor_analytics
from ..auth import AuthError, require_admin, resolve_identity
from ..sessions import record_session_open

logger = logging.getLogger(__name__)

analytics_bp = Blueprint("analytics", __name__, url_prefix="/api")


def _requested_modules() -> list[str]:
    raw = request.args.get("modules") or ""
    return [module.strip() for module in raw.split(",") if module.strip()]


@analytics_bp.post("/session")
def open_session():
    """Record a login timestamp and open the student's module session."""
    payload = request.get_json(silent=True) or {}
    session_id = str(payload.get("session_id") or "").strip()
    module_id = str(payload.get("module_id") or "").strip()
    if not session_id or not module_id:
        return jsonify({"error": "`session_id` and `module_id` are required."}), 400

    identity = resolve_identity(request)
    try:
        record = record_session_open(
            session_id=session_id,
            student_id=identity.student_id,
            email=identity.email,
            module_id=module_id,
        )
    except Exception as exc:  # noqa: BLE001 - Data Tier must not take the API down
        logger.error("Session could not be recorded (data tier unavailable): %s", exc)
        return (
            jsonify({"error": "The session store is temporarily unavailable."}),
            503,
        )
    return jsonify(record), 201


@analytics_bp.get("/tutor/analytics")
def tutor_dashboard_analytics():
    """Analytics strictly filtered to the caller's assigned modules."""
    identity = resolve_identity(request)
    if not identity.can_tutor():
        raise AuthError("Tutor or admin access required.", status_code=403)
    scope = resolve_scope(identity, _requested_modules())
    return jsonify(tutor_analytics(scope)), 200


@analytics_bp.get("/admin/analytics")
def admin_analytics_route():
    """System-wide telemetry aggregates for the admin dashboard."""
    require_admin(request)
    return jsonify(admin_analytics()), 200


@analytics_bp.get("/admin/overview")
def admin_overview_route():
    """Users, role counts, active sessions and guardrail flag totals."""
    require_admin(request)
    return jsonify(admin_overview()), 200
