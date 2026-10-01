"""The practice-question bank and the tests drawn from it.

Two sources feed the bank, as specified: questions a tutor or lecturer **authors**
or imports from an uploaded past paper, and questions a staff member asks the
local model to **generate from a section of the notes**. Both end up in
:class:`~src.models.Question` rows, so a practice test is always a reproducible
sample of reviewable material rather than a fresh generation.

Generation is deliberately staff-triggered and asynchronous from the student's
point of view: on this hardware the model produces a handful of tokens per
second, so generating during a student's test would make it unusable. A
generated question is therefore *reviewed by staff before it can be issued*,
which is also what stops a hallucinated question reaching a student.
"""

from __future__ import annotations

import logging
import random
import re
from pathlib import Path
from typing import Optional, Sequence

from sqlalchemy import func, select

from .config import settings
from .db import session_scope
from .loaders import THIRD_PARTY_CATEGORIES
from .models import (
    ANSWER_SOURCES,
    QUESTION_DIFFICULTIES,
    QUESTION_ORIGINS,
    CurriculumChunk,
    Question,
)

logger = logging.getLogger(__name__)


class PracticeError(Exception):
    """A request the practice service cannot satisfy."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _clean_difficulty(value: object, default: str = "medium") -> str:
    text = str(value or "").strip().lower()
    return text if text in QUESTION_DIFFICULTIES else default


def _clean_origin(value: object, default: str = "authored") -> str:
    text = str(value or "").strip().lower()
    return text if text in QUESTION_ORIGINS else default


def _clean_answer_source(value: object) -> Optional[str]:
    """Normalise an answer source. Anything unrecognised becomes None.

    None means "nothing vouched for this answer", which is the safe outcome: an
    unrecognised label is not treated as ``authored`` and so is never marked
    against automatically.
    """
    text = str(value or "").strip().lower()
    return text if text in ANSWER_SOURCES else None


# ---------------------------------------------------------------------------
# Bank management
# ---------------------------------------------------------------------------
def add_question(
    *,
    module_id: str,
    prompt: object,
    created_by: Optional[int],
    answer_notes: object = None,
    answer_source: object = None,
    difficulty: object = "medium",
    origin: object = "authored",
    source_label: object = None,
) -> dict:
    """Add one question to the bank.

    ``answer_source`` decides whether the answer may be auto-graded; see
    ``Question.answer_source``. An answer with no source set is never marked
    against, because nothing vouched for it.
    """
    text = str(prompt or "").strip()
    if len(text) < 8:
        raise PracticeError("A question needs at least a few words of prompt.")
    with session_scope() as session:
        row = Question(
            module_id=str(module_id).strip().upper(),
            prompt=text,
            answer_notes=(str(answer_notes).strip() or None) if answer_notes else None,
            answer_source=_clean_answer_source(answer_source),
            difficulty=_clean_difficulty(difficulty),
            origin=_clean_origin(origin),
            source_label=(str(source_label).strip() or None) if source_label else None,
            created_by=created_by,
        )
        session.add(row)
        session.flush()
        return _to_dict(row, include_answer=True)


def list_questions(
    module_id: str, *, include_inactive: bool = False, limit: int = 500
) -> list[dict]:
    """The bank for a module, newest first. Answers are stripped."""
    with session_scope() as session:
        stmt = select(Question).where(Question.module_id == str(module_id).strip().upper())
        if not include_inactive:
            stmt = stmt.where(Question.is_active.is_(True))
        rows = session.execute(stmt.order_by(Question.created_at.desc()).limit(limit)).scalars()
        return [_to_dict(r, include_answer=False) for r in rows]


def delete_question(question_id: int) -> bool:
    """Retire a question. Rows are kept so past attempts stay resolvable."""
    with session_scope() as session:
        row = session.get(Question, int(question_id))
        if row is None:
            return False
        row.is_active = False
        return True


def bank_size(module_id: str) -> int:
    with session_scope() as session:
        return int(
            session.execute(
                select(func.count())
                .select_from(Question)
                .where(Question.module_id == str(module_id).strip().upper(),
                       Question.is_active.is_(True))
            ).scalar_one()
        )


# ---------------------------------------------------------------------------
# Drawing a test
# ---------------------------------------------------------------------------
def draw_test(
    *, module_id: str, count: Optional[int] = None, seed: Optional[int] = None
) -> dict:
    """Sample a random set of questions for a practice run.

    Returns the questions **without** their answers, so a student cannot read
    them out of the response payload.
    """
    module = str(module_id).strip().upper()
    wanted = count if count and count > 0 else settings.practice_default_size
    wanted = max(1, min(int(wanted), settings.practice_max_size))

    pool = list_questions(module, limit=1000)
    if not pool:
        raise PracticeError(
            f"There are no questions in the bank for {module} yet. "
            "A tutor or lecturer needs to add some first.",
            status_code=404,
        )

    rng = random.Random(seed) if seed is not None else random.Random()
    # Sample without replacement; if the bank is smaller than the requested
    # size, take everything rather than failing.
    picked = rng.sample(pool, min(wanted, len(pool)))
    return {
        "module_id": module,
        "requested": wanted,
        "available": len(pool),
        "questions": [
            {
                "question_id": q["question_id"],
                "prompt": q["prompt"],
                "difficulty": q["difficulty"],
            }
            for q in picked
        ],
    }


def mark_attempt(
    *,
    module_id: str,
    question_ids: Sequence[int],
    answers: dict[str, str],
    student_id: Optional[int],
    user_id: Optional[int],
    attempt_id: Optional[int] = None,
) -> dict:
    """Score a submitted test.

    Marks on exact match after normalisation. That is deliberately blunt: the
    marking notes are written for a human marker, and pretending to do
    keyword-based partial credit would report a number the staff did not set.
    """
    from .models import PracticeAttempt

    wanted = [int(q) for q in question_ids]
    if not wanted:
        raise PracticeError("No questions were answered.")

    with session_scope() as session:
        rows = {
            r.question_id: r
            for r in session.execute(
                select(Question).where(Question.question_id.in_(wanted))
            ).scalars()
        }
        missing = [q for q in wanted if q not in rows]
        if missing:
            raise PracticeError(f"Unknown question id(s): {missing}.")

        correct = 0
        breakdown = []
        for qid in wanted:
            row = rows[qid]
            given = _normalise(str((answers or {}).get(str(qid), "")))

            # Only a human-authored answer may be marked against automatically.
            # Marking is exact match, so an answer the *model* drafted is a trap:
            # if it is subtly wrong, a student who answered correctly is scored
            # wrong, which penalises them for the bank's inaccuracy. A drafted
            # answer is shown as a reference for the student to self-assess
            # against instead.
            gradeable = row.answer_source == "authored"
            expected = _normalise(row.answer_notes or "") if gradeable else ""
            markable = gradeable and bool(expected)
            hit = markable and given == expected
            if hit:
                correct += 1

            has_answer = bool((row.answer_notes or "").strip())
            if not has_answer:
                mode = "none"
            elif gradeable:
                mode = "auto"
            else:
                mode = "reference"

            breakdown.append(
                {
                    "question_id": qid,
                    "correct": hit,
                    "markable": markable,
                    "mode": mode,
                    "answer_notes": row.answer_notes if has_answer else None,
                }
            )

        markable_count = sum(1 for b in breakdown if b["markable"])
        reference_count = sum(1 for b in breakdown if b["mode"] == "reference")
        no_answer_count = sum(1 for b in breakdown if b["mode"] == "none")
        attempt = PracticeAttempt(
            student_id=student_id,
            user_id=user_id,
            module_id=str(module_id).strip().upper(),
            question_ids=wanted,
            answers={str(k): str(v) for k, v in (answers or {}).items()},
            score=correct,
            max_score=markable_count,
        )
        from datetime import datetime, timezone

        attempt.submitted_at = datetime.now(timezone.utc)
        session.add(attempt)
        session.flush()
        attempt_id = int(attempt.attempt_id)

    return {
        "attempt_id": attempt_id,
        "score": correct,
        "max_score": markable_count,
        "unmarked": len(breakdown) - markable_count,
        # Reported separately so the UI can say why the score is what it is,
        # rather than showing a bare number that looks broken when nothing could
        # be marked.
        "reference_only": reference_count,
        "no_answer": no_answer_count,
        "breakdown": breakdown,
    }


def _normalise(value: str) -> str:
    """Case-, whitespace- and punctuation-insensitive, for comparison only."""
    return re.sub(r"[^a-z0-9 ]+", "", value.lower()).strip()


# ---------------------------------------------------------------------------
# Staff-triggered generation
# ---------------------------------------------------------------------------
def first_party_material(module_id: str, *, limit_chars: int = 6000) -> str:
    """Grounding text drawn from a module's own ingested material.

    Third-party textbooks are excluded. A question generated from a commercial
    book's wording is not the module's material, would not match how the course
    is examined, and is the same class of problem as letting retrieval cite a
    textbook chapter as a lecture slide.

    Sections are visited in round-robin rather than file order, so the sample
    spans the module instead of being three questions from the first deck.
    """
    module = str(module_id).strip().upper()
    with session_scope() as session:
        rows = session.execute(
            select(
                CurriculumChunk.source_file,
                CurriculumChunk.section_title,
                CurriculumChunk.chunk_text,
            )
            .where(
                CurriculumChunk.module_id == module,
                CurriculumChunk.source_category.notin_(sorted(THIRD_PARTY_CATEGORIES)),
                func.length(CurriculumChunk.chunk_text) > 200,
            )
            .order_by(CurriculumChunk.source_file, CurriculumChunk.chunk_id)
        ).all()

    if not rows:
        return ""

    by_file: dict[str, list[tuple]] = {}
    for source_file, section_title, chunk_text in rows:
        by_file.setdefault(str(source_file), []).append((section_title, str(chunk_text or "")))

    files = sorted(by_file)
    cursors = {f: 0 for f in files}
    parts: list[str] = []
    total = 0
    while total < limit_chars:
        progressed = False
        for f in files:
            items = by_file[f]
            idx = cursors[f]
            if idx >= len(items):
                continue
            section_title, text = items[idx]
            cursors[f] = idx + 1
            heading = f"### {Path(f).stem}" + (f" - {section_title}" if section_title else "")
            block = f"{heading}\n{text}\n"
            parts.append(block)
            total += len(block)
            progressed = True
            if total >= limit_chars:
                break
        if not progressed:
            break
    return "\n".join(parts)[:limit_chars]


_NUMBERED = re.compile(r"(?:^|\n)\s*(?:\d+[.)]|[-*])\s+")
#: A dangling heading marker at the end of a question, left when the model runs
#: into the next section heading of the grounding material.
_TRAILING_HEADING = re.compile(r"\n*[\s#>*_-]*$")
#: The model tends to echo the instruction's own scaffolding into the body -
#: "### Question:", "**Question:**", "Question 1:" - in whatever emphasis it
#: feels like. That is formatting, not the question.
_SCAFFOLD = re.compile(
    r"^[\s>*#]*(?:question|q)[\s>*#]*\d*[\s>*#]*[:.\-]?[\s>*]*",
    re.IGNORECASE,
)


def _strip_model_scaffolding(text: str) -> str:
    body = (text or "").strip()
    previous = None
    # Only ever strips from the front, and only a leading heading, so a question
    # that legitimately contains the word "question" keeps it.
    while previous != body:
        previous = body
        body = _SCAFFOLD.sub("", body, count=1).strip()
    return body.strip()


def _too_similar(module_id: str, prompt: str, *, threshold: float = 0.82) -> bool:
    """True when a generated question is a near-duplicate of one already banked.

    Generating repeatedly from the same material makes the model return the same
    question with different wording, so a bank filled with six restatements of the
    same idea is worse than a small bank.
    """
    try:
        existing = [q["prompt"] for q in list_questions(module_id, limit=500)]
    except Exception:  # noqa: BLE001 - never let dedup break generation
        return False
    if not existing:
        return False

    def shingles(text: str) -> set[str]:
        words = re.findall(r"[a-z0-9]+", text.lower())
        return {" ".join(words[i : i + 3]) for i in range(max(0, len(words) - 2))}

    target = shingles(prompt)
    if not target:
        return False
    for other in existing:
        candidate = shingles(other)
        if not candidate:
            continue
        overlap = len(target & candidate) / len(target | candidate)
        if overlap >= threshold:
            return True
    return False


def generate_questions_from_text(
    *,
    module_id: str,
    text: str,
    count: int,
    created_by: Optional[int],
    difficulty: str = "medium",
    source_label: str = "AI-generated for revision - not from a past paper",
) -> list[dict]:
    """Ask the local model for practice questions grounded in ``text``.

    Cost is the reason this is staff-triggered rather than on-demand: on CPU the
    model emits a few tokens a second, and a student waiting two minutes for a
    "randomised" question would be worse served than by the bank.

    ``source_label`` is stored on every row and shown to the student. It defaults
    to wording that makes the provenance unmissable, because an AI-written
    question has not been through the same approval route as an authored one.
    """
    from .llm_router import LLMError, get_router

    source = str(text or "").strip()
    if len(source) < 120:
        raise PracticeError("Provide at least a paragraph of material to generate from.")
    count = max(1, min(int(count or 3), 10))

    prompt = (
        "You are helping a lecturer build a practice question bank from course "
        "material. Write one self-contained exam question based only on the "
        f"material below, then a line starting with ANSWER: followed by the "
        "expected answer. No preamble.\n\nMATERIAL:\n" + source[:6000]
    )

    try:
        response = get_router().generate(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
        )
    except LLMError as exc:
        raise PracticeError(
            "The model is unavailable, so questions could not be generated. "
            "Add them by hand instead.",
            status_code=503,
        ) from exc

    draft = response.text

    return _parse_generated(
        module_id=module_id,
        draft=draft,
        count=count,
        created_by=created_by,
        difficulty=difficulty,
        source_label=source_label,
    )


def _parse_generated(
    *,
    module_id: str,
    draft: str,
    count: int,
    created_by: Optional[int],
    difficulty: str,
    source_label: str,
) -> list[dict]:
    """Split a numbered draft into one row per question.

    The model is asked for a rigid format, but it does not always comply, so
    anything that does not contain a recognisable answer line is rejected rather
    than stored as a blank question.
    """
    created: list[dict] = []
    blocks = _NUMBERED.split(str(draft or ""))
    for block in blocks:
        if len(created) >= count:
            break
        body = _strip_model_scaffolding(block)
        if not body or "ANSWER:" not in body.upper():
            continue
        upper = body.upper()
        idx = upper.rfind("ANSWER:")
        question_text = body[:idx].strip().rstrip(":").strip()
        answer_text = body[idx + len("ANSWER:") :].strip()
        # The model sometimes trails off into the next section heading when the
        # grounding material is chopped mid-block.
        question_text = _TRAILING_HEADING.sub("", question_text).strip()
        if len(question_text) < 12 or len(answer_text) < 1:
            continue
        if _too_similar(module_id, question_text):
            logger.info("Skipping a question that repeats the bank: %s", question_text[:70])
            continue
        created.append(
            add_question(
                module_id=module_id,
                prompt=question_text,
                answer_notes=answer_text,
                difficulty=difficulty,
                origin="generated",
                source_label=source_label,
                created_by=created_by,
            )
        )
    if not created:
        raise PracticeError(
            "The model did not return anything in the expected format. "
            "Add the question by hand instead.",
            status_code=502,
        )
    logger.info("Stored %d generated question(s) for %s", len(created), module_id)
    return created


def _to_dict(row: Question, *, include_answer: bool) -> dict:
    return {
        "question_id": int(row.question_id),
        "module_id": row.module_id,
        "prompt": row.prompt,
        "difficulty": row.difficulty,
        "origin": row.origin,
        "answer_source": row.answer_source,
        "source_label": row.source_label,
        "is_active": bool(row.is_active),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        **({"answer_notes": row.answer_notes} if include_answer else {}),
    }
