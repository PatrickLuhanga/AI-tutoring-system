"""Generate practice questions from the past-paper bank with the Groq LLM.

The ``exam_papers/question_bank.json`` fixture is real exam content, but it was
produced by OCR from scanned papers: wording is imperfect, PDF footer bleed and
page markers leak into prompts, and several questions are only half-present.
This module takes each messy past-paper question and asks the configured **cloud
LLM** (Groq by default) to do two things:

1. **Clean** the question: fix the formatting and remove the PDF footer/header
   bleed so it reads as a proper exam question.
2. **Generate** three brand-new, distinct multiple-choice questions (each with
   four options, a correct answer index and a short explanation) at the *same
   topic and difficulty* as the source question.

Results are written to the database (``questions`` table) and/or a new JSON file
so the bank can be seeded on another machine. MCQs are stored with their stem in
``prompt`` and a rendered ``answer_notes`` block, because the practice UI and
marker are built around a prompt plus free-text answer notes; keeping that shape
means no schema change and no change to the marking path.

Usage::

    python -m src.generate_practice --dry-run --limit 3
    python -m src.generate_practice --limit 5 --out exam_papers/generated_practice.json
    python -m src.generate_practice --module RESK301 --limit 4 --in-db

The cloud provider is chosen by the router's active configuration, which is Groq
by default (``LLM_PROVIDER=cloud``). A local Ollama model would be far too slow
for the per-question generation this script performs.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .config import PROJECT_ROOT

logger = logging.getLogger(__name__)

#: Where the messy past-paper bank lives.
DEFAULT_BANK = PROJECT_ROOT / "exam_papers" / "question_bank.json"
#: Where generated output goes when ``--out`` is not given.
DEFAULT_OUT = PROJECT_ROOT / "exam_papers" / "generated_practice.json"

#: A question is "messy" (worth cleaning) when it carries OCR/footer noise.
_FOOTER_MARKERS = (
    "ocr - verify",
    "page ",
    "part ",
    "downloaded",
    "please turn over",
    "copyright",
    "all rights reserved",
)


@dataclass(slots=True)
class SourceQuestion:
    module_id: str
    prompt: str
    difficulty: str
    origin: str
    source_label: Optional[str]
    answer_notes: Optional[str]


@dataclass(slots=True)
class GeneratedQuestion:
    module_id: str
    prompt: str
    options: list[str]
    correct_index: int
    explanation: str
    difficulty: str
    topic: str
    based_on: str
    model: str


def load_bank(path: Path) -> list[SourceQuestion]:
    """Read the fixture into :class:`SourceQuestion` rows."""
    if not path.exists():
        raise FileNotFoundError(f"No question bank at {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows: list[SourceQuestion] = []
    for item in payload.get("questions") or []:
        prompt = str(item.get("prompt") or "").strip()
        module_id = str(item.get("module_id") or "").strip()
        if not prompt or not module_id:
            continue
        rows.append(
            SourceQuestion(
                module_id=module_id,
                prompt=prompt,
                difficulty=str(item.get("difficulty") or "medium").lower(),
                origin=str(item.get("origin") or "past_paper"),
                source_label=item.get("source_label"),
                answer_notes=item.get("answer_notes"),
            )
        )
    return rows


def _looks_messy(question: SourceQuestion) -> bool:
    blob = f"{question.prompt} {question.source_label or ''}".lower()
    if any(marker in blob for marker in _FOOTER_MARKERS):
        return True
    # Collapsed whitespace, stray backticks or a dangling "diagram below" are
    # also signs the OCR pass left the prompt in a rough state.
    return "  " in question.prompt or question.prompt.endswith("...")


#: The generation prompt. It is deliberately explicit about the output shape,
#: because a 3-8B model follows a worked schema far more reliably than prose.
_SYSTEM = """You are an academic exam editor and question author for a university module.
You are given one messy past-paper question. Do two jobs and reply with a single
JSON object and nothing else:

1. Clean the question: fix punctuation and spacing, expand obvious OCR mistakes,
   and delete any PDF footer/header/page-number bleed ("Page 3 of 9", "(OCR -
   verify)", "Please turn over", copyright lines). Keep the original meaning and
   the exact topic and difficulty. Do NOT add new facts.
2. Write THREE brand new, mutually distinct multiple-choice questions that test
   the SAME topic at the SAME difficulty as the cleaned question. Each must have
   exactly four options, one correct option, and a one-sentence explanation of
   why the correct option is right.

Reply with this exact JSON shape:
{
  "cleaned_question": "<the de-noised question>",
  "mcqs": [
    {"question": "<stem>", "options": ["<a>", "<b>", "<c>", "<d>"],
     "correct_index": 0, "explanation": "<why>"}
  ]
}
"""


def _extract_json(text: str) -> Optional[dict[str, Any]]:
    """Leniently pull the first JSON object out of a model reply."""
    if not text:
        return None
    candidates = [text.strip()]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def _coerce_mcq(raw: Any, *, module_id: str, difficulty: str, topic: str, model: str) -> Optional[GeneratedQuestion]:
    if not isinstance(raw, dict):
        return None
    stem = str(raw.get("question") or "").strip()
    options = raw.get("options")
    if not stem or not isinstance(options, list):
        return None
    cleaned_options = [str(o).strip() for o in options if str(o).strip()]
    if len(cleaned_options) != 4:
        return None
    try:
        correct = int(raw.get("correct_index"))
    except (TypeError, ValueError):
        return None
    if correct < 0 or correct > 3:
        return None
    explanation = str(raw.get("explanation") or "").strip()
    return GeneratedQuestion(
        module_id=module_id,
        prompt=stem,
        options=cleaned_options,
        correct_index=correct,
        explanation=explanation,
        difficulty=difficulty,
        topic=topic,
        based_on="",
        model=model,
    )


def _render_mcq_notes(mcq: GeneratedQuestion) -> str:
    """Free-text answer notes for the existing ``questions``-table shape.

    The practice UI shows ``answer_notes`` after submission and never auto-grades
    a generated answer, so the rendered block is a safe, self-contained key.
    """
    letters = "ABCD"
    lines = ["Correct answer: " + f"{letters[mcq.correct_index]}. {mcq.options[mcq.correct_index]}"]
    for i, option in enumerate(mcq.options):
        lines.append(f"  {letters[i]}. {option}" + ("  \u2713" if i == mcq.correct_index else ""))
    if mcq.explanation:
        lines.append("")
        lines.append(mcq.explanation)
    return "\n".join(lines)


def generate_for_question(
    question: SourceQuestion,
    *,
    router,
    cleaned_out: list[dict],
    generated_out: list[GeneratedQuestion],
    per_question: int = 3,
) -> tuple[Optional[str], list[GeneratedQuestion]]:
    """Clean one question and generate ``per_question`` MCQs.

    Returns ``(cleaned, mcqs)``. ``per_question`` defaults to 3; a module whose
    source bank is thin (PBDV301 has 8 questions) can ask for more so the module
    reaches a usable bank size.
    """
    from .llm_router import LLMError

    user_prompt = (
        f"Module: {question.module_id}\n"
        f"Difficulty: {question.difficulty}\n"
        f"Existing reference answer (may be empty): {question.answer_notes or 'none'}\n\n"
        f"Messy past-paper question:\n{question.prompt}"
    )
    # The system prompt is fixed at "three"; when a caller wants more, say so
    # explicitly rather than silently truncating its output.
    system = _SYSTEM
    if per_question != 3:
        system = _SYSTEM.replace(
            "Write THREE brand new", f"Write {per_question} brand new"
        ).replace('"mcqs": [', '"mcqs": [')

    try:
        response = router.generate(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=1200 + max(0, per_question - 3) * 300,
            json_mode=True,
        )
    except LLMError as exc:
        logger.error("Generation failed for %s: %s", question.module_id, exc)
        return None, []

    data = _extract_json(response.text)
    if not data:
        logger.warning("Model returned no parsable JSON for: %s", question.prompt[:70])
        return None, []

    cleaned = str(data.get("cleaned_question") or "").strip() or question.prompt
    topic = question.source_label or question.module_id

    mcqs: list[GeneratedQuestion] = []
    for raw in data.get("mcqs") or []:
        mcq = _coerce_mcq(
            raw,
            module_id=question.module_id,
            difficulty=question.difficulty,
            topic=topic,
            model=response.model,
        )
        if mcq is None:
            continue
        mcq.based_on = cleaned
        mcqs.append(mcq)
        if len(mcqs) >= per_question:
            break

    # Record the cleaned prompt alongside its source so the transformation is
    # reviewable before anything is written to the database.
    cleaned_out.append(
        {
            "module_id": question.module_id,
            "prompt": cleaned,
            "original_prompt": question.prompt,
            "difficulty": question.difficulty,
            "source_label": question.source_label,
            "answer_notes": question.answer_notes,
        }
    )
    generated_out.extend(mcqs)
    return cleaned, mcqs


def write_output(path: Path, *, cleaned: list[dict], mcqs: list[GeneratedQuestion]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "source": "AI-generated practice material (Groq). Review before relying on it.",
        "cleaned_questions": cleaned,
        "generated_mcqs": [
            {
                "module_id": m.module_id,
                "prompt": m.prompt,
                "options": m.options,
                "correct_index": m.correct_index,
                "explanation": m.explanation,
                "difficulty": m.difficulty,
                "topic": m.topic,
                "based_on": m.based_on,
                "model": m.model,
            }
            for m in mcqs
        ],
        "counts": {
            "cleaned": len(cleaned),
            "generated_mcqs": len(mcqs),
            "by_module": _counts_by_module(mcqs),
        },
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _counts_by_module(mcqs: list[GeneratedQuestion]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in mcqs:
        counts[m.module_id] = counts.get(m.module_id, 0) + 1
    return dict(sorted(counts.items()))


def persist_to_database(mcqs: list[GeneratedQuestion]) -> int:
    """Insert the MCQs into the ``questions`` table, idempotently."""
    from .practice import PracticeError, add_question

    inserted = 0
    for mcq in mcqs:
        try:
            add_question(
                module_id=mcq.module_id,
                prompt=mcq.prompt,
                answer_notes=_render_mcq_notes(mcq),
                answer_source="generated",
                difficulty=mcq.difficulty,
                origin="generated",
                source_label="AI-generated practice - review before relying on it",
                created_by=None,
            )
        except PracticeError as exc:
            # Usually a near-duplicate of an existing question; skip it.
            logger.info("Skipped a generated question: %s", exc.message)
            continue
        inserted += 1
    return inserted


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bank", type=Path, default=DEFAULT_BANK, help="input fixture")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="where to write the JSON output")
    parser.add_argument("--module", default=None, help="restrict to one module_id, e.g. RESK301")
    parser.add_argument("--limit", type=int, default=5, help="how many source questions to process")
    parser.add_argument("--all", action="store_true", help="process the whole bank (ignores --limit)")
    parser.add_argument(
        "--force",
        action="store_true",
        help="process every selected question even if it does not look messy "
             "(needed for a module whose source bank is already clean, e.g. PBDV301)",
    )
    parser.add_argument(
        "--per-question",
        type=int,
        default=3,
        help="MCQs to request per source question (default 3)",
    )
    parser.add_argument("--in-db", action="store_true", help="also insert the MCQs into the questions table")
    parser.add_argument("--dry-run", action="store_true", help="print the plan without calling the model")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    try:
        bank = load_bank(args.bank)
    except (FileNotFoundError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.module:
        wanted = args.module.strip().upper()
        bank = [q for q in bank if q.module_id.upper() == wanted]

    messy = [q for q in bank if _looks_messy(q)]
    pool = bank if args.force else messy
    selected = pool if args.all else pool[: max(1, args.limit)]

    print(f"Loaded {len(bank)} question(s) from {args.bank}")
    if args.force:
        print(f"--force: processing from all {len(bank)} (messy filter bypassed)")
    else:
        print(f"{len(messy)} look OCR/footer-messy")
    print(f"processing {len(selected)}, {args.per_question} MCQ(s) each")

    if args.dry_run:
        for q in selected:
            print(f"  [{q.module_id}] {q.prompt[:96]}")
        print("\n(dry run - no model calls, nothing written)")
        return 0

    if not selected:
        print("Nothing to process.", file=sys.stderr)
        return 1

    from .llm_router import get_router

    router = get_router()
    try:
        active = router.describe_active()
        print(f"Provider: {active.get('provider')} / {active.get('effective', {}).get('model')}")
    except Exception as exc:  # noqa: BLE001 - provenance is best-effort
        print(f"(could not describe the active model: {exc})")

    cleaned: list[dict] = []
    mcqs: list[GeneratedQuestion] = []

    for i, question in enumerate(selected, start=1):
        print(f"  [{i}/{len(selected)}] {question.module_id}: {question.prompt[:70]}")
        _, produced = generate_for_question(
            question,
            router=router,
            cleaned_out=cleaned,
            generated_out=mcqs,
            per_question=max(1, args.per_question),
        )
        print(f"          -> cleaned + {len(produced)} MCQ(s)")

    write_output(args.out, cleaned=cleaned, mcqs=mcqs)
    print(f"\nWrote {args.out} ({len(cleaned)} cleaned, {len(mcqs)} MCQ(s))")

    if args.in_db:
        inserted = persist_to_database(mcqs)
        print(f"Inserted {inserted} MCQ(s) into the questions table (rest were duplicates).")

    print("These are AI drafts. A lecturer should review them before a student relies on them.")
    return 0 if mcqs else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
