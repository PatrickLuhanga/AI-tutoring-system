"""Retrieval evaluation harness (paper section 3.4).

Scores the RAG Orchestrator against the expert-labelled test set in
``eval/test_set.csv`` and reports recall@k, MRR and grounding diagnostics.

Retrieval only - no LLM is called, so this is deterministic and cheap. The
generation-side metrics (answer quality, over-reliance) are a separate harness.

Usage:
    python -m eval.run_retrieval_eval                      # default policy
    python -m eval.run_retrieval_eval --top-k 5
    python -m eval.run_retrieval_eval --allow-answers      # ablation
    python -m eval.run_retrieval_eval --include-third-party
    python -m eval.run_retrieval_eval --no-answer-filter   # ablation
    python -m eval.run_retrieval_eval --validate-only
"""

from __future__ import annotations

import argparse
import csv
import sys
import unicodedata
from dataclasses import dataclass
from dataclasses import fields as dataclass_fields
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select  # noqa: E402

from src.config import settings  # noqa: E402
from src.db import session_scope  # noqa: E402
from src.models import CurriculumChunk  # noqa: E402
from src.retriever import RetrievalPolicy, get_retriever  # noqa: E402

TEST_SET = PROJECT_ROOT / "eval" / "test_set.csv"


def norm(text: str | None) -> str:
    """Normalise a section title for tolerant comparison."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if not unicodedata.category(ch).startswith("C"))
    return " ".join(text.split()).strip().casefold()


def section_matches(expected: str, actual: str | None) -> bool:
    """A retrieved section counts as correct if it names the same material.

    Tolerates the trailing whitespace and duplicated ``Slide N:`` prefixes that
    survive OCR, in either direction.
    """
    e, a = norm(expected), norm(actual)
    if not e or not a:
        return False
    return e == a or e in a or a in e


@dataclass
class Row:
    test_id: str
    module_id: str
    question: str
    question_type: str
    expected_source_file: str
    expected_section: str
    must_include: str
    expected_behaviour: str
    verified_by: str
    notes: str


def load_rows(path: Path) -> list[Row]:
    """Load the test set, tolerating a stray unquoted comma in a free-text column.

    A lecturer editing this in Excel will eventually leave a comma unquoted. That
    should not take the whole evaluation down, so extra fields are dropped and
    missing ones default to empty rather than raising.
    """
    if not path.exists():
        raise SystemExit(f"Test set not found: {path}")
    fields = {f.name for f in dataclass_fields(Row)}
    rows: list[Row] = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for raw in csv.DictReader(fh, restkey="__extra__"):
            if not raw.get("test_id"):
                continue
            if raw.get("__extra__"):
                print(
                    f"  WARNING: {raw['test_id']} has more columns than the header "
                    f"(unquoted comma in a note?). Extra text ignored."
                )
            rows.append(Row(**{k: (v or "") for k, v in raw.items() if k in fields}))
    # Skip the leading DRAFT-STATUS banner row if present.
    return [r for r in rows if not r.test_id.upper().startswith("DRAFT")]


def validate(rows: list[Row]) -> list[str]:
    """Check every expected source/section actually exists in the vector store."""
    problems: list[str] = []
    with session_scope() as s:
        for row in rows:
            if not row.expected_source_file:
                continue
            files = {
                f for f in s.execute(
                    select(CurriculumChunk.source_file).distinct().where(
                        CurriculumChunk.module_id == row.module_id
                    )
                ).scalars()
            }
            if row.expected_source_file not in files:
                close = [f for f in files if norm(row.expected_source_file) in norm(f)]
                problems.append(
                    f"{row.test_id}: source_file '{row.expected_source_file}' not in "
                    f"{row.module_id}." + (f" Did you mean {close[0]!r}?" if close else "")
                )
                continue
            if not row.expected_section:
                continue
            sections = [
                t
                for t in s.execute(
                    select(CurriculumChunk.section_title).distinct().where(
                        CurriculumChunk.module_id == row.module_id,
                        CurriculumChunk.source_file == row.expected_source_file,
                    )
                ).scalars()
                if t
            ]
            if not any(section_matches(row.expected_section, t) for t in sections):
                problems.append(
                    f"{row.test_id}: section '{row.expected_section}' not found in "
                    f"{row.expected_source_file}"
                )
    return problems


#: Maps the test set's question_type onto the Intent Agent's label vocabulary.
_EXPECTED_LABEL = {
    "factual": "factual",
    "conceptual": "conceptual",
    "debugging": "debugging",
    "problem_solving": "problem_solving",
    "bypass": "bypass",
}


def score_intent(rows: list[Row], *, per_question: bool = False) -> int:
    """Score Intent Agent label + route against the test set's question_type.

    Deterministic and free: the heuristic classifier needs no LLM. ``route`` is
    what actually matters pedagogically - ``direct`` bypasses the Scaffolding
    Engine, so a conceptual question misrouted to ``direct`` hands the student the
    answer on turn one.
    """
    from src.agents.intent_agent import IntentAgent

    agent = IntentAgent(use_llm=False)
    total = correct = route_ok = 0
    per_type: dict[str, list[int]] = {}
    route_misses: list[str] = []

    for row in rows:
        expected = _EXPECTED_LABEL.get(row.question_type.strip().lower())
        if expected is None:
            continue
        total += 1
        intent = agent._heuristic(row.question)
        hit = intent.label == expected
        correct += hit
        per_type.setdefault(expected, []).append(1 if hit else 0)
        # The pedagogically dangerous failure: should scaffold, answered directly.
        dangerous = expected in {"conceptual", "problem_solving", "bypass"} and intent.route == "direct"
        if not dangerous:
            route_ok += 1
        else:
            route_misses.append(f"{row.test_id} ({expected} -> {intent.label}/{intent.route})")
        if per_question or not hit:
            flag = "ok  " if hit else "MISS"
            print(f"  [{flag}] {row.test_id:<8} want={expected:<15} got={intent.label:<15} "
                  f"route={intent.route:<9} {'<-- SCAFFOLDING BYPASSED' if dangerous else ''}")
            if not hit:
                print(f"           Q: {row.question[:70]}")

    n = max(total, 1)
    print("\n" + "=" * 78)
    print("INTENT AGENT RESULTS (heuristic, no LLM)")
    print("=" * 78)
    print(f"  Label accuracy            : {correct / n:.3f}  ({correct}/{total})")
    print(f"  Scaffolding preserved     : {route_ok / n:.3f}  ({route_ok}/{total})")
    print("\n  Label accuracy by expected class:")
    for label, outcomes in sorted(per_type.items()):
        print(f"    {label:<18} {sum(outcomes) / len(outcomes):.3f}  (n={len(outcomes)})")
    if route_misses:
        print(f"\n  FAIL: {len(route_misses)} question(s) that should have scaffolded were "
              f"answered directly:")
        for m in route_misses:
            print(f"    - {m}")
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--test-set", type=Path, default=TEST_SET)
    ap.add_argument("--top-k", type=int, default=None)
    ap.add_argument("--allow-answers", action="store_true",
                    help="Ablation: do not withhold worked solutions.")
    ap.add_argument("--include-third-party", action="store_true",
                    help="Ablation: do not exclude commercial textbooks.")
    ap.add_argument("--no-answer-filter", action="store_true",
                    help="Ablation alias for --allow-answers.")
    ap.add_argument("--no-distance-floor", action="store_true",
                    help="Ablation: accept chunks beyond RETRIEVAL_MAX_DISTANCE.")
    ap.add_argument("--validate-only", action="store_true")
    ap.add_argument("--intent-only", action="store_true",
                    help="Score Intent Agent routing only (no retrieval, no LLM).")
    ap.add_argument("--per-question", action="store_true", help="Show every query.")
    args = ap.parse_args(argv)

    rows = load_rows(args.test_set)
    print("=" * 78)
    print(f"RETRIEVAL EVALUATION   {len(rows)} queries from {args.test_set.name}")
    print("=" * 78)
    verified = sum(1 for r in rows if r.verified_by.strip())
    print(f"  labelled by a domain expert : {verified}/{len(rows)}"
          + ("" if verified == len(rows) else "   <-- INCOMPLETE, paper requires expert sign-off"))

    problems = validate(rows)
    if problems:
        print(f"\n  {len(problems)} ground-truth problem(s):")
        for p in problems:
            print(f"    - {p}")
    else:
        print("  ground truth validates against the vector store")
    if args.validate_only:
        return 1 if problems else 0

    if args.intent_only:
        return score_intent(rows, per_question=args.per_question)

    allow_answers = args.allow_answers or args.no_answer_filter
    policy = RetrievalPolicy(
        allow_answers=allow_answers,
        include_third_party=args.include_third_party or settings.retrieval_include_third_party,
        max_distance=(None if args.no_distance_floor else (settings.retrieval_max_distance or None)),
    )
    retriever = get_retriever()
    if args.top_k:
        retriever.top_k = args.top_k

    print(f"\n  policy: top_k={retriever.top_k} allow_answers={policy.allow_answers} "
          f"include_third_party={policy.include_third_party} "
          f"max_distance={policy.max_distance}")
    if allow_answers or args.include_third_party or args.no_distance_floor:
        print("  (ABLATION RUN - not the deployed configuration)")

    hit_at_1 = hit_at_k = 0
    file_hit_at_1 = file_hit_at_k = 0
    reciprocal_rank = 0.0
    scored = 0
    fallbacks = 0
    leaks = 0
    empties = 0
    per_type: dict[str, list[int]] = {}
    misses: list[str] = []

    print()
    for row in rows:
        result = retriever.retrieve(row.question, row.module_id, include_patterns=False,
                                    policy=policy)
        chunks = result.chunks
        if result.third_party_fallback:
            fallbacks += 1
        if any(c.is_answer for c in chunks):
            leaks += 1
        if result.is_empty:
            empties += 1

        # Two granularities. "File" = did we reach the right deck/document at all.
        # "Section" = did we reach the right slide within it. Retrieval is graded
        # far more harshly on the second, and the gap between them is itself a
        # finding worth reporting.
        rank = 0
        file_rank = 0
        for i, c in enumerate(chunks, start=1):
            if not file_rank and c.source_file == row.expected_source_file:
                file_rank = i
            if (c.source_file == row.expected_source_file
                    and section_matches(row.expected_section, c.section_title)):
                rank = i
                break
        scored += 1
        if rank == 1:
            hit_at_1 += 1
        if rank:
            hit_at_k += 1
            reciprocal_rank += 1.0 / rank
        else:
            misses.append(row.test_id)
        if file_rank == 1:
            file_hit_at_1 += 1
        if file_rank:
            file_hit_at_k += 1
        per_type.setdefault(row.question_type, []).append(1 if rank else 0)

        if args.per_question:
            if chunks:
                mark = f"rank {rank}" if rank else "MISS"
                cats = ",".join(result.grounding_categories)
                extra = "  FALLBACK" if result.third_party_fallback else ""
                print(f"  [{mark:>6}] {row.test_id}  d0={chunks[0].distance:.3f}  "
                      f"{row.question_type:<11}{extra}")
                print(f"            Q: {row.question[:68]}")
                print(f"            -> {cats} | {chunks[0].source_name}")
            else:
                print(f"  [ EMPTY] {row.test_id}  {row.question_type:<11} "
                      f"(no chunk passed the filters)")
                print(f"            Q: {row.question[:68]}")

    n = max(scored, 1)
    k = retriever.top_k
    print("\n" + "=" * 78)
    print("RESULTS")
    print("=" * 78)
    print("  Document-level (did we reach the right deck/source file?)")
    print(f"    Recall@1          : {file_hit_at_1 / n:.3f}  ({file_hit_at_1}/{scored})")
    print(f"    Recall@{k}         : {file_hit_at_k / n:.3f}  ({file_hit_at_k}/{scored})")
    print("\n  Slide-level (did we reach the right slide within it?)")
    print(f"    Recall@1          : {hit_at_1 / n:.3f}  ({hit_at_1}/{scored})")
    print(f"    Recall@{k}         : {hit_at_k / n:.3f}  ({hit_at_k}/{scored})")
    print(f"    MRR               : {reciprocal_rank / n:.3f}")
    print(f"    Precision@{k}       : {hit_at_k / max(n * k, 1):.3f}"
          "   (of the chunks returned, how many were the right slide)")
    print("\n  Grounding diagnostics")
    print(f"    Empty contexts        : {empties}/{scored}")
    print(f"    Third-party fallbacks : {fallbacks}/{scored}")
    print(f"    Answer-chunk leaks    : {leaks}/{scored}"
          + ("   <-- policy disabled" if allow_answers else "   (must be 0)"))
    print("\n  Slide-level recall by question type:")
    for qtype, outcomes in sorted(per_type.items()):
        print(f"    {qtype:<14} {sum(outcomes) / len(outcomes):.3f}  (n={len(outcomes)})")
    if misses:
        print(f"\n  Slide-level misses: {', '.join(misses)}")
    if leaks and not allow_answers:
        print("\n  FAIL: worked solutions were returned while answers were withheld.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
