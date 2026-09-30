"""Shared fixtures for the test suite.

Two things are expensive and process-wide: the Flask app, and the sentence
transformer behind every embedding. Building either per-test would dominate the
run, so both are session-scoped here.

Tests that need an ingested corpus are skipped rather than failed when the
vector store is empty, because "no data ingested" is a state of the machine, not
a defect in the code.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def app():
    from src.app import create_app

    application = create_app()
    application.config.update(TESTING=True)
    return application


@pytest.fixture(scope="session")
def client(app):
    return app.test_client()


@pytest.fixture(scope="session")
def settings():
    from src.config import settings as _settings

    return _settings


@pytest.fixture(scope="session")
def embedder():
    """Skip rather than download a model if embeddings are unavailable."""
    from src.embeddings import get_embedder

    try:
        return get_embedder()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"embedding model unavailable: {exc}")


@pytest.fixture(scope="session")
def corpus_loaded(embedder):
    """True when curriculum chunks are present, so retrieval assertions are meaningful."""
    from sqlalchemy import func, select

    from src.db import session_scope
    from src.models import CurriculumChunk

    try:
        with session_scope() as session:
            count = session.execute(
                select(func.count()).select_from(CurriculumChunk)
            ).scalar_one()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"database unavailable: {exc}")
    if not count:
        pytest.skip("no curriculum chunks ingested; run the ingestion scripts first")
    return count


@pytest.fixture(scope="session")
def retriever():
    """A retriever bound to the configured ceiling, for threshold tests."""
    from src.retriever import Retriever

    return Retriever()


@pytest.fixture(scope="session")
def patterns_loaded(embedder):
    from sqlalchemy import func, select

    from src.db import session_scope
    from src.models import CodeRepairPattern

    try:
        with session_scope() as session:
            count = session.execute(
                select(func.count()).select_from(CodeRepairPattern)
            ).scalar_one()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"database unavailable: {exc}")
    if not count:
        pytest.skip("no code-repair patterns ingested; run src.ingest_code_patterns")
    return count


def find_javac() -> str | None:
    """Locate a JDK.

    Not on PATH on this machine, but the JetBrains runtimes bundled with Android
    Studio and PyCharm both ship a full javac.
    """
    from shutil import which

    found = which("javac")
    if found:
        return found
    for path in (
        r"C:\Program Files\Android\Android Studio\jbr\bin\javac.exe",
        r"C:\Program Files\JetBrains\PyCharm 2026.2\jbr\bin\javac.exe",
        "/usr/bin/javac",
        "/Library/Java/JavaVirtualMachines/*/Contents/Home/bin/javac",
    ):
        if "*" in path:
            import glob

            for match in sorted(glob.glob(path)):
                if os.path.isfile(match):
                    return match
        elif os.path.isfile(path):
            return path
    return None
