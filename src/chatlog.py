"""Persist chat exchanges so a tutor can review how a module is being used.

``telemetry_logs`` is deliberately lean - routing metadata, no text - because it
is written on the hot path. That left one half of the Human Adjustment Cycle
unimplemented: :mod:`src.unanswered` captures questions RAG could not answer, so
a tutor saw the failures and nothing else. A student confused by a *well-grounded
but unhelpful* answer produced no record anywhere.

This module writes the exchange itself. It is separate from the tutor queue on
purpose:

* ``chat_turns`` is the record of what happened - every turn, grounded or not.
* ``unanswered_questions`` is a short worklist of real curriculum gaps to fix.

A row here is not a request for attention.

**Retention.** This is students' conversation content and is the most sensitive
data the application holds, so a caller should be able to age it out. Deleting a
student account or a session row should reach this table too; the foreign keys
are ``SET NULL`` rather than ``CASCADE`` precisely so that removing a *directory*
record does not silently erase teaching evidence a tutor may still need.
"""

from __future__ import annotations

import logging
from typing import Optional, Sequence

from sqlalchemy import delete, select

from .db import session_scope
from .models import GROUNDING_LEVELS, ChatTurn

logger = logging.getLogger(__name__)

#: Conversations older than this are the operator's call to keep or drop. Not
#: enforced automatically - deleting students' questions without warning would be
#: worse than keeping them too long - but surfaced by :func:`turns_older_than`.
REVIEW_AFTER_DAYS = 180


def record_turn(
    *,
    session_id: str,
    question_text: str,
    module_id: Optional[str],
    intent: Optional[str] = None,
    grounding: str = "ungrounded",
    message_id: Optional[str] = None,
    user_id: Optional[int] = None,
    student_id: Optional[int] = None,
    answer_text: Optional[str] = None,
    cited_chunk_ids: Optional[Sequence[int]] = None,
    guardrail_flagged: bool = False,
    was_bypass: bool = False,
    response_latency_ms: Optional[int] = None,
) -> Optional[int]:
    """Store one exchange. Returns the row id, or ``None`` if it could not be stored."""
    text = (question_text or "").strip()
    if not text:
        return None

    level = grounding if grounding in GROUNDING_LEVELS else "ungrounded"

    try:
        with session_scope() as session:
            row = ChatTurn(
                session_id=str(session_id)[:64],
                message_id=(str(message_id)[:64] if message_id else None),
                user_id=user_id,
                student_id=student_id,
                module_id=(str(module_id).strip().upper() if module_id else None),
                question_text=text,
                answer_text=(str(answer_text).strip() or None) if answer_text else None,
                intent=(str(intent)[:32] if intent else None),
                grounding=level,
                cited_chunk_ids=[int(c) for c in (cited_chunk_ids or [])][:20],
                guardrail_flagged=bool(guardrail_flagged),
                was_bypass=bool(was_bypass),
                response_latency_ms=response_latency_ms,
            )
            session.add(row)
            session.flush()
            return int(row.turn_id)
    except Exception as exc:  # noqa: BLE001 - never cost a student their answer
        logger.warning("Could not record chat turn: %s", exc)
        return None


def list_turns(
    *,
    module_ids: Sequence[str],
    grounding: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    """Recent exchanges in the given modules, newest first.

    ``module_ids`` is always applied by the caller from the staff member's own
    resolved scope - never from the request - so this cannot widen access.
    """
    modules = [str(m).strip().upper() for m in (module_ids or []) if m]
    if not modules:
        return []

    with session_scope() as session:
        stmt = select(ChatTurn).where(ChatTurn.module_id.in_(modules))
        if grounding in GROUNDING_LEVELS:
            stmt = stmt.where(ChatTurn.grounding == grounding)
        if search:
            needle = f"%{search.strip().lower()}%"
            stmt = stmt.where(ChatTurn.question_text.ilike(needle))
        rows = session.execute(
            stmt.order_by(ChatTurn.created_at.desc()).limit(max(1, min(limit, 500)))
        ).scalars().all()

        return [
            {
                "turn_id": int(r.turn_id),
                "session_id": r.session_id,
                "module_id": r.module_id,
                "student_id": r.student_id,
                "question_text": r.question_text,
                "answer_text": r.answer_text,
                "intent": r.intent,
                "grounding": r.grounding,
                "cited_count": len(r.cited_chunk_ids or []),
                "guardrail_flagged": bool(r.guardrail_flagged),
                "was_bypass": bool(r.was_bypass),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


def turn_counts(*, module_ids: Sequence[str]) -> dict:
    """Counts by grounding level, for the tutor's summary row."""
    modules = [str(m).strip().upper() for m in (module_ids or []) if m]
    if not modules:
        return {"grounded": 0, "weak": 0, "ungrounded": 0, "total": 0}

    counts = {level: 0 for level in GROUNDING_LEVELS}
    with session_scope() as session:
        rows = session.execute(
            select(ChatTurn.grounding).where(ChatTurn.module_id.in_(modules))
        ).scalars().all()
    for row in rows:
        counts[row] = counts.get(row, 0) + 1
    counts["total"] = sum(counts[level] for level in GROUNDING_LEVELS)
    return counts


def turns_older_than(days: int = REVIEW_AFTER_DAYS) -> int:
    """How many turns are past the review window. Reported, not deleted."""
    import datetime as dt

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=max(1, days))
    with session_scope() as session:
        from sqlalchemy import func

        return int(
            session.scalar(
                select(func.count()).select_from(ChatTurn).where(ChatTurn.created_at < cutoff)
            )
            or 0
        )


def delete_session(session_id: str) -> int:
    """Remove a student's conversation on request."""
    with session_scope() as session:
        result = session.execute(
            delete(ChatTurn).where(ChatTurn.session_id == str(session_id)[:64])
        )
        return int(result.rowcount or 0)


def record_content_miss(
    *,
    session_id: str,
    question_text: str,
    module_id: Optional[str] = None,
    message_id: Optional[str] = None,
    student_id: Optional[int] = None,
    web_used: bool = False,
    web_result_count: int = 0,
    domains: Optional[Sequence[str]] = None,
) -> Optional[int]:
    """Log a question the module's own material could not answer.

    Best-effort, like every other telemetry write here: a failure must never cost
    a student their answer. Returns the row id or ``None``.
    """
    text = (question_text or "").strip()
    if not text:
        return None
    try:
        from .models import ContentMiss

        with session_scope() as session:
            row = ContentMiss(
                session_id=str(session_id)[:64],
                message_id=(str(message_id)[:64] if message_id else None),
                student_id=student_id,
                module_id=(str(module_id).strip().upper() if module_id else None),
                question_text=text,
                web_used=bool(web_used),
                web_result_count=int(web_result_count or 0),
                domains=[str(d) for d in (domains or [])][:20],
            )
            session.add(row)
            session.flush()
            return int(row.miss_id)
    except Exception as exc:  # noqa: BLE001 - telemetry must not break a reply
        logger.warning("Could not record content miss: %s", exc)
        return None