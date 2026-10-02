"""Tests for processing stored uploads.

An upload used to be a dead end: the file was written, a row was created with
``ingest_status='pending'``, and nothing in the codebase ever moved that status
again. The Content page reported "pending" forever and the chatbot could not
answer from anything a lecturer had just uploaded.

The behaviour pinned here is that an upload *ends up somewhere*.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from src.db import session_scope
from src.ingest_uploads import (
    INDEXABLE_CATEGORIES,
    pending_uploads,
    process_all,
    process_upload,
)
from src.models import CurriculumChunk, UploadedDocument
from src.uploads import content_root, store_upload

NOTES = (
    b"# Tutorial notes on the observer pattern\n\n"
    b"The observer pattern defines a one-to-many dependency between objects, so\n"
    b"that when one object changes state all of its dependents are notified.\n\n"
    b"## Participants\n\n"
    b"- Subject maintains the list of observers and notifies them on a change.\n"
    b"- Observer defines the update interface that dependents must implement.\n"
    b"- ConcreteObserver implements it to keep derived state consistent.\n\n"
    b"## Trade-offs\n\n"
    b"Notifying every observer can be expensive when there are many of them, and\n"
    b"a leaked observer reference keeps the subject object alive.\n"
)


@pytest.fixture
def upload():
    """A stored note, fully removed afterwards (row, chunks and file)."""
    marker = f"zz_upload_{uuid.uuid4().hex[:10]}"
    doc = store_upload(
        module_id="IPRT301",
        category="notes",
        filename=f"{marker}.md",
        data=NOTES,
        uploaded_by=None,
        content_type="text/markdown",
    )
    yield doc

    rel = doc["stored_path"]
    with session_scope() as session:
        session.execute(
            CurriculumChunk.__table__.delete().where(CurriculumChunk.source_file == rel)
        )
        row = session.get(UploadedDocument, doc["document_id"])
        if row is not None:
            session.delete(row)
    (Path(content_root()) / rel).unlink(missing_ok=True)


def _purge_chunks(rel: str) -> int:
    with session_scope() as session:
        result = session.execute(
            CurriculumChunk.__table__.delete().where(CurriculumChunk.source_file == rel)
        )
        return int(result.rowcount or 0)


def test_an_uploaded_note_is_indexed_and_marked_ingested(upload):
    """The whole point: the upload reaches the corpus."""
    pending_before = {p["document_id"] for p in pending_uploads()}
    assert upload["document_id"] in pending_before
    assert upload["ingest_status"] == "pending"

    result = process_upload(
        {
            "document_id": upload["document_id"],
            "module_id": upload["module_id"],
            "category": upload["category"],
            "original_name": upload["original_name"],
            "stored_path": upload["stored_path"],
            "ingest_status": "pending",
        }
    )

    assert result["status"] == "ingested", result
    assert result["chunks"] > 0

    with session_scope() as session:
        row = session.get(UploadedDocument, upload["document_id"])
        assert row.ingest_status == "ingested"
        assert row.ingest_detail and "chunk" in row.ingest_detail
        chunks = session.execute(
            select(CurriculumChunk).where(CurriculumChunk.source_file == upload["stored_path"])
        ).scalars().all()
        assert len(chunks) > 0
        assert all(c.module_id == "IPRT301" for c in chunks)


def test_the_row_leaves_the_pending_queue_once_processed(upload):
    process_all()
    still_pending = {p["document_id"] for p in pending_uploads()}
    assert upload["document_id"] not in still_pending


def test_processing_is_idempotent(upload):
    """A second pass must not double the corpus or rewrite a finished row."""
    process_upload(_row(upload))
    with session_scope() as session:
        first = session.execute(
            select(CurriculumChunk).where(CurriculumChunk.source_file == upload["stored_path"])
        ).scalars().all()

    result = process_upload(_row(upload))
    assert result.get("skipped") is True

    with session_scope() as session:
        second = session.execute(
            select(CurriculumChunk).where(CurriculumChunk.source_file == upload["stored_path"])
        ).scalars().all()
    assert len(second) == len(first)


def test_a_note_with_no_extractable_content_is_reported_as_failed():
    """Ingesting nothing would show a green "ingested" over an empty result."""
    marker = f"zz_upload_{uuid.uuid4().hex[:10]}"
    doc = store_upload(
        module_id="IPRT301",
        category="notes",
        filename=f"{marker}.md",
        data=b"\n\n   \n\n\t\n",
        uploaded_by=None,
        content_type="text/markdown",
    )
    try:
        result = process_upload(_row(doc))
        assert result["status"] == "failed", result
        with session_scope() as session:
            row = session.get(UploadedDocument, doc["document_id"])
            assert row.ingest_status == "failed"
            assert row.ingest_detail
    finally:
        _purge_chunks(doc["stored_path"])
        with session_scope() as session:
            row = session.get(UploadedDocument, doc["document_id"])
            if row is not None:
                session.delete(row)
        (Path(content_root()) / doc["stored_path"]).unlink(missing_ok=True)


def test_a_missing_file_fails_cleanly_rather_than_raising():
    """The file could have been deleted off disk after upload."""
    marker = f"zz_upload_{uuid.uuid4().hex[:10]}"
    doc = store_upload(
        module_id="IPRT301",
        category="notes",
        filename=f"{marker}.md",
        data=NOTES,
        uploaded_by=None,
        content_type="text/markdown",
    )
    (Path(content_root()) / doc["stored_path"]).unlink(missing_ok=True)
    try:
        result = process_upload(_row(doc))
        assert result["status"] == "failed"
        assert "missing" in (result["detail"] or "").lower()
    finally:
        _purge_chunks(doc["stored_path"])
        with session_scope() as session:
            row = session.get(UploadedDocument, doc["document_id"])
            if row is not None:
                session.delete(row)


def test_a_stored_path_outside_the_content_root_is_refused():
    """Defence in depth: a stored_path should never escape, but check anyway."""
    result = process_upload(
        {
            "document_id": 999999,
            "module_id": "IPRT301",
            "category": "notes",
            "original_name": "evil.md",
            "stored_path": "../../../../etc/passwd",
            "ingest_status": "pending",
        }
    )
    assert result["status"] == "failed"
    assert "escape" in (result["detail"] or "").lower()


def test_every_indexable_category_has_a_processing_path():
    from src.ingest_uploads import QUESTION_CATEGORIES

    assert set(INDEXABLE_CATEGORIES) | set(QUESTION_CATEGORIES) == {
        "notes",
        "exercises",
        "examples",
        "textbooks",
        "past_paper",
    }


def _row(doc: dict) -> dict:
    """The row as the processor would see it, with its *current* status.

    Read from the database rather than hardcoded, so the idempotency test is not
    defeated by a fixture that claims everything is still pending.
    """
    with session_scope() as session:
        row = session.get(UploadedDocument, doc["document_id"])
        if row is None:
            raise AssertionError(f"upload {doc['document_id']} vanished")
        return {
            "document_id": int(row.document_id),
            "module_id": row.module_id,
            "category": row.category,
            "original_name": row.original_name,
            "stored_path": row.stored_path,
            "ingest_status": row.ingest_status,
        }