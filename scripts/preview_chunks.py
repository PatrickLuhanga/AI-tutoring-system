"""Preview the chunking strategy for a single document (no database writes).

Prints each produced chunk's metadata and the exact ``chunk_text`` that will be
embedded, so the heading-aware split + contextual breadcrumb can be inspected
before a real ingestion run.

Usage (run from the project root):
    python scripts/preview_chunks.py "academic content\\IPRT\\04_Study guide\\1_2026 Study Guide IPRT301.pdf"
    python scripts/preview_chunks.py <path> --index 3
    python scripts/preview_chunks.py <path> --limit 5 --text-chars 600
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.config import settings  # noqa: E402
from src.ingest_curriculum import build_chunk_rows, build_splitters  # noqa: E402
from src.loaders import ContentFile, _normalise_topic, extract_sections  # noqa: E402


def build_content_file(path: Path) -> ContentFile:
    content_dir = Path(settings.content_dir).resolve()
    rel = path.resolve().relative_to(content_dir)
    parts = rel.parts
    module = settings.resolve_module(parts[0])
    if module is None:
        sys.exit(f"'{parts[0]}' is not a registered module folder")
    return ContentFile(
        path=path,
        rel_path=rel.as_posix(),
        module_folder=parts[0].upper(),
        module_id=module["module_id"],
        source_type=path.suffix.lstrip(".").lower(),
        topic=_normalise_topic(parts[1]) if len(parts) > 2 else None,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview heading-aware chunks for one file.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--limit", type=int, default=2, help="how many chunks to preview")
    parser.add_argument("--index", type=int, default=None, help="preview one specific chunk index")
    parser.add_argument("--text-chars", type=int, default=700, help="chunk_text preview length")
    args = parser.parse_args()

    path: Path = args.path
    if not path.is_file():
        sys.exit(f"not a file: {path}")

    content_file = build_content_file(path)
    sections = extract_sections(path)
    rows = build_chunk_rows(content_file, sections, build_splitters())

    print(f"file        : {path.name}")
    print(f"module      : {content_file.module_id}   topic: {content_file.topic}")
    print(f"sections    : {len(sections)}   chunks: {len(rows)}")
    if rows:
        strategies = Counter(row["doc_metadata"]["chunk_strategy"] for row in rows)
        tokens = [row["token_count"] for row in rows]
        print(f"strategies  : {dict(strategies)}")
        print(f"tokens      : min={min(tokens)} avg={sum(tokens) // len(tokens)} max={max(tokens)}")
    print("=" * 78)

    indices = [args.index] if args.index is not None else list(range(min(args.limit, len(rows))))
    for index in indices:
        if not 0 <= index < len(rows):
            print(f"[chunk {index}] out of range")
            continue
        row = rows[index]
        payload = {key: value for key, value in row.items() if key != "embedding"}
        print(f"CHUNK {index}  (of {len(rows)})")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        text = row["chunk_text"]
        preview = text if len(text) <= args.text_chars else text[: args.text_chars] + " …"
        print("-" * 78)
        print("chunk_text:")
        print(preview)
        print("=" * 78)


if __name__ == "__main__":
    main()
