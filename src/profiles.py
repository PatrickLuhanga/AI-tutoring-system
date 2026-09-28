"""Dynamic DUT4life profile provisioning and admin tutor grants.

The auth flow no longer depends on a hard-coded list of demo profiles. Any
valid ``*@dut4life.ac.za`` address signs in; on first contact a profile row is
provisioned and the client is asked to complete onboarding (name + enrolled
modules). Admins, identified by a configurable allowlist, can grant tutor
privileges to any registered user by student number, email or id — a student may
hold tutor privileges at the same time (dual role).
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .config import settings
from .db import session_scope
from .models import Enrollment, Module, Student, TutorAssignment

logger = logging.getLogger(__name__)


class ProfileError(ValueError):
    """Raised when a profile / role request is invalid (maps to HTTP 400/404)."""


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------
def effective_roles(student: Student) -> list[str]:
    """Capability roles: ``student``/``tutor``/``admin`` (a user may hold both).

    ``admin`` is exclusive (administrators already reach every workspace). A
    legacy ``tutor`` account is treated as a student who can tutor.
    """
    if student.role == "admin":
        return ["admin"]
    roles = ["student"]
    if student.is_tutor or student.role == "tutor":
        roles.append("tutor")
    return roles


def _module_names(session) -> dict[str, str]:
    return {
        str(mid): str(name)
        for mid, name in session.execute(
            select(Module.module_id, Module.module_name)
        ).all()
    }


def _enrolled_modules(session, student_id: int) -> list[str]:
    return [
        str(row)
        for row in session.execute(
            select(Enrollment.module_id)
            .where(Enrollment.student_id == student_id, Enrollment.is_active.is_(True))
            .order_by(Enrollment.module_id)
        ).scalars().all()
    ]


def _tutor_modules(session, student_id: int) -> list[str]:
    return [
        str(row)
        for row in session.execute(
            select(TutorAssignment.module_id)
            .where(TutorAssignment.tutor_id == student_id)
            .order_by(TutorAssignment.module_id)
        ).scalars().all()
    ]


def _serialize(session, student: Student) -> dict:
    sid = int(student.student_id)
    enrolled = _enrolled_modules(session, sid)
    tutor = _tutor_modules(session, sid)
    needs_onboarding = not student.full_name
    if student.role == "student":
        needs_onboarding = needs_onboarding or not enrolled
    return {
        "student_id": sid,
        "email": student.dut4life_email,
        "full_name": student.full_name,
        "student_number": student.student_number,
        "role": student.role,
        "roles": effective_roles(student),
        "is_tutor": bool(student.is_tutor) or student.role == "tutor",
        "is_admin": student.role == "admin",
        "modules": tutor,
        "enrolled_modules": enrolled,
        "needs_onboarding": needs_onboarding,
    }


def _validate_modules(session, module_ids: Optional[Sequence[str]]) -> list[str]:
    requested = [str(m).strip() for m in (module_ids or []) if str(m).strip()]
    if not requested:
        return []
    rows = session.execute(
        select(Module.module_id).where(Module.module_id.in_(requested))
    ).scalars().all()
    return [str(row) for row in rows]


def _find_identity(session, identifier: str) -> Optional[Student]:
    """Resolve an admin-supplied identifier: email, student number or id.

    A student number is numeric too, so it is checked before the primary-key
    id to avoid misreading ``22000000`` as ``student_id=22000000``.
    """
    ident = (identifier or "").strip()
    if not ident:
        return None
    if "@" in ident:
        return session.execute(
            select(Student).where(func.lower(Student.dut4life_email) == ident.lower())
        ).scalar_one_or_none()

    student = session.execute(
        select(Student).where(Student.student_number == ident)
    ).scalar_one_or_none()
    if student is not None:
        return student
    if ident.isdigit():
        student = session.get(Student, int(ident))
        if student is not None:
            return student
    return session.execute(
        select(Student).where(func.lower(Student.dut4life_email) == ident.lower())
    ).scalar_one_or_none()


def _sync_enrollments(session, student: Student, module_ids: list[str]) -> None:
    valid = set(_validate_modules(session, module_ids))
    existing = {
        str(e.module_id): e
        for e in session.execute(
            select(Enrollment).where(Enrollment.student_id == student.student_id)
        ).scalars().all()
    }
    for module_id, enrollment in existing.items():
        if module_id not in valid:
            session.delete(enrollment)
    year = str(dt.date.today().year)
    for module_id in valid:
        if module_id in existing:
            existing[module_id].is_active = True
        else:
            session.add(
                Enrollment(
                    student_id=student.student_id,
                    module_id=module_id,
                    academic_year=year,
                    is_active=True,
                )
            )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def login_or_create(email: str) -> dict:
    """Resolve a DUT4life address to a profile, provisioning it on first login."""
    normalized = (email or "").strip().lower()
    if not settings.is_institutional_email(normalized):
        raise ProfileError(
            f"Sign in with an institutional @{settings.allowed_email_domain} address."
        )

    with session_scope() as session:
        student = session.execute(
            select(Student).where(func.lower(Student.dut4life_email) == normalized)
        ).scalar_one_or_none()

        if student is None:
            role = "admin" if settings.is_admin_email(normalized) else "student"
            student = Student(dut4life_email=normalized, role=role, is_tutor=False)
            session.add(student)
            session.flush()
            logger.info("Provisioned profile for %s (role=%s)", normalized, role)
        elif settings.is_admin_email(normalized) and student.role != "admin":
            student.role = "admin"
            logger.info("Promoted %s to admin (allowlist)", normalized)

        return _serialize(session, student)


def get_profile(student_id: int) -> Optional[dict]:
    with session_scope() as session:
        student = session.get(Student, student_id)
        return _serialize(session, student) if student is not None else None


def update_profile(
    student_id: int,
    *,
    full_name: Optional[str] = None,
    student_number: Optional[str] = None,
    modules: Optional[Sequence[str]] = None,
) -> dict:
    """Save onboarding / profile fields and (re)sync the student's modules."""
    with session_scope() as session:
        student = session.get(Student, student_id)
        if student is None:
            raise ProfileError("Profile not found.")

        if full_name is not None:
            clean = full_name.strip()
            if len(clean) < 2:
                raise ProfileError("Enter your full name.")
            student.full_name = clean

        if student_number is not None:
            sn = student_number.strip() or None
            if sn is not None:
                conflict = session.execute(
                    select(Student.student_id).where(
                        Student.student_number == sn,
                        Student.student_id != student.student_id,
                    )
                ).scalar_one_or_none()
                if conflict is not None:
                    raise ProfileError("That student number is already registered.")
            student.student_number = sn

        if modules is not None:
            _sync_enrollments(session, student, list(modules))

        session.flush()
        return _serialize(session, student)


def grant_tutor(
    identifier: str,
    module_ids: Optional[Sequence[str]] = None,
    granted_by: Optional[int] = None,
) -> dict:
    """Grant tutor privileges (dual role) and assign modules to a registered user."""
    with session_scope() as session:
        student = _find_identity(session, identifier)
        if student is None:
            raise ProfileError(f"No registered user matches '{identifier}'.")
        if student.role == "admin":
            # Admins already reach the tutor dashboard; still record assignments.
            pass
        student.is_tutor = True

        for module_id in _validate_modules(session, module_ids):
            session.execute(
                pg_insert(TutorAssignment)
                .values(
                    tutor_id=student.student_id,
                    module_id=module_id,
                    granted_by=granted_by,
                )
                .on_conflict_do_nothing(constraint="uq_tutor_module")
            )
        session.flush()
        logger.info("Granted tutor privileges to %s", student.dut4life_email)
        return _serialize(session, student)


def revoke_tutor(identifier: str) -> dict:
    """Remove tutor privileges and module assignments from a registered user."""
    with session_scope() as session:
        student = _find_identity(session, identifier)
        if student is None:
            raise ProfileError(f"No registered user matches '{identifier}'.")
        if student.role == "tutor":
            student.role = "student"
        student.is_tutor = False
        for assignment in session.execute(
            select(TutorAssignment).where(TutorAssignment.tutor_id == student.student_id)
        ).scalars().all():
            session.delete(assignment)
        session.flush()
        logger.info("Revoked tutor privileges from %s", student.dut4life_email)
        return _serialize(session, student)


def list_modules() -> list[dict]:
    """Active modules for onboarding checkboxes and the admin grant picker."""
    with session_scope() as session:
        rows = session.execute(
            select(
                Module.module_id, Module.module_code, Module.module_name, Module.language
            )
            .where(Module.is_active.is_(True))
            .order_by(Module.module_id)
        ).all()
    return [
        {
            "module_id": row.module_id,
            "module_code": row.module_code,
            "module_name": row.module_name,
            "language": row.language,
        }
        for row in rows
    ]
