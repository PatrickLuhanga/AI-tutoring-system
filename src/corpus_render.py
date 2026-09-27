"""Corpus rendering: Markdown source documents as styled, linkable HTML.

Serves the ingested course material as a browsable library so that a citation in
a tutor reply can link a student to the exact notes a claim came from, rather
than leaving them to hunt for a PDF.

Two things this module is careful about:

**Path safety.** ``module_id`` and the document path both arrive in the URL, so
every resolved path is checked to be inside ``ACADEMIC_CONTENT_DIR`` after
resolution. Rejecting on the *resolved* path rather than on ``..`` in the input
is what makes this robust - a symlink inside the content directory would defeat
a naive string check.

**Raw HTML.** ``markdown-it-py`` in commonmark mode passes raw HTML straight
through, so ``<script>alert(1)</script>`` survives into the output. Source
documents are institutional material, but they are also authored by hand and
copied from PDFs, so the renderer runs with ``html=False`` and escapes
everything. This is verified by :func:`_assert_html_is_escaped` in the tests.

Anchors
-------
Headings get stable ``id`` attributes derived from their text, and the retriever
builds citation URLs from the same slug function. ``<!-- Slide N -->`` markers
are turned into visible section boundaries so a deep link lands on the slide a
citation refers to rather than at the top of a forty-slide deck.
"""

from __future__ import annotations

import html
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from .config import settings

logger = logging.getLogger(__name__)

#: Extensions the renderer will serve as a readable page.
RENDERABLE_SUFFIXES = {".md", ".markdown", ".txt"}

_SLIDE_MARKER_RE = re.compile(r"<!--\s*slide\s*(\d+)\s*-->", re.IGNORECASE)
_ATX_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_HR_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")

#: Depth of the generated page's own styling. Deliberately small and inlined: the
#: resource page has to render with no build step and no external requests.
_CSS = """
:root {
  --ink: #0f172a; --muted: #64748b; --line: #e2e8f0; --bg: #f8fafc;
  --card: #ffffff; --accent: #1d4ed8; --accent-soft: #eff6ff; --code-bg: #f1f5f9;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 16px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
header.site {
  background: var(--card); border-bottom: 1px solid var(--line);
  padding: 14px 24px; display: flex; gap: 16px; align-items: center;
  flex-wrap: wrap; position: sticky; top: 0; z-index: 10;
}
header.site .brand { font-weight: 650; letter-spacing: -0.01em; }
header.site .crumb { color: var(--muted); font-size: 14px; }
header.site .spacer { flex: 1; }
nav.modules { display: flex; gap: 6px; flex-wrap: wrap; }
nav.modules a {
  color: var(--muted); text-decoration: none; font-size: 13px;
  padding: 4px 9px; border-radius: 999px; border: 1px solid var(--line);
}
nav.modules a:hover { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
nav.modules a[aria-current="page"] {
  color: var(--accent); background: var(--accent-soft); border-color: var(--accent); font-weight: 600;
}
main { max-width: 860px; margin: 0 auto; padding: 28px 24px 96px; }
h1.doc { font-size: 30px; line-height: 1.25; margin: 0 0 6px; letter-spacing: -0.02em; }
p.meta { color: var(--muted); font-size: 13px; margin: 0 0 26px; }
h2, h3, h4 { line-height: 1.3; margin: 30px 0 10px; letter-spacing: -0.01em; scroll-margin-top: 76px; }
h2 { font-size: 21px; padding-bottom: 6px; border-bottom: 1px solid var(--line); }
h3 { font-size: 17px; } h4 { font-size: 15px; color: #334155; }
p, ul, ol, table { margin: 0 0 14px; }
a { color: var(--accent); }
code { background: var(--code-bg); padding: 1px 5px; border-radius: 4px; font-size: 0.9em;
       font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
pre { background: #0f172a; color: #e2e8f0; padding: 14px 16px; border-radius: 8px;
      overflow-x: auto; font-size: 13.5px; line-height: 1.55; }
pre code { background: none; color: inherit; padding: 0; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { border: 1px solid var(--line); padding: 7px 10px; text-align: left; vertical-align: top; }
th { background: var(--code-bg); font-weight: 600; }
blockquote { margin: 0 0 14px; padding: 8px 16px; border-left: 3px solid var(--accent);
             background: var(--accent-soft); color: #334155; }
hr { border: 0; border-top: 1px solid var(--line); margin: 26px 0; }
.slide-anchor { scroll-margin-top: 76px; }
.anchor-link {
  opacity: 0; margin-left: 8px; font-size: 13px; text-decoration: none; color: var(--muted);
}
h2:hover .anchor-link, h3:hover .anchor-link, .slide-anchor:hover .anchor-link { opacity: 1; }
.flash { animation: flash 2.4s ease-out; }
@keyframes flash { 0%, 40% { background: #fef9c3; } 100% { background: transparent; } }
footer.doc { margin-top: 40px; padding-top: 16px; border-top: 1px solid var(--line);
             color: var(--muted); font-size: 13px; display: flex; gap: 14px; flex-wrap: wrap; }
footer.doc a { text-decoration: none; }
"""


def slugify(text: str) -> str:
    """Stable anchor id for a heading.

    ASCII-folded, lowercase, hyphen-separated. Shared with the retriever so a
    citation URL and the heading it points at cannot drift apart.
    """
    folded = unicodedata.normalize("NFKD", text or "")
    ascii_only = "".join(ch for ch in folded if not unicodedata.combining(ch))
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-").lower()
    return slug or "section"


@dataclass(slots=True)
class Heading:
    level: int
    title: str
    anchor: str
    slide: Optional[int] = None


@dataclass(slots=True)
class RenderedDoc:
    module_id: str
    rel_path: str
    title: str
    html: str
    headings: list[Heading] = field(default_factory=list)
    source_name: str = ""
    category: str = "notes"


def content_root() -> Path:
    return Path(settings.content_dir)


def resolve_doc_path(module_id: str, rel_path: str) -> Optional[Path]:
    """Resolve a module + relative document path to a real file inside the corpus.

    Returns ``None`` for anything that escapes the content directory. The check
    is on the *resolved* path, so neither ``..`` segments nor a symlink pointing
    outside the corpus can get through.
    """
    module = (module_id or "").strip()
    if not module or not re.fullmatch(r"[A-Za-z0-9_\-]+", module):
        return None
    resolved = settings.resolve_any(module)
    if resolved is None:
        return None
    folder, _record = resolved

    root = content_root().resolve()
    # The content folder is named by the registry key (``IPRT``), not the module id
    # (``IPRT301``). resolve_any gives us the translation in either direction.
    candidate = (root / folder / rel_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        logger.warning("Rejected corpus path outside the content directory: %s", rel_path)
        return None
    if not candidate.is_file():
        return None
    if candidate.suffix.lower() not in RENDERABLE_SUFFIXES:
        return None
    return candidate


def _unique_anchor(base: str, seen: set[str]) -> str:
    anchor = base
    n = 2
    while anchor in seen:
        anchor = f"{base}-{n}"
        n += 1
    seen.add(anchor)
    return anchor


def _markdown_renderer():
    """A markdown-it instance with raw HTML disabled."""
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
    md.enable("table")
    md.enable("strikethrough")
    return md


def render_markdown_document(
    module_id: str,
    rel_path: str,
    markdown_text: str,
    *,
    source_name: str = "",
    category: str = "notes",
) -> RenderedDoc:
    """Render one corpus document to styled HTML with stable heading anchors.

    Slide markers become their own anchor targets so a citation to "Slide 16"
    deep-links to that slide rather than to the top of the deck.
    """
    md = _markdown_renderer()
    seen: set[str] = set()
    headings: list[Heading] = []
    lines = markdown_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    out: list[str] = []
    in_fence = False
    buffer: list[str] = []
    slide_no: Optional[int] = None

    def flush() -> None:
        nonlocal buffer
        if buffer:
            out.append(md.render("\n".join(buffer)))
            buffer = []

    for line in lines:
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            buffer.append(line)
            continue
        if in_fence:
            buffer.append(line)
            continue

        marker = _SLIDE_MARKER_RE.search(line)
        if marker:
            flush()
            slide_no = int(marker.group(1))
            # The slide's own heading supplies the title; this anchor guarantees a
            # landing point even for a slide with no heading.
            anchor = _unique_anchor(f"slide-{slide_no}", seen)
            headings.append(Heading(level=2, title=f"Slide {slide_no}", anchor=anchor, slide=slide_no))
            out.append(f'<div class="slide-anchor" id="{anchor}"></div>')
            continue

        if _HR_RE.match(line):
            flush()
            out.append("<hr>")
            continue

        heading = _ATX_HEADING_RE.match(line)
        if heading:
            flush()
            from .loaders import _clean_title

            title = _clean_title(heading.group(2))
            level = len(heading.group(1))
            anchor = _unique_anchor(slugify(title), seen)
            headings.append(Heading(level=level, title=title, anchor=anchor, slide=slide_no))
            rendered = md.render(f"{'#' * min(level + 1, 6)} {title}")
            # markdown-it assigns its own id only with the attr plugin, so inject
            # ours on the first heading tag of the rendered fragment.
            rendered = re.sub(
                r"(<h[1-6])(>)",
                rf'\1 id="{anchor}"><a class="anchor-link" href="#{anchor}" '
                f'title="Link to this section">#</a>\1',
                rendered,
                count=1,
            )
            out.append(rendered)
            continue

        buffer.append(line)

    flush()

    doc_title = headings[0].title if headings else (source_name or rel_path)
    body = "\n".join(out)
    return RenderedDoc(
        module_id=module_id,
        rel_path=rel_path,
        title=doc_title,
        html=body,
        headings=headings,
        source_name=source_name or Path(rel_path).name,
        category=category,
    )


def _esc(text: str) -> str:
    return html.escape(text or "", quote=True)


def module_nav(active_module_id: str) -> str:
    items = []
    for record in settings.modules.values():
        mid = record["module_id"]
        current = ' aria-current="page"' if mid == active_module_id else ""
        items.append(f'<a href="/resources/{_esc(mid)}"{current}>{_esc(mid)}</a>')
    return f'<nav class="modules">{"".join(items)}</nav>'


def render_resource_page(
    doc: RenderedDoc,
    *,
    module_name: str,
    prev_doc: Optional[dict] = None,
    next_doc: Optional[dict] = None,
    cited_anchor: Optional[str] = None,
) -> str:
    """Wrap a rendered document in the full styled page."""
    flash = ""
    if cited_anchor:
        # json.dumps already yields a valid JS string literal, and the contents of
        # a <script> element are not entity-decoded - so HTML-escaping the quotes
        # here would emit `document.getElementById(&quot;x&quot;)`, a syntax error
        # that silently kills the deep link.
        #
        # Instant rather than smooth: a citation is a "show me the evidence" jump,
        # and smooth scrolling is a no-op in some headless and reduced-motion
        # contexts, which would leave the student at the top of a forty-slide deck.
        # The flash highlight carries the visual feedback instead. Citation URLs
        # also carry the plain `#anchor` fragment, so the browser scrolls natively
        # even with scripting unavailable.
        flash = (
            "<script>window.addEventListener('load',function(){"
            f"var e=document.getElementById({json_str(cited_anchor)});"
            "if(e){e.classList.add('flash');"
            "e.scrollIntoView({behavior:'auto',block:'start'});}"
            "});</script>"
        )

    toc = []
    for heading in doc.headings:
        if heading.level > 3:
            continue
        toc.append(
            f'<li class="lvl{heading.level}">'
            f'<a href="#{_esc(heading.anchor)}">{_esc(heading.title)}</a></li>'
        )
    toc_html = (
        f'<details class="toc"><summary>Contents ({len(toc)})</summary>'
        f'<ul>{"".join(toc)}</ul></details>'
        if toc
        else ""
    )

    nav_links = []
    if prev_doc:
        nav_links.append(
            f'<a href="/resources/{_esc(doc.module_id)}/{_esc(prev_doc["path"])}">'
            f'&#8592; {_esc(prev_doc["name"])}</a>'
        )
    if next_doc:
        nav_links.append(
            f'<a href="/resources/{_esc(doc.module_id)}/{_esc(next_doc["path"])}">'
            f'{_esc(next_doc["name"])} &#8594;</a>'
        )
    nav_links.append(f'<a href="/resources/{_esc(doc.module_id)}">All {_esc(doc.module_id)} documents</a>')

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(doc.title)} &middot; {_esc(module_name)}</title>
<style>{_CSS}
.toc {{ background: var(--card); border: 1px solid var(--line); border-radius: 10px;
        padding: 10px 14px; margin: 0 0 24px; }}
.toc summary {{ cursor: pointer; font-size: 14px; color: var(--muted); font-weight: 600; }}
.toc ul {{ margin: 10px 0 4px; padding-left: 18px; columns: 2; column-gap: 28px; }}
.toc li {{ margin: 3px 0; font-size: 14px; break-inside: avoid; }}
.toc li.lvl3 {{ padding-left: 14px; color: var(--muted); }}
</style>
</head>
<body>
<header class="site">
  <span class="brand">AI Tutor</span>
  <span class="crumb">{_esc(doc.module_id)} &middot; {_esc(module_name)}</span>
  <span class="spacer"></span>
  {module_nav(doc.module_id)}
</header>
<main>
  <h1 class="doc">{_esc(doc.title)}</h1>
  <p class="meta">Source file <code>{_esc(doc.rel_path)}</code></p>
  {toc_html}
  {doc.html}
  <footer class="doc">{"".join(nav_links)}</footer>
</main>
{flash}
</body>
</html>"""


def render_module_index(
    module_id: str,
    module_name: str,
    documents: list[dict],
) -> str:
    """Styled index of one module's documents, grouped by provenance."""
    from html import escape

    groups: dict[str, list[dict]] = {}
    for doc in documents:
        groups.setdefault(doc.get("source_category", "notes"), []).append(doc)

    order = ["slides", "notes", "exercises", "examples", "tutorials", "books", "tutor_answers"]
    labels = {
        "slides": "Lecture slides",
        "notes": "Lecture notes",
        "exercises": "Exercises",
        "examples": "Worked examples",
        "tutorials": "Tutorials",
        "books": "Textbooks (third-party)",
        "tutor_answers": "Tutor answers",
    }
    ordered = [c for c in order if c in groups] + [
        c for c in sorted(groups) if c not in order
    ]

    blocks = []
    for category in ordered:
        items = groups[category]
        rows = []
        for doc in items:
            size_kb = doc.get("size_bytes", 0) / 1024
            size = f"{size_kb / 1024:.1f} MB" if size_kb >= 1024 else f"{size_kb:.0f} KB"
            third = " third-party" if category == "books" else ""
            rows.append(
                f'<li><a href="{escape(doc["url"])}">'
                f'<span class="doc-name">{escape(doc["name"])}</span>'
                f'<span class="doc-meta">{escape(size)}{third}</span></a></li>'
            )
        blocks.append(
            f'<section class="group"><h2>{escape(labels.get(category, category))}'
            f'<span class="count">{len(items)}</span></h2>'
            f'<ul>{"".join(rows)}</ul></section>'
        )

    total = len(documents)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(module_id)} &middot; {escape(module_name)}</title>
<style>{_CSS}
main {{ max-width: 900px; }}
.group {{ margin: 0 0 26px; }}
.group h2 {{ font-size: 15px; text-transform: uppercase; letter-spacing: .06em;
             color: var(--muted); border: 0; padding: 0; margin: 0 0 10px;
             display: flex; align-items: center; gap: 8px; }}
.group h2 .count {{ background: var(--code-bg); color: var(--muted); border-radius: 999px;
                    padding: 1px 8px; font-size: 11px; letter-spacing: 0; }}
.group ul {{ list-style: none; margin: 0; padding: 0;
             background: var(--card); border: 1px solid var(--line); border-radius: 10px;
             overflow: hidden; }}
.group li + li {{ border-top: 1px solid var(--line); }}
.group a {{ display: flex; align-items: baseline; justify-content: space-between; gap: 14px;
            padding: 10px 14px; text-decoration: none; color: inherit; }}
.group a:hover {{ background: var(--accent-soft); }}
.doc-name {{ font-size: 14px; font-weight: 500; }}
.group a:hover .doc-name {{ color: var(--accent); }}
.doc-meta {{ color: var(--muted); font-size: 12px; white-space: nowrap; }}
</style>
</head>
<body>
<header class="site">
  <span class="brand">AI Tutor</span>
  <span class="crumb">{escape(module_id)} &middot; {escape(module_name)}</span>
  <span class="spacer"></span>
  {module_nav(module_id)}
</header>
<main>
  <h1 class="doc">{escape(module_name)}</h1>
  <p class="meta">{escape(module_id)} &middot; {total} document(s)</p>
  {"".join(blocks) if blocks else "<p>No documents have been ingested for this module yet.</p>"}
  <footer class="doc"><a href="/">Back to the tutor</a></footer>
</main>
</body>
</html>"""


def json_str(value: str) -> str:
    """JSON-encode a string for safe embedding inside a <script> block."""
    import json

    return json.dumps(value)


def document_rel_path(source_file: str, module_id: str) -> str:
    """Reduce a stored ``source_file`` to a path relative to its module folder.

    Ingestion stores ``source_file`` relative to the content root, so it carries
    the module folder: ``IPRT/slides/01_Inheritance.md``. The resource routes are
    mounted per module (``/resources/IPRT301/<path>``), so the leading folder has
    to come off or every citation 404s.
    """
    path = (source_file or "").strip().lstrip("/")
    parts = path.split("/")
    folder = settings.folder_for_module_id(module_id)
    if folder and parts and parts[0].upper() == folder.upper():
        parts = parts[1:]
    return "/".join(parts)


def citation_url(module_id: str, source_file: str, section_title: Optional[str]) -> tuple[str, str]:
    """Return ``(url, anchor)`` for a retrieved chunk.

    The anchor is derived with the same :func:`slugify` the page renderer uses, so
    a citation and its target cannot drift. Both ``?at=`` and the bare ``#anchor``
    fragment are emitted: the fragment makes the browser scroll natively even with
    scripting unavailable, ``?at=`` drives the highlight.

    Known imprecision: two headings with identical text in one document get
    ``-2`` / ``-3`` suffixes on the page, and a citation built from the section
    title alone always resolves to the first. Rare enough in this corpus to accept
    rather than carry a per-chunk anchor through ingestion.
    """
    rel = document_rel_path(source_file, module_id)
    anchor = slugify(section_title) if section_title else ""
    if not rel:
        return "", anchor
    base = f"/resources/{module_id}/{rel}"
    if anchor:
        return f"{base}?at={anchor}#{anchor}", anchor
    return base, anchor
