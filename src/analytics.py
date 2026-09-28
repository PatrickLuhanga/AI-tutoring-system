"""Read-only analytics aggregates for the tutor and admin dashboards.

All queries are module-scoped: the tutor endpoints only ever see the modules the
tutor is assigned to (``tutor_assignments``), while the admin endpoints see the
whole registry. Everything is wrapped in a single transactional scope and
degrades to an empty payload rather than raising, so a Data Tier hiccup shows an
empty dashboard instead of a 500.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

from sqlalchemy import func, select, text

from .db import session_scope
from .models import (
    Enrollment,
    HintFeedback,
    Module,
    Student,
    TelemetryLog,
    TutorAssignment,
)

logger = logging.getLogger(__name__)

FAILURE_LABELS = {
    "too_confusing": "Still stuck",
    "too_long_or_too_short": "Too long / too short",
    "gave_away_answer": "Gave away answer",
    "incorrect_answer": "Incorrect answer",
}

#: An interaction counts as a "struggle" when it was flagged, was a debugging /
#: problem-solving turn, or needed a second+ hint in the same session.
_STRUGGLE_PREDICATE = (
    "(t.guardrail_flagged = true "
    "OR t.intent IN ('debugging', 'problem_solving') "
    "OR t.hint_sequence_depth >= 2)"
)


def _all_module_ids() -> list[str]:
    with session_scope() as session:
        return [str(row) for row in session.execute(select(Module.module_id)).scalars().all()]


def tutor_analytics(module_ids: list[str], window_minutes: int = 60) -> dict:
    """Scoped analytics for a tutor's assigned modules."""
    module_ids = [m for m in module_ids if m]
    generated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    payload = {
        "scope": [],
        "window_minutes": int(window_minutes),
        "active_students": 0,
        "total_sessions": 0,
        "total_queries": 0,
        "avg_hint_depth": 0.0,
        "guardrail_flags": 0,
        "struggle_topics": [],
        "repeat_help_students": [],
        "generated_at": generated_at,
    }
    if not module_ids:
        return payload

    params = {"modules": module_ids, "window": int(window_minutes)}
    try:
        with session_scope() as session:
            scope_rows = session.execute(
                select(Module.module_id, Module.module_name).where(
                    Module.module_id.in_(module_ids)
                )
            ).all()
            payload["scope"] = [
                {"module_id": mid, "module_name": name} for mid, name in scope_rows
            ]

            # --- Active students (session heartbeat within the window) -----
            payload["active_students"] = int(
                session.execute(
                    text(
                        """
                        SELECT COUNT(DISTINCT COALESCE(student_id::text, student_email))
                        FROM tutoring_sessions
                        WHERE module_id = ANY(:modules)
                          AND last_activity_at >= now() - make_interval(mins => :window)
                        """
                    ),
                    params,
                ).scalar_one()
                or 0
            )

            # --- Query / scaffolding totals --------------------------------
            totals = session.execute(
                text(
                    """
                    SELECT
                        COUNT(DISTINCT session_id) AS sessions,
                        COUNT(*) AS queries,
                        COALESCE(AVG(hint_sequence_depth), 0) AS avg_depth,
                        COUNT(*) FILTER (WHERE guardrail_flagged) AS flags
                    FROM telemetry_logs
                    WHERE module_id = ANY(:modules)
                      AND created_at >= now() - make_interval(mins => :window)
                    """
                ),
                params,
            ).one()
            payload["total_sessions"] = int(totals.sessions or 0)
            payload["total_queries"] = int(totals.queries or 0)
            payload["avg_hint_depth"] = round(float(totals.avg_depth or 0), 2)
            payload["guardrail_flags"] = int(totals.flags or 0)

            # --- Struggle topics (retrieved curriculum section) ------------
            topic_rows = session.execute(
                text(
                    f"""
                    SELECT
                        t.module_id AS module_id,
                        COALESCE(c.section_title, c.topic, 'General') AS topic,
                        COUNT(*) AS struggles,
                        COUNT(DISTINCT COALESCE(t.student_id::text, ts.student_email)) AS students
                    FROM telemetry_logs t
                    JOIN curriculum_chunks c
                      ON c.chunk_id IN (
                            SELECT (jsonb_array_elements_text(
                                COALESCE(t.retrieved_curriculum_chunk_ids, '[]'::jsonb)
                            ))::bigint
                         )
                    LEFT JOIN tutoring_sessions ts ON ts.session_id = t.session_id
                    WHERE t.module_id = ANY(:modules)
                      AND t.created_at >= now() - make_interval(mins => :window)
                      AND {_STRUGGLE_PREDICATE}
                    GROUP BY t.module_id, COALESCE(c.section_title, c.topic, 'General')
                    ORDER BY struggles DESC
                    LIMIT 12
                    """
                ),
                params,
            ).all()
            payload["struggle_topics"] = [
                {
                    "module_id": row.module_id,
                    "topic": row.topic,
                    "struggles": int(row.struggles),
                    "students": int(row.students or 0),
                }
                for row in topic_rows
            ]

            # --- Repeat help requests --------------------------------------
            repeat_rows = session.execute(
                text(
                    """
                    SELECT
                        t.student_id AS student_id,
                        COALESCE(s.dut4life_email, ts.student_email) AS email,
                        t.module_id AS module_id,
                        COUNT(DISTINCT t.session_id) AS sessions,
                        COUNT(*) AS turns,
                        COALESCE(SUM(CASE WHEN hf.rating = -1 THEN 1 ELSE 0 END), 0) AS thumbs_down
                    FROM telemetry_logs t
                    LEFT JOIN students s ON s.student_id = t.student_id
                    LEFT JOIN tutoring_sessions ts ON ts.session_id = t.session_id
                    LEFT JOIN hint_feedback hf
                      ON hf.session_id = t.session_id AND hf.message_id = t.message_id
                    WHERE t.module_id = ANY(:modules)
                      AND t.created_at >= now() - make_interval(mins => :window)
                    GROUP BY t.student_id,
                             COALESCE(s.dut4life_email, ts.student_email),
                             t.module_id
                    HAVING COUNT(DISTINCT t.session_id) >= 2
                        OR COUNT(*) >= 4
                        OR COALESCE(SUM(CASE WHEN hf.rating = -1 THEN 1 ELSE 0 END), 0) >= 1
                    ORDER BY thumbs_down DESC, turns DESC
                    LIMIT 20
                    """
                ),
                params,
            ).all()
            payload["repeat_help_students"] = [
                {
                    "student_id": row.student_id,
                    "email": row.email,
                    "module_id": row.module_id,
                    "sessions": int(row.sessions or 0),
                    "turns": int(row.turns or 0),
                    "thumbs_down": int(row.thumbs_down or 0),
                }
                for row in repeat_rows
            ]
    except Exception as exc:  # noqa: BLE001 - dashboard must not 500 on DB issues
        logger.warning("Tutor analytics unavailable; returning empty payload: %s", exc)

    return payload


def admin_analytics() -> dict:
    """System-wide telemetry aggregates (mirrors ``TelemetryAnalytics``)."""
    try:
        with session_scope() as session:
            sessions = int(
                session.execute(select(func.count(func.distinct(TelemetryLog.session_id)))).scalar_one()
                or 0
            )
            hints = int(session.execute(select(func.count(TelemetryLog.log_id))).scalar_one() or 0)
            ups = int(
                session.execute(
                    select(func.count(HintFeedback.feedback_id)).where(HintFeedback.rating == 1)
                ).scalar_one()
                or 0
            )
            downs = int(
                session.execute(
                    select(func.count(HintFeedback.feedback_id)).where(HintFeedback.rating == -1)
                ).scalar_one()
                or 0
            )
            latency = session.execute(
                select(func.coalesce(func.avg(TelemetryLog.response_latency_ms), 0))
            ).scalar_one()

            failure_rows = session.execute(
                select(HintFeedback.reason_tag, func.count(HintFeedback.feedback_id))
                .where(HintFeedback.rating == -1, HintFeedback.reason_tag.is_not(None))
                .group_by(HintFeedback.reason_tag)
            ).all()

            # Count responses and feedback per module separately, then merge, to
            # avoid a cartesian product between the two joined tables.
            response_rows = dict(
                session.execute(
                    select(TelemetryLog.module_id, func.count(TelemetryLog.log_id))
                    .where(TelemetryLog.module_id.is_not(None))
                    .group_by(TelemetryLog.module_id)
                ).all()
            )
            feedback_rows = session.execute(
                select(
                    HintFeedback.module_id,
                    func.count(HintFeedback.feedback_id).filter(HintFeedback.rating == 1),
                    func.count(HintFeedback.feedback_id).filter(HintFeedback.rating == -1),
                )
                .where(HintFeedback.module_id.is_not(None))
                .group_by(HintFeedback.module_id)
            ).all()
            modules = session.execute(
                select(Module.module_id, Module.module_name).order_by(Module.module_id)
            ).all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Admin analytics unavailable; returning empty payload: %s", exc)
        return {
            "total_sessions": 0,
            "total_hints": 0,
            "satisfaction": {"thumbs_up": 0, "thumbs_down": 0},
            "failure_categories": [],
            "by_module": [],
            "average_latency_ms": 0,
        }

    feedback_map = {
        mid: (int(module_ups or 0), int(module_downs or 0))
        for mid, module_ups, module_downs in feedback_rows
    }
    by_module = []
    for module_id, module_name in modules:
        module_ups, module_downs = feedback_map.get(module_id, (0, 0))
        by_module.append(
            {
                "module_id": module_id,
                "module_name": module_name,
                "responses": int(response_rows.get(module_id, 0) or 0),
                "satisfaction_rate": round(
                    module_ups / max(1, module_ups + module_downs), 4
                ),
            }
        )

    return {
        "total_sessions": sessions,
        "total_hints": hints,
        "satisfaction": {"thumbs_up": ups, "thumbs_down": downs},
        "failure_categories": [
            {
                "reason_tag": tag,
                "label": FAILURE_LABELS.get(str(tag), str(tag)),
                "count": int(count or 0),
            }
            for tag, count in failure_rows
        ],
        "by_module": by_module,
        "average_latency_ms": int(latency or 0),
    }


def admin_overview(window_minutes: int = 60) -> dict:
    """Users, role counts and guardrail flag totals for the admin dashboard."""
    payload = {
        "users": [],
        "guardrail_flags": [],
        "total_students": 0,
        "total_tutors": 0,
        "total_admins": 0,
        "active_sessions": 0,
    }
    try:
        with session_scope() as session:
            students = session.execute(
                select(Student).order_by(Student.role, Student.dut4life_email)
            ).scalars().all()

            enrollments: dict[int, list[str]] = {}
            for student_id, module_id in session.execute(
                select(Enrollment.student_id, Enrollment.module_id)
            ).all():
                enrollments.setdefault(int(student_id), []).append(str(module_id))

            assignments: dict[int, list[str]] = {}
            for tutor_id, module_id in session.execute(
                select(TutorAssignment.tutor_id, TutorAssignment.module_id)
            ).all():
                assignments.setdefault(int(tutor_id), []).append(str(module_id))

            flag_rows = session.execute(
                text(
                    """
                    SELECT flag, COUNT(*) AS count FROM (
                        SELECT jsonb_array_elements_text(guardrail_failure_flags) AS flag
                        FROM telemetry_logs
                        WHERE guardrail_flagged = true
                    ) f
                    GROUP BY flag
                    ORDER BY count DESC
                    """
                )
            ).all()

            active = int(
                session.execute(
                    text(
                        """
                        SELECT COUNT(*) FROM tutoring_sessions
                        WHERE last_activity_at >= now() - make_interval(mins => :window)
                        """
                    ),
                    {"window": int(window_minutes)},
                ).scalar_one()
                or 0
            )

        users = []
        for student in students:
            sid = int(student.student_id)
            is_tutor = bool(student.is_tutor) or student.role == "tutor"
            tutor_modules = assignments.get(sid, [])
            enrolled_modules = enrollments.get(sid, [])
            modules = list(dict.fromkeys(tutor_modules + enrolled_modules))
            if student.role == "admin":
                roles = ["admin"]
            else:
                roles = ["student"] + (["tutor"] if is_tutor else [])
            users.append(
                {
                    "student_id": sid,
                    "email": student.dut4life_email,
                    "full_name": student.full_name,
                    "student_number": student.student_number,
                    "role": student.role,
                    "roles": roles,
                    "is_tutor": is_tutor,
                    "is_active": bool(student.is_active),
                    "modules": modules,
                    "tutor_modules": tutor_modules,
                    "enrolled_modules": enrolled_modules,
                    "last_login_at": student.last_login_at.isoformat()
                    if student.last_login_at
                    else None,
                }
            )

        payload.update(
            {
                "users": users,
                "guardrail_flags": [
                    {"flag": flag, "count": int(count or 0)} for flag, count in flag_rows
                ],
                "total_students": sum(1 for u in users if u["role"] == "student"),
                "total_tutors": sum(1 for u in users if u["is_tutor"]),
                "total_admins": sum(1 for u in users if u["role"] == "admin"),
                "active_sessions": active,
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Admin overview unavailable; returning empty payload: %s", exc)

    return payload


def resolve_scope(identity, requested: Optional[list[str]] = None) -> list[str]:
    """Resolve the effective module scope for a tutor/admin analytics request.

    * Admins see the whole registry (or just the modules they explicitly asked
      for).
    * Tutors are clamped to their ``tutor_assignments``; a requested module is
      honoured only when it is a subset of that scope.
    * An unassigned tutor in dev mode falls back to the requested modules so the
      prototype stays usable before the directory is seeded.
    """
    from .sessions import assigned_modules  # local import avoids a cycle at import time

    requested = [m for m in (requested or []) if m]

    if getattr(identity, "role", None) == "admin":
        return requested or _all_module_ids()

    assigned = assigned_modules(getattr(identity, "student_id", None))
    if requested:
        narrowed = [m for m in requested if m in assigned] if assigned else requested
        return narrowed or assigned
    return assigned
