"""Content discovery and structure-preserving extraction.

Each supported file is decomposed into one or more *sections* (a whole
document, a PowerPoint slide, ...). Sections are the unit handed to the chunker,
which lets every resulting vector record carry a meaningful ``section_title``.

Phase 1 of the ingestion upgrade replaces flat ``page.extract_text()`` output
with **Markdown** that preserves a document's heading hierarchy:

* PDF   - font size / weight infer ``#``-``####`` heading levels (via PyMuPDF).
* DOCX  - Word ``Heading N`` styles become ``#``-``######``; tables stay tables.
* PPTX  - each slide title becomes ``##``; body paragraphs follow it.
* HTML  - ``<h1>``-``<h6>`` become headings; tables become Markdown tables.
* CSV   - rendered as a Markdown table.
* Text  - passed through unchanged (already Markdown-compatible).

Heading-aware chunking (Phase 2) relies on this structure, so the extractor's
job is to emit stable, well-formed Markdown rather than to guess at semantics.
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

#: Block-level tags converted to Markdown by the HTML extractor.
_HTML_BLOCKS = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "blockquote")
#: Tags skipped when nested inside a table (the table renderer handles them).
_HTML_TABLE_NESTED = {"p", "li", "pre", "blockquote"}
#: Numbered headings ("1.", "2.3", "4)") may legitimately end with punctuation.
_NUMBER_PREFIX = re.compile(r"^\d+(?:\.\d+)*[.)]?\s")

#: Organisational numbering prefix on content folders
#: ("10_Topic 1 ..." -> "Topic 1 ...", "02_Time table" -> "Time table").
_TOPIC_NUMBER_PREFIX = re.compile(r"^\d+\s*[-_.]\s*")


class Section(NamedTuple):
    """A logical Markdown block extracted from a document."""

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

        topic = _normalise_topic(rel_parts[1]) if len(rel_parts) > 2 else None
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


def _normalise_topic(raw: str) -> Optional[str]:
    """Strip the folder-numbering prefix from a topic name.

    ``"10_Topic 1 Inheritance, Interfaces and Design patterns"`` becomes
    ``"Topic 1 Inheritance, Interfaces and Design patterns"``; ``"02_Time
    table"`` becomes ``"Time table"``. Returns ``None`` when nothing is left.
    """
    cleaned = _TOPIC_NUMBER_PREFIX.sub("", raw.strip()).strip()
    return cleaned or None


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
# Public extraction API
# ---------------------------------------------------------------------------
def extract_sections(path: Path) -> List[Section]:
    """Extract Markdown sections from ``path``. Never raises; returns ``[]``."""
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


def extract_markdown(path: Path) -> str:
    """Return the concatenated Markdown for ``path`` (empty string on failure)."""
    return "\n\n".join(section.text for section in extract_sections(path) if section.text.strip())


# ---------------------------------------------------------------------------
# Shared Markdown helpers
# ---------------------------------------------------------------------------
def _md_clean(text: str) -> str:
    """Normalise Markdown whitespace without collapsing block separation."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _md_escape_cell(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("|", "\\|").replace("\n", " ")).strip()


def _md_table(rows: list[list[str]]) -> str:
    """Render rows as a GitHub-flavoured Markdown table (first row = header)."""
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    padded = [list(row) + [""] * (width - len(row)) for row in rows]
    header, body = padded[0], padded[1:]

    def render(row: list[str]) -> str:
        return "| " + " | ".join(_md_escape_cell(cell) for cell in row) + " |"

    lines = [render(header), "| " + " | ".join("---" for _ in range(width)) + " |"]
    lines.extend(render(row) for row in body)
    return "\n".join(lines)


def _first_heading(markdown: str, fallback: str) -> str:
    """Return the text of the first Markdown heading, else ``fallback``."""
    for line in markdown.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
        if match and match.group(2).strip():
            return match.group(2).strip()[:120]
    return fallback


def _heading_level_from_style(style: str) -> int:
    """Map a Word/Normalised style name to a Markdown heading level."""
    match = re.search(r"(\d+)\s*$", style)
    if match:
        return max(1, min(int(match.group(1)), 6))
    if style == "subtitle":
        return 2
    return 2


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def _load_fitz():
    """Import PyMuPDF under either the ``pymupdf`` or legacy ``fitz`` name."""
    try:
        import pymupdf as fitz  # type: ignore

        return fitz
    except ImportError:
        try:
            import fitz  # type: ignore

            return fitz
        except ImportError:
            return None


def _extract_pdf(path: Path) -> List[Section]:
    fitz = _load_fitz()
    if fitz is None:
        logger.info("PyMuPDF not installed; using pypdf fallback for %s", path)
        return _extract_pdf_pypdf(path)
    try:
        return _extract_pdf_markdown(path, fitz)
    except Exception as exc:  # noqa: BLE001 - fall back to plain text
        logger.warning("PyMuPDF extraction failed for %s (%s); using pypdf", path, exc)
        return _extract_pdf_pypdf(path)


def _span_is_bold(span: dict) -> bool:
    try:
        if int(span.get("flags", 0)) & 16:  # PyMuPDF bold bit
            return True
    except (TypeError, ValueError):
        pass
    font = (span.get("font") or "").lower()
    return any(token in font for token in ("bold", "black", "semibold"))


def _pdf_page_lines(page) -> list[tuple[str, float, bool]]:
    """Extract ``(text, max_span_size, majority_bold)`` for every visible line."""
    lines: list[tuple[str, float, bool]] = []
    data = page.get_text("dict", sort=True)
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            text = re.sub(r"[ \t]+", " ", "".join(sp.get("text", "") for sp in spans)).strip()
            if not text:
                continue
            size = max(float(sp.get("size", 0)) for sp in spans)
            bold_chars = sum(len(sp.get("text", "")) for sp in spans if _span_is_bold(sp))
            total_chars = sum(len(sp.get("text", "")) for sp in spans) or 1
            lines.append((text, size, bold_chars / total_chars >= 0.5))
    return lines


def _body_size_from_lines(lines: list[tuple[str, float, bool]]) -> float:
    """Char-weighted mode of line font sizes - the page's body text size.

    Sizing is computed per page because form/contact pages often render every
    line a point or two larger than the surrounding prose; a document-wide mode
    would misread those whole pages as headings.
    """
    counts: dict[float, int] = {}
    for text, size, _bold in lines:
        key = round(size * 2) / 2
        if key <= 0:
            continue
        counts[key] = counts.get(key, 0) + len(text)
    if not counts:
        return 10.0
    return max(counts.items(), key=lambda item: item[1])[0]


def _detect_body_size(doc) -> float:
    """Char-weighted mode of span font sizes across the whole document."""
    counts: dict[float, int] = {}
    for page in doc:
        for text, size, _bold in _pdf_page_lines(page):
            key = round(size * 2) / 2
            if key <= 0:
                continue
            counts[key] = counts.get(key, 0) + len(text)
    if not counts:
        return 10.0
    return max(counts.items(), key=lambda item: item[1])[0]


def _looks_like_heading(text: str) -> bool:
    if len(text) > 90:
        return False
    if "@" in text or "://" in text:
        return False
    if sum(ch.isdigit() for ch in text) >= 4:
        return False
    if text.endswith((".", ",", ";", ":")) and not _NUMBER_PREFIX.match(text):
        return False
    return True


def _pdf_heading_level(size: float, body_size: float, bold: bool, text: str) -> int:
    """Infer a 1-3 heading level (0 = body text) from font metrics."""
    stripped = text.strip()
    if not stripped or len(stripped) > 90:
        return 0

    digits = sum(ch.isdigit() for ch in stripped)
    if digits and digits / max(len(stripped), 1) > 0.4:
        return 0
    if not _looks_like_heading(stripped):
        return 0

    ratio = (size / body_size) if body_size else 1.0
    if ratio >= 1.6:
        return 1
    if ratio >= 1.35:
        return 2
    if ratio >= 1.18:
        return 3
    if bold and ratio >= 1.12:
        return 3
    return 0


def _pdf_page_markdown(page) -> str:
    lines = _pdf_page_lines(page)
    body_size = _body_size_from_lines(lines)
    rendered: list[str] = []
    for text, size, bold in lines:
        level = _pdf_heading_level(size, body_size, bold, text)
        rendered.append(("#" * level + " " + text) if level else text)
    return "\n".join(rendered)


def _extract_pdf_markdown(path: Path, fitz) -> List[Section]:
    doc = fitz.open(str(path))
    try:
        if getattr(doc, "needs_pass", False):
            if not doc.authenticate(""):
                logger.warning("Encrypted PDF skipped: %s", path)
                return []
        pages: list[str] = []
        for page in doc:
            page_md = _pdf_page_markdown(page)
            if page_md.strip():
                pages.append(page_md.strip())
        if not pages:
            return []
        body = "\n\n".join(
            f"{page}\n\n---\n\n*Page {index}*" for index, page in enumerate(pages, start=1)
        )
        markdown = _md_clean(body)
        return [Section(title=_first_heading(markdown, path.stem), text=markdown)]
    finally:
        doc.close()


def _extract_pdf_pypdf(path: Path) -> List[Section]:
    """Fallback extractor (no heading detection) when PyMuPDF is unavailable."""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:  # noqa: BLE001
            logger.warning("Encrypted PDF skipped: %s", path)
            return []

    pages: list[str] = []
    for page in reader.pages:
        text = _clean(page.extract_text() or "")
        if text:
            pages.append(text)
    if not pages:
        return []
    body = "\n\n".join(
        f"{page}\n\n---\n\n*Page {index}*" for index, page in enumerate(pages, start=1)
    )
    markdown = _md_clean(body)
    return [Section(title=path.stem, text=markdown)]


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------
def _extract_docx(path: Path) -> List[Section]:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(str(path))
    blocks: list[str] = []
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            md = _docx_paragraph_markdown(Paragraph(child, document))
            if md:
                blocks.append(md)
        elif child.tag == qn("w:tbl"):
            md = _docx_table_markdown(Table(child, document))
            if md:
                blocks.append(md)

    markdown = _md_clean("\n\n".join(blocks))
    if not markdown:
        return []
    return [Section(title=_first_heading(markdown, path.stem), text=markdown)]


def _docx_paragraph_markdown(paragraph) -> str:
    text = paragraph.text.strip()
    if not text:
        return ""
    style = (paragraph.style.name or "").lower() if paragraph.style else ""
    if style.startswith("heading"):
        if not any(ch.isalnum() for ch in text):
            return text  # stray punctuation styled as a heading, e.g. ":"
        level = _heading_level_from_style(style)
        return "#" * level + " " + text
    if style in {"title", "document title"}:
        return "# " + text
    if style == "subtitle":
        return "## " + text
    if "list bullet" in style or "list number" in style or "list paragraph" in style:
        return "- " + text
    if style.startswith("quote"):
        return "> " + text
    return text


def _docx_table_markdown(table) -> str:
    rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
    return _md_table(rows)


# ---------------------------------------------------------------------------
# PPTX
# ---------------------------------------------------------------------------
def _extract_pptx(path: Path) -> List[Section]:
    from pptx import Presentation

    presentation = Presentation(str(path))
    slides: list[str] = []
    for index, slide in enumerate(presentation.slides, start=1):
        title_shape = slide.shapes.title
        title_text = (title_shape.text or "").strip() if title_shape is not None else ""
        title_id = title_shape.shape_id if title_shape is not None else None

        body_lines: list[str] = []
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False):
                continue
            if title_id is not None and shape.shape_id == title_id:
                continue
            for paragraph in shape.text_frame.paragraphs:
                text = paragraph.text.strip()
                if text:
                    body_lines.append(text)

        if not title_text and body_lines:
            # Slides exported from design tools often have no title placeholder;
            # promote the first line so the slide still gets a real heading.
            title_text = body_lines.pop(0)

        lines = ["## " + (title_text or f"Slide {index}")]
        lines.extend(body_lines)
        slides.append("\n".join(lines))

    markdown = _md_clean("\n\n".join(slides))
    if not markdown:
        return []
    return [Section(title=_first_heading(markdown, path.stem), text=markdown)]


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
def _extract_html(path: Path) -> List[Section]:
    from bs4 import BeautifulSoup

    raw = _read_text(path)
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    root = soup.body or soup
    blocks: list[str] = []
    for element in root.find_all(_HTML_BLOCKS):
        if element.name in _HTML_TABLE_NESTED and element.find_parent(["td", "th", "table"]):
            continue
        text = re.sub(r"\s+", " ", element.get_text(" ", strip=True)).strip()
        if not text:
            continue
        if element.name.startswith("h") and len(element.name) == 2 and element.name[1].isdigit():
            blocks.append("#" * int(element.name[1]) + " " + text)
        elif element.name == "li":
            blocks.append("- " + text)
        elif element.name == "pre":
            blocks.append("```\n" + element.get_text("\n", strip=True) + "\n```")
        elif element.name == "blockquote":
            blocks.append("> " + text)
        else:
            blocks.append(text)

    for table in root.find_all("table"):
        rows: list[list[str]] = []
        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if cells:
                rows.append([re.sub(r"\s+", " ", cell.get_text(" ", strip=True)) for cell in cells])
        md = _md_table(rows)
        if md:
            blocks.append(md)

    markdown = _md_clean("\n\n".join(blocks))
    if not markdown:
        return []
    fallback = soup.title.string.strip() if soup.title and soup.title.string else path.stem
    return [Section(title=_first_heading(markdown, fallback or path.stem), text=markdown)]


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
def _extract_csv(path: Path) -> List[Section]:
    raw = _read_text(path)
    rows = list(csv.reader(raw.splitlines()))
    markdown = _md_table(rows)
    if not markdown:
        return []
    return [Section(title=path.stem, text=markdown)]


# ---------------------------------------------------------------------------
# Plain text
# ---------------------------------------------------------------------------
def _extract_text(path: Path) -> List[Section]:
    text = _clean(_read_text(path))
    if not text:
        return []
    return [Section(title=path.stem, text=text)]


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------
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


def _read_text(path: Path) -> str:
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")
