"""Debug helper: print (font size, ratio, bold, text) for one PDF page.

Useful when PDF heading detection looks off - it shows the font-size
distribution the extractor is working from.

Usage (run from the project root):
    python scripts/debug_pdf_sizes.py "academic content\\IPRT\\04_Study guide\\1_2026 Study Guide IPRT301.pdf" --page 3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.loaders import _detect_body_size, _load_fitz, _span_is_bold  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--page", type=int, default=1, help="1-based page number")
    args = parser.parse_args()

    fitz = _load_fitz()
    if fitz is None:
        sys.exit("PyMuPDF is not installed; install it from requirements.txt.")

    doc = fitz.open(str(args.path))
    body = _detect_body_size(doc)
    print(f"detected body size (document-wide): {body}")
    page = doc[args.page - 1]
    for block in page.get_text("dict", sort=True).get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            text = "".join(sp.get("text", "") for sp in spans).strip()
            if not text:
                continue
            size = max(float(sp.get("size", body)) for sp in spans)
            bold = any(_span_is_bold(sp) for sp in spans)
            print(f"[{size:5.1f} ratio={size / body:4.2f} bold={str(bold):5}] {text[:80]}")
    doc.close()


if __name__ == "__main__":
    main()
