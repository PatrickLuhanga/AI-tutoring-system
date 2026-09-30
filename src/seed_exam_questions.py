"""Extract questions from scanned past-paper PDFs into the practice bank.

The corpus is a folder of scanned question papers. ``pypdf`` returns nothing for
them - they are page images, not text - so each page is rasterised and run
through the Windows OCR engine. That needs no download and handles printed
English well, but an exam paper is not clean prose: it has mark allocations in
the margin, two-column layouts, embedded code listings and hand-drawn diagrams.

So the extractor is deliberately conservative:

* only text under a recognised question number is taken, and a question needs a
  minimum body length before it is stored at all;
* the mark allocation is captured as metadata rather than left in the prompt;
* a question whose OCR confidence is poor, or whose text is mostly boilerplate
  (the cover page's instructions, "ANSWER ALL...", page furniture), is dropped;
* everything is seeded with ``origin='past_paper'`` and the paper it came from, so
  a lecturer can see what is real exam material and what needs proofreading.

Run ``python -m src.seed_exam_questions --dry-run`` to see what would be stored
without touching the database.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

#: Module code as it appears in the filenames -> the id in ``modules``.
MODULE_ALIASES = {
    "IPRT301": "IPRT301",
    # The RESK papers are branded RESK401; the registry uses RESK301.
    "RESK401": "RESK301",
    "RESK301": "RESK301",
    "SPRI301": "SPRI301",
    "PBDV301": "PBDV301",
}

#: A question heading. The papers use "Question 2:", "QUESTION 3;" (the colon
#: is frequently mis-read as a semicolon), "1.4.", "3)" and bare "4.1" - so the
#: separator is deliberately permissive. OCR on these scans is inconsistent
#: about punctuation, and rejecting a real question over a stray colon would
#: discard most of the corpus.
_Q_START = re.compile(
    r"^\s*(?:question\s*|q\.?\s*)?(\d{1,2}(?:\.\d{1,2})?)\s*[.):;\-]?\s+(\S.{2,})$",
    re.IGNORECASE,
)
#: Trailing or bracketed mark allocation: "[6]", "(12 marks)", "[6 M]"
_MARKS = re.compile(r"[\[(]\s*(\d{1,2})\s*(?:m(?:arks?)?)?\s*[\])]", re.IGNORECASE)
#: Page furniture and boilerplate that is never a question.
_BOILERPLATE = re.compile(
    r"(page\s+\d+\s+of\s+\d+|integrative programming and technologies|"
    r"research skills|social (?:and|&) professional issues|"
    r"^\s*duration\b|^\s*total\b|memorandum\s*$|"
    r"answer\s+all|instructions to candidates|"
    r"^\s*\*?\s*(?:begin|end)\s+of\s+(?:page|question))",
    re.IGNORECASE | re.MULTILINE,
)
_CLEAN = re.compile(r"[^A-Za-z0-9 ,.:;()'\"?!/=\-\n]+")
#: A run of bare option letters, which is what a two-column MCQ page reduces to
#: once OCR has separated the numbering column from the question column.
_BARE_OPTIONS = re.compile(r"^[\sA-Da-d.,]{0,60}$")
#: Two or more consecutive sub-item numbers ("1.1. 1.2. 1.3.") or option runs
#: ("A. B. C. D.") inside one block. On these scans this is the signature of a
#: column that OCR read without its question stems, so the block is column
#: debris rather than a question.
_OPTION_RUN = re.compile(r"(?:[A-D]\.\s*){3,}")
_SUBITEM_RUN = re.compile(r"(?:\b\d{1,2}\.\d{1,2}\.?\s+){3,}")

MIN_CHARS = 40
MIN_WORDS = 8

#: An exam question either asks for something (imperative) or asks about
#: something (interrogative). Requiring one of these is what separates a real
#: question from the cover page, the instructions block and the rubric that the
#: length and boilerplate filters alone let through.
_ASK_FOR = re.compile(
    r"^\W*(?:describe|explain|discuss|list|define|compare|contrast|state|outline|"
    r"write|draw|calculate|differentiate|illustrate|give|suggest|provide|analyse|"
    r"analyze|evaluate|examine|justify|recommend|indicate|identify|critically)\b",
    re.IGNORECASE,
)
_ASK_ABOUT = re.compile(
    r"\b(?:which|what|how|why|when|where|who)\b", re.IGNORECASE
)

#: Cover pages, rubrics and instructions. These are the blocks that survive
#: every other filter and are the main source of nonsense in a raw extraction.
_NOT_A_QUESTION = re.compile(
    r"(examin(?:er|ation)\s*/?\s*s?|moderator|HOURS?\s+\d|re\s+mark|mc?q\s+card|"
    r"answer\s+(?:section\s+\w\s+)?(?:on|in|using)\s+the|booklet|"
    r"write your surname|make sure you|do not open|may not be|"
    r"INSTRUCTIONS?\s+TO\s+CANDIDATES|where stated\b|"
    r"total\s*[:.]?\s*\d*\s*marks?|number of pages|formula sheet|"
    r"blank|space\b.*\d+\s*cm|\(\s*INCLUDING\s+COVER)",
    re.IGNORECASE,
)


def looks_like_question(text: str) -> bool:
    """True when a block reads like an exam question rather than page furniture."""
    body = (text or "").strip()
    if not body or _NOT_A_QUESTION.search(body):
        return False
    if len(body.split()) < MIN_WORDS:
        return False
    return bool(_ASK_FOR.match(body) or body.rstrip().endswith("?") or _ASK_ABOUT.search(body))


@dataclass
class ExtractedQuestion:
    module_id: str
    prompt: str
    marks: Optional[int]
    source_label: str
    paper: str
    number: Optional[int] = None


@dataclass
class PaperResult:
    module_id: str
    paper: str
    questions: list[ExtractedQuestion] = field(default_factory=list)
    ocr_chars: int = 0
    pages: int = 0


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------
_OCR_PS1 = r"""
param([string]$InFile, [string]$OutFile)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() |
  Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
                 $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) {
  $t = $asTaskGeneric.MakeGenericMethod($type).Invoke($null, @($op))
  $t.Wait(-1) | Out-Null
  $t.Result
}
[Windows.Media.Ocr.OcrEngine,Windows.Media.Ocr,ContentType=WindowsRuntime] | Out-Null
[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime] | Out-Null

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage(
  [Windows.Globalization.Language]::new('en-GB'))
if (-not $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }

# Paths arrive as a JSON file, not argv: passing many paths to `powershell -File`
# only bound the first one, so only page 0 was ever read.
$files = Get-Content -Raw -LiteralPath $InFile | ConvertFrom-Json

$out = @()
foreach ($f in $files) {
  $text = ""
  try {
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync([string]$f)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    # Per-line, not Result.Text: Text collapses the whole page into one line,
    # destroying the question-numbering structure the parser depends on.
    $lines = @()
    foreach ($ln in $result.Lines) { $lines += [string]$ln.Text }
    $text = ($lines -join "`n")
  } catch {
    $text = ""
  }
  $out += ,$text
}
# Results go to a file, not stdout: OCR text is multi-line and PowerShell's
# console output mangles it, producing unparseable JSON on the way back.
[System.IO.File]::WriteAllText($OutFile, ($out | ConvertTo-Json -Compress), [System.Text.Encoding]::UTF8)
"""


def _ocr_windows(image_paths: Iterable[Path]) -> dict[int, str]:
    """Run Windows OCR over images, returning ``{page_index: text}``.

    Uses the WinRT OCR engine through PowerShell, which avoids pulling a
    multi-hundred-megabyte model onto the machine for what is printed English.
    Paths go in and results come out as JSON *files*: argv binding silently kept
    only the first path, and stdout mangled the multi-line result.
    """
    import json
    import subprocess
    import tempfile

    paths = [str(p) for p in image_paths]
    if not paths:
        return {}

    tmp = Path(tempfile.gettempdir())
    in_file = tmp / "exam_ocr_in.json"
    out_file = tmp / "exam_ocr_out.json"
    script_path = tmp / "exam_ocr.ps1"
    script_path.write_text(_OCR_PS1, encoding="utf-8")
    in_file.write_text(json.dumps(paths), encoding="utf-8")
    out_file.unlink(missing_ok=True)

    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(script_path), "-InFile", str(in_file), "-OutFile", str(out_file)],
        capture_output=True,
        text=True,
        timeout=3600,
    )
    if not out_file.exists():
        logger.warning(
            "OCR produced no output file (rc=%s): %s", proc.returncode, (proc.stderr or "")[:400]
        )
        return {}
    try:
        # utf-8-sig: PowerShell's UTF8 encoder writes a BOM, which json.loads
        # rejects outright.
        data = json.loads(out_file.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        logger.warning("OCR output was not JSON: %s", exc)
        return {}
    if isinstance(data, str):
        data = [data]
    return {i: (t or "") for i, t in enumerate(data)}


def ocr_pdf(path: Path, *, dpi: int = 200, workdir: Optional[Path] = None) -> dict[int, str]:
    """Rasterise and OCR a PDF. Returns ``{page_index: text}``."""
    import pymupdf

    tmp = workdir or Path(tempfile.mkdtemp(prefix="examocr_"))
    tmp.mkdir(parents=True, exist_ok=True)
    images: list[Path] = []
    with pymupdf.open(str(path)) as doc:
        for n, page in enumerate(doc):
            pix = page.get_pixmap(dpi=dpi)
            img = tmp / f"p{n:03d}.png"
            pix.save(str(img))
            images.append(img)
    logger.info("Rasterised %d page(s) from %s at %d dpi", len(images), path.name, dpi)
    return _ocr_windows(images)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def module_for(paper_name: str) -> Optional[str]:
    """Map a filename to a module id, e.g. ``... RESK401.pdf`` -> ``RESK301``."""
    upper = paper_name.upper()
    for token, module_id in MODULE_ALIASES.items():
        if token in upper:
            return module_id
    return None


def pretty_paper_name(stem: str) -> str:
    """``2023 MIDYEAR MAIN QP INTEGRATIVE PROGRAMMING AND TECHNOLOGIES 3 IPRT301``
    -> ``2023 Midyear Main - IPRT301``."""
    stem = stem.replace("_", " ").strip()
    upper = stem.upper()
    for token in MODULE_ALIASES:
        if token in upper:
            year = stem[:4] if stem[:4].isdigit() else ""
            tail = upper.split("QP", 1)[-1].strip() if "QP" in upper else upper
            kind = ""
            for word in ("MIDYEAR MAIN", "MIDYEAR SUPP", "FEBRUARY SPECIAL", "SPECIAL", "SUPP", "MAIN"):
                if word in tail:
                    kind = word.title()
                    break
            bits = [b for b in (year, kind, token) if b]
            return " - ".join(bits) if bits else stem
    return stem


def _tidy(text: str) -> str:
    text = text.replace("\u00a0", " ").replace("\uf0b7", " ")
    text = _CLEAN.sub(" ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _is_boilerplate(text: str) -> bool:
    return bool(_BOILERPLATE.search(text))


def parse_questions(module_id: str, paper: str, page_texts: dict[int, str]) -> list[ExtractedQuestion]:
    """Pull numbered questions out of OCR'd page text.

    Exam papers are laid out in columns, and OCR reads them in reading order, so
    a multiple-choice page can arrive as a line of bare option letters
    ("1.6. A. B. C. D. 1.7.") with the actual question stem somewhere else
    entirely. Those fragments are recognised and skipped rather than stored as
    nonsense; only questions whose text actually follows their number survive.
    """
    found: list[ExtractedQuestion] = []
    seen: set[str] = set()

    for page_no in sorted(page_texts):
        text = page_texts[page_no] or ""
        if not text.strip():
            continue

        current: Optional[ExtractedQuestion] = None
        buf: list[str] = []

        def close() -> None:
            nonlocal current, buf
            if current is not None:
                body = _tidy("\n".join(buf))
                # Too short, or just option letters with no stem: not a question.
                if (
                    len(body) >= MIN_CHARS
                    and len(body.split()) >= MIN_WORDS
                    and not _BARE_OPTIONS.match(body)
                    and not _OPTION_RUN.search(body)
                    and not _SUBITEM_RUN.search(body)
                    and not _is_boilerplate(body)
                    and looks_like_question(body)
                ):
                    key = body.lower()[:180]
                    if key not in seen:
                        seen.add(key)
                        marks = _MARKS.search(body)
                        current.marks = int(marks.group(1)) if marks else None
                        current.prompt = _MARKS.sub("", body).strip()
                        if len(current.prompt) >= MIN_CHARS:
                            found.append(current)
            current = None
            buf = []

        for line in text.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            # A heading is short. Anything longer is a wrapped sentence that
            # happens to begin with a digit, and must stay body text.
            match = _Q_START.match(stripped) if len(stripped) < 200 else None
            if match:
                close()
                try:
                    number: Optional[int] = int(match.group(1).split(".")[0])
                except ValueError:
                    number = None
                current = ExtractedQuestion(
                    module_id=module_id,
                    prompt="",
                    marks=None,
                    source_label=f"{paper} (p{page_no + 1})",
                    paper=paper,
                    number=number,
                )
                buf.append(match.group(2))
                continue
            if current is not None:
                buf.append(stripped)
        close()

    return found


def extract_paper(path: Path) -> PaperResult:
    """OCR and parse one paper."""
    module_id = module_for(path.name)
    paper = pretty_paper_name(path.stem)
    if module_id is None:
        logger.warning("No module id in %s; skipping.", path.name)
        return PaperResult(module_id="", paper=paper)

    pages = ocr_pdf(path)
    result = PaperResult(
        module_id=module_id,
        paper=paper,
        ocr_chars=sum(len(v or "") for v in pages.values()),
        pages=len(pages),
    )
    result.questions = parse_questions(module_id, paper, pages)
    return result


def discover(directory: Path) -> list[Path]:
    return sorted(p for p in directory.glob("*.pdf"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", help="folder of past-paper PDFs")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be stored, without writing to the database",
    )
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument(
        "--min-chars", type=int, default=MIN_CHARS, help="drop anything shorter"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    directory = Path(args.directory)
    if not directory.is_dir():
        print(f"Not a directory: {directory}", file=sys.stderr)
        return 2

    papers = discover(directory)
    print(f"Found {len(papers)} paper(s) in {directory}\n")

    results: list[PaperResult] = []
    for paper_path in papers:
        print(f"-> {paper_path.name}")
        try:
            result = extract_paper(paper_path)
        except Exception as exc:  # noqa: BLE001 - one bad paper must not stop the run
            print(f"   FAILED: {exc}")
            continue
        results.append(result)
        print(
            f"   module={result.module_id or '?'} pages={result.pages} "
            f"ocr_chars={result.ocr_chars} questions={len(result.questions)}"
        )
        for q in result.questions[:2]:
            preview = q.prompt.replace("\n", " ")[:110]
            print(f"     [{q.number}] ({q.marks or '?'} marks) {preview}")
    print()

    total = sum(len(r.questions) for r in results)
    by_module: dict[str, int] = {}
    for r in results:
        if r.module_id:
            by_module[r.module_id] = by_module.get(r.module_id, 0) + len(r.questions)
    print(f"TOTAL: {total} question(s) across {len(papers)} paper(s)")
    for module_id, n in sorted(by_module.items()):
        print(f"  {module_id}: {n}")

    if args.dry_run:
        print("\nDry run - nothing written.")
        return 0

    if not total:
        print("\nNothing to seed.")
        return 1

    from .practice import PracticeError, add_question

    seeded = 0
    for result in results:
        for q in result.questions:
            try:
                add_question(
                    module_id=q.module_id,
                    prompt=q.prompt,
                    answer_notes=None,  # not printed on the paper
                    difficulty="medium",
                    origin="past_paper",
                    # Provenance a lecturer needs before trusting it: which paper,
                    # which page, and that it came through OCR.
                    source_label=f"{q.source_label} (OCR - verify)",
                    created_by=None,
                )
                seeded += 1
            except PracticeError as exc:
                print(f"  skipped one question: {exc}")

    print(f"\nSeeded {seeded} question(s) into the bank.")
    print("These carry origin='past_paper'. Proofread them before use - the")
    print("source PDFs are scans, so OCR will have made some mistakes.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
