"""Draft reference answers for banked questions, tiered by what is actually knowable.

The request was "generate the answers and make sure they are accurate". Accuracy
is the part that cannot be delivered by generating, so this module is built
around measuring what it does not know and refusing to fill the gap.

**Why a tiered design.** Running the questions against the module's own ingested
notes puts roughly a quarter of them inside the retrieval threshold. The rest
return nothing above it, and they are not a random sample of difficulty - the
misses concentrate on questions that ask you to complete code against a class
definition, a DTD or a UML figure that was printed on the paper and lost to OCR.
There is no material anywhere in this repository that would let anyone answer
"Write the method calcAmount in ICalcFund" correctly, human or model. Asking a
3B model anyway produces fluent fiction, and under exact-match marking that
fiction would mark correct student answers wrong.

So questions fall into three tiers:

``grounded``
    The notes cover it. The answer is written *from the retrieved passages*, and
    the label records that it was.

``general``
    Retrieval found nothing, but the question is answerable from ordinary subject
    knowledge - "Briefly explain the importance of standardization in data
    exchange" needs no lecture slide. Written without passages, and labelled as
    ungrounded so a lecturer can tell the two apart.

``unanswerable``
    The question depends on material that does not exist in this repository:
    a figure, a code listing, a class definition, or a multiple-choice stem
    whose options were lost with their column. **No answer is written.** The
    question stays in the bank so a student can attempt it, with a note saying
    why there is no reference.

Every answer written here is stored with ``answer_source='generated'``, which
:meth:`src.practice.mark_attempt` refuses to mark against automatically. A draft
is shown to the student as a reference to self-assess against, never used as a
key. Only ``answer_source='authored'`` is marked automatically.

Resumable: questions that already carry an answer are skipped, so a run can be
interrupted and restarted.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_FIXTURE = (
    Path(__file__).resolve().parent.parent / "exam_papers" / "question_bank.json"
)

#: Above this cosine distance a retrieved passage is not about the question.
GROUNDING_THRESHOLD = 0.40

GROUNDED_LABEL = (
    "AI draft, grounded in this module's lecture material - review before relying on it"
)
GENERAL_LABEL = (
    "AI draft from general subject knowledge - review before relying on it"
)
UNANSWERABLE_LABEL = "No reference answer - needs a lecturer to write one"

#: A question that cannot be answered from anything in this repository.
#:
#: Each pattern marks a dependency on material that was printed on the paper and
#: lost: a figure, a listing, a definition of a class the question refers to, or
#: the options of a multiple-choice question whose column OCR separated.
_NEEDS_MISSING_CONTEXT = re.compile(
    r"""\b(
        below | above |
        the \s+ (?:figure|diagram|listing|code|class|output|structure|xml|dtd|
                     table|source|method|package|interface) |
        this \s+ (?:code|class|listing|figure|diagram|output|method) |
        figure | diagram | shown | as \s+ follows |
        according \s+ to \s+ the \s+ (?:figure|diagram|table|output|code)
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)
#: Completing code against a symbol the repository never defines. The question
#: names a type, and answering needs its body.
_CODE_COMPLETION = re.compile(
    r"^\s*(?:complete|write|create|implement|fill in|finish)\b.*"
    r"\b(?:class|method|header|interface|constructor|jsp|tag|dtd|xml|"
    r"scriptlet|directive|attribute)\b",
    re.IGNORECASE,
)
#: A multiple-choice stem whose options did not survive, so there is one correct
#: answer out of four that cannot be determined.
_MULTIPLE_CHOICE = re.compile(
    r"\bwhich\s+of\s+the\s+following\b|\bnot\s+listed\b|\bbest\s+describes\b",
    re.IGNORECASE,
)

MIN_ANSWER_CHARS = 60


def is_answerable(prompt: str) -> tuple[bool, str]:
    """Can this question be answered from anything available?

    Returns ``(answerable, reason)``. The reason is for the log and for the
    fixture's own notes - it is not shown to students verbatim.
    """
    text = (prompt or "").strip()

    if _CODE_COMPLETION.search(text):
        return False, "code completion against a definition that was not captured"
    if _NEEDS_MISSING_CONTEXT.search(text):
        return False, "refers to a figure, listing or class definition that was not captured"
    if _MULTIPLE_CHOICE.search(text) and not re.search(r"\bA\.", text):
        return False, "multiple-choice stem whose options were not captured"
    if re.search(r"\bcalculate\b|\bnumeric|\bvalue of\b|\bsum of\b", text, re.IGNORECASE) and not _CODE_COMPLETION.search(text):
        # A worked calculation needs the inputs, which are usually in the
        # listing that was lost. Treated as unanswerable rather than guessed.
        if re.search(r"\bthe following\b|\bvalues?\b|\bdata\b", text, re.IGNORECASE):
            return False, "worked calculation whose inputs were not captured"
    return True, ""


def retrieve_grounding(prompt: str, module_id: str, *, top_k: int = 3) -> tuple[str, Optional[float]]:
    """Fetch the module's own material for this question.

    Returns ``(passages, best_distance)``; ``best_distance`` is ``None`` when
    nothing was retrieved at all.
    """
    from .retriever import get_retriever

    try:
        result = get_retriever().retrieve(query=prompt, module_id=module_id)
    except Exception as exc:  # noqa: BLE001 - retrieval is best-effort here
        logger.warning("Retrieval failed for %s: %s", module_id, exc)
        return "", None

    chunks = getattr(result, "chunks", None) or []
    if not chunks:
        return "", None

    best = min((float(getattr(c, "distance", 1.0)) for c in chunks), default=1.0)
    parts: list[str] = []
    for chunk in chunks[:top_k]:
        # ``RetrievedChunk.text``, not ``chunk_text`` - the latter is the column
        # name on CurriculumChunk, and reading the wrong one silently yields zero
        # passages, which looks exactly like "the notes do not cover this".
        text = str(getattr(chunk, "text", "") or "").strip()
        if text:
            heading = str(getattr(chunk, "section_title", "") or "").strip()
            source = str(getattr(chunk, "source_name", "") or "").strip()
            label = heading or source
            parts.append(f"[{label}]\n{text}" if label else text)
    return "\n\n".join(parts), best


def _clean(answer: str) -> str:
    text = (answer or "").strip()
    # The model echoes its own scaffolding often enough to be worth stripping.
    text = re.sub(r"^(?:\*\*)?(?:answer|response)(?:\*\*)?\s*[:\-]\s*", "", text, flags=re.I).strip()
    # A bolded or headed opening line, e.g. "**Advantage of an Interface:**".
    text = re.sub(r"^(?:#+\s*|\*\*(?:[^*:]{0,80}?)\*\*\s*:?\s*\n)", "", text).strip()
    text = re.sub(r"^(?:\*\*)?[^*]{0,80}:\*\*\s*", "", text).strip()
    # Trailing "Note: ..." the model likes to add on the end.
    text = re.sub(r"\n+\s*(?:note|remember)\s*:.*\Z", "", text, flags=re.IGNORECASE | re.DOTALL)
    # Leftover emphasis markers anywhere in the body.
    text = text.replace("**", "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def draft_answer(prompt: str, passages: str) -> Optional[str]:
    """Ask the local model for a reference answer, given the passages."""
    from .llm_router import get_router

    if passages:
        instruction = (
            "You are a marking scheme for a university exam. Using ONLY the "
            "notes below, write the expected answer to the question. Be concise "
            "and specific to the course. If the notes do not cover the question, "
            "reply with exactly: NOT COVERED\n\n"
            f"NOTES:\n{passages[:5000]}\n\n"
            f"QUESTION: {prompt}"
        )
    else:
        instruction = (
            "You are a marking scheme for a university exam. Write the expected "
            "answer to the question, in three to five sentences, at the level a "
            "final-year undergraduate would give. Be specific and factual.\n\n"
            f"QUESTION: {prompt}"
        )

    try:
        response = get_router().generate(
            messages=[{"role": "user", "content": instruction}],
            max_tokens=260,
        )
    except Exception as exc:  # noqa: BLE001 - the model being down must not stop a run
        logger.warning("Generation failed: %s", exc)
        return None

    answer = _clean(response.text)
    if answer.upper().startswith("NOT COVERED"):
        return None
    if len(answer) < MIN_ANSWER_CHARS:
        logger.info("Discarding a too-short draft: %s", answer[:60])
        return None
    return answer


def _load_fixture(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("questions") or []


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--limit", type=int, default=0, help="stop after N questions")
    parser.add_argument("--module", default="", help="restrict to one module")
    parser.add_argument("--dry-run", action="store_true", help="tier only, generate nothing")
    parser.add_argument(
        "--only",
        choices=("grounded", "general", "unanswerable"),
        default="",
        help="restrict generation to one tier",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not args.fixture.exists():
        print(f"No fixture at {args.fixture}", file=sys.stderr)
        return 2

    all_questions = _load_fixture(args.fixture)
    if args.module:
        questions = [q for q in all_questions if q["module_id"] == args.module.upper()]
    else:
        questions = list(all_questions)
    # Skip anything that already has an answer, so a run is resumable.
    questions = [q for q in questions if not (q.get("answer_notes") or "").strip()]

    if args.limit:
        questions = questions[: args.limit]

    print(f"{len(questions)} question(s) without an answer")

    tiers: dict[str, int] = {"grounded": 0, "general": 0, "unanswerable": 0}
    written = 0

    def save() -> None:
        """Write progress to the fixture.

        Called periodically as well as at the end. On CPU the model manages only a
        few tokens a second, so a full run takes over an hour; saving only on
        completion would throw away every draft if the run were interrupted.

        Writes ``all_questions``, not the ``questions`` work list. That work
        list is filtered down to the questions still lacking an answer, so
        writing it would replace the whole bank with just the unanswered subset -
        which is exactly what an earlier version did, truncating a 317-question
        fixture to 12.
        """
        payload = json.loads(args.fixture.read_text(encoding="utf-8"))
        payload["questions"] = all_questions
        counts: dict[str, int] = {}
        answerable: dict[str, int] = {}
        for entry in questions:
            module_id = entry["module_id"]
            counts[module_id] = counts.get(module_id, 0) + 1
            if (entry.get("answer_notes") or "").strip():
                answerable[module_id] = answerable.get(module_id, 0) + 1
        payload["counts_by_module"] = dict(sorted(counts.items()))
        payload["counts_with_answer_by_module"] = dict(sorted(answerable.items()))
        args.fixture.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    for index, question in enumerate(questions, start=1):
        prompt = question["prompt"]
        module_id = question["module_id"]

        answerable, reason = is_answerable(prompt)
        if not answerable:
            tiers["unanswerable"] += 1
            if args.dry_run:
                print(f"  [{index}] UNANSWERABLE  {prompt[:64]}")
            else:
                question["answer_notes"] = None
                question["answer_source"] = None
                question["answer_note"] = UNANSWERABLE_LABEL
                question["answer_blocked_reason"] = reason
            continue

        passages, best = retrieve_grounding(prompt, module_id)
        grounded = bool(passages) and best is not None and best <= GROUNDING_THRESHOLD
        tier = "grounded" if grounded else "general"
        if args.only and tier != args.only:
            continue

        if args.dry_run:
            tiers[tier] += 1
            print(f"  [{index}] {tier.upper():12s} d={best if best is None else round(best,3)}  {prompt[:56]}")
            continue

        answer = draft_answer(prompt, passages if grounded else "")
        if answer is None:
            # Grounded but the notes did not actually support an answer: treat it
            # as unanswerable rather than falling back to invention.
            if grounded:
                tiers["unanswerable"] += 1
                question["answer_notes"] = None
                question["answer_source"] = None
                question["answer_note"] = UNANSWERABLE_LABEL
                question["answer_blocked_reason"] = (
                    "the lecture material did not cover this question"
                )
                print(f"  [{index}] UNANSWERABLE (notes silent)  {prompt[:52]}")
                continue
            tiers["unanswerable"] += 1
            question["answer_notes"] = None
            question["answer_source"] = None
            question["answer_note"] = UNANSWERABLE_LABEL
            question["answer_blocked_reason"] = "the model produced nothing usable"
            print(f"  [{index}] UNANSWERABLE (no draft)      {prompt[:52]}")
            continue

        tiers[tier] += 1
        written += 1
        question["answer_notes"] = answer
        question["answer_source"] = "generated"
        question["answer_note"] = GROUNDED_LABEL if grounded else GENERAL_LABEL
        if grounded and best is not None:
            question["answer_grounding_distance"] = round(float(best), 4)
        print(f"  [{index}] {tier.upper():12s} d={best if best is None else round(best,3)}  {prompt[:52]}")

        if index % 10 == 0:
            print(f"      ... {index}/{len(questions)}  written={written}", flush=True)
            if not args.dry_run:
                save()

    total = max(1, sum(tiers.values()))
    print()
    print(f"grounded    : {tiers['grounded']:4d}  ({100*tiers['grounded']//total}%)  answers written from the notes")
    print(f"general     : {tiers['general']:4d}  ({100*tiers['general']//total}%)  answers written from general knowledge")
    print(f"unanswerable: {tiers['unanswerable']:4d}  ({100*tiers['unanswerable']//total}%)  no answer written")

    if args.dry_run:
        print("\nDry run - nothing written.")
        return 0

    if written and not args.dry_run:
        save()
        print(f"\nWrote {args.fixture}")

    print("\nEvery draft is answer_source='generated', which mark_attempt refuses to")
    print("mark against. Only a lecturer-authored answer is auto-graded.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())