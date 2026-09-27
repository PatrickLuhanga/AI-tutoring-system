"""Inspect Markdown extraction + heading structure for a single document.

Usage (run from the project root):
    python scripts/inspect_markdown.py "academic content\\IPRT\\04_Study guide\\1_2026 Study Guide IPRT301.pdf"
    python scripts/inspect_markdown.py <path> --headings-only
    python scripts/inspect_markdown.py <path> --chars 8000
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.loaders import extract_markdown, extract_sections  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect Markdown extraction for one file.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--headings-only", action="store_true", help="print only heading lines")
    parser.add_argument("--chars", type=int, default=4000, help="body preview length")
    args = parser.parse_args()

    path: Path = args.path
    if not path.is_file():
        sys.exit(f"not a file: {path}")

    sections = extract_sections(path)
    markdown = extract_markdown(path)

    headings = [line for line in markdown.splitlines() if line.lstrip().startswith("#")]

    print(f"file          : {path.name}")
    print(f"size          : {path.stat().st_size:,} bytes")
    print(f"sections      : {len(sections)}  (titles: {[s.title[:40] for s in sections]})")
    print(f"markdown chars: {len(markdown):,}")
    print(f"heading lines : {len(headings)}")
    print("=" * 78)

    print("HEADING STRUCTURE")
    for heading in headings[:60]:
        print("  " + heading)
    if len(headings) > 60:
        print(f"  ... ({len(headings) - 60} more)")

    if not args.headings_only:
        print("=" * 78)
        print(f"BODY PREVIEW (first {args.chars} chars)")
        print(markdown[: args.chars])


if __name__ == "__main__":
    main()
