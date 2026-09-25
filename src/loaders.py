"""Content discovery and text extraction for the ``academic content`` folder.

Each supported file is decomposed into one or more *sections* (a PDF page, a
PowerPoint slide, a Word heading block, a whole source file...). Sections are
the unit handed to the chunker, which lets every resulting vector record carry a
meaningful ``section_title``.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, NamedTuple, Optional

from .config import Settings, settings

logger = logging.getLogger(__name__)


class Section(NamedTuple):
    """A logical text block extracted from a document."""

    title: str
    text: str


@dataclass(frozen=True)
class ContentFile:
    """A discovered, ingestable content file with its module metadata."""

    path: Path
    rel_path: str
    module_folder: str
    module_id: str
    source_type: str
    topic: Optional[str]


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def iter_content_files(
    content_dir: Optional[Path] = None,
    module_filter: Optional[str] = None,
    max_files: Optional[int] = None,
    cfg: Settings = settings,
) -> Iterator[ContentFile]:
    """Yield every supported file under ``content_dir`` tagged with its module.

    Only files whose top-level folder matches a key in the module registry are
    returned; everything else is skipped. Files are yielded in a stable sorted
    order so re-runs are deterministic.
    """
    root = Path(content_dir or cfg.content_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"Content directory not found: {root}")

    wanted = module_filter.strip().upper() if module_filter else None
    emitted = 0

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue

        rel_parts = path.relative_to(root).parts
        if len(rel_parts) < 2:
            # Loose file sitting directly in the content root - no module scope.
            continue

        module_folder = rel_parts[0]
        module = cfg.resolve_module(module_folder)
        if module is None:
            continue
        if wanted and module_folder.upper() != wanted:
            continue

        suffix = path.suffix.lower()
        if suffix in cfg.skipped_extensions or suffix not in cfg.supported_extensions:
            continue

        topic = rel_parts[1] if len(rel_parts) > 2 else None
        yield ContentFile(
            path=path,
            rel_path=path.relative_to(root).as_posix(),
            module_folder=module_folder.upper(),
            module_id=module["module_id"],
            source_type=suffix.lstrip("."),
            topic=topic,
        )

        emitted += 1
        if max_files is not None and emitted >= max_files:
            return


def count_skipped(content_dir: Optional[Path] = None, cfg: Settings = settings) -> dict[str, int]:
    """Return a histogram of skipped extensions (for reporting only)."""
    root = Path(content_dir or cfg.content_dir)
    skipped: dict[str, int] = {}
    if not root.is_dir():
        return skipped
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in cfg.skipped_extensions:
            skipped[suffix] = skipped.get(suffix, 0) + 1
    return skipped


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------
def extract_sections(path: Path) -> List[Section]:
    """Extract text sections from ``path``. Never raises; returns ``[]`` on error."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            return _extract_pdf(path)
        if suffix == ".docx":
            return _extract_docx(path)
        if suffix == ".pptx":
            return _extract_pptx(path)
        if suffix in {".html", ".htm"}:
            return _extract_html(path)
        if suffix == ".csv":
            return _extract_csv(path)
        return _extract_text(path)
    except Exception as exc:  # noqa: BLE001 - one bad file must not stop a run
        logger.warning("Failed to extract %s: %s", path, exc)
        return []


def _clean(text: str) -> str:
    """Collapse excessive blank lines / whitespace while keeping paragraphs."""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    cleaned: list[str] = []
    blank_run = 0
    for line in lines:
        if line.strip():
            blank_run = 0
            cleaned.append(line)
        else:
            blank_run += 1
            if blank_run <= 1:
                cleaned.append("")
    return "\n".join(cleaned).strip()


def _extract_pdf(path: Path) -> List[Section]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:  # noqa: BLE001
            logger.warning("Encrypted PDF skipped: %s", path)
            return []

    sections: list[Section] = []
    for index, page in enumerate(reader.pages, start=1):
        text = _clean(page.extract_text() or "")
        if text:
            sections.append(Section(title=f"{path.stem} - Page {index}", text=text))
    return sections


def _extract_docx(path: Path) -> List[Section]:
    from docx import Document

    document = Document(str(path))
    sections: list[Section] = []
    current_title = path.stem
    buffer: list[str] = []

    def flush() -> None:
        text = _clean("\n".join(buffer))
        if text:
            sections.append(Section(title=current_title, text=text))

    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style = (paragraph.style.name or "").lower() if paragraph.style else ""
        if style.startswith("heading"):
            flush()
            buffer.clear()
            current_title = text
        else:
            buffer.append(text)

    flush()
    if not sections:
        full = _clean("\n".join(p.text for p in document.paragraphs))
        if full:
            sections.append(Section(title=path.stem, text=full))

    for table in document.tables:
        rows = [
            " | ".join(cell.text.strip() for cell in row.cells)
            for row in table.rows
        ]
        text = _clean("\n".join(rows))
        if text:
            sections.append(Section(title=f"{path.stem} - Table", text=text))
    return sections


def _extract_pptx(path: Path) -> List[Section]:
    from pptx import Presentation

    presentation = Presentation(str(path))
    sections: list[Section] = []
    for index, slide in enumerate(presentation.slides, start=1):
        chunks: list[str] = []
        title = f"{path.stem} - Slide {index}"
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False):
                continue
            text = shape.text_frame.text.strip()
            if not text:
                continue
            chunks.append(text)
        if slide.shapes.title is not None and slide.shapes.title.text:
            title = f"{path.stem} - {slide.shapes.title.text.strip()}"
        body = _clean("\n".join(chunks))
        if body:
            sections.append(Section(title=title, text=body))
    return sections


def _extract_html(path: Path) -> List[Section]:
    from bs4 import BeautifulSoup

    raw = _read_text(path)
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = _clean(soup.get_text("\n"))
    if not text:
        return []
    title = path.stem
    if soup.title and soup.title.string:
        title = soup.title.string.strip() or path.stem
    return [Section(title=title, text=text)]


def _extract_csv(path: Path) -> List[Section]:
    raw = _read_text(path)
    rows = list(csv.reader(raw.splitlines()))
    text = _clean("\n".join(" | ".join(cell.strip() for cell in row) for row in rows))
    if not text:
        return []
    return [Section(title=path.stem, text=text)]


def _extract_text(path: Path) -> List[Section]:
    text = _clean(_read_text(path))
    if not text:
        return []
    return [Section(title=path.stem, text=text)]


def _read_text(path: Path) -> str:
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")
