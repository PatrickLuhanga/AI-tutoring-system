# Practice question bank and source papers

This folder holds everything needed to reproduce the practice question bank on a
fresh clone. Without it the app starts with an empty bank and Practice returns
nothing.

## Contents

| File | What it is |
|---|---|
| `question_bank.json` | The bank itself: 108 questions, the fixture the app loads from |
| `*.pdf` | The 12 scanned question papers the 100 past-paper questions were extracted from |

## Loading it

```powershell
python -m src.seed_question_bank
```

Additive and idempotent — a question already present for that module is left
alone, so it is safe to run on every setup and it will not undo anything a
lecturer has added or edited.

```powershell
python -m src.seed_question_bank --check   # report drift, write nothing
python -m src.seed_question_bank --export # database -> JSON (after editing the bank)
```

`--reset` empties the whole table before loading. It cannot tell a seeder row
from a lecturer-authored one, so it will delete hand-written questions too, and
it refuses to run without `--yes`.

## Provenance

The 100 `past_paper` questions come from real DUT question papers: six IPRT301,
three RESK and three SPRI. They are genuine exam content, not invented.

The 8 `generated` questions are for **PBDV301**, which has no past papers — it is
not examinable from a paper corpus. They were written by the local model from the
module's own slide decks and are labelled *AI-generated for revision - not from a
past paper*. A question generated this way has not been through academic
approval, and a student revising for an unseen assessment should be able to see
that difference rather than assume every question in the bank carries equal
weight.

**Module name mapping.** The RESK papers are branded `RESK401`; the module
registry calls that module `RESK301`. Banked questions use `RESK301`, since the
bank is keyed on the registry.

## Read this before using the past-paper questions

The papers are **scanned images, not text**. `pypdf` extracts nothing from them,
so the questions were produced by rasterising each page and running it through
the Windows OCR engine. Three consequences:

1. **Wording is imperfect.** Numbers are occasionally dropped, punctuation is
   unreliable, and some phrases are garbled.
2. **Diagrams and code listings are lost entirely.** OCR recovers the text
   around them and nothing of the figure itself. Several IPRT301 questions refer
   to "the diagram below" or a class listing with nothing below them — a student
   cannot answer those from the bank alone.
3. **There are no answers.** The papers do not print marking guidelines, so those
   100 questions carry no `answer_notes`. They are excluded from a practice test's
   score rather than counted wrong, because marking them would be meaningless.

Every one of these rows is tagged `(OCR - verify)` in its `source_label` and
carries the paper and page it came from. **A lecturer should proofread them
before a student relies on them.**

## Re-extracting from the PDFs

Only needed if the extraction logic changes. It needs Windows and the OCR engine
that ships with it; there is nothing to install.

```powershell
python -m src.seed_exam_questions exam_papers --dry-run   # show what would be stored
python -m src.seed_exam_questions exam_papers             # store it
```

This overwrites nothing — it inserts. Clear the previous rows first if you want a
clean re-extraction:

```powershell
python -m src.seed_question_bank --reset --yes
python -m src.seed_exam_questions exam_papers
```

Extraction is filtered hard. Blocks that are not questions get dropped: cover
pages, rubrics, instructions to candidates, and the bare option-letter debris
that a two-column multiple-choice page reduces to once OCR separates the numbering
column from the question column. Raw extraction yields about 266 blocks; roughly
100 survive as real questions.

## A note on publishing

These papers are the actual questions students will be examined on, so this
bank is effectively an answer bank for real assessments. That is normal practice
and a deliberate institutional choice, but it is worth deciding explicitly rather
than inheriting by accident. Setting `is_active = false` on a paper's questions
once it has been sat is the straightforward control.