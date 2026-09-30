"""Human-in-the-loop queue for questions the tutor could not ground (section 17).

When the RAG Orchestrator cannot find trustworthy course material, the workflow
calls :func:`record_ungrounded` instead of letting the model answer from its
parametric memory. Those questions accumulate here, and a tutor - escalating to
the module lecturer when needed - can:

* answer a question, so the student gets a real reply, and
* **promote** that answer into ``curriculum_chunks``, so every later student who
  asks something similar is grounded in the tutor's own words.

That promotion step is the feedback loop the architecture describes: retrieval
gaps become curriculum, and the corpus improves because students asked.

Tutor visibility is enforced at query level from ``tutor_assignments`` (section
4.2), never in the frontend.
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .config import settings
from .db import session_scope
from .embeddings import get_embedder
from .models import CurriculumChunk, Student, TutorAssignment, UnansweredQuestion

logger = logging.getLogger(__name__)

#: Provenance label for answers written by tutors. Treated as first-party course
#: material by the retriever, so promoted answers outrank commercial textbooks.
TUTOR_ANSWER_CATEGORY = "tutor_answers"

_WORD_RE = re.compile(r"[a-z0-9]+")


def question_key(text: str) -> str:
    """Stable key for collapsing near-identical questions.

    Lower-cased, stripped of punctuation and stop-word-ish noise, then hashed. Two
    students asking "what is inheritance?" and "What is inheritance" share a key.
    """
    cleaned = unicodedata.normalize("NFKC", text or "").casefold()
    words = _WORD_RE.findall(cleaned)
    # Order-independent so "inheritance is what" matches "what is inheritance".
    significant = sorted({w for w in words if len(w) > 2})
    basis = " ".join(significant) or " ".join(words)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


def classify_reason(
    *,
    chunk_count: int,
    below_threshold: int,
    third_party_fallback: bool,
    grounded_categories: list[str],
) -> str:
    """Explain, in the tutor's language, why this question is in the queue."""
    if chunk_count == 0:
        return "third_party_only" if third_party_fallback else "no_context"
    if below_threshold:
        return "below_threshold"
    if third_party_fallback and not grounded_categories:
        return "third_party_only"
    return "low_confidence"


def record_ungrounded(
    *,
    question_text: str,
    module_id: Optional[str],
    reason: str,
    session_id: Optional[str] = None,
    message_id: Optional[str] = None,
    student_id: Optional[int] = None,
    intent: Optional[str] = None,
    best_distance: Optional[float] = None,
) -> Optional[int]:
    """Queue one question for tutor review. Returns the row id.

    Never raises: a failure to record must not break the student's turn.
    """
    if not (question_text or "").strip():
        return None
    key = question_key(question_text)
    try:
        with session_scope() as session:
            existing = session.execute(
                select(UnansweredQuestion).where(
                    UnansweredQuestion.module_id == module_id,
                    UnansweredQuestion.question_key == key,
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing.occurrences = int(existing.occurrences) + 1
                # A question a student asks again is open again, even if a tutor
                # had dismissed it: the gap is still there.
                if existing.status in {"dismissed", "duplicate"}:
                    existing.status = "open"
                session.flush()
                return int(existing.question_id)

            row = UnansweredQuestion(
                module_id=module_id,
                student_id=student_id,
                session_id=session_id,
                message_id=message_id,
                question_text=question_text.strip(),
                question_key=key,
                intent=intent,
                reason=reason,
                best_distance=best_distance,
                status="open",
            )
            session.add(row)
            session.flush()
            logger.info(
                "Queued ungrounded question for %s: %r", module_id, question_text[:70]
            )
            return int(row.question_id)
    except IntegrityError:
        # Concurrent identical question - the unique constraint did its job.
        logger.debug("Duplicate ungrounded question raced; ignoring.")
        return None
    except Exception as exc:  # noqa: BLE001 - telemetry must never break a reply
        logger.warning("Could not queue ungrounded question: %s", exc)
        return None


@dataclass(slots=True)
class TutorScope:
    """Which modules a caller may see. Admins see everything."""

    is_admin: bool
    module_ids: list[str]

    def allows(self, module_id: Optional[str]) -> bool:
        if self.is_admin:
            return True
        return module_id is not None and module_id in self.module_ids


def resolve_tutor_scope(identity) -> TutorScope:
    """Derive module scope for a staff identity (DB level, section 4.2).

    Two sources, because there are two ways to be staff:

    * a **login account**, whose scope lives in ``user_module_access`` and is
      resolved once during login;
    * a **dev header** identity, whose scope still comes from the legacy
      ``tutor_assignments`` table.

    A ``lecturer`` is a superset of ``tutor`` and is scoped identically; both are
    also given any modules already resolved onto the identity, so a session
    never has to re-query to answer a scope question.
    """
    if identity.role == "admin":
        return TutorScope(is_admin=True, module_ids=[])

    if identity.role not in {"tutor", "lecturer"}:
        return TutorScope(is_admin=False, module_ids=[])

    resolved = list(getattr(identity, "module_ids", ()) or ())
    if resolved:
        return TutorScope(is_admin=False, module_ids=resolved)

    if identity.student_id is None:
        return TutorScope(is_admin=False, module_ids=[])
    try:
        with session_scope() as session:
            ids = list(
                session.execute(
                    select(TutorAssignment.module_id).where(
                        TutorAssignment.tutor_id == identity.student_id
                    )
                ).scalars()
            )
        return TutorScope(is_admin=False, module_ids=ids)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not resolve tutor module scope: %s", exc)
        return TutorScope(is_admin=False, module_ids=[])


def list_questions(
    scope: TutorScope,
    *,
    status: str = "open",
    module_id: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    """Return the tutor's queue, most-demanded first."""
    with session_scope() as session:
        stmt = select(UnansweredQuestion)
        if status and status != "all":
            stmt = stmt.where(UnansweredQuestion.status == status)
        if not scope.is_admin:
            if not scope.module_ids:
                return []
            stmt = stmt.where(UnansweredQuestion.module_id.in_(scope.module_ids))
        if module_id:
            if not scope.allows(module_id):
                return []
            stmt = stmt.where(UnansweredQuestion.module_id == module_id)

        rows = list(
            session.execute(
                stmt.order_by(
                    UnansweredQuestion.occurrences.desc(),
                    UnansweredQuestion.created_at.desc(),
                ).limit(max(1, min(limit, 500)))
            ).scalars()
        )
        return [_row_to_dict(r) for r in rows]


def summary(scope: TutorScope) -> dict:
    """Counts for the tutor dashboard header."""
    with session_scope() as session:
        stmt = select(
            UnansweredQuestion.status, func.count(), func.sum(UnansweredQuestion.occurrences)
        ).group_by(UnansweredQuestion.status)
        if not scope.is_admin:
            if not scope.module_ids:
                return {"scope": {"is_admin": False, "modules": []}, "counts": {}}
            stmt = stmt.where(UnansweredQuestion.module_id.in_(scope.module_ids))
        counts = {
            status: {"questions": int(n), "occurrences": int(occ or 0)}
            for status, n, occ in session.execute(stmt)
        }
    return {
        "scope": {"is_admin": scope.is_admin, "modules": sorted(scope.module_ids)},
        "counts": counts,
        "open_total": counts.get("open", {}).get("questions", 0),
        "open_occurrences": counts.get("open", {}).get("occurrences", 0),
        "answered_total": counts.get("answered", {}).get("questions", 0),
    }


def _row_to_dict(row: UnansweredQuestion) -> dict:
    return {
        "question_id": int(row.question_id),
        "module_id": row.module_id,
        "question_text": row.question_text,
        "intent": row.intent,
        "reason": row.reason,
        "best_distance": round(float(row.best_distance), 4) if row.best_distance is not None else None,
        "occurrences": int(row.occurrences),
        "status": row.status,
        "answer_text": row.answer_text,
        "answered_at": row.answered_at.isoformat() if row.answered_at else None,
        "promoted_chunk_id": row.promoted_chunk_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _load_for_update(question_id: int, scope: TutorScope) -> Optional[UnansweredQuestion]:
    with session_scope() as session:
        row = session.execute(
            select(UnansweredQuestion).where(UnansweredQuestion.question_id == question_id)
        ).scalar_one_or_none()
        if row is None or not scope.allows(row.module_id):
            return None
        # Detach a plain snapshot; the write happens in its own transaction.
        session.expunge(row)
        return row


def answer_question(
    question_id: int,
    scope: TutorScope,
    *,
    answer_text: str,
    answered_by: Optional[int],
    promote: bool = False,
) -> Optional[dict]:
    """Record a tutor's answer and optionally promote it into the corpus."""
    if not (answer_text or "").strip():
        raise ValueError("answer_text is required")
    row = _load_for_update(question_id, scope)
    if row is None:
        return None

    promoted_chunk_id: Optional[int] = None
    if promote:
        promoted_chunk_id = promote_answer_to_corpus(
            question_text=row.question_text,
            answer_text=answer_text,
            module_id=row.module_id,
            answered_by=answered_by,
        )

    with session_scope() as session:
        target = session.execute(
            select(UnansweredQuestion).where(
                UnansweredQuestion.question_id == question_id
            )
        ).scalar_one()
        target.answer_text = answer_text.strip()
        target.answered_by = answered_by
        target.answered_at = datetime.now(timezone.utc)
        target.status = "answered"
        if promoted_chunk_id is not None:
            target.promoted_chunk_id = promoted_chunk_id
        session.flush()
        return _row_to_dict(target)


def dismiss_question(question_id: int, scope: TutorScope) -> Optional[dict]:
    """Mark a question as not needing an answer (duplicate, out of scope, ...)."""
    row = _load_for_update(question_id, scope)
    if row is None:
        return None
    with session_scope() as session:
        target = session.execute(
            select(UnansweredQuestion).where(
                UnansweredQuestion.question_id == question_id
            )
        ).scalar_one()
        target.status = "dismissed"
        target.answered_at = datetime.now(timezone.utc)
        session.flush()
        return _row_to_dict(target)


def promote_answer_to_corpus(
    *,
    question_text: str,
    answer_text: str,
    module_id: Optional[str],
    answered_by: Optional[int] = None,
) -> Optional[int]:
    """Write a tutor answer into ``curriculum_chunks`` so RAG can ground on it.

    The stored chunk is prefixed with the question it answers so the embedding
    captures both sides of the Q&A, which measurably helps a future student who
    phrases the same question differently.
    """
    if not module_id or not (answer_text or "").strip():
        return None
    try:
        embedder = get_embedder()
        text = f"Question: {question_text.strip()}\n\nAnswer: {answer_text.strip()}"
        vector = embedder.encode_one(text)
        answerer = None
        if answered_by is not None:
            with session_scope() as session:
                answerer = session.execute(
                    select(Student.dut4life_email).where(Student.student_id == answered_by)
                ).scalar_one_or_none()

        with session_scope() as session:
            next_index = int(
                session.execute(
                    select(func.coalesce(func.max(CurriculumChunk.chunk_index), -1) + 1).where(
                        CurriculumChunk.source_file == "tutor_answers/curated.md"
                    )
                ).scalar_one()
            )
            row = CurriculumChunk(
                module_id=module_id,
                source_file="tutor_answers/curated.md",
                source_name="Tutor answers",
                source_type="md",
                topic="tutor_answers",
                section_title=(question_text.strip()[:120] or "Tutor answer"),
                source_category=TUTOR_ANSWER_CATEGORY,
                is_answer=False,
                chunk_index=next_index,
                chunk_text=text,
                token_count=len(text.split()),
                content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                doc_metadata={
                    "module_id": module_id,
                    "rel_path": "tutor_answers/curated.md",
                    "source_category": TUTOR_ANSWER_CATEGORY,
                    "question": question_text.strip(),
                    "answered_by": answerer,
                },
                embedding_model=settings.embedding_model_name,
                embedding=vector,
            )
            session.add(row)
            session.flush()
            logger.info(
                "Promoted tutor answer into %s as chunk %s", module_id, row.chunk_id
            )
            return int(row.chunk_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not promote tutor answer to corpus: %s", exc)
        return None
