"""Database engine / session helpers.

A single SQLAlchemy engine is shared by every script in the data tier. The
``session_scope`` context manager handles commit / rollback / close so callers
never leak connections.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from .config import settings

logger = logging.getLogger(__name__)

# PostgreSQL-only: give every connection a server-side statement budget so a
# runaway query is cancelled instead of holding a pool slot and a worker
# forever. Other backends (e.g. SQLite in tests) don't understand ``options``.
_connect_args: dict = {}
if make_url(settings.database_url).drivername.startswith("postgres"):
    _connect_args["options"] = (
        f"-c statement_timeout={int(settings.db_statement_timeout_ms)}"
    )

engine: Engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    future=True,
    connect_args=_connect_args,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
    future=True,
)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transactional scope around a series of operations."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_engine() -> Engine:
    return engine


def ensure_database_exists() -> bool:
    """Create the target database if it does not exist yet.

    Connects to the maintenance ``postgres`` database with psycopg2 and issues
    ``CREATE DATABASE``. Returns ``True`` when the database already existed.
    Requires the configured role to have CREATEDB privileges (the docker-compose
    role does).
    """
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    url = make_url(settings.database_url)
    target_db = url.database
    assert target_db, "DATABASE_URL must include a database name"

    try:
        conn = psycopg2.connect(
            host=url.host or "localhost",
            port=url.port or 5432,
            user=url.username,
            password=url.password,
            dbname="postgres",
        )
    except psycopg2.OperationalError as exc:
        raise RuntimeError(
            "Could not connect to PostgreSQL. Is the server running and are the "
            "credentials in .env correct?"
        ) from exc

    try:
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target_db,))
            exists = cur.fetchone() is not None
            if not exists:
                logger.info("Creating database %s", target_db)
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_db)))
            return exists
    finally:
        conn.close()


def check_connection() -> str:
    """Return the PostgreSQL server version, raising on failure."""
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version()")).scalar_one()
    return str(version)
