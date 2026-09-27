"""Content discovery and text extraction for the ``academic content`` folder.

Each supported file is decomposed into one or more *sections* (a PDF page, a
PowerPoint slide, a Word heading block, a whole source file...). Sections are
the unit handed to the chunker, which lets every resulting vector record carry a
meaningful ``section_title``.
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, NamedTuple, Optional

from .config import Settings, settings

logger = logging.getLogger(__name__)


class Section(NamedTuple):
    """A logical text block extracted from a document.

    ``is_answer`` marks a block that contains a worked answer, model solution or
    marking guidance rather than teaching material. The RAG Orchestrator withholds
    these blocks until the Scaffolding Engine reaches the Explanation stage, so
    retrieval cannot hand the model a finished solution to copy (section 7.4).
    """

    title: str
    text: str
    is_answer: bool = False


@dataclass(frozen=True)
class ContentFile:
    """A discovered, ingestable content file with its module metadata."""

    path: Path
    rel_path: str
    module_folder: str
    module_id: str
    source_type: str
    topic: Optional[str]
    source_category: str = "notes"


# ---------------------------------------------------------------------------
# Source categories
# ---------------------------------------------------------------------------
#: Maps a second-level content folder to a coarse provenance label. The label is
#: stored on every chunk so retrieval can prefer the module's own teaching
#: material over third-party material, and so analytics can report a grounding
#: rate per provenance class (see src/retriever.py).
SOURCE_CATEGORIES: dict[str, str] = {
    "slides": "slides",
    "slide": "slides",
    "lectures": "slides",
    "lecture": "slides",
    "books": "books",
    "book": "books",
    "textbooks": "books",
    "exercises": "exercises",
    "exercise": "exercises",
    "examples": "examples",
    "example": "examples",
    "tutorials": "tutorials",
    "tutorial": "tutorials",
    "notes": "notes",
    "labs": "exercises",
}

#: Provenance classes that are commercial third-party works rather than
#: faculty-approved course material. Retrieval ranks these below module material.
THIRD_PARTY_CATEGORIES = frozenset({"books"})

#: Provenance class for answers curated by tutors through the unanswered-question
#: queue (see src/unanswered.py). Treated as first-party course material.
TUTOR_ANSWER_CATEGORY = "tutor_answers"


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
        # Provenance comes from the second-level folder, e.g. ``slides`` / ``books``.
        category = SOURCE_CATEGORIES.get(topic.strip().lower(), "notes") if topic else "notes"
        yield ContentFile(
            path=path,
            rel_path=path.relative_to(root).as_posix(),
            module_folder=module_folder.upper(),
            module_id=module["module_id"],
            source_type=suffix.lstrip("."),
            topic=topic,
            source_category=category,
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
        if suffix in {".md", ".markdown"}:
            return _extract_markdown(path)
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


# ---------------------------------------------------------------------------
# Structure-aware Markdown extraction
# ---------------------------------------------------------------------------
# The course material is authored as Markdown with explicit structure: slide decks
# are delimited by ``<!-- Slide N -->`` markers and every slide/topic opens with a
# heading. A flat character window ignores that structure and cuts mid-sentence in
# the middle of a slide, which is why chunking here follows the document's own
# boundaries instead.

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_SLIDE_MARKER_RE = re.compile(r"<!--\s*slide\s*\d+\s*-->", re.IGNORECASE)
_ATX_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_HR_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")

#: A heading whose *title* marks a worked answer / model solution. Everything from
#: that heading onwards is flagged so the retriever can withhold it until the
#: Scaffolding Engine reaches the Explanation stage.
_ANSWER_TITLE_RE = re.compile(
    r"^(?:the\s+)?(?:answers?|solutions?|worked\s+solutions?|"
    r"model\s+answers?|marking\s+memo|complete\s+solutions?|"
    r"sample\s+solutions?)\b",
    re.IGNORECASE,
)


def _clean_title(text: str) -> str:
    """Tidy a heading for use as a section title.

    Slide decks are authored with the whole heading in bold (``## **1. The research
    process**``). The asterisks are Markdown, not content, and they end up in the
    prompt as ``[Curriculum 1] **1. The research process**``, so strip the
    emphasis, trailing hashes and surrounding whitespace.
    """
    cleaned = text.strip()
    cleaned = re.sub(r"[*_`~]+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or text.strip()


def split_markdown_sections(text: str, fallback_title: str) -> List[Section]:
    """Split Markdown into sections that respect its own structural boundaries.

    Two modes, because the material is authored two ways:

    * **Slide decks** (they carry ``<!-- Slide N -->`` markers) are split on those
      markers only. The headings *inside* a slide are slide furniture - a bolded
      term, a diagram label - so treating each one as a boundary would shred a
      single slide into a dozen fragments.
    * **Everything else** (books, exercises, notes) is split on top-level ATX
      headings only. Deeper headings are content, not structure.

    A new section starts at every boundary outside a fenced code block, so ``#``
    comments inside a code sample are never mistaken for headings. Once an
    answer/solution heading is seen, the remaining sections are flagged
    ``is_answer=True``.
    """
    if _SLIDE_MARKER_RE.search(text):
        return _split_by_slides(text, fallback_title)
    return _split_by_headings(text, fallback_title)


def _iter_boundaries(text: str, *, slide_mode: bool):
    """Yield structural events for ``text``.

    ``("boundary", title)`` starts a new section; ``("answer", title)`` starts a
    new section that is flagged as worked-answer material; ``("title", text)`` is a
    heading kept inline as content; ``("line", text)`` is an ordinary line.
    """
    in_fence = False
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            yield ("line", line)
            continue
        if in_fence:
            yield ("line", line)
            continue
        if slide_mode and _SLIDE_MARKER_RE.search(line):
            yield ("boundary", None)
            continue
        heading = _ATX_HEADING_RE.match(line)
        if heading:
            title = _clean_title(heading.group(2))
            is_answer = bool(_ANSWER_TITLE_RE.match(title))
            if slide_mode:
                # Inside a slide a heading is furniture, not a boundary - except an
                # answer heading, which must still switch the flag on.
                yield ("answer" if is_answer else "title", title)
                continue
            if len(heading.group(1)) <= 2:
                yield ("answer" if is_answer else "boundary", title)
                continue
        yield ("line", line)


def _assemble(text: str, fallback_title: str, *, slide_mode: bool) -> List[Section]:
    sections: List[Section] = []
    title = fallback_title
    title_candidate: Optional[str] = None
    is_answer = False
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf, title_candidate
        body = _clean("\n".join(buf))
        if body:
            resolved = title_candidate or title or fallback_title
            sections.append(Section(title=resolved, text=body, is_answer=is_answer))
        buf = []
        title_candidate = None

    for kind, value in _iter_boundaries(text, slide_mode=slide_mode):
        if kind in ("boundary", "answer"):
            flush()
            title = value or fallback_title
            if kind == "answer":
                is_answer = True
            continue
        line = value  # type: ignore[assignment]
        if kind == "title":
            if title_candidate is None and line.strip():
                title_candidate = line.strip()
            buf.append(line)
            continue
        if _HR_RE.match(line) and not buf:
            # Separator between slides; keep it out of the chunk text.
            continue
        buf.append(line)

    flush()
    return sections


def _split_by_slides(text: str, fallback_title: str) -> List[Section]:
    return _assemble(text, fallback_title, slide_mode=True)


def _split_by_headings(text: str, fallback_title: str) -> List[Section]:
    return _assemble(text, fallback_title, slide_mode=False)


def _extract_markdown(path: Path) -> List[Section]:
    """Structure-aware extraction for ``.md`` course material."""
    text = _read_text(path)
    if not text.strip():
        return []
    sections = split_markdown_sections(text, path.stem)
    if sections:
        return sections
    # No recognisable structure - fall back to the whole file as one section.
    body = _clean(text)
    return [Section(title=path.stem, text=body)] if body else []


def _read_text(path: Path) -> str:
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")
