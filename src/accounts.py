"""Account-based authentication: signup, login, sessions, roles, module scope.

This is the credential layer the four user types sit on. It is deliberately
separate from :mod:`src.auth`, which resolves an *identity* for a request; this
module establishes that identity from a password and a session token.

Design notes
------------
* **Passwords** use Werkzeug's scrypt hash. Werkzeug already ships with Flask, so
  this adds no dependency.
* **Sessions are server-side.** A login mints 32 random bytes, returns the token
  once, and stores only its SHA-256. That is what makes logout, suspension and
  "sign out everywhere" take effect immediately, which a self-contained token
  cannot do. :func:`authenticate_request` is the single place that turns a
  ``Authorization: Bearer <token>`` header into a verified user.
* **Email domains are enforced per role.** Students must use the DUT4life
  student domain and supply their student number; lecturers must use the
  academic staff domain; tutors are not domain-restricted. The System Admin
  cannot be created by signup.
* **Staff module scope** is written at signup from the modules the person claims
  to teach, and is then enforced in SQL. A signup for a module that does not
  exist is rejected rather than silently ignored, so a typo cannot quietly
  produce an account that sees nothing.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from .config import settings
from .db import session_scope
from .models import (
    ACCOUNT_ROLE_VALUES,
    MODULE_SCOPED_ROLES,
    SELF_SIGNUP_ROLES,
    STAFF_ROLES,
    Module,
    Student,
    User,
    UserModuleAccess,
    UserSession,
)

logger = logging.getLogger(__name__)

#: DUT student numbers are 8 digits (e.g. 22000000).
STUDENT_NUMBER_RE = re.compile(r"^\d{8}$")

#: Pragmatic address check: one @, a non-empty local part, a dotted domain.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AccountError(Exception):
    """A signup/login request that cannot be satisfied.

    ``status_code`` distinguishes "you typed something wrong" (400) from "this
    account may not do that" (403) and "not you" (401), so the API layer does
    not have to guess.
    """

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(slots=True)
class AuthenticatedUser:
    """A verified account plus the module scope it may use."""

    user_id: int
    email: str
    full_name: Optional[str]
    role: str
    status: str
    student_number: Optional[str]
    student_id: Optional[int]
    session_id: int

    @property
    def is_staff(self) -> bool:
        return self.role in STAFF_ROLES

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def to_dict(self, module_ids: Sequence[str] = ()) -> dict:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "full_name": self.full_name,
            "role": self.role,
            "status": self.status,
            "student_number": self.student_number,
            "student_id": self.student_id,
            "is_staff": self.is_staff,
            "modules": sorted(module_ids),
        }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def normalise_email(raw: object) -> str:
    email = str(raw or "").strip().lower()
    if not email:
        raise AccountError("Email is required.")
    if len(email) > 255 or not EMAIL_RE.match(email):
        raise AccountError("Enter a valid email address.")
    return email


def _domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower()


# ---------------------------------------------------------------------------
# System Admin bootstrap guard
# ---------------------------------------------------------------------------
#: A single shared secret has no per-account rate limit of its own, so guesses
#: are throttled per client address. In-process only: a multi-worker deployment
#: would give each worker its own bucket, which is why the attempt cap is set
#: low enough to still be a meaningful obstacle.
_ADMIN_ATTEMPT_LIMIT = 5
_ADMIN_LOCKOUT_SECONDS = 300
_admin_attempts: dict[str, list[float]] = {}
_admin_lock = threading.Lock()


def _admin_throttle_key(address: Optional[str]) -> str:
    return (address or "unknown").strip().lower()


def _admin_is_locked_out(key: str, now: float) -> Optional[float]:
    """Seconds remaining in the lockout for ``key``, or None if not locked."""
    with _admin_lock:
        recent = [t for t in _admin_attempts.get(key, []) if now - t < _ADMIN_LOCKOUT_SECONDS]
        if recent:
            _admin_attempts[key] = recent
        if len(recent) >= _ADMIN_ATTEMPT_LIMIT:
            return max(0, int(_ADMIN_LOCKOUT_SECONDS - (now - recent[0]) + 1))
    return None


def _admin_record_failure(key: str, now: float) -> None:
    with _admin_lock:
        bucket = _admin_attempts.setdefault(key, [])
        bucket.append(now)
        _admin_attempts[key] = [t for t in bucket if now - t < _ADMIN_LOCKOUT_SECONDS]


def _admin_clear(key: str) -> None:
    with _admin_lock:
        _admin_attempts.pop(key, None)


# ---------------------------------------------------------------------------
# Login throttling
# ---------------------------------------------------------------------------
#: Failed password attempts per client address.
#:
#: ``login`` was the one authentication path with no rate limit at all, while the
#: admin signup guard right above already had one - so the shared secret was the
#: best-protected credential in the system and an ordinary account's password was
#: brute-forceable at whatever rate the network allowed.
#:
#: Two buckets, because there are two things worth limiting. The address bucket
#: stops a single host spraying many accounts. The per-account bucket stops one
#: account being sprayed from many hosts, which the address bucket alone would
#: never notice.
#:
#: In-process only: under multiple workers each gets its own bucket. A shared
#: store would be the fix, and the limits are set low enough to still bite.
_LOGIN_ATTEMPT_LIMIT = 5
_LOGIN_ACCOUNT_LIMIT = 10
_LOGIN_LOCKOUT_SECONDS = 300
_login_address_attempts: dict[str, list[float]] = {}
_login_account_attempts: dict[str, list[float]] = {}
_login_lock = threading.Lock()


def _throttle_key(address: Optional[str]) -> str:
    return (address or "unknown").strip().lower()


def _lockout_remaining(bucket: dict[str, list[float]], key: str, limit: int, now: float) -> Optional[int]:
    """Seconds left on the lockout for ``key``, or None if not locked."""
    with _login_lock:
        recent = [t for t in bucket.get(key, []) if now - t < _LOGIN_LOCKOUT_SECONDS]
        if recent:
            bucket[key] = recent
        else:
            bucket.pop(key, None)
        if len(recent) >= limit:
            return max(0, int(_LOGIN_LOCKOUT_SECONDS - (now - recent[0]) + 1))
    return None


def _record_failure(bucket: dict[str, list[float]], key: str, now: float) -> int:
    with _login_lock:
        entries = bucket.setdefault(key, [])
        entries.append(now)
        bucket[key] = [t for t in entries if now - t < _LOGIN_LOCKOUT_SECONDS]
        return len(bucket[key])


def _clear_failure(bucket: dict[str, list[float]], key: str) -> None:
    with _login_lock:
        bucket.pop(key, None)


def _guard_login_rate(identifier: str, client_address: Optional[str]) -> None:
    """Refuse a login while either bucket is locked out.

    Raises **before** any password check, so a locked-out caller cannot use the
    endpoint's response to learn whether an account exists.
    """
    now = time.monotonic()
    address_key = _throttle_key(client_address)
    account_key = identifier.strip().lower()

    remaining = _lockout_remaining(
        _login_address_attempts, address_key, _LOGIN_ATTEMPT_LIMIT, now
    )
    if remaining is None:
        remaining = _lockout_remaining(
            _login_account_attempts, account_key, _LOGIN_ACCOUNT_LIMIT, now
        )
    if remaining is not None:
        raise AccountError(
            f"Too many failed sign-in attempts. Try again in {remaining} seconds.",
            status_code=429,
        )


def _record_login_failure(identifier: str, client_address: Optional[str]) -> None:
    now = time.monotonic()
    _record_failure(_login_address_attempts, _throttle_key(client_address), now)
    _record_failure(_login_account_attempts, identifier.strip().lower(), now)


def _clear_login_failures(identifier: str, client_address: Optional[str]) -> None:
    _clear_failure(_login_address_attempts, _throttle_key(client_address))
    _clear_failure(_login_account_attempts, identifier.strip().lower())


def _guard_admin_signup(presented: str, client_address: Optional[str] = None) -> None:
    """Authorise a System Admin registration against the shared secret.

    Fails closed: with no ``ADMIN_SIGNUP_SECRET`` configured, admin
    self-registration is simply unavailable, which is the safe default for a
    deployment that has not decided who may bootstrap one.
    """
    expected = settings.admin_signup_secret or ""
    if not expected:
        raise AccountError(
            "System Admin self-registration is not enabled on this deployment.",
            status_code=403,
        )

    key = _admin_throttle_key(client_address)
    now = time.monotonic()
    remaining = _admin_is_locked_out(key, now)
    if remaining is not None:
        raise AccountError(
            f"Too many incorrect admin registration attempts. "
            f"Try again in {remaining} seconds.",
            status_code=429,
        )

    # Constant-time so the secret cannot be recovered by timing the response.
    if not hmac.compare_digest(presented, expected):
        _admin_record_failure(key, now)
        left = max(0, _ADMIN_ATTEMPT_LIMIT - len(_admin_attempts.get(key, [])))
        raise AccountError(
            "The System Admin registration code is incorrect."
            + (f" {left} attempt(s) remaining." if left else ""),
            status_code=403,
        )
    _admin_clear(key)


def _require_domain(email: str, domain: str, role_label: str) -> None:
    """Reject an address outside ``domain`` (or its subdomains).

    Subdomains are allowed so ``cs.dut.ac.za`` works for a lecturer, but
    ``dut4life.ac.za.evil.com`` does not: the comparison is on a label boundary.
    """
    actual = _domain_of(email)
    if actual == domain or actual.endswith("." + domain):
        return
    article = "An" if role_label[0] in "aeiou" else "A"
    raise AccountError(
        f"{article} {role_label} account must use an @{domain} email address "
        f"(you used @{actual}).",
        status_code=403,
    )


def validate_signup(
    *,
    email: str,
    password: str,
    role: str,
    student_number: Optional[str],
    full_name: Optional[str],
    client_address: Optional[str] = None,
) -> tuple[str, Optional[str]]:
    """Apply every signup rule and return the values to persist.

    Returns ``(email, student_number)``. Raises :class:`AccountError` on the
    first rule that fails, with a message meant to be shown to the user.
    """
    role = str(role or "").strip().lower()
    if role not in ACCOUNT_ROLE_VALUES:
        raise AccountError("Choose a valid account type.")
    if role == "admin":
        # Guarded bootstrap rather than open self-registration: the caller must
        # present the shared admin secret. Checked here, in the validation layer,
        # so no other signup path can reach account creation without it.
        _guard_admin_signup(str(password or ""), client_address)
    elif role not in SELF_SIGNUP_ROLES:
        article = "An" if role[0] in "aeiou" else "A"
        raise AccountError(
            f"{article} {role} account cannot be created through signup; "
            "ask an existing System Admin to make one.",
            status_code=403,
        )
    if not settings.allow_self_signup:
        raise AccountError("Registration is currently closed.", status_code=403)

    password = str(password or "")
    if role == "admin":
        # The admin secret *is* the password for an admin account, so it is
        # exempt from the length rule below but must still be hashed like any
        # other credential.
        pass
    elif len(password) < settings.min_password_length:
        raise AccountError(
            f"Password must be at least {settings.min_password_length} characters."
        )

    clean_number: Optional[str] = None
    if role == "student":
        _require_domain(email, settings.student_email_domain, "student")
        raw_number = str(student_number or "").strip()
        if not raw_number:
            raise AccountError("Enter your student number.")
        if not STUDENT_NUMBER_RE.match(raw_number):
            raise AccountError("Student number must be 8 digits, e.g. 22000000.")
        clean_number = raw_number
    elif role == "lecturer":
        _require_domain(email, settings.lecturer_email_domain, "lecturer")
    # tutors: no domain restriction, by design.

    if full_name is not None:
        full_name = full_name.strip() or None
    return email, clean_number


# ---------------------------------------------------------------------------
# Module scope
# ---------------------------------------------------------------------------
def resolve_module_ids(user_id: int) -> list[str]:
    """Modules this account may see. Empty for students (they use enrolment)."""
    with session_scope() as session:
        rows = session.execute(
            select(UserModuleAccess.module_id).where(UserModuleAccess.user_id == user_id)
        ).scalars()
        return sorted({str(r) for r in rows})


def _assert_modules_exist(module_ids: Sequence[str]) -> list[str]:
    """Reject unknown module ids instead of granting an invisible scope."""
    wanted = [str(m).strip().upper() for m in (module_ids or []) if str(m).strip()]
    if not wanted:
        return []
    unique = list(dict.fromkeys(wanted))
    with session_scope() as session:
        known = {
            str(r)
            for r in session.execute(
                select(Module.module_id).where(Module.module_id.in_(unique))
            ).scalars()
        }
    missing = [m for m in unique if m not in known]
    if missing:
        raise AccountError(
            f"Unknown module{'s' if len(missing) > 1 else ''}: {', '.join(missing)}. "
            "Pick a module from the list.",
        )
    return unique


def _link_student_record(
    email: str, student_number: Optional[str], full_name: Optional[str] = None
) -> Optional[int]:
    """Attach a student account to its directory row, creating one if needed.

    Telemetry, enrolment and module announcements all key off ``student_id``, so an
    account without one is invisible to them: its chat turns cannot be attributed
    and it never receives a module announcement.

    The academic directory is meant to be populated by the DUT sync, but until
    that exists a self-registered student would have no row at all - which is why
    every one of them had ``student_id = NULL``. So signup creates the row from
    the details the student supplied, and the sync reconciles it later by email or
    student number rather than having to insert one.

    A pre-existing row always wins: the directory is authoritative about identity,
    and a signup must never overwrite it.
    """
    with session_scope() as session:
        student = session.execute(
            select(Student).where(Student.dut4life_email == email).limit(1)
        ).scalar_one_or_none()
        if student is None and student_number:
            student = session.execute(
                select(Student).where(Student.student_number == student_number).limit(1)
            ).scalar_one_or_none()
        if student is not None:
            return int(student.student_id)

        student = Student(
            student_number=student_number,
            dut4life_email=email,
            full_name=(str(full_name).strip() or None) if full_name else None,
        )
        session.add(student)
        try:
            session.flush()
        except IntegrityError:
            # Raced with the DUT sync or another signup; re-read and link.
            session.rollback()
            student = session.execute(
                select(Student).where(Student.dut4life_email == email).limit(1)
            ).scalar_one_or_none()
            if student is None and student_number:
                student = session.execute(
                    select(Student).where(Student.student_number == student_number).limit(1)
                ).scalar_one_or_none()
            return int(student.student_id) if student is not None else None
        return int(student.student_id)


# ---------------------------------------------------------------------------
# Signup / login / logout
# ---------------------------------------------------------------------------
def create_account(
    *,
    email: object,
    password: object,
    role: object,
    full_name: object = None,
    student_number: object = None,
    module_ids: Sequence[str] = (),
    client_address: Optional[str] = None,
) -> tuple[AuthenticatedUser, str]:
    """Register a self-service account and open a session for it.

    Returns the new user and the one-time bearer token, matching :func:`login`.
    """
    clean_email = normalise_email(email)
    clean_role = str(role or "").strip().lower()
    clean_email, clean_number = validate_signup(
        email=clean_email,
        password=str(password or ""),
        role=clean_role,
        student_number=(str(student_number).strip() if student_number else None),
        full_name=(str(full_name) if full_name else None),
        client_address=client_address,
    )

    # A student is scoped by enrolment, and a System Admin is system-wide, so
    # neither picks modules at signup; only tutors and lecturers do.
    granted = (
        _assert_modules_exist(module_ids) if clean_role in MODULE_SCOPED_ROLES else []
    )
    if clean_role in MODULE_SCOPED_ROLES and not granted:
        raise AccountError("Select at least one module you teach.")

    student_id = (
        _link_student_record(clean_email, clean_number, full_name=full_name)
        if clean_role == "student"
        else None
    )

    with session_scope() as session:
        existing = session.execute(
            select(User).where(User.email == clean_email).limit(1)
        ).scalar_one_or_none()
        if existing is not None:
            raise AccountError("An account with that email already exists.", status_code=409)

        user = User(
            email=clean_email,
            password_hash=generate_password_hash(str(password)),
            full_name=(str(full_name).strip() or None) if full_name else None,
            role=clean_role,
            student_number=clean_number,
            student_id=student_id,
        )
        session.add(user)
        try:
            session.flush()
        except IntegrityError as exc:  # lost a race on the unique constraints
            raise AccountError(
                "An account with that email or student number already exists.",
                status_code=409,
            ) from exc

        for module_id in granted:
            session.add(UserModuleAccess(user_id=user.user_id, module_id=module_id))

        session_id, token = _open_session(session, user)
        result = AuthenticatedUser(
            user_id=int(user.user_id),
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            status=user.status,
            student_number=user.student_number,
            student_id=user.student_id,
            session_id=session_id,
        )

    logger.info(
        "Registered %s account for %s with module scope %s",
        result.role,
        result.email,
        granted or "-",
    )
    return result, token


def login(
    *,
    email: object,
    password: object,
    user_agent: Optional[str] = None,
    client_address: Optional[str] = None,
) -> tuple[AuthenticatedUser, str]:
    """Verify a password and open a session.

    Returns the user and the one-time bearer token. The same message is used for
    an unknown address and a wrong password so the endpoint cannot be used to
    enumerate registered accounts.

    Failed attempts are throttled per client address *and* per account; see
    :func:`_guard_login_rate`. Both are checked before the password is compared,
    so a locked-out caller learns nothing about whether the account exists.
    """
    clean_email = str(email or "").strip().lower()
    bad = AccountError("Email or password is incorrect.", status_code=401)

    _guard_login_rate(clean_email, client_address)

    with session_scope() as session:
        user = session.execute(
            select(User).where(User.email == clean_email).limit(1)
        ).scalar_one_or_none()
        if user is None or not check_password_hash(user.password_hash, str(password or "")):
            _record_login_failure(clean_email, client_address)
            raise bad
        if user.status != "active":
            # Not a failed guess, so it must not consume an attempt - but it is
            # also not a success, so the bucket is left as it was.
            raise AccountError(
                "This account has been suspended. Contact your System Admin.", status_code=403
            )
        user.last_login_at = datetime.now(timezone.utc)
        session_id, token = _open_session(session, user, user_agent=user_agent)
        resolved = AuthenticatedUser(
            user_id=int(user.user_id),
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            status=user.status,
            student_number=user.student_number,
            student_id=user.student_id,
            session_id=session_id,
        )

    _clear_login_failures(clean_email, client_address)
    logger.info("Login: %s (%s)", resolved.email, resolved.role)
    return resolved, token


def logout(session_id: int) -> None:
    """Revoke one session."""
    with session_scope() as session:
        row = session.get(UserSession, session_id)
        if row is not None and row.revoked_at is None:
            row.revoked_at = datetime.now(timezone.utc)


def logout_everywhere(user_id: int) -> int:
    """Revoke every live session for an account. Returns how many were closed."""
    with session_scope() as session:
        rows = session.execute(
            select(UserSession).where(
                UserSession.user_id == user_id, UserSession.revoked_at.is_(None)
            )
        ).scalars()
        now = datetime.now(timezone.utc)
        count = 0
        for row in rows:
            row.revoked_at = now
            count += 1
    return count


# ---------------------------------------------------------------------------
# Session plumbing
# ---------------------------------------------------------------------------
def _open_session(
    session, user: User, *, user_agent: Optional[str] = None
) -> tuple[int, str]:
    token = secrets.token_urlsafe(32)
    row = UserSession(
        user_id=user.user_id,
        token_hash=_hash_token(token),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours),
        user_agent=(user_agent or "")[:255] or None,
    )
    session.add(row)
    session.flush()
    return int(row.session_id), token


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def authenticate_request(bearer_token: Optional[str]) -> AuthenticatedUser:
    """Resolve a bearer token to a live account, or raise 401.

    Expiry and revocation are both checked, and a suspended account is refused
    even if its token has not yet run out.
    """
    if not bearer_token:
        raise AccountError("Sign in to continue.", status_code=401)

    now = datetime.now(timezone.utc)
    with session_scope() as session:
        row = session.execute(
            select(UserSession).where(UserSession.token_hash == _hash_token(bearer_token)).limit(1)
        ).scalar_one_or_none()
        if row is None or row.revoked_at is not None:
            raise AccountError("Your session is no longer valid. Sign in again.", status_code=401)
        if row.expires_at is not None and row.expires_at < now:
            raise AccountError("Your session has expired. Sign in again.", status_code=401)

        user = session.get(User, row.user_id)
        if user is None:
            raise AccountError("Your session is no longer valid. Sign in again.", status_code=401)
        if user.status != "active":
            raise AccountError("This account has been suspended.", status_code=403)

        row.last_seen_at = now
        return AuthenticatedUser(
            user_id=int(user.user_id),
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            status=user.status,
            student_number=user.student_number,
            student_id=user.student_id,
            session_id=int(row.session_id),
        )


def count_active_sessions(user_id: int) -> int:
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        return int(
            session.execute(
                select(func.count())
                .select_from(UserSession)
                .where(
                    UserSession.user_id == user_id,
                    UserSession.revoked_at.is_(None),
                    UserSession.expires_at > now,
                )
            ).scalar_one()
        )
