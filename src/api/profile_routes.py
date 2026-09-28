"""Dynamic profile provisioning, onboarding and admin tutor grants.

* ``POST /api/auth/login``         - provision/resolve a DUT4life profile
* ``GET  /api/profile``            - the caller's profile
* ``PUT  /api/profile``            - save name, student number and modules
* ``POST /api/admin/grant-tutor``  - grant tutor privileges (dual role)
* ``POST /api/admin/revoke-tutor`` - revoke tutor privileges
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from ..auth import AuthError, require_admin, resolve_identity
from ..config import settings
from ..profiles import (
    ProfileError,
    get_profile,
    grant_tutor,
    login_or_create,
    revoke_tutor,
    update_profile,
)

logger = logging.getLogger(__name__)

profile_bp = Blueprint("profile", __name__, url_prefix="/api")


def _require_student_id(request) -> tuple:
    identity = resolve_identity(request)
    if not identity.authenticated or identity.student_id is None:
        raise AuthError("Sign in with a registered DUT4life address first.", status_code=401)
    return identity, int(identity.student_id)


@profile_bp.post("/auth/login")
def login():
    """Provision (first login) or resolve a profile for a DUT4life email."""
    payload = request.get_json(silent=True) or {}
    if settings.auth_mode == "strict":
        # Strict mode derives the address from a verified Microsoft token only;
        # the request body can never assert an identity.
        identity = resolve_identity(request)
        email = (identity.email or "").strip()
        if not identity.authenticated or not email:
            raise AuthError("A valid DUT4life token is required.", status_code=401)
    else:
        email = str(payload.get("email") or "").strip()
    if not email:
        return jsonify({"error": "`email` is required."}), 400
    try:
        profile = login_or_create(email)
    except ProfileError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001 - Data Tier must not take the API down
        logger.error("Login failed (data tier unavailable): %s", exc)
        return jsonify({"error": "The profile store is temporarily unavailable."}), 503
    return jsonify(profile), 200


@profile_bp.get("/profile")
def read_profile():
    identity, student_id = _require_student_id(request)
    profile = get_profile(student_id)
    if profile is None:
        raise AuthError("Profile not found.", status_code=404)
    return jsonify(profile), 200


@profile_bp.put("/profile")
def save_profile():
    identity, student_id = _require_student_id(request)
    payload = request.get_json(silent=True) or {}
    modules = payload.get("modules")
    try:
        profile = update_profile(
            student_id,
            full_name=payload.get("full_name") if "full_name" in payload else None,
            student_number=payload.get("student_number")
            if "student_number" in payload
            else None,
            modules=modules if isinstance(modules, list) else None,
        )
    except ProfileError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        logger.error("Profile update failed (data tier unavailable): %s", exc)
        return jsonify({"error": "The profile store is temporarily unavailable."}), 503
    return jsonify(profile), 200


@profile_bp.post("/admin/grant-tutor")
def admin_grant_tutor():
    """Grant tutor privileges to a registered user by number / email / id."""
    admin = require_admin(request)
    payload = request.get_json(silent=True) or {}
    identifier = str(payload.get("identifier") or "").strip()
    if not identifier:
        return jsonify({"error": "`identifier` (student number, email or id) is required."}), 400
    modules = payload.get("modules")
    try:
        profile = grant_tutor(
            identifier,
            modules if isinstance(modules, list) else [],
            granted_by=admin.student_id,
        )
    except ProfileError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:  # noqa: BLE001
        logger.error("Grant tutor failed (data tier unavailable): %s", exc)
        return jsonify({"error": "The profile store is temporarily unavailable."}), 503
    return jsonify(profile), 200


@profile_bp.post("/admin/revoke-tutor")
def admin_revoke_tutor():
    """Revoke tutor privileges and module assignments from a user."""
    require_admin(request)
    payload = request.get_json(silent=True) or {}
    identifier = str(payload.get("identifier") or "").strip()
    if not identifier:
        return jsonify({"error": "`identifier` is required."}), 400
    try:
        profile = revoke_tutor(identifier)
    except ProfileError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:  # noqa: BLE001
        logger.error("Revoke tutor failed (data tier unavailable): %s", exc)
        return jsonify({"error": "The profile store is temporarily unavailable."}), 503
    return jsonify(profile), 200
