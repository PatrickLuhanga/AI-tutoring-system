"""Initialise the Hybrid Data Tier schema.

Creates the ``vector`` extension, every relational table, the pgvector
similarity indexes, the JSONB indexes and seeds the four modules.

Usage:
    python -m src.setup_database
    python -m src.setup_database --drop          # destructive: recreate everything
"""

from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import func, inspect, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .db import engine, ensure_database_exists
from .models import (
    INTENT_VALUES,
    SCAFFOLDING_STAGES,
    Base,
    Enrollment,
    LLMConfig,
    Module,
    Student,
    TutorAssignment,
)
from .config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
logger = logging.getLogger("setup_database")


def create_extension() -> None:
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    logger.info("Extensions ensured: vector, pg_trgm")


def create_tables(drop: bool = False) -> None:
    if drop:
        logger.warning("Dropping all data-tier tables (--drop)")
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    logger.info("Tables ensured (%d)", len(Base.metadata.tables))


def sync_enum_constraints() -> None:
    """Bring the telemetry CHECK constraints in line with the current vocabulary.

    ``create_all`` never alters an existing table, so a database created before
    the two-track routing refactor still carries the old intent/stage vocabulary
    and rejects the new ``factual`` intent and ``direct_answer`` stage at write
    time. Dropping and re-adding the two constraints widens the allowed set
    without rewriting any rows, so it is safe to run against live data.
    """
    intent_values = ", ".join(f"'{value}'" for value in INTENT_VALUES)
    stage_values = ", ".join(f"'{value}'" for value in SCAFFOLDING_STAGES)
    statements = [
        "ALTER TABLE telemetry_logs DROP CONSTRAINT IF EXISTS ck_telemetry_intent",
        f"ALTER TABLE telemetry_logs ADD CONSTRAINT ck_telemetry_intent "
        f"CHECK (intent IN ({intent_values}))",
        "ALTER TABLE telemetry_logs DROP CONSTRAINT IF EXISTS ck_telemetry_stage",
        f"ALTER TABLE telemetry_logs ADD CONSTRAINT ck_telemetry_stage "
        f"CHECK (scaffolding_stage IN ({stage_values}))",
    ]
    try:
        with engine.begin() as conn:
            for sql in statements:
                conn.execute(text(sql))
        logger.info("Telemetry CHECK constraints synced (intent, scaffolding_stage)")
    except Exception as exc:  # noqa: BLE001 - setup should not fail on a missing table
        logger.warning("Could not sync telemetry CHECK constraints: %s", exc)


def create_vector_indexes() -> None:
    """Create HNSW indexes (falling back to IVFFlat when HNSW is unavailable)."""
    dim_ops = "vector_cosine_ops"
    index_type = settings.vector_index_type

    statements = []
    if index_type == "ivfflat":
        statements = [
            (
                "idx_curriculum_chunks_embedding",
                "curriculum_chunks",
                "embedding",
                f"WITH (lists = {settings.vector_index_lists})",
            ),
            (
                "idx_code_patterns_embedding",
                "code_repair_patterns",
                "embedding",
                f"WITH (lists = {settings.vector_index_lists})",
            ),
        ]
    else:
        statements = [
            (
                "idx_curriculum_chunks_embedding",
                "curriculum_chunks",
                "embedding",
                f"WITH (m = {settings.vector_index_m}, ef_construction = {settings.vector_index_ef_construction})",
            ),
            (
                "idx_code_patterns_embedding",
                "code_repair_patterns",
                "embedding",
                f"WITH (m = {settings.vector_index_m}, ef_construction = {settings.vector_index_ef_construction})",
            ),
        ]

    with engine.begin() as conn:
        for name, table, column, options in statements:
            sql = (
                f"CREATE INDEX IF NOT EXISTS {name} ON {table} "
                f"USING {index_type} ({column} {dim_ops}) {options}"
            )
            try:
                conn.execute(text(sql))
                logger.info("Vector index ready: %s (%s)", name, index_type)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not create %s index %s (%s); trying ivfflat", index_type, name, exc)
                fallback = (
                    f"CREATE INDEX IF NOT EXISTS {name} ON {table} "
                    f"USING ivfflat ({column} {dim_ops}) "
                    f"WITH (lists = {settings.vector_index_lists})"
                )
                conn.execute(text(fallback))
                logger.info("Vector index ready: %s (ivfflat fallback)", name)


def ensure_hybrid_search_columns() -> None:
    """Add the generated ``content_tsv`` columns to pre-existing tables.

    ``create_all`` never alters an existing table, so a database created before
    the hybrid-search port lacks the generated tsvector columns. Adding them
    here rewrites the table and backfills the full-text index for every row.
    On a fresh install ``create_all`` already created them and these statements
    are no-ops.
    """
    statements = [
        "ALTER TABLE curriculum_chunks "
        "ADD COLUMN IF NOT EXISTS content_tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, chunk_text)) STORED",
        "ALTER TABLE code_repair_patterns "
        "ADD COLUMN IF NOT EXISTS content_tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, "
        "(error_title || ' ' || broken_code || ' ' || conceptual_tutor_hint))) STORED",
    ]
    try:
        with engine.begin() as conn:
            for sql in statements:
                conn.execute(text(sql))
        logger.info("Hybrid-search tsvector columns ensured (curriculum_chunks, code_repair_patterns)")
    except Exception as exc:  # noqa: BLE001 - schema upgrade should not block startup
        logger.warning("Could not ensure hybrid-search tsvector columns: %s", exc)


def create_secondary_indexes() -> None:
    statements = [
        "CREATE INDEX IF NOT EXISTS idx_curriculum_chunks_metadata ON curriculum_chunks USING gin (doc_metadata jsonb_path_ops)",
        "CREATE INDEX IF NOT EXISTS idx_code_patterns_tags ON code_repair_patterns USING gin (tags)",
        "CREATE INDEX IF NOT EXISTS idx_curriculum_chunks_text_trgm ON curriculum_chunks USING gin (chunk_text gin_trgm_ops)",
        "CREATE INDEX IF NOT EXISTS idx_curriculum_chunks_tsv ON curriculum_chunks USING gin (content_tsv)",
        "CREATE INDEX IF NOT EXISTS idx_code_patterns_tsv ON code_repair_patterns USING gin (content_tsv)",
    ]
    with engine.begin() as conn:
        for sql in statements:
            try:
                conn.execute(text(sql))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skipped index (%s): %s", exc, sql)


def seed_modules() -> int:
    """Upsert the module registry so every module_id has a parent row."""
    rows = [
        {
            "module_id": module["module_id"],
            "module_code": module.get("module_code", module["module_id"]),
            "module_name": module["module_name"],
            "course_code": module.get("course_code"),
            "language": module.get("language"),
            "is_active": True,
        }
        for module in settings.modules.values()
    ]
    if not rows:
        return 0

    with engine.begin() as conn:
        stmt = pg_insert(Module.__table__).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Module.__table__.c.module_id],
            set_={
                "module_code": stmt.excluded.module_code,
                "module_name": stmt.excluded.module_name,
                "course_code": stmt.excluded.course_code,
                "language": stmt.excluded.language,
            },
        )
        conn.execute(stmt)
    logger.info("Seeded %d modules", len(rows))
    return len(rows)


def ensure_student_columns() -> None:
    """Add columns that predate the current model to a pre-existing ``students``.

    ``create_all`` never alters an existing table, so databases created before
    login telemetry was added lack ``students.last_login_at``.
    """
    statements = [
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS last_login_at timestamptz",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS is_tutor boolean NOT NULL DEFAULT false",
        "CREATE INDEX IF NOT EXISTS ix_students_is_tutor ON students (is_tutor)",
    ]
    try:
        with engine.begin() as conn:
            for sql in statements:
                conn.execute(text(sql))
        logger.info("Student columns ensured (last_login_at, is_tutor)")
    except Exception as exc:  # noqa: BLE001 - schema upgrade should not block startup
        logger.warning("Could not ensure student columns: %s", exc)


def ensure_session_columns() -> None:
    """Add chat-history columns to a pre-existing ``tutoring_sessions`` table.

    The ``session_messages`` table itself is created by ``create_all`` (it is
    new); only the two new columns on the existing sessions table need an
    explicit ``ALTER``.
    """
    statements = [
        "ALTER TABLE tutoring_sessions ADD COLUMN IF NOT EXISTS title varchar(255)",
        "ALTER TABLE tutoring_sessions ADD COLUMN IF NOT EXISTS message_count integer NOT NULL DEFAULT 0",
    ]
    try:
        with engine.begin() as conn:
            for sql in statements:
                conn.execute(text(sql))
        logger.info("Session columns ensured (title, message_count)")
    except Exception as exc:  # noqa: BLE001 - schema upgrade should not block startup
        logger.warning("Could not ensure session columns: %s", exc)


#: Demo identities seeded for local development. Real users are provisioned
#: dynamically on first DUT4life login (see ``src/profiles.py``); these rows just
#: bootstrap a student, a dual-role tutor and the admin allowlist account.
DUMMY_USERS = [
    {
        "dut4life_email": "student@dut4life.ac.za",
        "full_name": "Sanele Ndlovu",
        "student_number": "22000000",
        "role": "student",
        "is_tutor": False,
        "modules": ["IPRT301", "PBDV301"],
    },
    {
        "dut4life_email": "tutor.dev@dut4life.ac.za",
        "full_name": "Dr. Thabo Mokoena",
        "student_number": None,
        "role": "tutor",
        "is_tutor": True,
        "modules": ["IPRT301", "PBDV301"],
    },
    {
        "dut4life_email": "tutor.research@dut4life.ac.za",
        "full_name": "Prof. Naledi Khumalo",
        "student_number": None,
        "role": "tutor",
        "is_tutor": True,
        "modules": ["RESK301", "SPRI301"],
    },
    {
        "dut4life_email": "admin.system@dut4life.ac.za",
        "full_name": "System Administrator",
        "student_number": None,
        "role": "admin",
        "is_tutor": False,
        "modules": [],
    },
]


def seed_users() -> int:
    """Upsert the demo identities, their enrollments and tutor assignments.

    Idempotent: re-running updates names/roles and re-asserts module scope
    without duplicating rows.
    """
    with engine.begin() as conn:
        for user in DUMMY_USERS:
            conn.execute(
                pg_insert(Student.__table__)
                .values(
                    dut4life_email=user["dut4life_email"],
                    full_name=user["full_name"],
                    student_number=user["student_number"],
                    role=user["role"],
                    is_tutor=user["is_tutor"],
                    is_active=True,
                )
                .on_conflict_do_update(
                    index_elements=[Student.__table__.c.dut4life_email],
                    set_={
                        "full_name": user["full_name"],
                        "student_number": user["student_number"],
                        "role": user["role"],
                        "is_tutor": user["is_tutor"],
                        "is_active": True,
                    },
                )
            )

            student_id = conn.execute(
                select(Student.student_id).where(Student.dut4life_email == user["dut4life_email"])
            ).scalar_one()

            for module_id in user["modules"]:
                if user["role"] == "student":
                    conn.execute(
                        pg_insert(Enrollment.__table__)
                        .values(
                            student_id=student_id,
                            module_id=module_id,
                            academic_year="2026",
                            is_active=True,
                        )
                        .on_conflict_do_update(
                            constraint="uq_enrollment",
                            set_={"is_active": True},
                        )
                    )
                if user["is_tutor"]:
                    conn.execute(
                        pg_insert(TutorAssignment.__table__)
                        .values(tutor_id=student_id, module_id=module_id)
                        .on_conflict_do_nothing(constraint="uq_tutor_module")
                    )
    logger.info("Seeded %d demo identities", len(DUMMY_USERS))
    return len(DUMMY_USERS)


def seed_llm_config() -> int:
    """Insert the default LLM routing config if none exists (Tier 2).

    Existing rows are left untouched so an admin's runtime changes survive a
    re-run of this script.
    """
    with engine.begin() as conn:
        existing = conn.execute(select(func.count()).select_from(LLMConfig)).scalar_one()
        if existing:
            logger.info("LLM config already present (%d row(s)); leaving untouched", existing)
            return 0
        conn.execute(
            pg_insert(LLMConfig).values(
                name="default",
                is_active=True,
                provider=settings.default_llm_provider,
                ollama_base_url=settings.ollama_base_url,
                local_model=settings.default_local_model,
                cloud_provider=settings.default_cloud_provider,
                cloud_base_url=settings.default_cloud_base_url,
                cloud_model=settings.default_cloud_model,
                temperature=settings.llm_temperature,
                max_tokens=settings.llm_max_tokens,
                top_p=settings.llm_top_p,
                updated_by="setup_database",
            )
        )
    logger.info("Seeded default LLM configuration (provider=%s)", settings.default_llm_provider)
    return 1


def print_summary() -> None:
    inspector = inspect(engine)
    logger.info("Tables in %s:", settings.db_schema)
    with engine.connect() as conn:
        for table in sorted(inspector.get_table_names(schema=settings.db_schema)):
            count = conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one()
            logger.info("  %-24s %8d rows", table, count)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Initialise the Hybrid Data Tier schema.")
    parser.add_argument(
        "--drop",
        action="store_true",
        help="Drop all data-tier tables before recreating them (DESTRUCTIVE).",
    )
    parser.add_argument(
        "--skip-modules",
        action="store_true",
        help="Do not seed the module registry.",
    )
    parser.add_argument(
        "--skip-users",
        action="store_true",
        help="Do not seed the Dummy Data Login identities.",
    )
    args = parser.parse_args(argv)

    ensure_database_exists()
    create_extension()
    create_tables(drop=args.drop)
    sync_enum_constraints()
    ensure_hybrid_search_columns()
    ensure_student_columns()
    ensure_session_columns()
    create_vector_indexes()
    create_secondary_indexes()
    if not args.skip_modules:
        seed_modules()
    if not args.skip_users:
        seed_users()
    seed_llm_config()
    print_summary()
    logger.info("Database schema initialisation complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
