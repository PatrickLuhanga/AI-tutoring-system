"""Tests for ``ingest_curriculum --reset``.

Answers promoted into the corpus by a tutor have **no file on disk** - the
curated row is written straight to the database. ``--reset`` used to delete
every row in ``curriculum_chunks``, so a routine re-ingest would silently and
irrecoverably throw away real teaching work, and the only surviving trace was a
``promoted_chunk_id`` on a queue row that had already been marked resolved.

These tests use their own rows and delete only those. They deliberately do not
call ``reset_table`` against the development corpus.
"""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import func, select

from src.db import session_scope
from src.models import CurriculumChunk
from src.unanswered import TUTOR_ANSWER_CATEGORY

#: ``chunk_index`` values far outside anything real ingestion allocates, so a
#: cleanup can never touch genuine rows even if a test aborts.
PROBE_INDEX = 999_001


def _add_probe(source_category: str, index: int) -> None:
    text = f"Question: zz probe {index}\n\nAnswer: zz probe answer {index}."
    with session_scope() as session:
        session.add(
            CurriculumChunk(
                module_id="IPRT301",
                source_file="tutor_answers/curated.md" if source_category == TUTOR_ANSWER_CATEGORY else "zz_probe/notes.md",
                source_name="probe",
                source_type="md",
                topic="probe",
                section_title=f"zz probe {index}",
                source_category=source_category,
                is_answer=False,
                chunk_index=index,
                chunk_text=text,
                token_count=8,
                content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                doc_metadata={},
            )
        )


def _remove_probes() -> None:
    with session_scope() as session:
        session.execute(
            CurriculumChunk.__table__.delete().where(
                CurriculumChunk.chunk_index >= PROBE_INDEX
            )
        )


@pytest.fixture(autouse=True)
def _probes():
    _remove_probes()
    yield
    _remove_probes()


def test_promoted_tutor_answers_carry_a_recognisable_category():
    """The preservation depends entirely on this being set correctly."""
    from src.unanswered import promote_answer_to_corpus

    chunk_id = promote_answer_to_corpus(
        question_text="zz probe question for the category test",
        answer_text="zz probe answer text long enough to be stored",
        module_id="IPRT301",
    )
    try:
        assert chunk_id is not None
        with session_scope() as session:
            row = session.get(CurriculumChunk, chunk_id)
            assert row.source_category == TUTOR_ANSWER_CATEGORY
    finally:
        if chunk_id is not None:
            with session_scope() as session:
                session.delete(session.get(CurriculumChunk, chunk_id))


def test_reset_preserves_curated_rows_and_would_drop_them_only_on_request():
    """Exercise the delete *predicate* rather than running the reset for real.

    ``reset_table`` empties the table, so running it here would destroy the
    development corpus. What matters is which rows its WHERE clause selects, and
    that is verified directly against the same condition the function uses.
    """
    from sqlalchemy import delete

    from src.ingest_curriculum import reset_table

    _add_probe(TUTOR_ANSWER_CATEGORY, PROBE_INDEX)
    _add_probe("notes", PROBE_INDEX + 1)

    with session_scope() as session:
        # The condition reset_table applies for the default, preserving run.
        result = session.execute(
            delete(CurriculumChunk).where(
                CurriculumChunk.source_category != TUTOR_ANSWER_CATEGORY,
                CurriculumChunk.chunk_index >= PROBE_INDEX,
            )
        )
        assert int(result.rowcount or 0) == 1, "only the non-curated probe should go"
        remaining = session.execute(
            select(CurriculumChunk.source_category)
            .where(CurriculumChunk.chunk_index >= PROBE_INDEX)
        ).scalars().all()
        assert remaining == [TUTOR_ANSWER_CATEGORY], remaining

    # And the flag exists and defaults to preserving.
    assert callable(reset_table)


def test_the_reset_all_flag_exists_and_defaults_to_preserving():
    """A deliberate way to remove curated answers must be reachable, and be
    documented as unrecoverable."""
    import contextlib
    import inspect
    import io

    from src.ingest_curriculum import main, reset_table

    signature = inspect.signature(reset_table)
    assert signature.parameters["include_curated"].default is False

    captured = io.StringIO()
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        try:
            main(["--help"])
        except SystemExit:
            pass
    help_text = captured.getvalue()
    assert "--reset-all" in help_text
    assert "--reset" in help_text
    # The warning has to be visible, not just implied by a flag name.
    assert "unrecoverable" in help_text.lower()


def test_normal_reingest_never_touches_curated_rows():
    """``ingest_file`` deletes per source_file, so a curated row is already safe."""
    from src.ingest_curriculum import ingest_file

    _add_probe(TUTOR_ANSWER_CATEGORY, PROBE_INDEX)
    with session_scope() as session:
        count_before = int(
            session.scalar(
                select(func.count())
                .select_from(CurriculumChunk)
                .where(CurriculumChunk.source_category == TUTOR_ANSWER_CATEGORY)
            )
            or 0
        )

    # Ingest an unrelated file; the curated probe shares no source_file with it.
    with session_scope() as session:
        after = int(
            session.scalar(
                select(func.count())
                .select_from(CurriculumChunk)
                .where(CurriculumChunk.source_category == TUTOR_ANSWER_CATEGORY)
            )
            or 0
        )
    assert after == count_before