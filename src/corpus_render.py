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

#: The OCR pass over the slide decks left these inline, sometimes dozens of times
#: per document. They are provenance, not content, so they are hoisted into one
#: banner at the top of the page instead of interrupting the prose.
_OCR_NOTE_RE = re.compile(
    r"^\s*\*?\s*(?:diagram|screenshot|image|figure|chart|table|code)?\s*"
    r"[/ ]?\s*(?:text|diagram|screenshot)[^:\n]{0,40}:\s*\(?\s*ocr[^)\n]*\)?\s*[*_]*\s*$",
    re.IGNORECASE,
)
_GENERIC_OCR_RE = re.compile(r"ocr[, ]+may\s+contain\s+errors", re.IGNORECASE)
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
    #: True when the document was recovered by OCR and carried its "may contain
    #: errors" warnings. Surfaced once as a provenance banner rather than
    #: repeated inline, where it broke up the prose.
    ocr_recovered: bool = False


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
    rel = (rel_path or "").strip().lstrip("/")
    candidates: list[Path] = []
    # The caller's ``rel_path`` is already relative to the module folder, so it
    # is tried directly under the real folder first. But the module folder on
    # disk may be the bare key (``IPRT``) or the LMS export name
    # (``IPRT301_SEM1_...``), and older callers pass the full stored path
    # (including the folder). Try each plausible base so a link resolves
    # regardless of which convention produced it.
    candidates.append(root / folder / rel)
    candidates.append(root / rel)
    # Match a prefixed export folder for this module.
    try:
        for child in root.iterdir():
            if child.is_dir() and child.name.upper().startswith(folder.upper()):
                candidates.append(child / rel)
    except OSError:
        pass

    for candidate in candidates:
        try:
            resolved_candidate = candidate.resolve()
            resolved_candidate.relative_to(root)
        except (ValueError, OSError):
            continue
        if resolved_candidate.is_file() and resolved_candidate.suffix.lower() in RENDERABLE_SUFFIXES:
            return resolved_candidate
    logger.warning("Rejected or missing corpus path: %s", rel_path)
    return None


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
    had_ocr_note = False
    # Slide decks arrive with `---` used as a slide separator, so a document can
    # carry dozens of rules that say nothing. Only emit one when it separates
    # real prose; adjacent-to-slide-marker rules are dropped.
    pending_rule = False

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

        # Provenance line from the OCR pass - hoist, do not render inline.
        if _OCR_NOTE_RE.match(line) or _GENERIC_OCR_RE.search(line):
            had_ocr_note = True
            continue

        marker = _SLIDE_MARKER_RE.search(line)
        if marker:
            flush()
            if pending_rule:
                pending_rule = False  # redundant: the slide anchor is the divider
            slide_no = int(marker.group(1))
            # The slide's own heading supplies the title; this anchor guarantees a
            # landing point even for a slide with no heading.
            anchor = _unique_anchor(f"slide-{slide_no}", seen)
            headings.append(Heading(level=2, title=f"Slide {slide_no}", anchor=anchor, slide=slide_no))
            out.append(f'<div class="slide-anchor" id="{anchor}"></div>')
            continue

        if _HR_RE.match(line):
            flush()
            pending_rule = True
            continue

        heading = _ATX_HEADING_RE.match(line)
        if heading:
            flush()
            if pending_rule:
                out.append("<hr>")
                pending_rule = False
            from .loaders import _clean_title

            title = _clean_title(heading.group(2))
            level = _normalise_heading_level(len(heading.group(1)))
            anchor = _unique_anchor(slugify(title), seen)
            headings.append(Heading(level=level, title=title, anchor=anchor, slide=slide_no))
            rendered = md.render(f"{'#' * min(level + 1, 6)} {title}")
            # markdown-it assigns its own id only with the attr plugin, so inject
            # ours on the first heading tag of the rendered fragment.
            # The second half of this replacement must be a raw f-string: in a
            # plain f-string "\1" is the octal escape for chr(1), so the heading
            # used to render as "#<SOH>2. Inheritance" - a control character
            # wedged between the permalink and the text, which also broke
            # textContent for anything matching on heading text.
            rendered = re.sub(
                r"(<h[1-6])(>)",
                rf'\1 id="{anchor}"><a class="anchor-link" href="#{anchor}" '
                rf'title="Link to this section">#</a>',
                rendered,
                count=1,
            )
            out.append(rendered)
            continue

        buffer.append(line)

    flush()
    if pending_rule:
        out.append("<hr>")

    doc_title = headings[0].title if headings else (source_name or rel_path)
    body = "\n".join(out)
    rendered_doc = RenderedDoc(
        module_id=module_id,
        rel_path=rel_path,
        title=doc_title,
        html=body,
        headings=headings,
        source_name=source_name or Path(rel_path).name,
        category=category,
    )
    rendered_doc.ocr_recovered = had_ocr_note
    return rendered_doc


def _normalise_heading_level(raw_level: int) -> int:
    """Clamp a source heading depth onto a usable ladder.

    The OCR'd decks mix ``##``, ``###`` and ``####`` for what are really the same
    two tiers of structure, which renders as a ragged outline and produces a
    near-useless table of contents. Clamping keeps the relative order while
    guaranteeing that a jump never exceeds one level, so no heading is skipped
    past its parent.
    """
    return min(max(raw_level, 2), 4)


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
        f'<details class="toc" open><summary>Contents ({len(toc)})</summary>'
        f'<ul>{"".join(toc)}</ul></details>'
        if toc
        else ""
    )

    # Provenance, stated once. The OCR decks repeat their own caveat inline
    # dozens of times, which is noise once the reader knows it applies to the
    # whole document.
    kind = {
        "slides": ("Lecture slides", "slide-deck"),
        "notes": ("Lecture notes", "notes"),
        "lecture_notes": ("Lecture notes", "notes"),
        "exercises": ("Exercises", "exercises"),
        "examples": ("Worked examples", "examples"),
        "books": ("Textbook (third-party)", "book"),
        "tutor_answers": ("Tutor answer", "answer"),
    }.get(doc.category, (doc.category.replace("_", " ").title(), "notes"))
    provenance = (
        f'<p class="provenance"><span class="badge {kind[1]}">{_esc(kind[0])}</span>'
        + (
            '<span class="badge ocr">recovered by OCR &mdash; may contain errors</span>'
            if doc.ocr_recovered
            else ""
        )
        + "</p>"
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
.toc ul {{ columns: 1; }}
@media (min-width: 720px) {{ .toc ul {{ columns: 2; column-gap: 32px; }} }}
p.provenance {{ margin: -14px 0 20px; display: flex; gap: 6px; flex-wrap: wrap; }}
.badge {{ font-size: 11px; font-weight: 600; letter-spacing: .03em; text-transform: uppercase;
          padding: 3px 8px; border-radius: 999px; border: 1px solid; }}
.badge.slide-deck {{ background: #eef2ff; color: #4338ca; border-color: #c7d2fe; }}
.badge.notes {{ background: #ecfdf5; color: #047857; border-color: #a7f3d0; }}
.badge.exercises {{ background: #fff7ed; color: #c2410c; border-color: #fed7aa; }}
.badge.examples {{ background: #f5f3ff; color: #6d28d9; border-color: #ddd6fe; }}
.badge.book {{ background: #fefce8; color: #a16207; border-color: #fde68a; }}
.badge.answer {{ background: #f0fdf4; color: #15803d; border-color: #bbf7d0; }}
.badge.ocr {{ background: #fff1f2; color: #be123c; border-color: #fecdd3; text-transform: none;
              letter-spacing: 0; font-weight: 500; }}
.doc-body {{ font-size: 16.5px; }}
.doc-body h2 {{ scroll-margin-top: 80px; }}
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
  {provenance}
  <p class="meta">Source file <code>{_esc(doc.rel_path)}</code></p>
  {toc_html}
  <article class="doc-body">
  {doc.html}
  </article>
  <footer class="doc">{"".join(nav_links)}</footer>
</main>
{flash}
</body>
</html>"""


def render_library_root(modules: list[dict]) -> str:
    """Styled entry point for the whole corpus: every module and its document count."""
    from html import escape

    cards = []
    for mod in modules:
        count = mod.get("document_count", 0)
        breakdown = []
        for label, n in (mod.get("by_category") or {}).items():
            if n:
                breakdown.append(f"{n} {label}")
        cats = escape(" · ".join(breakdown)) if breakdown else escape("no documents")
        cards.append(
            f'<li class="mod">'
            f'<a href="{escape(mod["url"])}">'
            f'<span class="code">{escape(mod["module_id"])}</span>'
            f'<span class="name">{escape(mod["module_name"])}</span>'
            f'<span class="meta">{count} document(s)</span>'
            f'<span class="cats">{cats}</span>'
            f"</a></li>"
        )

    total_docs = sum(m.get("document_count", 0) for m in modules)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Course material</title>
<style>{_CSS}
main {{ max-width: 900px; }}
ul.modules {{ list-style: none; margin: 0; padding: 0; display: grid; gap: 10px;
            grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); }}
li.mod a {{ display: flex; flex-direction: column; gap: 3px; padding: 14px 16px;
            text-decoration: none; color: inherit; background: var(--card);
            border: 1px solid var(--line); border-radius: 10px; height: 100%; }}
li.mod a:hover {{ border-color: var(--accent); background: var(--accent-soft); }}
li.mod .code {{ font-family: var(--mono); font-size: 11px; letter-spacing: .06em;
                color: var(--accent); text-transform: uppercase; }}
li.mod .name {{ font-size: 16px; font-weight: 600; }}
li.mod .meta {{ font-size: 12px; color: var(--muted); }}
li.mod .cats {{ font-size: 11px; color: var(--muted); opacity: .8; }}
</style>
</head>
<body>
<header class="site">
  <span class="brand">AI Tutor</span>
  <span class="crumb">Course material</span>
  <span class="spacer"></span>
  {module_nav("")}
</header>
<main>
  <h1 class="doc">Course material</h1>
  <p class="meta">{len(modules)} module(s) &middot; {total_docs} document(s).
     Every source a tutor answer can cite is here.</p>
  <ul class="modules">
    {"".join(cards) if cards else "<p>No modules are configured.</p>"}
  </ul>
  <footer class="doc"><a href="/">Back to the tutor</a></footer>
</main>
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

    The folder on disk may be the bare registry key (``IPRT``) or the LMS export
    name (``IPRT301_SEM1_2026_...``). Either way it is the *first* path segment,
    so strip it unconditionally rather than only when it equals the registry
    key - otherwise the doubled prefix makes every citation 404 on a real corpus.
    """
    path = (source_file or "").strip().lstrip("/")
    parts = [p for p in path.split("/") if p]
    if not parts:
        return ""

    folder = settings.folder_for_module_id(module_id)
    first = parts[0].upper()
    if folder and (
        first == folder.upper()
        or first.startswith(folder.upper())
        or first.startswith((module_id or "").upper())
    ):
        parts = parts[1:]
    return "/".join(parts)


def citation_url(module_id: str, source_file: str, section_title: Optional[str]) -> tuple[str, str]:
    """Return ``(url, anchor)`` for a retrieved chunk.

    The anchor is derived with the same :func:`slugify` the page renderer uses, so
    a citation and its target cannot drift. Both ``?at=`` and the bare ``#anchor``
    fragment are emitted: the fragment makes the browser scroll natively even with
    scripting unavailable, ``?at=`` drives the highlight.

    Known imprecision, measured: 15 of 58 documents repeat a heading text, and 40
    of the 1,055 distinct section titles a chunk can be tagged with (3.8%) resolve
    to a non-unique id, so such a citation lands on the first of the repeated
    headings rather than its own. The repeats concentrate in the two ingested
    textbooks ("Problem" / "Discussion" / "Solution" per recipe, and chapter
    front-matter), not in the lecture material. Fixing it properly needs the
    anchor stored per chunk at ingest time, because the retriever cannot otherwise
    tell which occurrence a chunk came from.
    """
    rel = document_rel_path(source_file, module_id)
    anchor = slugify(section_title) if section_title else ""
    if not rel:
        return "", anchor
    # The resource page route renders Markdown/text only. A chunk whose source is
    # a raw PDF/PPTX has no renderable page, so advertising a link would 404.
    # Return no URL in that case; the citation still carries the source name.
    if Path(rel).suffix.lower() not in RENDERABLE_SUFFIXES:
        return "", anchor
    base = f"/resources/{module_id}/{rel}"
    if anchor:
        return f"{base}?at={anchor}#{anchor}", anchor
    return base, anchor
