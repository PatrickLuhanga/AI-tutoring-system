"""Split merged exam blocks back into the individual questions they contain.

OCR reads a numbered or multi-part section as one run of text, so a row in the
bank is often several questions glued together - the kind of thing no marking
scheme can be written against and no student can answer sensibly. A block like

    State and briefly describe one advantage of an interface over abstract class.
    "Interfaces or abstracts classes are preferred to concrete class". Explain why.
    Briefly explain the importance of standardization in data exchange.

is three questions, not one.

Splitting is deliberately conservative. A line continues the previous question
unless it *starts* one - an imperative verb or an interrogative opening - because
OCR also wraps a single long question across several lines and cutting on every
line would shred it. A fragment left over from the cut is discarded rather than
banked, since a half-sentence is worse than no question at all.

``true or false`` sections are handled separately. OCR reliably captures the list
of statements and reliably loses which number belongs to which, so the statements
are recovered individually and each becomes its own question; they cannot be
re-paired with the original numbering, so the numbers are not invented either.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

DEFAULT_FIXTURE = (
    Path(__file__).resolve().parent.parent / "exam_papers" / "question_bank.json"
)

#: A line opening a question. Short verbs only - a longer phrase like "provides
#: an example" should not be read as an instruction.
_OPENS = re.compile(
    r"^\W*(?:state|briefly|explain|describe|discuss|define|list|outline|write|complete|"
    r"illustrate|compare|contrast|differentiate|calculate|give|suggest|provide|"
    r"identify|justify|recommend|evaluate|examine|analyse|analyze|critically|"
    r"indicate|draw|elaborate|name|comment|determine|examine)\b",
    re.IGNORECASE,
)
#: An interrogative opening a question.
_ASKS = re.compile(r"^\W*(?:what|which|how|why|when|where|who|whose|whom)\b", re.IGNORECASE)

#: A continuation of a sentence already in progress: it starts lowercase and does
#: not open or ask anything. Treated as body text, never as a new question.
_CONTINUATION = re.compile(r"^[a-z(]")

TRUE_FALSE = re.compile(
    r"true\s+or\s+false|select\s+true|state\s+whether", re.IGNORECASE
)

MIN_CHARS = 24
MIN_WORDS = 5


#: A quoted claim followed by its instruction, which is how papers phrase a
#: stimulus: ``"Interfaces are preferred to concrete classes". Explain why.``
#: The line starts with a quotation mark, so it matches neither an imperative at
#: the front nor an interrogative - the instruction is at the *end*.
_QUOTED_CLAIM = re.compile(
    r"""["'“”]\s*\.?\s*(?:briefly\s+)?(?:explain|describe|discuss|state|justify|"""
    r"""elaborate|illustrate|comment|defend|critically)\b""",
    re.IGNORECASE,
)


def _opens_question(line: str) -> bool:
    """True when a line begins a new question rather than continuing one.

    OCR wraps a long question across several lines, and a wrapped line starts
    lowercase - so capitalisation is the signal that separates the two. This has
    to be checked *before* the interrogative rule: a continuation like "which
    extends Employee). The method toString() returns the" opens with a
    question word, and treating it as a new question cuts the question in half
    and leaves both halves unanswerable.
    """
    stripped = line.strip()
    if not stripped:
        return False
    # Checked first: a quoted claim opens with a quotation mark, which is neither
    # an opening case nor a continuation.
    if _QUOTED_CLAIM.search(stripped):
        return True
    if _CONTINUATION.match(stripped):
        return False
    return bool(_ASKS.match(stripped) or _OPENS.match(stripped))


def _usable(text: str) -> bool:
    body = (text or "").strip()
    return len(body) >= MIN_CHARS and len(body.split()) >= MIN_WORDS


def split_blob(prompt: str) -> list[str]:
    """Split one merged block into the questions it contains.

    Returns a single-element list for a block that is already one question, so a
    caller can treat every block the same way.
    """
    lines = [line.strip() for line in (prompt or "").split("\n") if line.strip()]
    if len(lines) < 2:
        return [prompt.strip()] if _usable(prompt) else []

    questions: list[str] = []
    current: list[str] = []

    for line in lines:
        if _opens_question(line):
            if current and _usable("\n".join(current)):
                questions.append("\n".join(current).strip())
            current = [line]
        elif current:
            # Continuation of the question in progress.
            current.append(line)
        # A line before any opener is a fragment of a header; it is dropped.

    if current and _usable("\n".join(current)):
        questions.append("\n".join(current).strip())

    return questions or ([prompt.strip()] if _usable(prompt) else [])


def split_true_false(prompt: str) -> list[str]:
    """Recover the statements of a true/false section as separate questions.

    The statements are captured reliably; the numbering that pairs them to the
    instruction is not. Each statement therefore becomes its own question with
    the instruction restated, rather than being numbered as though we knew which
    number it had.
    """
    lines = [line.strip() for line in (prompt or "").split("\n") if line.strip()]
    if len(lines) < 3:
        return []

    instruction = lines[0]
    statements = [line for line in lines[1:] if _usable(line)]
    if len(statements) < 2:
        return []

    out: list[str] = []
    for statement in statements:
        # A statement that already carries its own instruction is not a bare
        # assertion and reads oddly with the prefix bolted on.
        if _opens_question(statement):
            out.append(statement)
        else:
            out.append(
                f'State whether the following is true or false: "{statement}"'
            )
    return [q for q in out if _usable(q)]


#: A line that ends a sentence. Used to tell a list of statements from prose that
#: merely happens to wrap: a wrapped line has no full stop, and OCR drops the
#: final one often enough that a strict test misses real lists.
_SENTENCE_END = re.compile(r"[.?!]\s*$")
#: A sentence boundary, for rejoining wrapped lines before splitting.
_SENTENCE_SPLIT = re.compile(r"(?<=[.?!])\s+")


def split_statement_list(prompt: str) -> list[str]:
    """Recover a bare list of assertions as individual true/false questions.

    Some sections lose the instruction entirely - "State whether the following is
    true or false" is dropped and only the assertions survive. Left whole, the
    block is ten questions in one row, and asking a model to answer it produces an
    answer to whichever assertion it happened to read first, which is worse than
    no answer because it looks right.

    Lines are rejoined before splitting so a single assertion wrapped across two
    lines stays one assertion. The block is only treated this way when most lines
    are complete sentences, which is what separates a list of claims from prose
    that wraps.
    """
    lines = [line.strip() for line in (prompt or "").split("\n") if line.strip()]
    if len(lines) < 3:
        return []

    complete = sum(1 for line in lines if _SENTENCE_END.search(line))
    if complete < 3 or complete * 2 < len(lines):
        # Most lines are fragments: this is wrapped prose, not a list.
        return []

    joined = " ".join(lines)
    statements = [
        part.strip()
        for part in _SENTENCE_SPLIT.split(joined)
        if _usable(part)
    ]
    if len(statements) < 3:
        return []

    return [
        f'State whether the following is true or false: "{statement}"'
        for statement in statements
    ]


def split_question(prompt: str) -> list[str]:
    """Split a block, choosing the strategy from its own text."""
    if TRUE_FALSE.search(prompt or ""):
        recovered = split_true_false(prompt)
        if recovered:
            return recovered

    # A section whose instruction was lost leaves no imperative anywhere, so the
    # ordinary splitter has nothing to cut on and returns the block whole.
    recovered = split_statement_list(prompt)
    if recovered:
        return recovered

    return split_blob(prompt)


def iter_atomic(questions: list[dict]) -> Iterator[dict]:
    """Yield one dict per atomic question, carrying provenance from its parent."""
    for entry in questions:
        parts = split_question(entry.get("prompt", ""))
        if not parts:
            logger.info("Dropped an unsplittable block: %.70s", entry.get("prompt", ""))
            continue
        for index, part in enumerate(parts, start=1):
            child = dict(entry)
            child["prompt"] = part
            # Provenance gains the part number so a lecturer can still find the
            # exact sub-question on the page.
            label = entry.get("source_label") or ""
            if len(parts) > 1 and label:
                child["source_label"] = f"{label} part {index}"
            yield child


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--out", type=Path, default=None, help="write here (default: in place)")
    parser.add_argument("--dry-run", action="store_true", help="report counts, write nothing")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not args.fixture.exists():
        print(f"No fixture at {args.fixture}", flush=True)
        return 2

    payload = json.loads(args.fixture.read_text(encoding="utf-8"))
    original = payload.get("questions") or []

    out: list[dict] = []
    split = 0
    for child in iter_atomic(original):
        out.append(child)
        if " part " in (child.get("source_label") or ""):
            split += 1

    before_by_module: dict[str, int] = {}
    after_by_module: dict[str, int] = {}
    for entry in original:
        before_by_module[entry["module_id"]] = before_by_module.get(entry["module_id"], 0) + 1
    for entry in out:
        after_by_module[entry["module_id"]] = after_by_module.get(entry["module_id"], 0) + 1

    print(f"{len(original)} blocks -> {len(out)} atomic questions")
    for module_id in sorted(before_by_module):
        print(
            f"  {module_id}: {before_by_module[module_id]:4d} -> {after_by_module.get(module_id, 0):4d}"
        )

    if args.dry_run:
        print("\nDry run - nothing written.")
        return 0

    payload["questions"] = out
    counts: dict[str, int] = {}
    for entry in out:
        counts[entry["module_id"]] = counts.get(entry["module_id"], 0) + 1
    payload["counts_by_module"] = dict(sorted(counts.items()))

    target = args.out or args.fixture
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nWrote {target}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())