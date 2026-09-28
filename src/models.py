"""SQLAlchemy ORM models for the Hybrid Data Tier.

Relational Store (the "rulebook"):
    * ``students``            - DUT4life identities + role + dual-role ``is_tutor``
    * ``modules``             - the four supported modules
    * ``enrollments``         - student <-> module mapping
    * ``tutor_assignments``   - tutor <-> module RBAC mapping
    * ``tutoring_sessions``   - one row per chat session (module, title, activity)
    * ``session_messages``    - persisted user/tutor turns for chat history
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
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .config import settings

EMBEDDING_DIM: int = settings.embedding_dim

# Role / stage vocabularies (kept as plain strings so the schema stays portable
# and easy to evolve; enforced with CHECK constraints in the DDL below).
ROLE_VALUES = ("student", "tutor", "admin")
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

#: Inference providers the Dynamic LLM Router can target.
LLM_PROVIDERS = ("local", "cloud")
#: Cloud API flavours understood by the router.
CLOUD_PROVIDERS = ("openai", "openai_compatible", "azure_openai", "anthropic")


def _enum_check(column: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    rendered = ", ".join(f"'{value}'" for value in values)
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
    #: Base/primary role: ``student`` or ``admin``. A legacy ``tutor`` value is
    #: treated as a student with tutor privileges (see :attr:`is_tutor`).
    role: Mapped[str] = mapped_column(String(16), nullable=False, server_default="student")
    #: Dual-role flag: an admin can grant tutor privileges to any registered
    #: user, so a student can hold the tutor capability at the same time.
    is_tutor: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    #: Stamped every time the identity opens the client (login telemetry).
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
        Index("ix_students_is_tutor", "is_tutor"),
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

    This is the relational anchor the dashboards read for "who is active now" and
    "who keeps coming back". ``started_at`` doubles as the login/open timestamp;
    ``last_activity_at`` and ``turn_count`` are refreshed on every chat turn so
    the tutor dashboard can compute active students and repeat help requests
    without scanning the full telemetry history.
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

    # Phase 2/3: the ``module › topic › heading path`` breadcrumb and its
    # segments, promoted out of ``doc_metadata`` so they are directly queryable.
    breadcrumb: Mapped[Optional[str]] = mapped_column(String(512))
    heading_path: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    chunk_strategy: Mapped[Optional[str]] = mapped_column(String(32))

    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Generated full-text vector feeding the keyword branch of hybrid search
    # (see src/retriever.py). ``simple`` config: language-agnostic, no stemming.
    content_tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple'::regconfig, chunk_text)", persisted=True),
        nullable=True,
    )
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
    # Generated full-text vector over the pattern's searchable prose + code,
    # mirroring curriculum_chunks.content_tsv.
    content_tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('simple'::regconfig, "
            "(error_title || ' ' || broken_code || ' ' || conceptual_tutor_hint))",
            persisted=True,
        ),
        nullable=True,
    )
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
