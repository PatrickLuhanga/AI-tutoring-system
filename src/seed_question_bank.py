"""Export and load the practice question bank, so a clone is not an empty app.

The bank is seeded content: 100 questions extracted from scanned past papers and
8 generated for the module that has no papers. None of it can be re-derived from
code, so it lives in ``exam_papers/question_bank.json`` and this module moves it
between that file and the ``questions`` table.

    python -m src.seed_question_bank --export   # database -> JSON
    python -m src.seed_question_bank            # JSON -> database (idempotent)
    python -m src.seed_question_bank --check    # report drift, write nothing

**Loading is additive and idempotent.** A question already in the database for
the same module is left alone, so running it twice does not double the bank, and
running it after a lecturer has edited or extended the bank does not undo their
work. ``--reset`` is the only destructive option and it empties the whole table,
not just this fixture's rows, because there is no reliable way to tell a
seeder-written row from a lecturer-written one that looks identical.

**The past-paper questions need proofreading.** They were produced by OCR from
scans, so wording is imperfect and diagrams and code listings are missing
entirely - several questions refer to "the diagram below" with nothing below
them. Every such row carries ``(OCR - verify)`` in its ``source_label`` for that
reason. The questions are genuine exam content, not invented, but they are not
ready to be sat unsupervised.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func, select

from .db import session_scope
from .models import Question

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

#: Where the fixture lives, relative to the repository root.
DEFAULT_FIXTURE = Path(__file__).resolve().parent.parent / "exam_papers" / "question_bank.json"

#: Columns carried across. ``question_id``, ``created_by`` and ``created_at`` are
#: deliberately excluded: the first is database-specific, the second refers to a
#: user that does not exist on another machine, and the third should reflect when
#: the row was actually loaded rather than when the fixture was authored.
#:
#: ``answer_source`` is carried because dropping it would promote every
#: model-drafted answer into an auto-graded key on reload - marking is exact
#: match, so a wrong draft would then mark correct student answers wrong.
CARRIED = (
    "module_id",
    "prompt",
    "answer_notes",
    "answer_source",
    "difficulty",
    "origin",
    "source_label",
)

#: Substrings that mark a row as seeder output rather than lecturer authoring.
MANAGED_MARKERS = ("(OCR - verify)", "AI-generated for revision")

_WHITESPACE = re.compile(r"\s+")


def _normalise(text: str) -> str:
    """Collapse whitespace and case, so a re-indented prompt still matches."""
    return _WHITESPACE.sub(" ", (text or "")).strip().lower()


def _is_managed(row: Question) -> bool:
    label = row.source_label or ""
    return any(marker in label for marker in MANAGED_MARKERS)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
def export_bank(path: Path, *, only_managed: bool = True) -> dict[str, Any]:
    """Write the current bank to ``path`` as JSON and return the payload.

    ``only_managed`` keeps lecturer-authored questions out of the fixture by
    default. They belong to whoever wrote them and to the machine they wrote them
    on; exporting them would mean every clone starts with someone else's
    half-finished notes, and a ``user_id`` reference that resolves to nothing.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    with session_scope() as session:
        rows = session.execute(
            select(Question).where(Question.is_active.is_(True)).order_by(
                Question.module_id, Question.question_id
            )
        ).scalars().all()
        live = [r for r in rows if not only_managed or _is_managed(r)]

        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "source": "Practice question bank, extracted from the papers in this folder.",
            "papers": [],
            "questions": [
                {key: getattr(row, key) for key in CARRIED} for row in live
            ],
        }

    counts: dict[str, int] = {}
    for q in payload["questions"]:
        counts[q["module_id"]] = counts.get(q["module_id"], 0) + 1
    payload["counts_by_module"] = dict(sorted(counts.items()))

    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------
def load_bank(
    path: Path, *, reset: bool = False, confirm_reset: bool = False
) -> tuple[int, int]:
    """Load the fixture into the database. Returns ``(inserted, skipped)``.

    Skipped means a question with the same module and prompt was already there.
    That is the normal case on a second run, and the normal case after a lecturer
    has added their own questions.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"No fixture at {path}. Run from a clone of the repository, or "
            f"pass --fixture with the right path."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"Fixture schema is {payload.get('schema_version')}, this module "
            f"understands {SCHEMA_VERSION}."
        )

    questions = payload.get("questions") or []
    if not questions:
        raise ValueError("Fixture contains no questions.")

    from .practice import PracticeError, add_question

    inserted = 0
    skipped = 0

    with session_scope() as session:
        if reset:
            total = session.scalar(select(func.count()).select_from(Question)) or 0
            if not confirm_reset:
                raise ValueError(
                    f"--reset would delete all {total} question(s) in the table, "
                    f"including any a lecturer has written. Re-run with --yes if "
                    f"that is what you want."
                )
            session.query(Question).delete(synchronize_session=False)
            logger.info("Reset: cleared the questions table (%d row(s))", total)

        existing = {
            (row.module_id, _normalise(row.prompt))
            for row in session.execute(select(Question.module_id, Question.prompt)).all()
        }

    for entry in questions:
        key = (entry["module_id"], _normalise(entry["prompt"]))
        if key in existing:
            skipped += 1
            continue
        try:
            add_question(
                module_id=entry["module_id"],
                prompt=entry["prompt"],
                answer_notes=entry.get("answer_notes"),
                answer_source=entry.get("answer_source"),
                difficulty=entry.get("difficulty") or "medium",
                origin=entry.get("origin") or "past_paper",
                source_label=entry.get("source_label"),
                created_by=None,
            )
        except PracticeError as exc:
            logger.warning("Skipped one fixture question: %s", exc.message)
            continue
        existing.add(key)
        inserted += 1

    return inserted, skipped


# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------
def check_bank(path: Path) -> tuple[list[str], list[str]]:
    """Compare the fixture with the database.

    Returns ``(missing, extra)``. ``missing`` are in the fixture but not the
    database. ``extra`` are seeder-marked rows in the database that the fixture
    does not describe - usually a lecturer deleting a question they no longer
    want, which is worth knowing about but is not an error.
    """
    if not path.exists():
        raise FileNotFoundError(f"No fixture at {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    wanted = {
        (q["module_id"], _normalise(q["prompt"])) for q in payload.get("questions") or []
    }

    with session_scope() as session:
        rows = session.execute(
            select(Question.module_id, Question.prompt, Question.source_label).where(
                Question.is_active.is_(True)
            )
        ).all()

    have_managed = {
        (module_id, _normalise(prompt))
        for module_id, prompt, label in rows
        if any(marker in (label or "") for marker in MANAGED_MARKERS)
    }

    missing = sorted(wanted - have_managed)
    extra = sorted(have_managed - wanted)
    return missing, extra


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--export", action="store_true", help="database -> fixture")
    group.add_argument("--check", action="store_true", help="report drift, write nothing")
    group.add_argument(
        "--reset",
        action="store_true",
        help="empty the questions table before loading. Destroys lecturer-authored questions too.",
    )
    parser.add_argument("--yes", action="store_true", help="confirm --reset")
    parser.add_argument(
        "--include-authored",
        action="store_true",
        help="with --export, also export lecturer-authored questions (default: only seeder rows)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    if args.export:
        payload = export_bank(args.fixture, only_managed=not args.include_authored)
        print(f"Wrote {args.fixture}")
        print(f"  {len(payload['questions'])} question(s):")
        for module_id, n in payload["counts_by_module"].items():
            print(f"    {module_id}: {n}")
        return 0

    if args.check:
        missing, extra = check_bank(args.fixture)
        if not missing and not extra:
            print("The database matches the fixture.")
            return 0
        if missing:
            print(f"{len(missing)} question(s) in the fixture but not the database:")
            for module_id, prompt in missing[:10]:
                print(f"    {module_id}  {prompt[:88]}")
        if extra:
            print(f"\n{len(extra)} seeder-marked question(s) in the database but not the fixture:")
            for module_id, prompt in extra[:10]:
                print(f"    {module_id}  {prompt[:88]}")
        return 1

    try:
        inserted, skipped = load_bank(
            args.fixture, reset=args.reset, confirm_reset=args.yes
        )
    except (FileNotFoundError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2

    print(f"Loaded {args.fixture}")
    print(f"  {inserted} inserted, {skipped} already present")
    if inserted == 0 and skipped:
        print("  The bank was already loaded; nothing to do.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())