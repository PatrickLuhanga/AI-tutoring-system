"""Account routes: signup, login, logout, and "who am I".

These are the only unauthenticated endpoints in the API. Everything else either
requires a bearer session or keeps using the identity seam in :mod:`src.auth`.

Routes
------
``POST /api/auth/signup``  register a student / tutor / lecturer account
``POST /api/auth/login``   exchange email + password for a session token
``POST /api/auth/logout``  revoke the current session
``POST /api/auth/logout-all`` revoke every session for the signed-in account
``GET  /api/auth/me``      the signed-in account and its module scope
``GET  /api/auth/config``  public signup policy the form needs to render itself
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from .. import accounts
from ..accounts import AccountError, authenticate_request
from ..config import settings
from ..models import SELF_SIGNUP_ROLES

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")


def _bearer() -> str | None:
    header = request.headers.get("Authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


def _user_agent() -> str | None:
    return request.headers.get("User-Agent")


@auth_bp.get("/config")
def get_config():
    """Signup policy, so the form can show the right fields per role.

    Deliberately public and free of secrets: it exposes only the domain rules
    and whether registration is open.
    """
    return jsonify(
        {
            "allow_self_signup": settings.allow_self_signup,
            "roles": list(SELF_SIGNUP_ROLES),
            "student_email_domain": settings.student_email_domain,
            "lecturer_email_domain": settings.lecturer_email_domain,
            "min_password_length": settings.min_password_length,
            "session_ttl_hours": settings.session_ttl_hours,
        }
    ), 200


@auth_bp.post("/signup")
def post_signup():
    """Register an account and sign the new user straight in.

    Body::

        {
          "email": "...", "password": "...",
          "role": "student" | "tutor" | "lecturer",
          "full_name": "...",              // optional
          "student_number": "22000000",    // students only
          "module_ids": ["IPRT301"]        // tutors and lecturers only
        }
    """
    payload = request.get_json(silent=True) or {}
    try:
        user, token = accounts.create_account(
            email=payload.get("email"),
            password=payload.get("password"),
            role=payload.get("role"),
            full_name=payload.get("full_name"),
            student_number=payload.get("student_number"),
            module_ids=payload.get("module_ids") or [],
            client_address=request.remote_addr,
        )
    except AccountError as exc:
        return jsonify({"error": exc.message}), exc.status_code

    return jsonify(
        {
            "user": user.to_dict(accounts.resolve_module_ids(user.user_id)),
            "token": token,
        }
    ), 201


@auth_bp.post("/login")
def post_login():
    """Body:: ``{"email": "...", "password": "..."}``"""
    payload = request.get_json(silent=True) or {}
    try:
        user, token = accounts.login(
            email=payload.get("email"),
            password=payload.get("password"),
            user_agent=_user_agent(),
        )
    except AccountError as exc:
        return jsonify({"error": exc.message}), exc.status_code

    return jsonify(
        {
            "user": user.to_dict(accounts.resolve_module_ids(user.user_id)),
            "token": token,
        }
    ), 200


@auth_bp.post("/logout")
def post_logout():
    """Revoke the session used for this request."""
    try:
        user = authenticate_request(_bearer())
    except AccountError as exc:
        return jsonify({"error": exc.message}), exc.status_code
    accounts.logout(user.session_id)
    return jsonify({"status": "signed_out"}), 200


@auth_bp.post("/logout-all")
def post_logout_all():
    """Revoke every live session for the signed-in account."""
    try:
        user = authenticate_request(_bearer())
    except AccountError as exc:
        return jsonify({"error": exc.message}), exc.status_code
    closed = accounts.logout_everywhere(user.user_id)
    return jsonify({"status": "signed_out", "sessions_closed": closed}), 200


@auth_bp.get("/me")
def get_me():
    """The signed-in account, its role and the modules it may see."""
    try:
        user = authenticate_request(_bearer())
    except AccountError as exc:
        return jsonify({"error": exc.message}), exc.status_code
    return jsonify(
        {
            "user": user.to_dict(accounts.resolve_module_ids(user.user_id)),
            "active_sessions": accounts.count_active_sessions(user.user_id),
        }
    ), 200
