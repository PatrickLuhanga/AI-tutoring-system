# Local setup notes

Working notes from standing this project up on Windows on 2026-09-26.
`GETTING_STARTED.md` is the canonical guide; this file records the places where
a clean clone did **not** match that guide on this machine.

---

## 1. Environment deviations

| Guide says | This machine | Outcome |
| --- | --- | --- |
| Python 3.10–3.12 (3.12 recommended) | 3.14.6 | **Fine.** `torch` 2.14.0 ships `cp314` `win_amd64` wheels, as do `numpy` 2.5.3 and `psycopg2-binary` 2.9.13. The ML-stack warning in the guide predates 3.14 wheels. |
| Node 18+ | 24.11.0 | Fine (Vite 8, React 19, TS 6). |
| Ollama pre-installed | not installed | Installed via `winget install Ollama.Ollama` (0.34.4). Auto-starts and listens on 11434. |

Git was installed but **not on `PATH`** — `C:\Program Files\Git\cmd` was missing
from both the user and machine `PATH`, which made Windows offer to "install Git"
on every `git` invocation. Added it to the user `PATH`.

## 2. Admin key must be aligned by hand

`.env.example` ships `ADMIN_API_KEY=change-me-admin-key` while
`frontend/.env.example` ships `VITE_ADMIN_KEY=dev-admin-key`. These
intentionally differ, so a default setup returns **401 on every `/api/admin/*`
call** (including the model dropdown in the Admin dashboard). Set both to the
same value.

## 3. Known repo gap: `src/corpora/` is git-ignored

`.gitignore` line 26 is:

```
corpora/
```

Unanchored, so it matched `src/corpora/` at any depth, not just a top-level
`corpora/` directory. Consequence: `src/corpora/java_error_corpus.py` (the 50
synthetic Java error patterns) was hidden from git — `git log --all --
src/corpora` returned nothing.

`python -m src.ingest_code_patterns` therefore failed immediately with
`ModuleNotFoundError: No module named 'src.corpora'`.

Only that one script imports it, so the gateway and retriever were unaffected —
but `code_repair_patterns` stayed empty and the tier could never be loaded.

**Resolved 2026-09-27.** The file is now present and committed. Its 50 patterns
were drafted by an LLM, not hand-authored, so the corpus should be treated as
unreviewed: read the hints before relying on them, and do not report a retrieval
score measured against it as though it generalised to real student error logs.

Fix for the pattern (anchors it, consistent with `models/` and `.cache/` above):

```
/corpora/
```

## 4. Known repo gap: `academic content/` is git-ignored by design

Line 25 excludes the course material, so a fresh clone has no source documents
and `python -m src.ingest_curriculum` has nothing to read. The tables and HNSW
indexes are created; they just stay empty. Retrieval therefore returns no
context until the IPRT / PBDV / RESK / SPRI files are supplied.

## 5. Dev-server quirk: first load hangs on "Loading view…"

After every Vite start, the first page load sits on the `Suspense` fallback
indefinitely, with **no console error** and the module itself serving HTTP 200.
A browser reload renders it correctly. Reproduced on every restart, so it is a
Vite 8 dep-optimizer / dynamic-import race rather than a code fault. Hit it
again after a restart and just reload once.

## 6. Empty vector store + guardrail interaction

With `GUARDRAIL_MIN_CONTEXT_OVERLAP=0.08` and zero indexed chunks, retrieved
context cannot overlap anything. Expect the guardrail to engage more readily
than it will once content is ingested — this is a consequence of gaps 3 and 4,
not a guardrail bug.

## 7. Start it all

```powershell
.\start-local.ps1
```

Preflights the venv and env files, brings up Postgres and waits for its
healthcheck, warns if Ollama or the `qwen3` model is missing, then starts Flask
and Vite.
