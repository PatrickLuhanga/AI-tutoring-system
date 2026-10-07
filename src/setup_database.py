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

from sqlalchemy import delete, func, inspect, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .db import engine, ensure_database_exists
from .models import (
    ANSWER_SOURCES,
    INTENT_VALUES,
    SCAFFOLDING_STAGES,
    Base,
    LLMConfig,
    Module,
    User,
    UserModuleAccess,
)
from .config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
logger = logging.getLogger("setup_database")

#: Staff accounts whose module scope is pinned in code, so every environment
#: agrees on what a role may see (navbar pill, API scoping and dashboards all
#: read ``user_module_access``). Matched on the login account only; the role
#: guard keeps a same-named student from being caught by the name.
#:
#: Sbusiso Luhanga is a RESK301 lecturer. IPRT301 must never attach to the
#: account, so the scope is declared as the full set and reconciled below.
STAFF_MODULE_SCOPE: tuple[dict[str, object], ...] = (
    {
        "email": "sbuda@dut.ac.za",
        "full_name": "Sbusiso Luhanga",
        "role": "lecturer",
        "modules": ("RESK301",),
    },
)


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


def add_missing_columns() -> None:
    """Add columns that ``create_all`` cannot, because it never alters a table.

    Every statement is ``IF NOT EXISTS``, so this is safe to run on every setup.
    A database created before a column existed simply gains it, with no data
    rewritten and no rows dropped.
    """
    statements = [
        # Questions can carry a reference answer now, and answer_source records
        # whether a human wrote it. NULL means "no answer", which excludes the
        # question from a score rather than marking it wrong.
        "ALTER TABLE questions ADD COLUMN IF NOT EXISTS answer_source VARCHAR(16)",
        # Records the outcome of processing an upload, so the Content console can
        # say what happened instead of leaving every row at "pending".
        "ALTER TABLE uploaded_documents ADD COLUMN IF NOT EXISTS ingest_detail TEXT",
        # Web fallback telemetry: was this reply generated from web results
        # because the vector store returned no material.
        "ALTER TABLE telemetry_logs ADD COLUMN IF NOT EXISTS web_sourced "
        "BOOLEAN NOT NULL DEFAULT false",
    ]
    try:
        with engine.begin() as conn:
            for sql in statements:
                conn.execute(text(sql))
        logger.info("Added missing columns (%d)", len(statements))
    except Exception as exc:  # noqa: BLE001 - setup should not fail on a missing table
        logger.warning("Could not add missing columns: %s", exc)

    # The CHECK constraint has to be (re)created separately: adding the column
    # does not add the constraint, and create_all will not revisit an existing
    # table to notice.
    try:
        source_values = ", ".join(f"'{value}'" for value in ANSWER_SOURCES)
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE questions DROP CONSTRAINT IF EXISTS ck_questions_answer_source"))
            conn.execute(
                text(
                    "ALTER TABLE questions ADD CONSTRAINT ck_questions_answer_source "
                    f"CHECK (answer_source IS NULL OR answer_source IN ({source_values}))"
                )
            )
        logger.info("Question answer_source constraint synced")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not sync the answer_source constraint: %s", exc)


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


def create_secondary_indexes() -> None:
    statements = [
        "CREATE INDEX IF NOT EXISTS idx_curriculum_chunks_metadata ON curriculum_chunks USING gin (doc_metadata jsonb_path_ops)",
        "CREATE INDEX IF NOT EXISTS idx_code_patterns_tags ON code_repair_patterns USING gin (tags)",
        "CREATE INDEX IF NOT EXISTS idx_curriculum_chunks_text_trgm ON curriculum_chunks USING gin (chunk_text gin_trgm_ops)",
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


def seed_staff_module_scope() -> int:
    """Pin known staff accounts to their exact modules (idempotent).

    Reconciles ``user_module_access`` to the declared set: any module not in
    :data:`STAFF_MODULE_SCOPE` (e.g. a stale IPRT301 grant) is removed, and every
    declared module is (re)inserted. Safe to run on every setup.
    """
    pinned = 0
    try:
        with engine.begin() as conn:
            for entry in STAFF_MODULE_SCOPE:
                user_id = conn.execute(
                    select(User.user_id).where(
                        User.role == entry["role"],
                        or_(
                            func.lower(User.email) == str(entry["email"]).lower(),
                            func.lower(User.full_name) == str(entry["full_name"]).lower(),
                        ),
                    ).limit(1)
                ).scalar_one_or_none()
                if user_id is None:
                    continue
                user_id = int(user_id)
                conn.execute(
                    delete(UserModuleAccess).where(UserModuleAccess.user_id == user_id)
                )
                for module_id in entry["modules"]:
                    conn.execute(
                        pg_insert(UserModuleAccess)
                        .values(user_id=user_id, module_id=module_id)
                        .on_conflict_do_nothing(constraint="uq_user_module")
                    )
                pinned += 1
    except Exception as exc:  # noqa: BLE001 - setup should not fail on a missing table
        logger.warning("Could not pin staff module scope: %s", exc)
    if pinned:
        logger.info("Pinned module scope for %d staff account(s)", pinned)
    return pinned


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
    args = parser.parse_args(argv)

    ensure_database_exists()
    create_extension()
    create_tables(drop=args.drop)
    add_missing_columns()
    sync_enum_constraints()
    create_vector_indexes()
    create_secondary_indexes()
    if not args.skip_modules:
        seed_modules()
    seed_llm_config()
    seed_staff_module_scope()
    print_summary()
    logger.info("Database schema initialisation complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
