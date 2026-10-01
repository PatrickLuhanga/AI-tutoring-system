"""Turn stored uploads into searchable material.

An upload used to be a dead end: :func:`src.uploads.store_upload` wrote the file
and a row with ``ingest_status='pending'``, and **nothing in the codebase ever
moved that status again**. The Content page therefore reported "pending" forever
and the chatbot could not answer from anything a lecturer had just uploaded -
which is the single most obvious thing a lecturer would try to do.

This module is that missing step. Two kinds, because the two upload categories
want opposite treatment:

``notes`` / ``exercises`` / ``examples`` / ``textbooks``
    Indexed into ``curriculum_chunks`` by the normal ingestion pipeline, so
    retrieval and the chatbot can use the material.

``past_paper``
    Not embedded - a question paper is a source of *questions*, and embedding it
    would let retrieval cite a student's own exam. Instead the extractor is run
    over it and the recovered questions go into the bank, tagged with the paper
    they came from.

Both paths are idempotent and safe to re-run: a row already ``ingested`` or
``failed`` is skipped unless ``force`` is given.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from .db import session_scope
from .models import UploadedDocument
from .uploads import content_root

logger = logging.getLogger(__name__)

#: Categories that should become searchable course material.
INDEXABLE_CATEGORIES = ("notes", "exercises", "examples", "textbooks")
#: Categories that become questions rather than embedded material.
QUESTION_CATEGORIES = ("past_paper",)


def pending_uploads(module_id: Optional[str] = None) -> list[dict]:
    """Uploads still waiting to be processed."""
    with session_scope() as session:
        stmt = select(UploadedDocument).where(UploadedDocument.ingest_status == "pending")
        if module_id:
            stmt = stmt.where(UploadedDocument.module_id == module_id.upper())
        rows = session.execute(stmt.order_by(UploadedDocument.document_id)).scalars().all()
        return [_to_row_dict(r) for r in rows]


def _to_row_dict(row: UploadedDocument) -> dict:
    return {
        "document_id": int(row.document_id),
        "module_id": row.module_id,
        "category": row.category,
        "original_name": row.original_name,
        "stored_path": row.stored_path,
        "ingest_status": row.ingest_status,
    }


def _set_status(document_id: int, status: str, detail: Optional[str] = None) -> None:
    with session_scope() as session:
        row = session.get(UploadedDocument, document_id)
        if row is None:
            return
        row.ingest_status = status
        if detail is not None:
            row.ingest_detail = detail[:1000]


def _resolve(row: dict) -> Path:
    """Absolute path for a stored upload, refusing anything outside the content root."""
    root = content_root().resolve()
    target = (root / row["stored_path"]).resolve()
    if not str(target).startswith(str(root)):
        # A stored_path should never escape the content root; refuse rather than
        # reading something outside it if it somehow does.
        raise ValueError(f"stored path escapes the content root: {row['stored_path']}")
    if not target.is_file():
        raise FileNotFoundError(f"file is missing on disk: {row['stored_path']}")
    return target


def process_upload(row: dict, *, force: bool = False) -> dict:
    """Process one upload. Returns ``{status, detail, ...}`` and never raises."""
    document_id = row["document_id"]
    if row["ingest_status"] != "pending" and not force:
        return {"document_id": document_id, "status": row["ingest_status"], "skipped": True}

    category = row["category"]
    try:
        if category in QUESTION_CATEGORIES:
            return _process_past_paper(row)
        if category in INDEXABLE_CATEGORIES:
            return _process_as_content(row)
        # Unknown category: nothing to do, but say so rather than hanging.
        _set_status(document_id, "failed", f"no processing path for category '{category}'")
        return {
            "document_id": document_id,
            "status": "failed",
            "detail": f"no processing path for category '{category}'",
        }
    except Exception as exc:  # noqa: BLE001 - one bad file must not stop the run
        logger.warning("Could not process upload %s: %s", document_id, exc)
        _set_status(document_id, "failed", str(exc)[:400])
        return {"document_id": document_id, "status": "failed", "detail": str(exc)[:200]}


def _process_as_content(row: dict) -> dict:
    """Index an uploaded note into ``curriculum_chunks``.

    Reuses the normal pipeline rather than reimplementing it, so an upload
    produces exactly the chunks a file already in ``academic content/`` would.
    """
    from .embeddings import get_embedder
    from .ingest_curriculum import build_splitter, ingest_file
    from .loaders import ContentFile

    path = _resolve(row)
    content_file = ContentFile(
        path=path,
        rel_path=row["stored_path"],
        module_folder=Path(row["stored_path"]).parts[0] if row["stored_path"] else "",
        module_id=row["module_id"],
        source_type=path.suffix.lstrip("."),
        topic=None,
        source_category=row["category"],
    )

    chunks, was_empty, _answers = ingest_file(
        content_file, build_splitter(), get_embedder(), dry_run=False
    )
    if was_empty or chunks == 0:
        # Nothing to index. "ingested" would be a lie the tutor dashboard shows.
        _set_status(row["document_id"], "failed", "no indexable content was found")
        return {
            "document_id": row["document_id"],
            "status": "failed",
            "detail": "no indexable content was found",
        }

    detail = f"{chunks} chunk(s) added to the corpus"
    _set_status(row["document_id"], "ingested", detail)
    return {
        "document_id": row["document_id"],
        "status": "ingested",
        "chunks": chunks,
        "detail": detail,
    }


def _process_past_paper(row: dict) -> dict:
    """Extract questions from an uploaded paper into the bank.

    Deliberately does not embed the paper: citing a student's own exam back to
    them is not useful, and it would swamp retrieval with question text.
    """
    from .seed_exam_questions import module_for, parse_questions, pretty_paper_name
    from .practice import PracticeError, add_question
    import json

    path = _resolve(row)
    module_id = row["module_id"]
    label = pretty_paper_name(Path(row["original_name"]).stem)

    pages = _ocr(path)
    if not pages:
        raise RuntimeError("OCR returned no text for this paper")

    found = parse_questions(module_id, label, pages)
    if not found:
        _set_status(
            row["document_id"],
            "failed",
            "no questions could be recovered from this paper",
        )
        return {
            "document_id": row["document_id"],
            "status": "failed",
            "detail": "no questions could be recovered from this paper",
        }

    added = 0
    skipped = 0
    for question in found:
        try:
            add_question(
                module_id=module_id,
                prompt=question.prompt,
                answer_notes=None,
                difficulty="medium",
                origin="past_paper",
                source_label=f"{question.source_label} (uploaded)",
                created_by=None,
            )
            added += 1
        except PracticeError:
            skipped += 1

    detail = f"{added} question(s) added from {label}" + (f", {skipped} skipped" if skipped else "")
    _set_status(row["document_id"], "ingested", detail)
    return {
        "document_id": row["document_id"],
        "status": "ingested",
        "questions_added": added,
        "questions_skipped": skipped,
        "detail": detail,
    }


def _ocr(path: Path) -> dict:
    from .seed_exam_questions import ocr_pdf

    return ocr_pdf(path)


def process_all(
    *, module_id: Optional[str] = None, force: bool = False
) -> list[dict]:
    """Process every pending upload. Safe to run repeatedly."""
    results = []
    for row in pending_uploads(module_id):
        results.append(process_upload(row, force=force))
    return results


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", default="", help="restrict to one module")
    parser.add_argument("--force", action="store_true", help="reprocess non-pending rows")
    parser.add_argument("--list", action="store_true", help="list pending and stop")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    pending = pending_uploads(args.module.upper() if args.module else None)
    if args.list:
        if not pending:
            print("Nothing pending.")
        for row in pending:
            print(f"  {row['document_id']:4d}  {row['module_id']:8s}  {row['category']:11s}  {row['original_name']}")
        return 0

    if not pending:
        print("No pending uploads.")
        return 0

    print(f"Processing {len(pending)} pending upload(s)\n")
    results = process_all(module_id=args.module.upper() if args.module else None, force=args.force)
    for result in results:
        status = result.get("status")
        detail = result.get("detail") or result.get("skipped") or ""
        print(f"  {result['document_id']:4d}  {status:9s}  {detail}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())