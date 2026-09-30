"""Announcements from staff, and each account's read state.

A notification stores its *audience* rather than fanning out into a per-recipient
inbox. Staff usually send to a module or to everyone, so copying a row per
recipient would cost a large insert for no benefit, and the audience on the row
stays editable - changing "IPRT301" to "everyone" afterwards is one update.

:meth:`list_for_user` is the only read path, and it filters in SQL. A client
cannot widen its own audience by asking for a different module.
"""

from __future__ import annotations

import logging
from typing import Optional, Sequence

from sqlalchemy import or_, select

from .db import session_scope
from .models import (
    NOTIFICATION_AUDIENCES,
    Notification,
    NotificationRead,
    User,
    UserModuleAccess,
)

logger = logging.getLogger(__name__)


class NotificationError(Exception):
    """A send request that cannot be satisfied."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def send(
    *,
    title: object,
    body: object,
    audience: object = "all",
    created_by: Optional[int] = None,
    role: object = None,
    module_id: object = None,
    user_id: object = None,
) -> dict:
    """Publish a notification.

    ``audience`` is ``all`` | ``module`` | ``role`` | ``user``. The extra fields
    required by the narrower audiences are validated here so a malformed send
    fails loudly instead of quietly reaching nobody.
    """
    heading = str(title or "").strip()
    text = str(body or "").strip()
    if not heading:
        raise NotificationError("A notification needs a title.")
    if not text:
        raise NotificationError("A notification needs a message body.")
    if len(heading) > 160:
        raise NotificationError("Title must be 160 characters or fewer.")

    target = str(audience or "all").strip().lower()
    if target not in NOTIFICATION_AUDIENCES:
        raise NotificationError(f"Audience must be one of {list(NOTIFICATION_AUDIENCES)}.")

    module: Optional[str] = None
    role_value: Optional[str] = None
    recipient: Optional[int] = None

    if target == "module":
        module = str(module_id or "").strip().upper()
        if not module:
            raise NotificationError("A module notification needs a module_id.")
    elif target == "role":
        role_value = str(role or "").strip().lower()
        if role_value not in {"student", "tutor", "lecturer", "admin"}:
            raise NotificationError("A role notification needs a valid role.")
    elif target == "user":
        try:
            recipient = int(user_id)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise NotificationError("A direct notification needs a user_id.") from None
        if not _user_exists(recipient):
            raise NotificationError("That user does not exist.", status_code=404)

    with session_scope() as session:
        row = Notification(
            created_by=created_by,
            audience=target,
            role=role_value,
            module_id=module,
            user_id=recipient,
            title=heading,
            body=text,
        )
        session.add(row)
        session.flush()
        result = _to_dict(row, read=False)

    logger.info(
        "Notification %s sent by %s to audience=%s module=%s role=%s",
        result["notification_id"],
        created_by,
        target,
        module,
        role_value,
    )
    return result


def _user_exists(user_id: int) -> bool:
    with session_scope() as session:
        return session.get(User, int(user_id)) is not None


def _resolve_modules(user_id: Optional[int], role: str, module_ids: Sequence[str]) -> list[str]:
    """The modules a notification audience should be matched against.

    A tutor or lecturer carries ``user_module_access``. A **student** does not -
    their modules come from ``enrollments`` - so without this a module
    announcement would reach teaching staff and never a single student, which is
    precisely the case the announcement exists for. Enrollment is read live
    rather than copied into ``user_module_access`` so it cannot go stale when a
    student is added to or removed from a module.
    """
    resolved = [str(m).strip().upper() for m in (module_ids or []) if str(m).strip()]
    if resolved or role != "student" or user_id is None:
        return list(dict.fromkeys(resolved))

    from .models import Enrollment, User

    try:
        with session_scope() as session:
            # A student account links to its directory row via `users.student_id`,
            # set at signup by accounts._link_student_record.
            student_id = session.execute(
                select(User.student_id).where(User.user_id == int(user_id)).limit(1)
            ).scalar_one_or_none()
            if student_id is None:
                return []
            rows = session.execute(
                select(Enrollment.module_id).where(
                    Enrollment.student_id == int(student_id),
                    Enrollment.is_active.is_(True),
                )
            ).scalars()
            return sorted({str(r).strip().upper() for r in rows if str(r).strip()})
    except Exception as exc:  # noqa: BLE001 - a missing scope must not 500 the inbox
        logger.warning("Could not resolve enrolled modules for user %s: %s", user_id, exc)
        return []


def list_for_user(
    user_id: int,
    *,
    role: str,
    module_ids: Sequence[str] = (),
    limit: int = 50,
) -> list[dict]:
    """Notifications addressed to this account, newest first.

    The predicate is: sent to everyone, OR to this account's role, OR to a module
    this account belongs to, OR sent to this account directly.
    """
    modules = _resolve_modules(user_id, role, module_ids)

    clauses = [
        Notification.audience == "all",
        Notification.user_id == int(user_id),
    ]
    role_clause = (Notification.audience == "role") & (Notification.role == str(role))
    clauses.append(role_clause)
    if modules:
        module_clause = (Notification.audience == "module") & (
            Notification.module_id.in_(modules)
        )
        clauses.append(module_clause)

    with session_scope() as session:
        read_ids = set(
            session.execute(
                select(NotificationRead.notification_id).where(
                    NotificationRead.user_id == int(user_id)
                )
            ).scalars()
        )
        rows = session.execute(
            select(Notification)
            .where(or_(*clauses))
            .order_by(Notification.created_at.desc())
            .limit(limit)
        ).scalars()
        return [_to_dict(r, read=r.notification_id in read_ids) for r in rows]


def mark_read(*, user_id: int, notification_ids: Sequence[int]) -> int:
    """Record read receipts. Idempotent, so a double-click is harmless."""
    ids = [int(n) for n in notification_ids]
    if not ids:
        return 0
    with session_scope() as session:
        existing = set(
            session.execute(
                select(NotificationRead.notification_id).where(
                    NotificationRead.user_id == int(user_id),
                    NotificationRead.notification_id.in_(ids),
                )
            ).scalars()
        )
        added = 0
        for nid in ids:
            if nid not in existing:
                session.add(NotificationRead(notification_id=nid, user_id=int(user_id)))
                added += 1
    return added


def unread_count(*, user_id: int, role: str, module_ids: Sequence[str] = ()) -> int:
    return sum(1 for n in list_for_user(user_id, role=role, module_ids=module_ids) if not n["is_read"])


def _to_dict(row: Notification, *, read: bool) -> dict:
    return {
        "notification_id": int(row.notification_id),
        "title": row.title,
        "body": row.body,
        "audience": row.audience,
        "role": row.role,
        "module_id": row.module_id,
        "is_read": bool(read),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
