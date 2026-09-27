"""Authentication, role and module-scope checks for the API Gateway.

The final system delegates identity to DUT's Microsoft-backed login (section 3
and 4.4): the app verifies the DUT4life identity and then applies its **own**
role and module permissions. That SSO integration is not wired yet, so this
module exposes a single seam - :func:`resolve_identity` - with a development
implementation and a clearly-marked strict implementation. Everything above it
(the chat/admin routes and the workflow) only depends on the :class:`Identity`
object, so swapping in the real verifier later touches nothing else.
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from typing import Optional

from flask import Request
from sqlalchemy import select

from .config import settings
from .db import session_scope
from .models import Enrollment, Module, Student

logger = logging.getLogger(__name__)


class AuthError(Exception):
    """Raised when a request cannot be authorized."""

    def __init__(self, message: str, status_code: int = 401) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(slots=True)
class Identity:
    email: Optional[str]
    role: str  # student | tutor | admin
    student_id: Optional[int]
    authenticated: bool
    source: str  # "dev", "sso", "anonymous"

    def to_dict(self) -> dict:
        return {
            "email": self.email,
            "role": self.role,
            "student_id": self.student_id,
            "authenticated": self.authenticated,
            "source": self.source,
        }


@dataclass(slots=True)
class ModuleAccess:
    module_id: str
    exists: bool
    enrolled: Optional[bool]  # None when we have no identity to check
    allowed: bool
    reason: str

    def to_dict(self) -> dict:
        return {
            "module_id": self.module_id,
            "exists": self.exists,
            "enrolled": self.enrolled,
            "allowed": self.allowed,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Identity resolution
# ---------------------------------------------------------------------------
def _identity_from_headers(request: Request) -> tuple[Optional[str], Optional[str]]:
    email = request.headers.get("X-User-Email") or request.headers.get("X-DUT4life-Email")
    role = request.headers.get("X-User-Role")
    if email or role:
        return email, role
    # Fall back to the JSON body for simple clients / curl testing.
    body = request.get_json(silent=True) or {}
    return body.get("student_email") or body.get("email"), body.get("role")


def _verify_dut4life_token(token: str) -> Optional[str]:
    """Placeholder for the DUT identity-provider integration (section 4.4).

    The real implementation will validate the token against DUT's Microsoft
    tenant and return the verified DUT4life email. Until then, strict mode
    refuses to guess.
    """
    raise AuthError(
        "DUT4life identity verification is not configured yet. Set AUTH_MODE=dev "
        "for local development, or wire the identity provider in auth.py.",
        status_code=501,
    )


def resolve_identity(request: Request) -> Identity:
    """Resolve the caller's identity, role and database link.

    In ``dev`` mode the role/email come from trusted headers (or the JSON body)
    and are matched against the ``students`` table when possible. In ``strict``
    mode a DUT4life token is required.
    """
    mode = settings.auth_mode

    if mode == "strict":
        token = request.headers.get("X-DUT4life-Token") or ""
        if not token:
            raise AuthError("Missing DUT4life token.", status_code=401)
        email = _verify_dut4life_token(token)  # raises 501 until wired
        if not email:
            raise AuthError("DUT4life identity could not be verified.", status_code=401)
        return _load_identity(email, requested_role=None, source="sso")

    # Development mode.
    email, requested_role = _identity_from_headers(request)
    if not email:
        return Identity(email=None, role="student", student_id=None, authenticated=False, source="anonymous")
    return _load_identity(email, requested_role, source="dev")


def _load_identity(email: str, requested_role: Optional[str], source: str) -> Identity:
    try:
        with session_scope() as session:
            student = session.execute(
                select(Student).where(Student.dut4life_email == email).limit(1)
            ).scalar_one_or_none()
            if student is not None:
                return Identity(
                    email=email,
                    role=student.role,
                    student_id=int(student.student_id),
                    authenticated=True,
                    source=source,
                )
    except Exception as exc:  # noqa: BLE001 - Data Tier must not take the API down
        logger.warning(
            "Directory lookup for %s failed (data tier unavailable); degrading to "
            "the requested role: %s",
            email,
            exc,
        )
    # Unknown identity: keep the requested role so the tier is usable without a
    # seeded directory. The real directory sync will populate `students`.
    role = (requested_role or "student").lower()
    if role not in {"student", "tutor", "admin"}:
        role = "student"
    return Identity(email=email, role=role, student_id=None, authenticated=True, source=source)


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------
#: Values that have shipped in a template or a local .env and are therefore
#: guessable by anyone who has read the repository. Configuring one of these is
#: treated as "no admin key configured" rather than as a working credential.
KNOWN_WEAK_ADMIN_KEYS = frozenset(
    {
        "",
        "change-me-admin-key",
        "change-me",
        "local-dev-admin-key",
        "admin",
        "secret",
        "password",
        "test",
    }
)


def require_admin(request: Request) -> Identity:
    """Authorize an admin-only request via the shared ``X-Admin-Key``.

    This is the gateway's admin gate until DUT4life roles are wired; it is
    intentionally separate from the student identity flow.

    Fails closed. An unset key gives 503, and a key still set to one of the
    placeholder values documented in ``.env.example`` is also rejected. A warning
    used to be logged and the request served anyway, which meant a deployment that
    copied the example key was quietly open. Override for a local throwaway with
    ``ADMIN_ALLOW_WEAK_KEY=true``.
    """
    provided = request.headers.get("X-Admin-Key") or ""
    expected = settings.admin_api_key or ""

    if not expected:
        raise AuthError(
            "Admin API key is not configured; set ADMIN_API_KEY to enable the admin API.",
            status_code=503,
        )
    if expected.casefold() in KNOWN_WEAK_ADMIN_KEYS and not settings.admin_allow_weak_key:
        logger.error(
            "ADMIN_API_KEY is still a placeholder from the example config, so the admin "
            "API is disabled. Generate a real key, or set ADMIN_ALLOW_WEAK_KEY=true for a "
            "local throwaway."
        )
        raise AuthError(
            "Admin API key is a known placeholder; the admin API is disabled.",
            status_code=503,
        )
    if not provided or not hmac.compare_digest(provided, expected):
        raise AuthError("Invalid or missing admin key.", status_code=401)
    return Identity(
        email=request.headers.get("X-User-Email"),
        role="admin",
        student_id=None,
        authenticated=True,
        source="admin-key",
    )


def check_module_access(identity: Identity, module_id: str) -> ModuleAccess:
    """Validate that ``module_id`` exists and (optionally) that the student is enrolled.

    The architecture defers automatic enrollment assignment (section 19), so
    enrollment is only *enforced* when ``ENFORCE_ENROLLMENT=true``; otherwise it
    is reported but does not block. If the database is unreachable the check is
    skipped (and reported) so a Data Tier outage degrades rather than 500s.
    """
    try:
        with session_scope() as session:
            module_exists = (
                session.execute(
                    select(Module.module_id).where(Module.module_id == module_id).limit(1)
                ).scalar_one_or_none()
                is not None
            )

            enrolled: Optional[bool] = None
            if module_exists and identity.student_id is not None:
                enrolled = (
                    session.execute(
                        select(Enrollment.enrollment_id)
                        .where(
                            Enrollment.student_id == identity.student_id,
                            Enrollment.module_id == module_id,
                            Enrollment.is_active.is_(True),
                        )
                        .limit(1)
                    ).scalar_one_or_none()
                    is not None
                )
    except Exception as exc:  # noqa: BLE001 - Data Tier must not take the API down
        logger.warning("Module access check skipped (data tier unavailable): %s", exc)
        return ModuleAccess(
            module_id,
            True,
            None,
            True,
            "Data tier unavailable; module scope was not verified.",
        )

    if not module_exists:
        return ModuleAccess(module_id, False, enrolled, False, "Unknown module_id.")

    if enrolled is False and settings.enforce_enrollment and identity.role == "student":
        return ModuleAccess(
            module_id, True, False, False, "Student is not enrolled in this module."
        )

    return ModuleAccess(module_id, True, enrolled, True, "ok")
