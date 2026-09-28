# Project 302 — System Audit Report (Live Codebase)

**Date:** 2026-09-28
**Branch at audit time:** `main` @ `5a94da2947c15a296819cf00c148fd01d0ffdb41` (dirty working tree)
**Scope:** Full repository — Flask API Gateway, PostgreSQL/pgvector data tier, ingestion
pipeline, RAG/hybrid retrieval, multi-agent orchestration, inference client, and the React
Client Tier.
**Method:** Live container/database inspection, source inspection across every module, and
execution checks (`create_app()` boots and exposes 19 routes; frontend `oxlint` 0 errors;
`tsc -b` exit 0).

---

## 1. Environment Snapshot

### 1.1 Containers & Database (live)

| Item | Value |
| --- | --- |
| PostgreSQL container | `ai_tutoring_pg` — image `pgvector/pgvector:pg16` — **Up (healthy)** |
| Host port | `0.0.0.0:5433 -> 5432` (`docker-compose.yml:11`) |
| Server version | `PostgreSQL 16.15 (Debian)` |
| Database size | `117 MB` |
| Extensions | `vector 0.8.6`, `pg_trgm 1.6`, `plpgsql 1.0` |
| Tables | **11** — `students`, `modules`, `enrollments`, `tutor_assignments`, `tutoring_sessions`, `session_messages`, `telemetry_logs`, `hint_feedback`, `llm_configs`, `curriculum_chunks`, `code_repair_patterns` |
| Indexes | **43** (incl. HNSW `vector_cosine_ops`, GIN `content_tsv`, GIN `chunk_text gin_trgm_ops`, GIN `doc_metadata jsonb_path_ops`) |

### 1.2 Live Row Counts

| Table | Rows | Note |
| --- | --- | --- |
| `modules` | 4 | IPRT301, PBDV301, RESK301, SPRI301 |
| `students` | 4 | seeded demo identities |
| `enrollments` | 2 | |
| `tutor_assignments` | 4 | |
| `curriculum_chunks` | **9,151** | IPRT 3721 · PBDV 4328 · RESK 924 · SPRI 178 |
| `code_repair_patterns` | 50 | synthetic Java corpus |
| `llm_configs` | 1 | active row: `provider=local`, `local_model=qwen3:8b`, `updated_by=env-audit` |
| `telemetry_logs` | 16 | test/audit sessions (`smoke-1`, `audit-e2e-001`, …) |
| `tutoring_sessions` | 0 | **orphan telemetry** — see §3 |
| `session_messages` | 0 | |
| `hint_feedback` | 1 | |

### 1.3 Inference Setup (Ollama)

- Runtime: `ollama 0.34.4`, served at `http://localhost:11434`.
- Models available: `nomic-embed-text:latest` (embedding), `qwen3:8b`, `qwen3.5:9b`,
  `deepseek-r1:14b`.
- Router active config (from `llm_configs`): provider `local`, model **`qwen3:8b`**
  (`src/llm_router.py:304-338`).
- Embedding model: **`nomic-embed-text`, 768-dim**, via Ollama `/api/embed` with
  `search_document:` / `search_query:` task prefixes (`src/embeddings.py:193-242`,
  `.env.example:25-39`).
- **Drift:** `src/config.py:326` and `.env.example:84` still default to `qwen3:4b`, which is
  **not pulled** on this machine; only the root `.env` override (`DEFAULT_LOCAL_MODEL=qwen3:8b`)
  keeps inference working. A fresh `.env.example` copy will 502 until `qwen3:4b` is pulled
  (documented in `README.md:158-161`).

### 1.4 Runtimes

| Tool | Version |
| --- | --- |
| Python | 3.14.3 (deps import OK: flask, sqlalchemy, pgvector, psycopg2, pymupdf) |
| Node.js | v24.14.1 |
| npm | 11.12.1 |
| Docker | 29.8.0 · Compose v5.5.1 |
| Backend typecheck/import | `create_app()` boots, prints “Gateway ready. Active provider: local” |
| Frontend lint/typecheck | `oxlint` 0 warnings / 0 errors · `tsc -b` exit 0 |

### 1.5 Git Repository State

| Item | Value |
| --- | --- |
| Branch | `main` |
| HEAD | `5a94da2` |
| Remote | `origin` → `github.com/PatrickLuhanga/AI-tutoring-system.git` |
| Status | **Dirty**: 17 modified/deleted tracked files, 15 untracked paths |
| Branches | `main`, `origin/main` (no `untested` yet) |

Modified tracked: `.env.example`, `README.md`, `frontend/src/App.tsx`,
`frontend/src/api/client.ts`, `frontend/src/api/mockData.ts`, `frontend/src/types.ts`,
deletions of `frontend/src/views/{AdminDashboard,StudentChat}.tsx`, and 10 backend files.
Untracked: `src/analytics.py`, `src/history.py`, `src/profiles.py`, `src/sessions.py`,
`src/api/{analytics,history,profile}_routes.py`, `src/agents/few_shot_registry.{py,json}`,
`scripts/generate_few_shots.py`, and the new `frontend/src/{pages,auth,components}` modules.

> **Untracked/ignored architecture assets:** `.gitignore:26` (`corpora/`) silently ignores
> `src/corpora/java_error_corpus.py`, and `.gitignore:25` ignores `academic content/`. Confirmed
> via `git check-ignore -v src/corpora/java_error_corpus.py`. **A fresh clone cannot run
> `python -m src.ingest_code_patterns`** (ImportError) and has no course material.

---

## 2. Present & Functional

| Component | Status | Evidence in Codebase (exact file:line) |
| --- | --- | --- |
| PostgreSQL schema & pgvector tables | **MATCHES/EXCEEDS SPEC** | 11 ORM tables with CHECK constraints, unique keys, FK cascades (`src/models.py:85-543`). Live: 11 tables, 43 indexes. |
| `content_tsv` generated columns + GIN migration | **MATCHES SPEC** | `CurriculumChunk.content_tsv` (`src/models.py:406-410`), `CodeRepairPattern.content_tsv` (`src/models.py:448-456`); pre-existing-DB backfill (`src/setup_database.py:137-161`); GIN indexes (`src/setup_database.py:164-171`). Live indexes `idx_curriculum_chunks_tsv`, `idx_code_patterns_tsv`. |
| pg_trgm substring index (camelCase sweep) | **WORKING** | `idx_curriculum_chunks_text_trgm` created at `src/setup_database.py:168`; queried at `src/retriever.py:470-474`. Live: extension `pg_trgm 1.6`. |
| Hybrid RAG search (tsvector + cosine + RRF) | **EXCEEDS SPEC** | `src/retriever.py:1-21` design; IDF-weighted term coverage `src/retriever.py:375-433`; keyword branch `src/retriever.py:442-515`; gated semantic branch `src/retriever.py:527-574`; RRF fusion `src/retriever.py:159-186`. |
| Module metadata filter before search | **MATCHES SPEC** | `CurriculumChunk.module_id == module_id` (`src/retriever.py:538`); patterns include untagged “general” rows (`src/retriever.py:292-295`). |
| Hybrid config + tunables | **WORKING** | `RETRIEVAL_*` parsed to settings (`src/config.py:340-350`, `.env.example:116-136`). |
| Markdown-preserving loaders | **EXCEEDS SPEC** | PDF font-metric headings (`src/loaders.py:331-362`), DOCX Heading styles/tables (`src/loaders.py:417-464`), PPTX slide titles (`src/loaders.py:470-503`), HTML h1-h6/tables (`src/loaders.py:509-550`), CSV tables (`src/loaders.py:556-562`). |
| Heading-aware chunking + breadcrumbs | **MATCHES SPEC** | `MarkdownHeaderTextSplitter` + recursive fallback (`src/ingest_curriculum.py:83-110`); `module › topic › heading path` breadcrumb (`src/ingest_curriculum.py:117-120`, `:151-171`); heading columns (`src/models.py:398-400`). |
| Corpus ingestion (idempotent upsert) | **WORKING** | Per-file replace (`src/ingest_curriculum.py:226-232`); corpus upsert (`src/ingest_code_patterns.py:93-114`). Live: 9,151 + 50 rows. |
| Flask API Gateway & routing | **WORKING** | App factory + collaborator wiring (`src/app.py:172-215`); 19 `/api/*` routes registered (verified at runtime); clean-JSON error handlers (`src/app.py:74-111`); `/api/health` + `?deep=1` (`src/app.py:114-149`). |
| Agent orchestration (Intent→Scaffold→RAG→Tutor→Guardrail) | **WORKING** | Pipeline `src/agents/workflow.py:101-170`; two routing tracks (`src/prompts.py:34-47`); graceful Data/Inference degradation (`src/agents/workflow.py:180-195`, `:197-211`). |
| Intent Agent (LLM + heuristic fallback + few-shots) | **WORKING** | LLM classifier `src/agents/intent_agent.py:181-215`; deterministic fallback `src/agents/intent_agent.py:218-242`; registry loader `src/agents/few_shot_registry.py:56-160`. Live registry: **80 examples / 4 modules**. |
| Scaffolding Engine (Socratic stages) | **WORKING** | 5-stage progression + direct/ bypass handling (`src/agents/scaffolding.py:26-60`); prompt assembly `src/agents/scaffolding.py:62-85`; stage copy `src/prompts.py:138-173`. |
| Guardrail Agent (content audit) | **IMPLEMENTED** | Code-leak/direct-answer/out-of-scope/length checks (`src/agents/guardrail.py:87-146`); block/truncate + Socratic fallback (`src/agents/guardrail.py:121-146`). |
| Dynamic LLM Router (local↔cloud) | **EXCEEDS SPEC** | Reads active DB row every generation (`src/llm_router.py:304-317`); Ollama/OpenAI/Azure/Anthropic backends (`src/llm_router.py:390-523`); env fallback (`src/llm_router.py:281-299`). |
| Encrypted cloud API keys | **WORKING** | Fernet at rest (`src/secrets_store.py:75-97`); key never returned, masked last-4 (`src/llm_router.py:216-246`). |
| Inference circuit breaker + timeouts | **WORKING** | Thread-safe breaker (`src/inference/ollama_client.py:81-144`); timeout/failure handling (`src/inference/ollama_client.py:186-241`); health probe + model list (`src/llm_router.py:349-388`). |
| Auth / dual-role identity & module scope | **WORKING (dev)** | Header/body identity (`src/auth.py:93-145`); capability roles (`src/auth.py:49-69`); admin key with constant-time compare (`src/auth.py:188-209`); module-access gate (`src/auth.py:212-261`). |
| Dynamic provisioning / onboarding / tutor grants | **WORKING** | `login_or_create` (`src/profiles.py:168-191`); profile + enrollment sync (`src/profiles.py:200-236`); grant/revoke (`src/profiles.py:239-284`); routes `src/api/profile_routes.py:38-124`. |
| Session + login telemetry | **WORKING** | `record_session_open` / `record_session_turn` (`src/sessions.py:72-168`); endpoint `src/api/analytics_routes.py:29-52`. |
| Persistent chat history | **WORKING** | Append/list/load/delete (`src/history.py:90-230`); routes `src/api/history_routes.py:29-73`; audit JSONB restored (`src/api/chat_routes.py:111-130`). |
| Tutor-scoped & admin analytics | **WORKING** | Scope resolution (`src/analytics.py:404-425`); tutor aggregates (`src/analytics.py:51-200`); admin aggregates + overview (`src/analytics.py:203-401`); routes `src/api/analytics_routes.py:55-76`. |
| Evidence/feedback pipeline | **WORKING** | Upsert with rating validation and reason tags (`src/api/chat_routes.py:134-182`); model CHECK (`src/models.py:365-370`). |
| Client Tier — routing & RBAC clamps | **WORKING** | Lazy per-role chunks and route clamp (`frontend/src/App.tsx:24-55`); capability nav (`frontend/src/navigation.tsx:53-77`). |
| Client Tier — Student Chat | **WORKING** | History sidebar, module dropdown, audit panel, thumb feedback, telemetry hooks (`frontend/src/pages/student/StudentChat.tsx:78-246`; `frontend/src/components/FeedbackControls.tsx:31-40`; `frontend/src/components/AuditPanel.tsx`). |
| Client Tier — Tutor Dashboard | **WORKING** | Scoped analytics + struggle chart + repeat-help table (`frontend/src/pages/tutor/TutorDashboard.tsx:40-254`). |
| Client Tier — Admin Dashboard | **WORKING** | LLM router control, Ollama dropdown, guardrail flags, user/role management (`frontend/src/pages/admin/AdminDashboard.tsx:49-439`; `frontend/src/pages/admin/UserRolesPanel.tsx`; `frontend/src/pages/admin/GuardrailFlagsPanel.tsx`). |
| Login / Onboarding views | **WORKING (dev)** | Unified institutional login (`frontend/src/pages/auth/LoginPage.tsx`); first-login onboarding (`frontend/src/pages/auth/OnboardingPage.tsx`). |
| Build/lint health | **WORKING** | `oxlint` 0 errors; `tsc -b` exit 0; backend factory imports cleanly. |

---

## 3. Present but Needs Work (Refinements Required)

### 3.1 Frontend ↔ Backend contract drift (real bugs visible only when `VITE_USE_MOCK=false`)
- **Scaffolding field name mismatch:** backend emits `hint_sequence_depth`
  (`src/agents/workflow.py:162`) but the client type and audit panel read `hint_depth`
  (`frontend/src/types.ts:45`, `frontend/src/components/AuditPanel.tsx:61`). Against the live
  gateway the audit panel renders “hint depth undefined”; the mock backend hides this by
  emitting `hint_depth` (`frontend/src/api/mockBackend.ts:192`).
- **Retrieval shape mismatch:** backend emits only `{chunks, patterns}`
  (`src/agents/workflow.py:164-167`), while the client expects `query` + `module_id`
  (`frontend/src/types.ts:73-78`) and `AuditPanel.tsx:89` renders `retrieval.module_id`
  (undefined live).
- **Identity shape mismatch:** backend `Identity.to_dict()` omits `modules`
  (`src/auth.py:60-69`) but the client `Identity` type declares it
  (`frontend/src/types.ts:148-155`).
- **Guardrail action vocabulary:** backend can emit `action="flagged"` for advisory-only flags
  (`src/agents/guardrail.py:138`), which is not in the client union
  (`frontend/src/types.ts:52`).
- **Guardrail flag label map drift:** `GuardrailFlagsPanel` labels `solution_leak`
  (`frontend/src/pages/admin/GuardrailFlagsPanel.tsx:5-10`), but the backend emits
  `code_leak` (`src/agents/guardrail.py:110`) — live flags render as the raw key.

### 3.2 Heuristics, regex and static constants still load-bearing
- Intent classification leans on hand-written regexes (`src/agents/intent_agent.py:63-125`),
  still mis-routing edge cases (e.g. “difference between …” ordering vs debugging/start signals).
- Guardrail detection is regex + lexical overlap, not a semantic audit:
  `_CODE_LINE_RE` / `_DIRECT_ANSWER_RE` (`src/agents/guardrail.py:24-40`),
  `_overlap_ratio` (`src/agents/guardrail.py:167-173`); thresholds unevaluated.
- Retriever stopword list and token regex are hard-coded (`src/retriever.py:42-62`).
- Struggle/repeat-help heuristics are a hardcoded SQL predicate
  (`src/analytics.py:39-43`); “active student” window is a static 60 minutes
  (`src/analytics.py:51`, `:301`).
- Module registry is a static Python dict (`src/config.py:68-97`), duplicated in the frontend
  mock (`frontend/src/api/mockData.ts`).
- Socratic stage advances purely by turn count, not by answer quality
  (`src/agents/scaffolding.py:54-60`); every bypass is pinned to the same hint stage.

### 3.3 Error-handling, timeout and telemetry gaps
- **Failed inference turns are not logged:** `_log_telemetry` runs only after a successful
  draft (`src/agents/workflow.py:146-155`); an `LLMError` at `:117-136` propagates to the route
  and returns 502 (`src/api/chat_routes.py:98-104`) with no telemetry row. “Abandoned /
  failed-turn” analytics cannot be computed.
- **No raw prompt/reply text in telemetry:** `TelemetryLog` stores ids/flags/depth only
  (`src/models.py:295-341`); model input/output is not persisted.
- **No DB statement timeouts:** the SQLAlchemy engine sets pooling but no `statement_timeout`
  (`src/db.py:22-28`), so a slow analytics query can pin a worker.
- **Keyword branch materialises all tsvector matches** with no `LIMIT` before scoring
  (`src/retriever.py:466-468`), a latency/DoS risk on large modules.
- **Orphan telemetry in live DB:** 16 `telemetry_logs` rows vs 0 `tutoring_sessions` /
  0 `session_messages`; chat persistence is best-effort and silently swallowed
  (`src/api/chat_routes.py:86-87`, `:129-130`), so referential integrity drifts.
- **`request.json` body fallback for role** means dev-role escalation is trivial
  (`src/auth.py:98-100`); expected for dev but must not survive to production.
- **Admin key default is insecure** (`change-me-admin-key`) and only logs a warning
  (`src/auth.py:200-201`, `.env.example:72`).

### 3.4 Configuration / environment mismatches
- Root `.env` omits `ALLOWED_EMAIL_DOMAIN`, `ADMIN_EMAILS`, `INTENT_USE_FEW_SHOTS`, every
  `RETRIEVAL_*` hybrid knob, the Ollama circuit-breaker vars and `OLLAMA_HEALTH_TIMEOUT`,
  even though `.env.example:74-136` documents them. Behaviour silently depends on code
  defaults → config drift between machines.
- Default local model mismatch: `qwen3:4b` in `src/config.py:326` / `.env.example:84` vs
  `qwen3:8b` actually pulled and configured in the DB; `qwen3:4b` is absent.
- `frontend/.env.example:9` pins `VITE_ADMIN_KEY=change-me-admin-key`, which must stay in sync
  with the backend `ADMIN_API_KEY`; there is no build-time assertion, only README guidance.
- CORS defaults to `*` (`src/config.py:323`) — permissive for any deployment.

### 3.5 Recent implementations needing tuning / edge cases
- **Hybrid search:** purely keyword hits are admitted for curriculum but gated to semantic for
  patterns (`src/retriever.py:320-325`); the curriculum path has no such guard, and
  `_keyword_score` counts any substring (`src/retriever.py:420-433`), which can over-match.
- **Intent classification:** `json_mode=True` with `max_tokens=120` depends on model JSON
  compliance; parse fallback discards malformed output silently
  (`src/agents/intent_agent.py:199-204`).
- **Few-shot registry:** only 12 shots are injected and selection is round-robin by label
  (`src/agents/intent_agent.py:36`, `:160-179`); generated by `deepseek-r1:14b`
  (`scripts/generate_few_shots.py:70`), so its quality is model-dependent and unvalidated.
- **Session management:** `record_session_open` accepts any `module_id` (validated to NULL if
  unknown, `src/sessions.py:36-48`) and history ownership falls back to email
  (`src/history.py:49-55`); no session expiry / cleanup job.
- **Tutor analytics scope:** an unassigned tutor in dev falls back to requested modules
  (`src/analytics.py:421-425`), which can leak cross-module aggregates before the directory is
  seeded.
- **Auth strict mode:** `_verify_dut4life_token` is a deliberate 501 stub
  (`src/auth.py:103-114`); the login UI’s Microsoft button is disabled (`frontend/src/pages/auth/LoginPage.tsx:85-93`).
- **Prompt constants are locked** in Python (`src/prompts.py`); there is no admin/DB override,
  so §17 “admins adjust prompts” is not met.

### 3.6 Repository hygiene
- `src/corpora/java_error_corpus.py` is **ignored and untracked** (`git check-ignore` →
  `.gitignore:26`), as is `academic content/` (`.gitignore:25`). Fresh clones are broken for
  corpus ingestion and empty for retrieval.
- README is partially stale: §2.6 says “9 tables” (live is 11), `src/embeddings.py` is
  described as an “all-MiniLM-L6-v2 wrapper” (`README.md:237`) while the shipped default is
  nomic-embed-text.
- No `GETTING_STARTED.md` present despite the previous audit referencing it.

---

## 4. Missing Entirely

- `[MISSING]` **Java error corpus not in version control** — `.gitignore:26` `corpora/`
  ignores `src/corpora/java_error_corpus.py`; a clone cannot run `python -m
  src.ingest_code_patterns` and has no `code_repair_patterns` data.
- `[MISSING]` **Course material not in version control** — `.gitignore:25` ignores
  `academic content/`; a clone cannot run `python -m src.ingest_curriculum` meaningfully.
- `[MISSING]` **DUT4life / Microsoft IDP integration** — `_verify_dut4life_token` raises 501
  (`src/auth.py:103-114`); no token validation, no `msal.js` redirect, Microsoft button
  disabled (`frontend/src/pages/auth/LoginPage.tsx:85-93`). `AUTH_MODE=strict` is unusable.
- `[MISSING]` **Formal evaluation harness** — no test dataset, no precision/recall/F1 suite,
  no accuracy harness (acknowledged in `README.md:544-545`). Confirmed: zero pytest/tests/CI
  files outside `academic content`.
- `[MISSING]` **Automated tests & CI/CD** — no `tests/`, `conftest.py`, `pytest.ini`, `.github/`
  workflows, or linters for Python.
- `[MISSING]` **Schema migration framework** — no Alembic; schema evolution is ad-hoc
  `ALTER TABLE ... IF NOT EXISTS` calls inside `src/setup_database.py:137-249`.
- `[MISSING]` **Prompt-adjustment cycle / admin-tunable prompts** — prompts are Python
  constants (`src/prompts.py`); no DB/API override despite §17.
- `[MISSING]` **Code-repair pattern maintenance workflow** — no CRUD/maintenance path for the
  50-pattern corpus (only bulk upsert, `src/ingest_code_patterns.py`).
- `[MISSING]` **Enrollment / user-lifecycle admin endpoints** — only `grant-tutor` /
  `revoke-tutor` exist (`src/api/profile_routes.py:86-124`); no endpoints to assign
  enrollments, activate/deactivate users, or seed the directory; `admin_overview` is read-only.
- `[MISSING]` **Observability / ops endpoints** — no metrics (`/metrics`), no structured request
  logging middleware, no rate limiting, no `/api/admin/audit-log`.
- `[MISSING]` **Documentation assets** — no `GETTING_STARTED.md`; no `CONTEXT.md`/ADRs; the
  README layout (`README.md:237`) and table count are out of date.

---

## 5. Prioritized Improvements & Next Steps

1. **Fix repository completeness first (P0).** Narrow `.gitignore:26` so it no longer swallows
   `src/corpora/` and force-add `src/corpora/java_error_corpus.py`; decide and document how
   `academic content/` is distributed to clones. Without this the repo is not reproducible.
2. **Repair the live frontend↔backend audit contract (P0).** Align
   `hint_sequence_depth`/`hint_depth`, `retrieval.query`/`module_id`, `identity.modules`, the
   `flagged` guardrail action, and the `code_leak` label map. Add a single shared schema test
   (or OpenAPI) so drift cannot recur.
3. **Wire DUT4life identity (P1).** Implement `_verify_dut4life_token` against the DUT Microsoft
   tenant, enable the MSAL redirect, flip `AUTH_MODE=strict`, and stop trusting dev headers.
4. **Build the evaluation harness (P1).** Curate a ground-truth Q/A + intent dataset; add
   precision/recall/F1, retrieval nDCG, and guardrail false-positive metrics as a runnable
   suite wired into CI.
5. **Introduce Alembic migrations (P1).** Replace the ad-hoc `setup_database.py` ALTERs with
   versioned migrations so schema upgrades are auditable and reversible.
6. **Reconcile configuration (P1).** Regenerate `.env` from `.env.example`, pin
   `DEFAULT_LOCAL_MODEL=qwen3:8b` (or pull `qwen3:4b`), and add startup validation that the
   configured model exists in Ollama and that `VITE_ADMIN_KEY == ADMIN_API_KEY`.
7. **Close telemetry gaps (P1).** Log failed inference turns, persist prompt/reply text
   (or hashes) for analytics, and add DB statement timeouts; reconcile the orphan telemetry
   rows to real sessions.
8. **Add tests and CI (P1).** Pytest for agents/retriever/routes + the existing frontend lint/tsc,
   run on every push; enforce the `tests/` directory the previous audit flagged.
9. **Harden retrieval and guardrail (P2).** `LIMIT` the tsvector keyword candidates, add a
   semantic out-of-scope audit pass, and back the guardrail thresholds with the evaluation data.
10. **Make prompts admin-tunable and tune the few-shot/registry cycle (P2).** Move prompt
    templates behind a versioned store + admin API; validate the generated registry.
11. **Flesh out RBAC and ops (P2).** Enrollment/user-lifecycle admin endpoints, session
    expiry, `/metrics`, request logging and rate limiting.
12. **Refresh documentation (P3).** Add `GETTING_STARTED.md`, correct the README table count and
    embedding description, and record the ingestion/corpus distribution contract.

---

*Generated by automated live inspection of the repository at commit `5a94da2` on the
`untested` branch.*
