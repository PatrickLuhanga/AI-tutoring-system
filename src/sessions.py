"""Session and login telemetry for the relational store.

When a student signs in and opens a module chat, the gateway records the event
here. Two things are written:

* ``students.last_login_at`` - the login timestamp, shown on the admin
  dashboard's user list.
* ``tutoring_sessions`` - one row per ``session_id`` carrying the module scope,
  the first-seen (login) timestamp, the last activity timestamp and a turn
  counter. The tutor and admin dashboards read this table to answer "who is
  active right now" and "who keeps coming back to the same topic".

Every write is best-effort: a Data Tier outage must degrade the dashboard, never
break a student's chat turn.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .db import session_scope
from .models import Module, Student, StudentSession, TutorAssignment

logger = logging.getLogger(__name__)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _valid_module(module_id: Optional[str]) -> Optional[str]:
    """Return ``module_id`` only when it exists (FK safety), else ``None``."""
    if not module_id:
        return None
    try:
        with session_scope() as session:
            exists = session.execute(
                select(Module.module_id).where(Module.module_id == module_id).limit(1)
            ).scalar_one_or_none()
        return str(exists) if exists is not None else None
    except Exception as exc:  # noqa: BLE001 - Data Tier must not break a request
        logger.warning("Module existence check failed for %s: %s", module_id, exc)
        return None


def _session_values(
    *,
    session_id: str,
    student_id: Optional[int],
    email: Optional[str],
    module_id: Optional[str],
    now: dt.datetime,
    turn_count: int,
) -> dict:
    return {
        "session_id": session_id,
        "student_id": student_id,
        "student_email": email,
        "module_id": module_id,
        "started_at": now,
        "last_activity_at": now,
        "turn_count": turn_count,
        "is_active": True,
    }


def record_session_open(
    *,
    session_id: str,
    student_id: Optional[int] = None,
    email: Optional[str] = None,
    module_id: Optional[str] = None,
) -> dict:
    """Stamp the login timestamp and open (or refresh) a tutoring session.

    Returns a JSON-ready summary of the persisted row so the client can display
    the recorded session id.
    """
    now = _now()
    resolved_module = _valid_module(module_id)

    with session_scope() as session:
        if student_id is not None:
            session.execute(
                update(Student)
                .where(Student.student_id == student_id)
                .values(last_login_at=now)
            )

        stmt = pg_insert(StudentSession).values(
            **_session_values(
                session_id=session_id,
                student_id=student_id,
                email=email,
                module_id=resolved_module,
                now=now,
                turn_count=0,
            )
        )
        refresh: dict = {"last_activity_at": now, "is_active": True}
        if student_id is not None:
            refresh["student_id"] = student_id
        if email:
            refresh["student_email"] = email
        if resolved_module:
            refresh["module_id"] = resolved_module
        stmt = stmt.on_conflict_do_update(
            index_elements=[StudentSession.session_id], set_=refresh
        )
        session.execute(stmt)

        row = session.execute(
            select(StudentSession).where(StudentSession.session_id == session_id)
        ).scalar_one()
        return {
            "session_id": row.session_id,
            "student_id": row.student_id,
            "module_id": row.module_id,
            "started_at": row.started_at.isoformat(),
            "last_activity_at": row.last_activity_at.isoformat(),
            "turn_count": row.turn_count,
        }


def record_session_turn(
    *,
    session_id: str,
    student_id: Optional[int] = None,
    email: Optional[str] = None,
    module_id: Optional[str] = None,
) -> None:
    """Refresh a session after a chat turn and increment its turn counter."""
    now = _now()
    resolved_module = _valid_module(module_id)
    try:
        with session_scope() as session:
            stmt = pg_insert(StudentSession).values(
                **_session_values(
                    session_id=session_id,
                    student_id=student_id,
                    email=email,
                    module_id=resolved_module,
                    now=now,
                    turn_count=1,
                )
            )
            refresh: dict = {
                "last_activity_at": now,
                "is_active": True,
                "turn_count": StudentSession.turn_count + 1,
            }
            if student_id is not None:
                refresh["student_id"] = student_id
            if email:
                refresh["student_email"] = email
            if resolved_module:
                refresh["module_id"] = resolved_module
            stmt = stmt.on_conflict_do_update(
                index_elements=[StudentSession.session_id], set_=refresh
            )
            session.execute(stmt)
    except Exception as exc:  # noqa: BLE001 - telemetry must never break a reply
        logger.warning("Could not update session activity for %s: %s", session_id, exc)


def assigned_modules(student_id: Optional[int]) -> list[str]:
    """Return the modules a tutor is explicitly assigned to (RBAC scope)."""
    if student_id is None:
        return []
    try:
        with session_scope() as session:
            rows = session.execute(
                select(TutorAssignment.module_id).where(
                    TutorAssignment.tutor_id == student_id
                )
            ).scalars().all()
            return [str(row) for row in rows]
    except Exception as exc:  # noqa: BLE001 - degrade to an empty scope
        logger.warning("Could not read tutor assignments for %s: %s", student_id, exc)
        return []
