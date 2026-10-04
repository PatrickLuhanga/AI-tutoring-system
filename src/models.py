"""SQLAlchemy ORM models for the Hybrid Data Tier.

Relational Store (the "rulebook"):
    * ``students``            - DUT4life identities + role (student / tutor / admin)
    * ``modules``             - the four supported modules
    * ``enrollments``         - student <-> module mapping
    * ``tutor_assignments``   - tutor <-> module RBAC mapping
    * ``telemetry_logs``      - interaction logs, failure flags, hint depth
    * ``hint_feedback``       - thumbs up/down + reason tags

Vector Store (the "searchable library"):
    * ``curriculum_chunks``   - chunked course material + embedding
    * ``code_repair_patterns``- synthetic Java error corpus + embedding
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .config import settings

EMBEDDING_DIM: int = settings.embedding_dim

# Role / stage vocabularies (kept as plain strings so the schema stays portable
# and easy to evolve; enforced with CHECK constraints in the DDL below).
ROLE_VALUES = ("student", "tutor", "admin")

#: Roles a person can hold on a login account. ``lecturer`` is a superset of
#: ``tutor``: it carries the same module-scoped access to the help queue and adds
#: content authoring, uploads and announcements. ``admin`` is the System Admin and
#: is never available through self-signup.
ACCOUNT_ROLE_VALUES = ("student", "tutor", "lecturer", "admin")

#: Roles that may create their own account at signup. ``admin`` is included but
#: only with the shared bootstrap secret (``ADMIN_SIGNUP_SECRET``); see
#: :func:`src.accounts._guard_admin_signup`.
SELF_SIGNUP_ROLES = ("student", "tutor", "lecturer", "admin")

#: Roles that get an explicit per-module scope (``user_module_access``) rather
#: than being scoped by student enrolment.
STAFF_ROLES = ("tutor", "lecturer", "admin")

#: Roles that must pick the modules they teach at signup. A System Admin is
#: system-wide, so it is staff but is *not* module-scoped.
MODULE_SCOPED_ROLES = ("tutor", "lecturer")

#: Account lifecycle. ``suspended`` accounts keep their rows so audit history
#: stays intact but cannot log in.
ACCOUNT_STATUSES = ("active", "suspended")

#: Practice-question difficulty bands.
QUESTION_DIFFICULTIES = ("easy", "medium", "hard")

#: How a practice question came to exist.
QUESTION_ORIGINS = ("authored", "past_paper", "generated")

#: Who wrote a question's ``answer_notes``. See ``Question.answer_source``.
ANSWER_SOURCES = ("authored", "generated")

#: How well a chatbot turn was grounded in the module's material. See
#: :class:`ChatTurn`.
GROUNDING_LEVELS = ("grounded", "weak", "ungrounded")

#: Who a notification is addressed to.
NOTIFICATION_AUDIENCES = ("all", "module", "role", "user")

#: What an uploaded file is for.
UPLOAD_CATEGORIES = ("past_paper", "notes", "exercises")

#: Whether an uploaded note has been through ingestion yet.
UPLOAD_STATUSES = ("pending", "ingested", "failed")
SCAFFOLDING_STAGES = (
    "question",
    "hint",
    "student_attempt",
    "feedback",
    "further_guidance",
    "explanation",
    "direct_answer",
)
INTENT_VALUES = ("factual", "conceptual", "debugging", "problem_solving", "bypass", "other")
FEEDBACK_REASON_TAGS = (
    "too_confusing",
    "too_long_or_too_short",
    "gave_away_answer",
    "incorrect_answer",
)

#: Why a student question could not be grounded in course material. Drives the
#: tutor-facing queue of questions that need a human (or lecturer) answer.
UNGROUNDED_REASONS = (
    "no_context",            # nothing passed the module filter / distance floor
    "below_threshold",       # chunks found but none close enough to trust
    "third_party_only",      # only commercial material was available
    "low_confidence",        # tutor/guardrail judged the grounding weak
    "student_flagged",       # the student reported the answer as wrong
)

#: Lifecycle of a queued question.
UNGROUNDED_STATUSES = ("open", "answered", "dismissed", "duplicate")

#: Inference providers the Dynamic LLM Router can target.
LLM_PROVIDERS = ("local", "cloud")
#: Cloud API flavours understood by the router. ``groq`` is OpenAI-compatible:
#: it is served by the same ``/chat/completions`` path as ``openai`` but named
#: separately so its key (``GROQ_API_KEY``) and endpoint resolve unambiguously.
CLOUD_PROVIDERS = ("openai", "openai_compatible", "azure_openai", "anthropic", "groq")


def _enum_check(
    column: str,
    values: tuple[str, ...],
    name: str,
    *,
    nullable: bool = False,
) -> CheckConstraint:
    rendered = ", ".join(f"'{value}'" for value in values)
    if nullable:
        # A CHECK passes on NULL in SQL, so this only has to exclude values
        # outside the set; the NULL case is already allowed.
        return CheckConstraint(f"{column} IS NULL OR {column} IN ({rendered})", name=name)
    return CheckConstraint(f"{column} IN ({rendered})", name=name)


class Base(DeclarativeBase):
    """Declarative base for every data-tier table."""


# ---------------------------------------------------------------------------
# Relational store
# ---------------------------------------------------------------------------
class Student(Base):
    """An authenticated DUT4life identity.

    The architecture treats students as the primary population; tutors and
    admins are the same identity with an elevated ``role``. Tutor module access
    is always granted explicitly through :class:`TutorAssignment`.
    """

    __tablename__ = "students"

    student_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dut4life_email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(255))
    student_number: Mapped[Optional[str]] = mapped_column(String(32), unique=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, server_default="student")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    #: Stamped every time the directory identity opens the client (login telemetry).
    last_login_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    enrollments: Mapped[list["Enrollment"]] = relationship(
        back_populates="student", cascade="all, delete-orphan"
    )
    tutor_assignments: Mapped[list["TutorAssignment"]] = relationship(
        back_populates="tutor",
        cascade="all, delete-orphan",
        foreign_keys="TutorAssignment.tutor_id",
    )
    sessions: Mapped[list["StudentSession"]] = relationship(
        back_populates="student", cascade="all, delete-orphan"
    )

    __table_args__ = (
        _enum_check("role", ROLE_VALUES, "ck_students_role"),
        Index("ix_students_role", "role"),
    )


class Module(Base):
    """A supported academic module (e.g. IPRT301)."""

    __tablename__ = "modules"

    module_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    module_code: Mapped[str] = mapped_column(String(32), nullable=False)
    module_name: Mapped[str] = mapped_column(String(255), nullable=False)
    course_code: Mapped[Optional[str]] = mapped_column(String(32))
    language: Mapped[Optional[str]] = mapped_column(String(32))
    description: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    enrollments: Mapped[list["Enrollment"]] = relationship(
        back_populates="module", cascade="all, delete-orphan"
    )


class Enrollment(Base):
    """Student <-> module mapping used for module-scope validation."""

    __tablename__ = "enrollments"

    enrollment_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.student_id", ondelete="CASCADE"), nullable=False
    )
    module_id: Mapped[str] = mapped_column(
        ForeignKey("modules.module_id", ondelete="CASCADE"), nullable=False
    )
    academic_year: Mapped[str] = mapped_column(String(16), nullable=False, server_default="2026")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    enrolled_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    student: Mapped[Student] = relationship(back_populates="enrollments")
    module: Mapped[Module] = relationship(back_populates="enrollments")

    __table_args__ = (
        UniqueConstraint("student_id", "module_id", "academic_year", name="uq_enrollment"),
        Index("ix_enrollments_module", "module_id"),
    )


class TutorAssignment(Base):
    """Explicit tutor <-> module RBAC mapping (enforced at DB/query level)."""

    __tablename__ = "tutor_assignments"

    assignment_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tutor_id: Mapped[int] = mapped_column(
        ForeignKey("students.student_id", ondelete="CASCADE"), nullable=False
    )
    module_id: Mapped[str] = mapped_column(
        ForeignKey("modules.module_id", ondelete="CASCADE"), nullable=False
    )
    granted_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("students.student_id", ondelete="SET NULL")
    )
    granted_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    tutor: Mapped[Student] = relationship(
        back_populates="tutor_assignments", foreign_keys=[tutor_id]
    )
    module: Mapped[Module] = relationship()

    __table_args__ = (
        UniqueConstraint("tutor_id", "module_id", name="uq_tutor_module"),
        Index("ix_tutor_assignments_module", "module_id"),
    )


class StudentSession(Base):
    """A tutoring session: opened when a student logs in and starts a module chat.

    This is the relational anchor the history sidebar and the dashboards read for
    "who is active now" and "who keeps coming back". ``started_at`` doubles as the
    login/open timestamp; ``last_activity_at`` and ``turn_count`` are refreshed on
    every chat turn so the tutor dashboard can compute active students and repeat
    help requests without scanning the full telemetry history.
    """

    __tablename__ = "tutoring_sessions"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    student_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("students.student_id", ondelete="SET NULL")
    )
    # Snapshot of the identity so an unseeded directory still attributes activity.
    student_email: Mapped[Optional[str]] = mapped_column(String(255))
    module_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("modules.module_id", ondelete="SET NULL")
    )

    #: Short label for the LLM-style history sidebar, set from the first user
    #: message in the session.
    title: Mapped[Optional[str]] = mapped_column(String(255))

    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_activity_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    student: Mapped[Optional[Student]] = relationship(back_populates="sessions")
    messages: Mapped[list["SessionMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_sessions_student", "student_id"),
        Index("ix_sessions_module_time", "module_id", "started_at"),
        Index("ix_sessions_activity", "last_activity_at"),
    )


class SessionMessage(Base):
    """One persisted turn in a chat session (user question or tutor reply).

    Telemetry logs the agent internals; this table stores the actual
    conversation so the client can restore a past session and so a session can
    be deleted together with its messages.
    """

    __tablename__ = "session_messages"

    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("tutoring_sessions.session_id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    module_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("modules.module_id", ondelete="SET NULL")
    )
    #: The agent audit trail for assistant turns, restored into the audit panel.
    audit: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    # ``clock_timestamp`` (not ``now``) so user and assistant turns appended in
    # separate statements get distinct, orderable timestamps.
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )

    session: Mapped[StudentSession] = relationship(back_populates="messages")

    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="ck_session_messages_role"),
        Index("ix_session_messages_session", "session_id", "created_at"),
    )


class User(Base):
    """A login account for any of the four user types.

    Kept separate from :class:`Student` on purpose. ``students`` is the academic
    directory (DUT4life identity, enrolment, telemetry) and is still the subject
    of every teaching feature; ``users`` is the credential store. A student holds
    both, linked by :attr:`student_id`, while a tutor or lecturer may exist with
    no directory record at all. Self-signup writes here only.
    """

    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    #: Werkzeug scrypt hash. Never selected into API responses.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), nullable=False, server_default="student")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    #: Required for students, unique across accounts, and the 8-digit DUT number.
    student_number: Mapped[Optional[str]] = mapped_column(String(32), unique=True)
    #: Set when the account mirrors a row in ``students``.
    student_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("students.student_id", ondelete="SET NULL")
    )
    last_login_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    module_access: Mapped[list["UserModuleAccess"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        # `user_module_access` references `users` twice (who was granted access,
        # and who granted it), so the join has to be named explicitly.
        foreign_keys="UserModuleAccess.user_id",
    )

    __table_args__ = (
        _enum_check("role", ACCOUNT_ROLE_VALUES, "ck_users_role"),
        _enum_check("status", ACCOUNT_STATUSES, "ck_users_status"),
        Index("ix_users_role", "role"),
    )


class UserSession(Base):
    """A server-side login session, addressed by an opaque bearer token.

    Only the SHA-256 of the token is stored, so a database leak does not hand
    over live sessions. Storing sessions server-side (rather than as a JWT) is
    what makes logout, suspension and "sign out everywhere" immediate.
    """

    __tablename__ = "user_sessions"

    session_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[Optional[str]] = mapped_column(String(255))

    user: Mapped[User] = relationship(back_populates="sessions")

    __table_args__ = (Index("ix_user_sessions_user", "user_id"),)


class UserModuleAccess(Base):
    """Which modules a tutor or lecturer may see and manage.

    This is the account-level twin of :class:`TutorAssignment`: staff scope is
    granted explicitly at signup and enforced in SQL by
    :func:`src.unanswered.resolve_tutor_scope`, never in the frontend.
    """

    __tablename__ = "user_module_access"

    access_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    module_id: Mapped[str] = mapped_column(
        ForeignKey("modules.module_id", ondelete="CASCADE"), nullable=False
    )
    granted_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.user_id", ondelete="SET NULL")
    )
    granted_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[User] = relationship(
        back_populates="module_access", foreign_keys=[user_id]
    )
    module: Mapped[Module] = relationship()

    __table_args__ = (
        UniqueConstraint("user_id", "module_id", name="uq_user_module"),
        Index("ix_user_module_access_module", "module_id"),
    )


class TelemetryLog(Base):
    """One row per interaction step in the tutoring flow.

    Mirrors the "Telemetry & Feedback" area of the architecture diagram:
    interaction logs, failure flags, hint sequence depth and the retrieved
    context ids used to build the prompt.
    """

    __tablename__ = "telemetry_logs"

    log_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    message_id: Mapped[Optional[str]] = mapped_column(String(64))
    student_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("students.student_id", ondelete="SET NULL")
    )
    module_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("modules.module_id", ondelete="SET NULL")
    )

    intent: Mapped[str] = mapped_column(String(32), nullable=False, server_default="other")
    scaffolding_stage: Mapped[str] = mapped_column(String(32), nullable=False, server_default="hint")
    hint_sequence_depth: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    guardrail_flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    guardrail_failure_flags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    retrieved_curriculum_chunk_ids: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    retrieved_code_pattern_ids: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False, default=list
    )

    embedding_model: Mapped[Optional[str]] = mapped_column(String(128))
    response_latency_ms: Mapped[Optional[int]] = mapped_column(Integer)

    #: Provenance classes present in the prompt context (e.g. ``["slides"]``).
    #: Lets the Admin report a grounding rate in faculty-approved material
    #: against third-party material.
    retrieval_categories: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    #: True when the module's own material yielded nothing and the retriever
    #: widened the search to third-party content.
    third_party_fallback: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    #: True when the answer was generated from web results because the vector
    #: store returned no material. Surfaces how often the tutor leaves the KB.
    web_sourced: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        _enum_check("intent", INTENT_VALUES, "ck_telemetry_intent"),
        _enum_check("scaffolding_stage", SCAFFOLDING_STAGES, "ck_telemetry_stage"),
        Index("ix_telemetry_session", "session_id"),
        Index("ix_telemetry_module_time", "module_id", "created_at"),
        Index("ix_telemetry_guardrail", "guardrail_flagged"),
    )


class HintFeedback(Base):
    """Student rating of an AI hint (the ``hint_feedback`` table)."""

    __tablename__ = "hint_feedback"

    feedback_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    message_id: Mapped[str] = mapped_column(String(64), nullable=False)
    student_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("students.student_id", ondelete="SET NULL")
    )
    module_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("modules.module_id", ondelete="SET NULL")
    )
    rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    reason_tag: Mapped[Optional[str]] = mapped_column(String(32))
    comment: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("rating IN (-1, 1)", name="ck_hint_feedback_rating"),
        _enum_check("reason_tag", FEEDBACK_REASON_TAGS, "ck_hint_feedback_reason"),
        UniqueConstraint("session_id", "message_id", name="uq_hint_feedback_message"),
        Index("ix_hint_feedback_module_time", "module_id", "created_at"),
    )


class ChatTurn(Base):
    """One persisted chatbot exchange.

    :class:`TelemetryLog` deliberately records routing metadata only - it is the
    hot path and carries no text. That left a gap: a tutor reviewing how their
    module is being used could only see questions RAG had *failed* on, because
    :class:`UnansweredQuestion` is the sole table holding what a student actually
    asked. A student confused by a well-grounded but unhelpful answer left no
    trace at all, which is the other half of what the Human Adjustment Cycle is
    supposed to surface.

    This table closes that gap by storing every turn, grounded or not. It is
    deliberately separate from the tutor queue: ``unanswered_questions`` is a
    short worklist of real curriculum gaps to fix, while this is the record of
    what happened. A row here is not a request for attention.

    Retention is the operational concern. This is students' conversation content,
    so it is the most sensitive thing the application stores.
    """

    __tablename__ = "chat_turns"

    turn_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    message_id: Mapped[Optional[str]] = mapped_column(String(64))

    #: The login account, when there was one. Separate from ``student_id`` so a
    #: tutor's own exploration of the system is attributable to them too.
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.user_id", ondelete="SET NULL")
    )
    student_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("students.student_id", ondelete="SET NULL"), index=True
    )
    module_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("modules.module_id", ondelete="CASCADE"), index=True
    )

    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    answer_text: Mapped[Optional[str]] = mapped_column(Text)

    intent: Mapped[Optional[str]] = mapped_column(String(32))
    #: ``grounded`` | ``weak`` | ``ungrounded`` - see ``GROUNDING_LEVELS``.
    grounding: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ungrounded")
    #: Chunks cited for this turn, for "which material is actually being used".
    cited_chunk_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    guardrail_flagged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    #: Whether the turn was a bypass attempt, which is a teaching signal rather
    #: than a curriculum gap and is excluded from the tutor's normal view.
    was_bypass: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    response_latency_ms: Mapped[Optional[int]] = mapped_column(Integer)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    __table_args__ = (
        _enum_check("grounding", GROUNDING_LEVELS, "ck_chat_turns_grounding"),
        Index("ix_chat_turns_module_created", "module_id", "created_at"),
    )


class UnansweredQuestion(Base):
    """A student question the tutor could not ground in course material.

    Closes the loop described in section 17 (Human Adjustment Cycle): when
    retrieval returns nothing trustworthy, the question is queued here instead of
    being answered from the model's parametric memory. A tutor - escalating to the
    module lecturer when needed - supplies the answer, and can promote it into
    ``curriculum_chunks`` so later students are answered from real course material.

    Repeated identical questions increment ``occurrences`` rather than adding rows,
    so the queue is ranked by demand instead of by noise.
    """

    __tablename__ = "unanswered_questions"

    question_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    module_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("modules.module_id", ondelete="CASCADE")
    )
    student_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("students.student_id", ondelete="SET NULL")
    )

    session_id: Mapped[Optional[str]] = mapped_column(String(64))
    message_id: Mapped[Optional[str]] = mapped_column(String(64))

    #: The student's own words. Telemetry deliberately stays lean, so this is
    #: where the raw question text is preserved for the tutor to read.
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    #: Normalised form of the question, used to collapse duplicates.
    question_key: Mapped[str] = mapped_column(String(64), nullable=False)

    intent: Mapped[Optional[str]] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="no_context"
    )
    #: Closest cosine distance seen while searching; ``None`` when nothing matched.
    best_distance: Mapped[Optional[float]] = mapped_column(Float)
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="open")
    answer_text: Mapped[Optional[str]] = mapped_column(Text)
    answered_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("students.student_id", ondelete="SET NULL")
    )
    answered_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    #: Set once the answer has been written into ``curriculum_chunks``.
    promoted_chunk_id: Mapped[Optional[int]] = mapped_column(BigInteger)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        _enum_check("reason", UNGROUNDED_REASONS, "ck_unanswered_reason"),
        _enum_check("status", UNGROUNDED_STATUSES, "ck_unanswered_status"),
        # One open row per distinct question per module; duplicates bump the count.
        UniqueConstraint("module_id", "question_key", name="uq_unanswered_open"),
        Index("ix_unanswered_status_module", "status", "module_id"),
        Index("ix_unanswered_module_time", "module_id", "created_at"),
    )


class ContentMiss(Base):
    """Lecturer telemetry: a question the module's own material could not answer.

    Distinct from :class:`UnansweredQuestion` (a tutor worklist). A *content
    miss* is recorded every time retrieval returns nothing and the turn leaves
    the knowledge base - whether that is because the web fallback answered, or
    because nothing was found anywhere. It is the signal a lecturer needs to see
    which topics their ingested notes do not cover, so they can fill the gap.

    One row per turn (not deduplicated), because the *rate* of misses per topic
    over time is the useful measure; aggregation happens at read time.
    """

    __tablename__ = "content_misses"

    miss_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    message_id: Mapped[Optional[str]] = mapped_column(String(64))
    student_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("students.student_id", ondelete="SET NULL")
    )
    module_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("modules.module_id", ondelete="SET NULL")
    )
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    #: True when the web fallback actually supplied context for the reply.
    web_used: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    #: How many web results contributed (0 when the web found nothing too).
    web_result_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: Domains that contributed, for "where did this answer come from".
    domains: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_content_misses_module_time", "module_id", "created_at"),
        Index("ix_content_misses_web", "web_used"),
    )


# ---------------------------------------------------------------------------
# Vector store
# ---------------------------------------------------------------------------
class CurriculumChunk(Base):
    """A chunk of official course material plus its embedding.

    ``module_id`` is the metadata filter applied before semantic search so that
    material from other modules never leaks into a response.
    """

    __tablename__ = "curriculum_chunks"

    chunk_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    module_id: Mapped[str] = mapped_column(
        ForeignKey("modules.module_id", ondelete="CASCADE"), nullable=False
    )

    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    topic: Mapped[Optional[str]] = mapped_column(String(255))
    section_title: Mapped[Optional[str]] = mapped_column(String(512))

    #: Provenance label derived from the second-level content folder
    #: (``slides`` / ``books`` / ``exercises`` / ``examples`` / ...). Lets the
    #: retriever prefer the module's own teaching material and lets analytics
    #: report a grounding rate per provenance class.
    source_category: Mapped[str] = mapped_column(
        String(32), nullable=False, default="notes", server_default="notes"
    )

    #: True when this chunk contains a worked answer, model solution or marking
    #: guidance. Withheld by the retriever until the Scaffolding Engine reaches the
    #: Explanation stage so retrieval cannot hand the model a finished solution.
    is_answer: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[Optional[int]] = mapped_column(Integer)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64))

    doc_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    embedding_model: Mapped[Optional[str]] = mapped_column(String(128))
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(EMBEDDING_DIM))

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("source_file", "chunk_index", name="uq_curriculum_chunk"),
        Index("ix_curriculum_chunks_module", "module_id"),
        Index("ix_curriculum_chunks_source", "source_file"),
        # Supports the Socratic-stage and provenance filters the retriever applies
        # before the similarity search.
        Index("ix_curriculum_chunks_module_answer", "module_id", "is_answer"),
        Index("ix_curriculum_chunks_module_category", "module_id", "source_category"),
    )


class CodeRepairPattern(Base):
    """A synthetic code-repair pattern (broken code -> error -> tutor hint)."""

    __tablename__ = "code_repair_patterns"

    pattern_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    module_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("modules.module_id", ondelete="SET NULL")
    )

    language: Mapped[str] = mapped_column(String(32), nullable=False, server_default="Java")
    error_title: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    error_category: Mapped[Optional[str]] = mapped_column(String(64))
    exception_thrown: Mapped[Optional[str]] = mapped_column(String(255))

    broken_code: Mapped[str] = mapped_column(Text, nullable=False)
    conceptual_tutor_hint: Mapped[str] = mapped_column(Text, nullable=False)
    common_cause: Mapped[Optional[str]] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    difficulty: Mapped[Optional[str]] = mapped_column(String(16))

    embedding_model: Mapped[Optional[str]] = mapped_column(String(128))
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(EMBEDDING_DIM))

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_code_patterns_module", "module_id"),
        Index("ix_code_patterns_exception", "exception_thrown"),
    )


# ---------------------------------------------------------------------------
# Orchestration-tier configuration (Tier 2)
# ---------------------------------------------------------------------------
class Question(Base):
    """One question in the staff-authored practice bank.

    Sourced either by hand from a lecturer, by importing an uploaded past paper,
    or generated by a staff member from a section of the notes. A practice test
    samples a random subset of the active questions for a module, so the bank is
    what makes a test reproducible and reviewable.
    """

    __tablename__ = "questions"

    question_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    module_id: Mapped[str] = mapped_column(
        ForeignKey("modules.module_id", ondelete="CASCADE"), nullable=False
    )
    #: Who authored it. Nullable only if a system import creates one.
    created_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.user_id", ondelete="SET NULL")
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    #: Marking notes or the expected answer. Never sent to the student until the
    #: attempt is submitted.
    answer_notes: Mapped[Optional[str]] = mapped_column(Text)
    #: Who wrote ``answer_notes``, which decides whether it may be auto-graded.
    #:
    #: ``authored`` - a lecturer wrote it. This is the only kind that is safe to
    #: mark against automatically, because a human set it as the expected answer.
    #:
    #: ``generated`` - the local model drafted it. Shown to the student as a
    #: reference, and deliberately **not** auto-graded. Auto-marking is exact
    #: match, so a drafted answer that is subtly wrong would mark a correct
    #: student response as wrong - penalising the student for the bank's
    #: inaccuracy. The student self-assesses against it instead.
    #:
    #: NULL means there is no answer, and the question is excluded from the score.
    answer_source: Mapped[Optional[str]] = mapped_column(String(16))
    #: ``easy`` | ``medium`` | ``hard``
    difficulty: Mapped[str] = mapped_column(String(16), nullable=False, server_default="medium")
    #: Where it came from: ``authored`` | ``past_paper`` | ``generated``
    origin: Mapped[str] = mapped_column(String(16), nullable=False, server_default="authored")
    source_label: Mapped[Optional[str]] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    module: Mapped[Module] = relationship()

    __table_args__ = (
        _enum_check("difficulty", QUESTION_DIFFICULTIES, "ck_questions_difficulty"),
        _enum_check("origin", QUESTION_ORIGINS, "ck_questions_origin"),
        _enum_check(
            "answer_source",
            ANSWER_SOURCES,
            "ck_questions_answer_source",
            nullable=True,
        ),
        Index("ix_questions_module_active", "module_id", "is_active"),
    )


class PracticeAttempt(Base):
    """One student's run through a randomised practice test."""

    __tablename__ = "practice_attempts"

    attempt_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    student_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("students.student_id", ondelete="CASCADE")
    )
    #: The signed-in account, when there is one.
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.user_id", ondelete="SET NULL")
    )
    module_id: Mapped[str] = mapped_column(
        ForeignKey("modules.module_id", ondelete="CASCADE"), nullable=False
    )
    #: JSON array of the question ids sampled, so the attempt can be re-rendered.
    question_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    #: JSON object of question_id -> the student's answer.
    answers: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    score: Mapped[Optional[int]] = mapped_column(Integer)
    max_score: Mapped[Optional[int]] = mapped_column(Integer)
    submitted_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    module: Mapped[Module] = relationship()

    __table_args__ = (Index("ix_practice_attempts_module", "module_id"),)


class Notification(Base):
    """A message from staff to users.

    ``audience`` decides who receives it: ``all`` reaches every active account,
    ``module`` everyone scoped to ``module_id``, ``role`` every holder of
    ``role``, and ``user`` the single ``user_id``. Keeping the audience on the row
    (rather than fanning out into a per-recipient inbox) keeps sending cheap; the
    read receipt lives in :class:`NotificationRead`.
    """

    __tablename__ = "notifications"

    notification_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.user_id", ondelete="SET NULL")
    )
    audience: Mapped[str] = mapped_column(String(16), nullable=False, server_default="all")
    role: Mapped[Optional[str]] = mapped_column(String(16))
    module_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("modules.module_id", ondelete="CASCADE")
    )
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        _enum_check("audience", NOTIFICATION_AUDIENCES, "ck_notifications_audience"),
        Index("ix_notifications_created", "created_at"),
    )


class NotificationRead(Base):
    """Per-account read receipt for a :class:`Notification`."""

    __tablename__ = "notification_reads"

    notification_id: Mapped[int] = mapped_column(
        ForeignKey("notifications.notification_id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True
    )
    read_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UploadedDocument(Base):
    """A file a tutor or lecturer uploaded into a module.

    Covers two jobs: a **past paper** whose questions can be imported into the
    practice bank, and a **note** that is handed to the ingestion pipeline so it
    becomes retrievable course material. The bytes live on disk under
    ``ACADEMIC_CONTENT_DIR``; this row is the index.
    """

    __tablename__ = "uploaded_documents"

    document_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    module_id: Mapped[str] = mapped_column(
        ForeignKey("modules.module_id", ondelete="CASCADE"), nullable=False
    )
    uploaded_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.user_id", ondelete="SET NULL")
    )
    #: ``past_paper`` | ``notes`` | ``exercises``
    category: Mapped[str] = mapped_column(String(16), nullable=False, server_default="notes")
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Path relative to the content root, so ingestion finds it like any other file.
    stored_path: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[Optional[str]] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    #: ``pending`` | ``ingested`` | ``failed`` - whether the notes pipeline ran.
    ingest_status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    #: Human-readable outcome of processing - how many chunks were indexed, how
    #: many questions were recovered, or why it failed. Shown in the Content
    #: console so "pending" is never the final word on an upload.
    ingest_detail: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    module: Mapped[Module] = relationship()

    __table_args__ = (
        _enum_check("category", UPLOAD_CATEGORIES, "ck_uploaded_documents_category"),
        _enum_check("ingest_status", UPLOAD_STATUSES, "ck_uploaded_documents_status"),
        Index("ix_uploaded_documents_module", "module_id"),
    )


class LLMConfig(Base):
    """The active inference configuration read by the Dynamic LLM Router.

    The architecture requires the generative model to be swappable without
    touching the pipeline (architecture document, section 10.2). Instead of
    editing ``.env`` and restarting, an admin flips the ``provider`` between
    ``local`` (Ollama) and ``cloud`` (an OpenAI-compatible or Anthropic API)
    through ``POST /api/admin/llm-config``. The cloud API key is stored
    **encrypted at rest** in :attr:`api_key_encrypted`; it is never returned by
    the API.

    Multiple rows may exist for history/auditing, but exactly one row should be
    flagged ``is_active`` - that is the row the router reads before every
    generation.
    """

    __tablename__ = "llm_configs"

    config_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, default="default")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    # ``local`` -> Ollama, ``cloud`` -> hosted API.
    provider: Mapped[str] = mapped_column(String(16), nullable=False, server_default="local")

    # Local (Ollama)
    ollama_base_url: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default="http://localhost:11434"
    )
    local_model: Mapped[str] = mapped_column(String(128), nullable=False, server_default="qwen3:4b")

    # Cloud
    cloud_provider: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="openai"
    )
    cloud_base_url: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default="https://api.openai.com/v1"
    )
    cloud_model: Mapped[str] = mapped_column(
        String(128), nullable=False, server_default="gpt-4o-mini"
    )

    # The API key is encrypted before it reaches this column.
    api_key_encrypted: Mapped[Optional[str]] = mapped_column(Text)
    # Last 4 characters kept in clear so the UI can render ``sk-...abcd``.
    api_key_last4: Mapped[Optional[str]] = mapped_column(String(8))

    # Generation parameters
    temperature: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.4")
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1024")
    top_p: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.9")

    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    updated_by: Mapped[Optional[str]] = mapped_column(String(255))

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        _enum_check("provider", LLM_PROVIDERS, "ck_llm_configs_provider"),
        _enum_check("cloud_provider", CLOUD_PROVIDERS, "ck_llm_configs_cloud_provider"),
        Index("ix_llm_configs_active", "is_active"),
    )
