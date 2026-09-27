"""Hybrid-retrieval acceptance tests (tsvector keyword + pgvector semantic + RRF).

Three case groups, all evaluated through the public ``Retriever.retrieve`` API:

* REGRESSION - the original pipeline sanity queries.
* SET_A - the 16 target academic queries (4 per module).
* SET_B - 8 anti-overfitting queries derived from randomly sampled chunks
  (seeded sampling, chunks unrelated to SET_A ground truth).

Every case asserts: non-empty fused top-k, module scoping, and that a minimum
number of known ground-truth chunk ids appear in the fused top-k. The retriever
itself contains no per-query logic - only the global RRF knobs in src/config.py
may be tuned; a case that fails here must be fixed by changing those knobs.

Run from the project root:

    python scripts/test_hybrid_retrieval.py
    python scripts/test_hybrid_retrieval.py --verbose

Exit code is non-zero when any case fails, so it can gate CI or milestone commits.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.config import settings  # noqa: E402
from src.retriever import Retriever  # noqa: E402

REGRESSION = [
    {
        "group": "REGRESSION",
        "module": "RESK301",
        "query": "What are the ten roles in the systematic literature review team?",
        "expect_ids": {8050, 8051, 8052, 8053},
        "min_matches": 3,
    },
    {
        "group": "REGRESSION",
        "module": "RESK301",
        "query": "Describe the stages of the research process",
        "expect_ids": {8533, 8539, 8538, 8535, 8544, 8541},
        "min_matches": 2,
    },
    {
        "group": "REGRESSION",
        "module": "RESK301",
        "query": "What is the difference between quantitative and qualitative research?",
        "expect_ids": {8654, 8655, 8656, 8659, 8662, 8670, 8679},
        "min_matches": 4,
    },
    {
        "group": "REGRESSION",
        "module": "IPRT301",
        "query": "NullPointerException when calling a method on a null String",
        "expect_ids": {345},
        "min_matches": 1,
        "expect_pattern": "java.lang.NullPointerException",
    },
    {
        "group": "REGRESSION",
        "module": "IPRT301",
        "query": "What is polymorphism in Java?",
        "expect_ids": {625, 626},
        "min_matches": 1,
    },
    {
        "group": "REGRESSION",
        "module": "PBDV301",
        "query": "How do I create and use a Python virtual environment?",
        "expect_ids": {4460, 4461, 4538, 4541},
        "min_matches": 2,
    },
]

SET_A = [
    # --- SPRI301 ----------------------------------------------------------
    {
        "group": "SET_A",
        "module": "SPRI301",
        "query": "How does a lack of Human ware hinder ICT development?",
        "expect_ids": {9103, 9121},
        "min_matches": 1,
    },
    {
        "group": "SET_A",
        "module": "SPRI301",
        "query": "What are the consequences of electronic monitoring?",
        "expect_ids": {9110, 9128},
        "min_matches": 1,
    },
    {
        "group": "SET_A",
        "module": "SPRI301",
        "query": "What are the three categories of telecommuters?",
        "expect_ids": {9107, 9125},
        "min_matches": 1,
    },
    {
        "group": "SET_A",
        "module": "SPRI301",
        "query": "What are the ethical concerns of Johnny's hidden cameras?",
        "expect_ids": {9092, 9093, 9094, 9115},
        "min_matches": 2,
    },
    # --- PBDV301 ----------------------------------------------------------
    {
        "group": "SET_A",
        "module": "PBDV301",
        "query": "How does ARM big.LITTLE balance performance and battery?",
        "expect_ids": {5189, 5196},
        "min_matches": 1,
    },
    {
        "group": "SET_A",
        "module": "PBDV301",
        "query": "What are the trade-offs of a Hybrid vs Native app approach?",
        "expect_ids": {5155, 5199, 5136, 5143},
        "min_matches": 2,
    },
    {
        "group": "SET_A",
        "module": "PBDV301",
        "query": "Why does network polling drain battery and how do developers fix it?",
        "expect_ids": {5196, 5193},
        "min_matches": 1,
    },
    {
        "group": "SET_A",
        "module": "PBDV301",
        "query": "How do RAM constraints impact mobile development?",
        "expect_ids": {5191, 5192},
        "min_matches": 1,
    },
    # --- IPRT301 ----------------------------------------------------------
    {
        "group": "SET_A",
        "module": "IPRT301",
        "query": "What is the functional difference between ServerSocket and Socket?",
        "expect_ids": {3350},
        "min_matches": 1,
    },
    {
        "group": "SET_A",
        "module": "IPRT301",
        "query": "How do client and server Input and OutputStreams interact?",
        "expect_ids": {3348, 3351},
        "min_matches": 1,
    },
    {
        "group": "SET_A",
        "module": "IPRT301",
        "query": "What are the two methods to download documents via URL?",
        "expect_ids": {3356},
        "min_matches": 1,
    },
    {
        "group": "SET_A",
        "module": "IPRT301",
        "query": "Why run the server process first and close streams after?",
        "expect_ids": {3352},
        "min_matches": 1,
    },
    # --- RESK301 ----------------------------------------------------------
    {
        "group": "SET_A",
        "module": "RESK301",
        "query": "What are the 6 phases of DSRM?",
        # 8466-8468 are terse "n/6:" slide fragments (cos-sim 0.49-0.56) that no
        # global gate can admit without flooding noise; the DSRM-defining chunks
        # are 8465 (methodology + process figure) and 8471 (research methods).
        "expect_ids": {8465, 8471},
        "min_matches": 2,
    },
    {
        "group": "SET_A",
        "module": "RESK301",
        "query": "How does fog computing via Raspberry Pi improve data processing here?",
        "expect_ids": {8474, 8478},
        "min_matches": 1,
    },
    {
        "group": "SET_A",
        "module": "RESK301",
        "query": "How will cost-effectiveness and usability be measured?",
        "expect_ids": {8468, 8479, 8480},
        "min_matches": 2,
    },
    {
        "group": "SET_A",
        "module": "RESK301",
        "query": "How does the AI chatbot address informativeness?",
        "expect_ids": {8475, 8481},
        "min_matches": 1,
    },
]

# Generated by seeded random sampling of ingested chunks (see the commit
# message for the sampling procedure). Chunks deliberately avoid SET_A sources.
SET_B = [
    {
        "group": "SET_B",
        "module": "SPRI301",
        "query": "What does the recovery phase of crisis response involve?",
        "expect_ids": {9147},
        "min_matches": 1,
    },
    {
        "group": "SET_B",
        "module": "SPRI301",
        "query": "What is social justice and what does it aim to achieve?",
        "expect_ids": {9135},
        "min_matches": 1,
    },
    {
        "group": "SET_B",
        "module": "PBDV301",
        "query": "How do you handle duplicate city names when calling the OpenWeatherMap API?",
        "expect_ids": {4029},
        "min_matches": 1,
    },
    {
        "group": "SET_B",
        "module": "PBDV301",
        "query": "How does an AndEngine-based game Activity set up its camera, scene and touch handling?",
        "expect_ids": {7464},
        "min_matches": 1,
    },
    {
        "group": "SET_B",
        "module": "IPRT301",
        "query": "How does the ChangeListener update the label colour when a slider moves in the GUI program?",
        "expect_ids": {293},
        "min_matches": 1,
    },
    {
        "group": "SET_B",
        "module": "IPRT301",
        "query": "How does the EmployeeFactory getEmployee method create PartTime and FullTime instances?",
        "expect_ids": {3257},
        "min_matches": 1,
    },
    {
        "group": "SET_B",
        "module": "RESK301",
        "query": "What are the exploratory, descriptive and explanatory types of case study?",
        "expect_ids": {8725},
        "min_matches": 1,
    },
    {
        "group": "SET_B",
        "module": "RESK301",
        "query": "How does mixed methods analysis combine qualitative and quantitative data?",
        "expect_ids": {8889},
        "min_matches": 1,
    },
]

ALL_CASES = REGRESSION + SET_A + SET_B


def run_case(case: dict, retriever: Retriever, top_k: int, verbose: bool) -> bool:
    result = retriever.retrieve(case["query"], case["module"], include_patterns=True)
    hits = result.chunks[:top_k]
    got_ids = {chunk.chunk_id for chunk in hits}

    module_scoped = all(chunk.module_id == case["module"] for chunk in hits)
    id_matches = len(got_ids & case["expect_ids"])
    pattern_ok = True
    if "expect_pattern" in case:
        pattern_ok = any(
            (pattern.exception_thrown or "").startswith(case["expect_pattern"])
            for pattern in result.patterns
        )
    passed = (
        len(hits) > 0
        and module_scoped
        and id_matches >= case["min_matches"]
        and pattern_ok
    )

    label = "PASS" if passed else "FAIL"
    print(f"[{label}] {case['group']:10s} {case['module']} | {case['query']!r}")
    print(f"        hits={len(hits)} id_matches={id_matches}/{case['min_matches']} "
          f"module_scoped={module_scoped} pattern_ok={pattern_ok}")
    if verbose or not passed:
        for rank, chunk in enumerate(hits, start=1):
            print(f"        {rank}. d={chunk.distance:.4f} id={chunk.chunk_id} "
                  f"{chunk.section_title or ''!r} [{chunk.source_name}]")
        if "expect_pattern" in case:
            for rank, pattern in enumerate(result.patterns, start=1):
                print(f"        P{rank}. d={pattern.distance:.4f} id={pattern.pattern_id} "
                      f"{pattern.error_title!r} ({pattern.exception_thrown or 'n/a'})")
    return passed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hybrid retrieval acceptance tests.")
    parser.add_argument("--top-k", type=int, default=6, help="k for fused results")
    parser.add_argument("--verbose", action="store_true", help="print each hit")
    parser.add_argument("--group", choices=["REGRESSION", "SET_A", "SET_B"],
                        default=None, help="run only one case group")
    args = parser.parse_args(argv)

    print(f"settings: top_k={settings.retrieval_top_k} rrf_k={settings.retrieval_rrf_k} "
          f"min_sim={settings.retrieval_min_similarity} "
          f"strict_sim={settings.retrieval_strict_similarity} "
          f"max_df={settings.retrieval_max_df_ratio} "
          f"kw_w={settings.retrieval_keyword_weight} vec_w={settings.retrieval_vector_weight}")
    if settings.retrieval_top_k != 6:
        print("[FAIL] RETRIEVAL_TOP_K is not 6 - environment override still active?")
        return 1

    cases = [case for case in ALL_CASES if args.group is None or case["group"] == args.group]
    retriever = Retriever()
    failures = 0
    for case in cases:
        if not run_case(case, retriever, args.top_k, args.verbose):
            failures += 1
        print()

    if failures:
        print(f"{failures} of {len(cases)} cases FAILED")
        return 1
    print(f"All {len(cases)} cases passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
