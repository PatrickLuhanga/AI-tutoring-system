"""Persistent chat history for the LLM-style student sidebar.

Every turn (student question and tutor reply) is written to
``session_messages``; the owning ``tutoring_sessions`` row carries the module,
title and activity timestamps the sidebar groups on. Deleting a session removes
its messages, its telemetry logs and its hint feedback in one transaction.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Optional

from sqlalchemy import delete, false, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .db import session_scope
from .models import (
    HintFeedback,
    Module,
    SessionMessage,
    StudentSession,
    TelemetryLog,
)

logger = logging.getLogger(__name__)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _valid_module(module_id: Optional[str]) -> Optional[str]:
    if not module_id:
        return None
    try:
        with session_scope() as session:
            exists = session.execute(
                select(Module.module_id).where(Module.module_id == module_id).limit(1)
            ).scalar_one_or_none()
        return str(exists) if exists is not None else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Module existence check failed for %s: %s", module_id, exc)
        return None


def _owned(student_id: Optional[int], email: Optional[str]):
    """SQL predicate restricting a session to its owner (id, else email)."""
    if student_id is not None:
        return StudentSession.student_id == student_id
    if email:
        return func.lower(StudentSession.student_email) == email.strip().lower()
    return false()


def _derive_title(content: str) -> str:
    collapsed = " ".join(content.split())
    return (collapsed[:80] + "…") if len(collapsed) > 80 else (collapsed or "New chat")


def _serialize_message(message: SessionMessage) -> dict:
    return {
        "message_id": message.message_id,
        "session_id": message.session_id,
        "role": message.role,
        "content": message.content,
        "module_id": message.module_id,
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "audit": message.audit,
    }


def _serialize_session(session: StudentSession, names: dict[str, str]) -> dict:
    return {
        "session_id": session.session_id,
        "module_id": session.module_id,
        "module_name": names.get(str(session.module_id)) if session.module_id else None,
        "title": session.title or "New chat",
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "last_activity_at": session.last_activity_at.isoformat()
        if session.last_activity_at
        else None,
        "turn_count": int(session.turn_count or 0),
        "message_count": int(session.message_count or 0),
    }


def append_message(
    *,
    session_id: str,
    role: str,
    content: str,
    module_id: Optional[str] = None,
    student_id: Optional[int] = None,
    email: Optional[str] = None,
    message_id: Optional[str] = None,
    audit: Optional[dict] = None,
) -> dict:
    """Persist one turn and refresh the parent session (title, activity, count)."""
    now = _now()
    resolved_module = _valid_module(module_id)
    with session_scope() as session:
        stmt = pg_insert(StudentSession).values(
            session_id=session_id,
            student_id=student_id,
            student_email=email,
            module_id=resolved_module,
            started_at=now,
            last_activity_at=now,
            turn_count=0,
            message_count=0,
            is_active=True,
        )
        refresh: dict = {"last_activity_at": now, "is_active": True}
        if student_id is not None:
            refresh["student_id"] = student_id
        if email:
            refresh["student_email"] = email
        if resolved_module:
            refresh["module_id"] = resolved_module
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=[StudentSession.session_id], set_=refresh
            )
        )

        row = session.execute(
            select(StudentSession).where(StudentSession.session_id == session_id)
        ).scalar_one()
        if role == "user" and not row.title:
            row.title = _derive_title(content)
        row.message_count = int(row.message_count or 0) + 1
        row.last_activity_at = now
        row.is_active = True

        message = SessionMessage(
            message_id=message_id or uuid.uuid4().hex,
            session_id=session_id,
            role=role,
            content=content,
            module_id=resolved_module,
            audit=audit,
            created_at=now,
        )
        session.add(message)
        session.flush()
        return _serialize_message(message)


def list_sessions(
    *,
    student_id: Optional[int],
    email: Optional[str] = None,
    module_id: Optional[str] = None,
) -> list[dict]:
    """Return the caller's sessions, newest activity first."""
    try:
        with session_scope() as session:
            names = {
                str(mid): str(name)
                for mid, name in session.execute(
                    select(Module.module_id, Module.module_name)
                ).all()
            }
            stmt = select(StudentSession).where(_owned(student_id, email))
            if module_id:
                stmt = stmt.where(StudentSession.module_id == module_id)
            stmt = stmt.order_by(StudentSession.last_activity_at.desc()).limit(200)
            rows = session.execute(stmt).scalars().all()
            return [_serialize_session(row, names) for row in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not list sessions: %s", exc)
        return []


def get_session(
    *, student_id: Optional[int], email: Optional[str], session_id: str
) -> Optional[dict]:
    """Return one owned session plus its full message history, or ``None``."""
    try:
        with session_scope() as session:
            row = session.execute(
                select(StudentSession).where(
                    StudentSession.session_id == session_id,
                    _owned(student_id, email),
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            names = {
                str(mid): str(name)
                for mid, name in session.execute(
                    select(Module.module_id, Module.module_name)
                ).all()
            }
            messages = session.execute(
                select(SessionMessage)
                .where(SessionMessage.session_id == session_id)
                .order_by(SessionMessage.created_at, SessionMessage.message_id)
            ).scalars().all()
            return {
                "session": _serialize_session(row, names),
                "messages": [_serialize_message(m) for m in messages],
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load session %s: %s", session_id, exc)
        return None


def delete_session(
    *, student_id: Optional[int], email: Optional[str], session_id: str
) -> bool:
    """Delete an owned session and its messages, telemetry and feedback."""
    with session_scope() as session:
        row = session.execute(
            select(StudentSession).where(
                StudentSession.session_id == session_id,
                _owned(student_id, email),
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        session.execute(delete(SessionMessage).where(SessionMessage.session_id == session_id))
        session.execute(delete(TelemetryLog).where(TelemetryLog.session_id == session_id))
        session.execute(delete(HintFeedback).where(HintFeedback.session_id == session_id))
        session.delete(row)
        logger.info("Deleted session %s and its associated data", session_id)
        return True
