"""System Admin account management, plus staff uploads.

Routes
------
``GET    /api/accounts``                 list accounts (admin)
``PATCH  /api/accounts/<id>``            change role or status (admin)
``POST   /api/accounts/<id>/sessions``   sign an account out everywhere (admin)
``POST   /api/content/upload``           upload a file into a module (lecturer/admin)
``GET    /api/content/uploads``          list uploads (staff, module-scoped)

Account routes are gated on a **session with the admin role**, not on the shared
``X-Admin-Key``: managing people's accounts is an action taken *as* someone, so
it needs an attributable actor. The key stays where it is, on model config.
"""

from __future__ import annotations

import io
import logging

from flask import Blueprint, jsonify, request
from sqlalchemy import or_, select

from .. import uploads
from ..accounts import logout_everywhere
from ..auth import AuthError, require_student
from ..db import session_scope
from ..models import ACCOUNT_ROLE_VALUES, UPLOAD_CATEGORIES, User, UserModuleAccess

logger = logging.getLogger(__name__)

admin_accounts_bp = Blueprint("admin_accounts", __name__, url_prefix="/api/accounts")
content_bp = Blueprint("content", __name__, url_prefix="/api/content")

UPLOAD_ROLES = {"lecturer", "admin"}


def _require_admin(identity) -> None:
    if identity.role != "admin":
        raise AuthError("System Admin role required.", status_code=403)
    if identity.user_id is None:
        raise AuthError("Sign in as an admin account.", status_code=401)


# ---------------------------------------------------------------------------
# Account management
# ---------------------------------------------------------------------------
@admin_accounts_bp.get("")
@admin_accounts_bp.get("/")
def list_accounts():
    """Accounts, filtered by ``?role=``, ``?status=`` or ``?q=``."""
    identity = require_student(request)
    _require_admin(identity)

    role = (request.args.get("role") or "").strip().lower()
    status = (request.args.get("status") or "").strip().lower()
    needle = (request.args.get("q") or "").strip().lower()

    with session_scope() as session:
        stmt = select(User)
        if role:
            stmt = stmt.where(User.role == role)
        if status:
            stmt = stmt.where(User.status == status)
        if needle:
            stmt = stmt.where(
                or_(User.email.ilike(f"%{needle}%"),
                    User.full_name.ilike(f"%{needle}%"),
                    User.student_number.ilike(f"%{needle}%"))
            )
        users = session.execute(stmt.order_by(User.created_at.desc()).limit(500)).scalars()
        rows = []
        for u in users:
            modules = sorted(
                str(r)
                for r in session.execute(
                    select(UserModuleAccess.module_id).where(
                        UserModuleAccess.user_id == u.user_id
                    )
                ).scalars()
            )
            rows.append(
                {
                    "user_id": int(u.user_id),
                    "email": u.email,
                    "full_name": u.full_name,
                    "role": u.role,
                    "status": u.status,
                    "student_number": u.student_number,
                    "modules": modules,
                    "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
                    "created_at": u.created_at.isoformat() if u.created_at else None,
                }
            )
    return jsonify({"count": len(rows), "accounts": rows}), 200


@admin_accounts_bp.patch("/<int:user_id>")
def patch_account(user_id: int):
    """Change ``role`` and/or ``status``.

    Body:: ``{"role": "lecturer", "status": "suspended"}``

    An admin cannot demote or suspend *themselves*: that is the usual way a
    single-admin deployment ends up with nobody able to undo it.
    """
    identity = require_student(request)
    _require_admin(identity)

    payload = request.get_json(silent=True) or {}
    new_role = (payload.get("role") or "").strip().lower() or None
    new_status = (payload.get("status") or "").strip().lower() or None

    if new_role is not None and new_role not in ACCOUNT_ROLE_VALUES:
        return jsonify({"error": f"Role must be one of {list(ACCOUNT_ROLE_VALUES)}."}), 400
    if new_status is not None and new_status not in {"active", "suspended"}:
        return jsonify({"error": "Status must be 'active' or 'suspended'."}), 400
    if new_role is None and new_status is None:
        return jsonify({"error": "Provide a role or a status to change."}), 400

    if int(user_id) == identity.user_id:
        return jsonify(
            {"error": "You cannot change your own role or status."}
        ), 400

    with session_scope() as session:
        target = session.get(User, int(user_id))
        if target is None:
            return jsonify({"error": "Account not found."}), 404
        if new_role:
            target.role = new_role
        if new_status:
            target.status = new_status
        # Suspension must also cut live sessions, otherwise the account keeps
        # working until the token happens to expire.
        session.flush()
        email, role, status = target.email, target.role, target.status

    closed = 0
    if new_status == "suspended":
        closed = logout_everywhere(int(user_id))

    logger.info(
        "Admin %s set account %s to role=%s status=%s (closed %d session(s))",
        identity.email, email, role, status, closed,
    )
    return jsonify(
        {"user_id": int(user_id), "email": email, "role": role,
         "status": status, "sessions_closed": closed}
    ), 200


@admin_accounts_bp.post("/<int:user_id>/sessions")
def revoke_sessions(user_id: int):
    """Sign an account out everywhere."""
    identity = require_student(request)
    _require_admin(identity)
    with session_scope() as session:
        target = session.get(User, int(user_id))
        if target is None:
            return jsonify({"error": "Account not found."}), 404
        email = target.email
    closed = logout_everywhere(int(user_id))
    logger.info("Admin %s closed %d session(s) for %s", identity.email, closed, email)
    return jsonify({"email": email, "sessions_closed": closed}), 200


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------
@content_bp.post("/upload")
def post_upload():
    """Upload one file into a module.

    Form fields: ``module_id``, ``category``, and the file itself under ``file``.
    """
    identity = require_student(request)
    if identity.role not in UPLOAD_ROLES:
        raise AuthError("Only a lecturer or admin can upload material.", status_code=403)

    module_id = (request.form.get("module_id") or "").strip().upper()
    category = (request.form.get("category") or "notes").strip().lower()
    if not module_id:
        return jsonify({"error": "module_id is required."}), 400
    if category not in UPLOAD_CATEGORIES:
        return jsonify({"error": f"Category must be one of {list(UPLOAD_CATEGORIES)}."}), 400

    if identity.role != "admin":
        allowed = {str(m).strip().upper() for m in (identity.module_ids or ())}
        if allowed and module_id not in allowed:
            raise AuthError(f"You are not assigned to {module_id}.", status_code=403)

    stored = request.files.get("file")
    if stored is None or not stored.filename:
        return jsonify({"error": "No file was attached."}), 400

    try:
        row = uploads.store_upload(
            module_id=module_id,
            filename=stored.filename,
            data=stored.read(),
            category=category,
            uploaded_by=identity.user_id,
            content_type=stored.mimetype,
        )
    except uploads.UploadError as exc:
        return jsonify({"error": exc.message}), exc.status_code
    return jsonify({"document": row}), 201


@content_bp.get("/uploads")
def get_uploads():
    """Uploads, for one module or all of them."""
    identity = require_student(request)
    if identity.role not in UPLOAD_ROLES:
        raise AuthError("Only a lecturer or admin can list uploads.", status_code=403)
    module_id = (request.args.get("module_id") or "").strip().upper() or None
    if module_id and identity.role != "admin":
        allowed = {str(m).strip().upper() for m in (identity.module_ids or ())}
        if allowed and module_id not in allowed:
            raise AuthError(f"You are not assigned to {module_id}.", status_code=403)
    rows = uploads.list_uploads(module_id)
    return jsonify({"count": len(rows), "uploads": rows}), 200
