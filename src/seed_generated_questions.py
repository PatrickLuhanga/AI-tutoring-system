"""Seed PBDV301's question bank with model-generated questions.

PBDV301 has no past papers to draw on - it is not examinable from a past-paper
corpus - so its bank is generated from the module's own lecture notes instead.

Every question it creates is marked ``origin='generated'`` and its
``source_label`` says so in words. That label is what the practice UI shows next
to the question, and it is deliberately blunt: an AI-written question has not
been through the same academic-approval route as an authored or past-paper one,
and a student revising for an unseen assessment should be able to see that
difference rather than assume every question in the bank carries equal weight.

Run with ``--dry-run`` to see the material that would be used without generating.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

#: Section titles of the PBDV lecture slides, used to slice the corpus into the
#: chunks a question is generated from.
PBDV_SLIDES = "PBDV/slides"


def slide_documents() -> list[Path]:
    from .config import settings

    root = Path(settings.content_dir)
    folder = root / "PBDV" / "slides"
    if not folder.is_dir():
        return []
    return sorted(folder.glob("*.md"))


def read_material(limit: int = 4) -> str:
    """Concatenate a few lecture decks as the grounding material."""
    docs = slide_documents()
    if not docs:
        return ""
    chunks: list[str] = []
    for path in docs[:limit]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Keep the headings so the model has the topic context, drop slide
        # markers and horizontal rules which are pure formatting noise.
        kept = [
            line.strip()
            for line in text.split("\n")
            if line.strip() and not line.strip().startswith("<!--") and set(line.strip()) != {"-"}
        ]
        chunks.append(f"### {path.stem}\n" + "\n".join(kept))
    return "\n\n".join(chunks)[:12000]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=8, help="questions to generate")
    parser.add_argument("--module", default="PBDV301")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--window",
        type=int,
        default=3000,
        help="characters of material per generation call; a small window forces "
             "variety, a large one gives more context",
    )
    parser.add_argument(
        "--label",
        default="AI-generated for revision - not from a past paper",
        help="source_label stamped on every generated question",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    material = read_material()
    print(f"Grounding material: {len(material)} chars from {len(slide_documents())} deck(s)\n")

    if args.dry_run:
        print(material[:1500])
        print("\n... (dry run, nothing generated)")
        return 0

    if len(material) < 120:
        print("Not enough material to generate from.", file=sys.stderr)
        return 2

    from .practice import PracticeError, generate_questions_from_text

    created = 0
    # Generated in small batches: one call per question keeps any single bad
    # completion from costing the whole run, and the model is slow enough that
    # batching everything risks a timeout partway through.
    remaining = max(1, min(args.count, 20))
    window_offset = 0
    while created < remaining:
        want = min(2, remaining - created)
        try:
            # Rotate the window so successive calls ask about different slides.
            # Feeding the same material every time returns near-identical
            # questions and the bank fills with restatements of one idea.
            window = material[window_offset : window_offset + args.window]
            if len(window) < 400:
                window = material
            window_offset = (window_offset + args.window) % max(1, len(material))

            rows = generate_questions_from_text(
                module_id=args.module,
                text=window,
                count=want,
                created_by=None,
                difficulty="medium",
            )
        except PracticeError as exc:
            print(f"Generation stopped: {exc.message}", file=sys.stderr)
            break

        for row in rows:
            # Re-label with the caller's wording, so the student-facing text is
            # unambiguous about provenance.
            from .db import session_scope
            from .models import Question

            with session_scope() as session:
                stored = session.get(Question, int(row["question_id"]))
                if stored is not None:
                    stored.source_label = args.label
            created += 1
            print(f"  [{created}] {row['prompt'][:96]}")
        if not rows:
            break

    print(f"\nSeeded {created} generated question(s) into {args.module}.")
    print(f"Labelled: {args.label}")
    print("These are drafts. A lecturer should read them before a student relies on them.")
    return 0 if created else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
