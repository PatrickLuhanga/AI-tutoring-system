"""Authentication, role and module-scope checks for the API Gateway.

The final system delegates identity to DUT's Microsoft-backed login (section 3
and 4.4): the app verifies the DUT4life identity and then applies its **own**
role and module permissions. This module exposes a single seam -
:func:`resolve_identity` - with a development implementation (trusted headers)
and a strict implementation that cryptographically verifies a Microsoft Entra ID
(Azure AD) access token against the tenant's JWKS. Everything above it (the
chat/admin routes and the workflow) only depends on the :class:`Identity`
object, so the two modes are interchangeable.
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass, field
from typing import Optional

import jwt
from flask import Request
from jwt import PyJWKClient
from sqlalchemy import select

from .config import settings
from .db import session_scope
from .models import Enrollment, Module, Student, TutorAssignment

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
    #: Primary role: ``student`` or ``admin`` (legacy ``tutor`` = student+tutor).
    role: str
    student_id: Optional[int]
    authenticated: bool
    source: str  # "dev", "sso", "anonymous"
    #: Dual-role capability: a student may also hold tutor privileges.
    is_tutor: bool = False
    #: Modules a tutor is explicitly assigned to (RBAC scope); empty otherwise.
    modules: list[str] = field(default_factory=list)

    def roles(self) -> list[str]:
        if self.role == "admin":
            return ["admin"]
        roles = ["student"]
        if self.is_tutor or self.role == "tutor":
            roles.append("tutor")
        return roles

    def can_tutor(self) -> bool:
        return self.role == "admin" or self.is_tutor or self.role == "tutor"

    def to_dict(self) -> dict:
        return {
            "email": self.email,
            "role": self.role,
            "roles": self.roles(),
            "is_tutor": self.is_tutor or self.role == "tutor",
            "student_id": self.student_id,
            "authenticated": self.authenticated,
            "source": self.source,
            "modules": self.modules,
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


#: Cached Microsoft JWKS clients, keyed by tenant id. ``PyJWKClient`` caches the
#: fetched signing keys internally, so one client per tenant per process is enough.
_JWKS_CLIENTS: dict[str, PyJWKClient] = {}


def _jwks_client(tenant_id: str) -> PyJWKClient:
    """Return (and memoise) the JWKS client for a Microsoft tenant."""
    client = _JWKS_CLIENTS.get(tenant_id)
    if client is None:
        client = PyJWKClient(
            f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
        )
        _JWKS_CLIENTS[tenant_id] = client
    return client


def _bearer_token(request: Request) -> str:
    """Extract the caller's token from ``Authorization: Bearer`` or the legacy header."""
    header = request.headers.get("Authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return (request.headers.get("X-DUT4life-Token") or "").strip()


def _verify_dut4life_token(token: str) -> str:
    """Validate a Microsoft Entra ID (Azure AD) access token and return its email.

    The RS256 signature is checked against the tenant's published JWKS and the
    ``aud``/``iss`` claims are pinned to this application (``AZURE_CLIENT_ID``)
    and tenant (``AZURE_TENANT_ID``). Any invalid, expired or foreign token is
    rejected with :class:`AuthError` (401).
    """
    tenant_id = settings.azure_tenant_id.strip()
    client_id = settings.azure_client_id.strip()
    if not tenant_id or not client_id:
        raise AuthError(
            "Microsoft identity is not configured. Set AZURE_TENANT_ID and "
            "AZURE_CLIENT_ID to use AUTH_MODE=strict.",
            status_code=501,
        )

    try:
        signing_key = _jwks_client(tenant_id).get_signing_key_from_jwt(token).key
    except Exception as exc:  # noqa: BLE001 - JWKS lookup/parse failures are 401s
        logger.warning("Could not resolve the Microsoft signing key: %s", exc)
        raise AuthError(
            "DUT4life signing key could not be resolved.", status_code=401
        ) from exc

    try:
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=[client_id, f"api://{client_id}"],
            issuer=[
                f"https://login.microsoftonline.com/{tenant_id}/v2.0",
                f"https://sts.windows.net/{tenant_id}/",
            ],
            options={"require": ["exp", "iat", "aud", "iss"]},
        )
    except jwt.PyJWTError as exc:
        logger.warning("Microsoft token rejected: %s", exc)
        raise AuthError(
            "DUT4life token could not be verified.", status_code=401
        ) from exc

    for claim in ("preferred_username", "email", "upn", "unique_name"):
        value = claims.get(claim)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    raise AuthError("DUT4life token carries no e-mail claim.", status_code=401)


def resolve_identity(request: Request) -> Identity:
    """Resolve the caller's identity, role and database link.

    In ``dev`` mode the role/email come from trusted headers (or the JSON body)
    and are matched against the ``students`` table when possible. In ``strict``
    mode a DUT4life token is required.
    """
    mode = settings.auth_mode

    if mode == "strict":
        # Strict mode never trusts the dev headers/body: identity is derived
        # solely from a cryptographically verified Microsoft token.
        token = _bearer_token(request)
        if not token:
            raise AuthError(
                "Missing DUT4life access token. Sign in with Microsoft.",
                status_code=401,
            )
        email = _verify_dut4life_token(token)
        return _load_identity(email, requested_role=None, source="sso")

    # Development mode.
    email, requested_role = _identity_from_headers(request)
    if not email:
        return Identity(
            email=None,
            role="student",
            student_id=None,
            authenticated=False,
            source="anonymous",
        )
    return _load_identity(email, requested_role, source="dev")


def _load_identity(email: str, requested_role: Optional[str], source: str) -> Identity:
    try:
        with session_scope() as session:
            student = session.execute(
                select(Student).where(Student.dut4life_email == email).limit(1)
            ).scalar_one_or_none()
            if student is not None:
                student_id = int(student.student_id)
                tutor_modules = [
                    str(row)
                    for row in session.execute(
                        select(TutorAssignment.module_id).where(
                            TutorAssignment.tutor_id == student_id
                        )
                    ).scalars().all()
                ]
                return Identity(
                    email=email,
                    role=student.role,
                    student_id=student_id,
                    authenticated=True,
                    source=source,
                    is_tutor=bool(student.is_tutor) or student.role == "tutor",
                    modules=tutor_modules,
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
    return Identity(
        email=email,
        role=role,
        student_id=None,
        authenticated=True,
        source=source,
        is_tutor=role == "tutor",
    )


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------
def require_admin(request: Request) -> Identity:
    """Authorize an admin-only request via the shared ``X-Admin-Key``.

    This is the gateway's admin gate until DUT4life roles are wired; it is
    intentionally separate from the student identity flow.
    """
    provided = request.headers.get("X-Admin-Key") or ""
    expected = settings.admin_api_key or ""
    if not expected:
        raise AuthError("Admin API key is not configured on the server.", status_code=503)
    if not provided or not hmac.compare_digest(provided, expected):
        raise AuthError("Invalid or missing admin key.", status_code=401)
    if expected == "change-me-admin-key":
        logger.warning("Admin API key is still the insecure default; change ADMIN_API_KEY.")
    return Identity(
        email=request.headers.get("X-User-Email"),
        role="admin",
        student_id=None,
        authenticated=True,
        source="admin-key",
        is_tutor=False,
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
