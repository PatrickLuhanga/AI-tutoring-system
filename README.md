# Hybrid AI Tutoring System

A hybrid AI tutoring platform for four DUT modules (IPRT301, PBDV301, RESK301,
SPRI301) that combines a local LLM, RAG, a multi-agent orchestration layer and
Socratic scaffolding so students get hints, not answers.

```
CLIENT TIER        Student Chat  +  Admin/Tutor Dashboard (React + Vite)
ORCHESTRATION TIER API Gateway (Flask) -> Intent -> Scaffolding -> RAG -> Tutor -> Guardrail
DATA TIER          PostgreSQL 16 + pgvector  (relational store + vector store)
INFERENCE TIER     Local Ollama (Qwen3)  or  a cloud API  -  via Dynamic LLM Router
```

**Repo:** https://github.com/PatrickLuhanga/AI-tutoring-system.git (branch: `main`)

---

## 1. What is implemented

| Architecture requirement | Implementation |
| --- | --- |
| Relational store: students, modules, enrollments (§4) | `students`, `modules`, `enrollments` tables (`src/models.py`) |
| Tutor ↔ module RBAC enforced at DB level (§4.2) | `tutor_assignments` table |
| Telemetry & feedback: logs, ratings, failure flags, hint depth (§13, §14) | `telemetry_logs`, `hint_feedback` tables |
| Vector store: curriculum chunks (§8) | `curriculum_chunks` with `module_id` metadata filter |
| Vector store: code-repair patterns (§14) | `code_repair_patterns` (50 synthetic Java patterns) |
| **Heading-aware chunking** (§3.2 data pipeline) | `MarkdownHeaderTextSplitter` on Markdown-preserving extraction + contextual breadcrumbs (`module › topic › heading path`) |
| Local, lightweight embedding model (§10.3) | `nomic-embed-text` (768-dim) via local Ollama with `search_document:` / `search_query:` task prefixes |
| API Gateway: authenticate, route, enforce module scope (§3, §5.1) | `app.py` + `src/app.py` + `src/auth.py` |
| Intent Agent: classify conceptual / debugging / problem-solving / bypass / factual (§6) | `src/agents/intent_agent.py` |
| Scaffolding Engine: Socratic progression (§7) | `src/agents/scaffolding.py` + `src/prompts.py` |
| RAG Orchestrator: embed query, module-filtered top-3 search (§8) | `src/retriever.py` |
| Guardrail Agent: block complete-solution leaks, log failure flags (§11) | `src/agents/guardrail.py` |
| Tutor Agent: draft the Socratic response (§7-9) | `src/agents/tutor_agent.py` |
| Dynamic LLM Router: read active config from DB, route local/cloud (§10.2) | `src/llm_router.py` |
| Inference fault isolation: timeout + circuit breaker around Ollama | `src/inference/ollama_client.py` |
| Cloud API key stored encrypted in PostgreSQL, not `.env` | `llm_configs` table + `src/secrets_store.py` |
| Feedback routed straight to telemetry (§3, §12) | `POST /api/feedback` |
| Student Chat UI (module dropdown, session_id, audit panel, thumbs feedback) | `frontend/src/views/StudentChat.tsx` |
| Admin/Tutor Dashboard (LLM router control + telemetry charts) | `frontend/src/views/AdminDashboard.tsx` |

The working embedding model is the architecture document's `nomic-embed-text`
(768-dim), served by the same local Ollama runtime used for inference. It is an
instruct-style model: documents are embedded with a `search_document:` prefix
and queries with `search_query:`. The embedding model, its dimension and its
backend are configuration, not code — see
[§5 Swapping the embedding model](#5-swapping-the-embedding-model). A local
`sentence-transformers` backend (e.g. `all-MiniLM-L6-v2`, 384-dim) and an
offline `hash` stub remain available for machines without Ollama.

---

## 2. Getting started (fresh clone)

### 2.1 Prerequisites

| Tool | Version | Check with |
| --- | --- | --- |
| Git | any recent | `git --version` |
| Python | **3.12 recommended** (3.10–3.12 supported) | `python --version` |
| Node.js | 18+ | `node --version` |
| Docker Desktop | with Compose v2 | `docker --version` |
| Ollama | latest | `ollama --version` |

> The ML stack (`torch` / `sentence-transformers`) has the most reliable wheels
> on Python 3.10–3.12. If a newer Python fails to find wheels, install 3.12 and
> recreate the venv.

### 2.2 Clone

```powershell
git clone https://github.com/PatrickLuhanga/AI-tutoring-system.git
cd AI-tutoring-system
git status   # "On branch main", clean tree
```

### 2.3 Environment variables

The repo ships **no secrets** — `.env` files are git-ignored. Duplicate the
examples:

```powershell
Copy-Item .env.example .env            # backend (project root)
Copy-Item frontend\.env.example frontend\.env   # frontend
```

Backend keys — defaults work locally; **request the marked values from the
project lead**:

| Key | Default | Request from lead? |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | `tutor_password` | Only if the lead changed the DB password |
| `ADMIN_API_KEY` | `change-me-admin-key` | **Yes — the shared admin key** (protects `/api/admin/*`). Change it locally, but keep it in sync with the frontend |
| `LLM_CONFIG_SECRET_KEY` | *(empty)* | **Yes — the encryption master key**; needed only for cloud-provider API keys |
| `DEFAULT_LOCAL_MODEL` | `qwen3:4b` | Set to `qwen3:8b` if you pulled that model (see §2.6) |

Frontend keys:

| Key | Value | Notes |
| --- | --- | --- |
| `VITE_USE_MOCK` | `true` = UI with zero backend; `false` = talk to Flask | Set `false` once the API is up (§2.7) |
| `VITE_ADMIN_KEY` | **must equal** backend `ADMIN_API_KEY` | Otherwise the dashboard gets `401` |

### 2.4 Backend setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
```

> If PowerShell blocks `Activate.ps1`, run
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first.
>
> Verify: `python -c "import flask, sqlalchemy, pgvector, sentence_transformers; print('deps OK')"`

### 2.5 Frontend setup

```powershell
cd frontend
npm install
npm run build     # must finish with no TypeScript errors
cd ..
```

### 2.6 Infrastructure

**PostgreSQL 16 + pgvector (Docker, port 5433):**

```powershell
docker compose up -d
docker compose ps                    # ai_tutoring_pg ... (healthy)
docker exec ai_tutoring_pg pg_isready -U tutor_admin -d ai_tutoring
```

Port 5433 is deliberate (avoids clashing with any PostgreSQL on 5432). If it is
taken, change `POSTGRES_PORT` in `.env` and recreate the container.

**Ollama (port 11434):**

```powershell
ollama list                          # expect qwen3:8b
ollama pull qwen3:8b                 # if missing
curl http://localhost:11434/api/tags # JSON with "name":"qwen3:8b"
```

> `.env.example` defaults to `qwen3:4b` (the model named in the architecture
> document), but the lead developer's machine runs **`qwen3:8b`**. Either pull
> `qwen3:4b` or set `DEFAULT_LOCAL_MODEL=qwen3:8b` in `.env`. The admin
> dashboard can switch models at runtime.

**Initialise the database and ingest course material** (venv active):

```powershell
python -m src.setup_database        # extensions, 9 tables, HNSW indexes, seeds 4 modules + default LLM config
python -m src.ingest_curriculum     # walks "academic content/", chunks on headings + breadcrumbs, embeds, stores
python -m src.ingest_code_patterns  # inserts the 50 Java code-repair patterns
python -m src.verify_data           # row counts + 2 module-filtered similarity searches
```

The first `ingest_curriculum` run needs `nomic-embed-text` pulled in Ollama
(see §2.6) — every chunk is embedded through Ollama's `/api/embed`. Full
ingestion takes a while — for a quick test:
`python -m src.ingest_curriculum --module IPRT --max-files 20`.

### 2.7 Boot sequence (two terminals)

**Terminal 1 — Flask API Gateway:**

```powershell
python app.py                       # "Gateway ready", listens on http://127.0.0.1:5000
```

**Terminal 2 — Vite dev server:**

```powershell
cd frontend
npm run dev                         # open http://localhost:5173
```

Vite proxies `/api` → `http://localhost:5000`. For the UI to talk to Flask (not
mocks), `frontend/.env` must have `VITE_USE_MOCK=false` — restart Vite after
changing it.

**Health + smoke test:**

```powershell
curl http://127.0.0.1:5000/api/health
curl "http://127.0.0.1:5000/api/health?deep=1"    # also pings Ollama + circuit breaker

curl -X POST http://127.0.0.1:5000/api/chat `
  -H "Content-Type: application/json" `
  -H "X-User-Email: 22000000@dut4life.ac.za" `
  -H "X-User-Role: student" `
  -d '{\"module_id\":\"IPRT301\",\"message\":\"Why does my Java code throw a NullPointerException?\",\"history\":[]}'
```

Pass the returned `session_id` back on the next call to advance the Socratic
progression (`hint → student_attempt → feedback → further_guidance → explanation`).

---

## 3. Project layout

```
Project302/
├── app.py                       # thin gateway entry point (no logic)
├── .env / .env.example          # database, gateway, LLM-router and guardrail settings
├── docker-compose.yml           # PostgreSQL 16 + pgvector
├── requirements.txt
├── frontend/                    # Client Tier — React client (Vite)
├── academic content/            # source material (IPRT, PBDV, Resk, SPRI) — read-only
└── src/
    ├── config.py                # settings + module registry
    ├── db.py                    # engine, session scope, database creation
    ├── models.py                # all tables (relational + vector + llm_configs)
    ├── loaders.py               # content discovery + Markdown-preserving extraction
    ├── embeddings.py            # all-MiniLM-L6-v2 wrapper
    ├── setup_database.py        # (1) schema initialisation + LLM config seed
    ├── ingest_curriculum.py     # (2) heading-aware chunking + ingestion pipeline
    ├── ingest_code_patterns.py  # (3) code corpus ingestion
    ├── verify_data.py           # post-ingestion sanity check
    ├── corpora/
    │   └── java_error_corpus.py # the 50 synthetic Java errors
    ├── app.py                   # Flask application factory
    ├── auth.py                  # identity, roles and module-scope checks
    ├── api/
    │   ├── chat_routes.py       # POST /api/chat, POST /api/feedback
    │   └── admin_routes.py      # GET/POST /api/admin/llm-config, GET /api/admin/ollama-models
    ├── agents/                  # one file per agent (separation of concerns)
    │   ├── intent_agent.py      # §6  Intent Agent
    │   ├── scaffolding.py       # §7  Scaffolding Engine
    │   ├── tutor_agent.py       # §7-9 Tutor Agent
    │   ├── guardrail.py         # §11 Guardrail Agent (content audit)
    │   └── workflow.py          # coordinates the agents + telemetry
    ├── inference/
    │   └── ollama_client.py     # timeout + circuit breaker around Ollama
    ├── retriever.py             # module-scoped pgvector retrieval
    ├── llm_router.py            # Dynamic LLM Router + llm_configs service
    ├── prompts.py               # Socratic prompt templates
    └── secrets_store.py         # Fernet encryption for stored API keys
```

---

## 4. Ingestion pipeline detail

The four steps in §2.6 are the full pipeline. Useful flags for
`ingest_curriculum`:

| Flag | Purpose |
| --- | --- |
| `--dry-run` | Extract + chunk only; no embeddings, no database writes |
| `--module IPRT` | Ingest a single module folder |
| `--max-files 20` | Stop after N files (useful for a quick test) |
| `--reset` | Delete all existing chunks first |
| `--content-dir PATH` | Override `ACADEMIC_CONTENT_DIR` |

**Chunking strategy (Phase 2 — heading-aware):** every file is first extracted
to structure-preserving Markdown (`src/loaders.py`: PDF heading levels inferred
from font metrics, DOCX `Heading N` styles, PPTX slide titles, HTML `<h1>`-`<h6>`),
then split with LangChain's `MarkdownHeaderTextSplitter` so a chunk never spans
two headings. Each chunk's embedded text is prefixed with a contextual
breadcrumb — `module › topic › heading path` — and the same path is stored in
`doc_metadata.heading_path`. A section larger than `CHUNK_SIZE` is broken up by
the recursive fallback splitter, preserving the breadcrumb.

```bash
python -m src.ingest_code_patterns --dry-run   # validate + summarise by category
python -m src.ingest_code_patterns --reset     # wipe first
```

---

## 5. Swapping the embedding model / backend

The dimension of the `vector` columns comes from `EMBEDDING_DIM`, and the
backend from `EMBEDDING_BACKEND`:

| `EMBEDDING_BACKEND` | Model | Notes |
| --- | --- | --- |
| `ollama` *(default)* | `nomic-embed-text` (768-dim) | Instruct embeddings; `EMBEDDING_DOC_PREFIX` / `EMBEDDING_QUERY_PREFIX` control the task prefixes (`search_document:` / `search_query:`) |
| `sentence-transformers` | e.g. `all-MiniLM-L6-v2` (384-dim) | Runs in-process; no Ollama needed |
| `hash` | — | Deterministic offline stub for smoke tests only; no semantic meaning |

To switch backend/model:

1. Set `EMBEDDING_MODEL_NAME`, `EMBEDDING_DIM` and `EMBEDDING_BACKEND` in `.env`.
2. For Ollama: `ollama pull <model>` and check the tag matches `EMBEDDING_MODEL_NAME`.
3. Recreate the schema: `python -m src.setup_database --drop` (or drop just the
   two vector tables — see the Phase 3 migration walkthrough below).
4. Re-run both ingestion scripts.

The setup and ingestion scripts fail loudly if `EMBEDDING_DIM` disagrees with
the model's real output dimension.

> **Offline smoke tests only:** set `EMBEDDING_BACKEND=hash` to replace the model
> with a deterministic, dependency-free stub. It produces correctly-shaped
> vectors so the pipeline can be exercised without the ML stack, but the vectors
> carry **no semantic meaning** — never use it for real retrieval.

### 5.1 Migration walkthrough (384-dim → nomic-embed-text 768-dim)

Changing the embedding dimension invalidates every stored vector, so the two
vector tables must be dropped and re-ingested. Telemetry, feedback and
`llm_configs` are untouched.

```powershell
# 1. Get the model
ollama pull nomic-embed-text
ollama list                                  # expect nomic-embed-text

# 2. Point .env at it (or copy the values from .env.example)
#    EMBEDDING_MODEL_NAME=nomic-embed-text
#    EMBEDDING_DIM=768
#    EMBEDDING_BACKEND=ollama

# 3. Drop the old 384-dim vector tables only (destructive for vector data)
docker exec -i ai_tutoring_pg psql -U tutor_admin -d ai_tutoring -c "DROP TABLE IF EXISTS curriculum_chunks CASCADE; DROP TABLE IF EXISTS code_repair_patterns CASCADE;"

# 4. Recreate the tables (non-destructive for everything else)
python -m src.setup_database

# 5. Re-ingest both vector stores
python -m src.ingest_curriculum
python -m src.ingest_code_patterns

# 6. Verify
python -m src.verify_data --module IPRT301 --query "NullPointerException on a null String"
```

If you prefer a full nuke of **all** data-tier tables instead of the targeted
drop in step 3, use `python -m src.setup_database --drop`.

---

## 6. Module registry

The four top-level folders under `academic content/` map to the four modules in
scope:

| Folder | `module_id` | Module | Language |
| --- | --- | --- | --- |
| `IPRT` | `IPRT301` | Internet Programming | Java |
| `PBDV` | `PBDV301` | Platform Based Development | Python |
| `RESK` | `RESK301` | Research Skills | N/A |
| `SPRI` | `SPRI301` | Social and Professional Issues | N/A |

Only files inside a module folder are ingested. Media, archives, unparsed
Office formats and binaries are skipped and reported. Edit the registry in
`src/config.py` (`MODULE_REGISTRY`) if module codes change.

---

## 7. Schema reference

**Relational**

| Table | Key columns |
| --- | --- |
| `students` | `student_id`, `dut4life_email` (unique), `role` (`student`/`tutor`/`admin`) |
| `modules` | `module_id` (PK), `module_name`, `language` |
| `enrollments` | `student_id`, `module_id`, `academic_year` (unique per triple) |
| `tutor_assignments` | `tutor_id`, `module_id` (unique per pair) — RBAC mapping |
| `telemetry_logs` | `session_id`, `module_id`, `intent`, `scaffolding_stage`, `hint_sequence_depth`, `guardrail_flagged`, `guardrail_failure_flags`, retrieved chunk/pattern ids |
| `hint_feedback` | `session_id`, `message_id`, `rating` (+1 / −1), `reason_tag` |
| `llm_configs` | `provider` (`local`/`cloud`), `local_model`, `cloud_provider`/`cloud_base_url`/`cloud_model`, `api_key_encrypted`, generation params, `is_active` |

**Vector** (cosine / HNSW)

| Table | Key columns |
| --- | --- |
| `curriculum_chunks` | `module_id`, `source_file`, `topic`, `section_title`, `breadcrumb`, `heading_path` (JSONB), `chunk_strategy`, `chunk_index`, `chunk_text`, `embedding vector(768)`, `doc_metadata` |
| `code_repair_patterns` | `error_title` (unique), `exception_thrown`, `broken_code`, `conceptual_tutor_hint`, `embedding vector(768)` |

Example module-filtered similarity query (mirrors §8.4 of the architecture
document):

```sql
SELECT section_title, chunk_text
FROM curriculum_chunks
WHERE module_id = 'IPRT301'
ORDER BY embedding <=> :query_vector
LIMIT 3;
```

---

## 8. Orchestration tier (Flask API Gateway)

### 8.1 Endpoints

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/chat` | dev identity | Run the full agent workflow for one student turn. |
| `POST` | `/api/feedback` | dev identity | Record a thumbs up/down + reason tag (straight to telemetry). |
| `GET` | `/api/modules` | none | Modules available to the client dropdown. |
| `GET` | `/api/health` | none | Liveness/readiness (`?deep=1` pings Ollama). |
| `GET` | `/api/admin/llm-config` | `X-Admin-Key` | Read the active provider/model (key never returned). |
| `POST` | `/api/admin/llm-config` | `X-Admin-Key` | Switch local ↔ cloud and/or rotate the API key. |
| `GET` | `/api/admin/ollama-models` | `X-Admin-Key` | List downloaded Ollama models for a dropdown. |

### 8.2 Example chat response (trimmed)

```json
{
  "session_id": "...",
  "message_id": "...",
  "reply": "Let's work through it. Look at the line in the stack trace — which variable could be null?",
  "intent": { "label": "debugging", "confidence": 0.75, "source": "heuristic" },
  "scaffolding": { "stage": "hint", "hint_sequence_depth": 0 },
  "guardrail": { "flagged": false, "flags": [], "action": "pass" },
  "retrieval": { "chunks": [ ... ], "patterns": [ ... ] },
  "llm": { "provider": "local", "model": "qwen3:8b", "backend": "ollama", "latency_ms": 2140 },
  "telemetry_log_id": 1
}
```

### 8.3 Fault tolerance

* **Ollama down / hung** — every call goes through `src/inference/ollama_client.py`
  (timeout + circuit breaker). `POST /api/chat` returns `502` with a clean JSON
  body; the API process stays up.
* **PostgreSQL down** — identity, module-scope, retrieval and telemetry degrade
  instead of raising. `/api/health` reports `"status": "degraded"` with `503`.
* **Any unhandled error** — a catch-all handler returns clean JSON, never an HTML
  traceback.

### 8.4 Dynamic LLM Routing (local ↔ cloud)

The router reads the active configuration from PostgreSQL before every
generation. Switch at runtime — no restart, no `.env` edit:

```bash
ADMIN="-H X-Admin-Key: change-me-admin-key -H Content-Type:application/json"
curl $ADMIN http://127.0.0.1:5000/api/admin/llm-config
curl $ADMIN http://127.0.0.1:5000/api/admin/ollama-models
curl -X POST $ADMIN http://127.0.0.1:5000/api/admin/llm-config -d '{"provider":"local","local_model":"qwen3:8b"}'
curl -X POST $ADMIN http://127.0.0.1:5000/api/admin/llm-config -d '{"provider":"cloud","cloud_provider":"openai","cloud_base_url":"https://api.openai.com/v1","cloud_model":"gpt-4o-mini","api_key":"sk-..."}'
```

Supported cloud flavours: `openai`, `openai_compatible`, `azure_openai`,
`anthropic`. Keys are Fernet-encrypted with `LLM_CONFIG_SECRET_KEY` before they
reach the database and are never returned by the API (only `....9876` masking).

### 8.5 Guardrail thresholds

The architecture fixes the guardrail *behaviour* but not the numbers (§11.1),
so these are configuration:

| Variable | Default | Meaning |
| --- | --- | --- |
| `GUARDRAIL_MAX_CODE_LINES` | `8` | More code lines than this is treated as a solution leak. |
| `GUARDRAIL_MAX_WORDS` | `400` | Responses above this are flagged `too_long`. |
| `GUARDRAIL_MIN_CONTEXT_OVERLAP` | `0.08` | Below this lexical overlap with retrieved context is flagged `out_of_scope`. |
| `GUARDRAIL_ACTION` | `block` | `block` replaces a leak with a Socratic fallback; `truncate` strips code/answers. |

### 8.6 Authentication note

`AUTH_MODE=dev` reads the caller from the `X-User-Email` / `X-User-Role`
headers (or the JSON body) and links them to the `students` table when
possible. `AUTH_MODE=strict` expects a DUT4life token; the identity-provider
call is isolated in `src/auth.py::_verify_dut4life_token` and is the single
place to wire up the real tenant (§4.4). Admin routes always require the
`X-Admin-Key` header.

---

## 9. Troubleshooting

| Symptom | Fix |
| --- | --- |
| `port is already allocated` (Docker) | Change `POSTGRES_PORT` in `.env`, then `docker compose down; docker compose up -d` |
| `ModuleNotFoundError: pgvector` | Activate the venv and run `pip install -r requirements.txt` |
| `Could not connect to PostgreSQL` | `docker compose ps` — container must report `(healthy)` |
| Chat returns 502 "inference engine unavailable" | Ollama down or model not pulled: `ollama list`, `curl http://localhost:11434/api/tags` |
| Admin dashboard returns 401 | `VITE_ADMIN_KEY` must equal backend `ADMIN_API_KEY` |
| UI shows "Mock data" badge | `VITE_USE_MOCK` is still `true` — set `false` and restart Vite |
| `sentence-transformers is not installed` | Install requirements, or use `EMBEDDING_BACKEND=hash` for a plumbing-only smoke test |
| Embedding dimension mismatch | Set `EMBEDDING_DIM` to the model's real dimension and re-run `setup_database --drop` |
| `Could not ensure the llm_configs table exists` | Run `python -m src.setup_database` (non-destructive) |
| `502 The active cloud provider has no API key` | POST an `api_key` to `/api/admin/llm-config` before switching to cloud |
| Stored key fails to decrypt | `LLM_CONFIG_SECRET_KEY` changed — re-enter the cloud API key via the admin endpoint |

---

## 10. Scope & roadmap

Implemented: all four tiers in their current form — the hybrid data tier, the
multi-agent orchestration tier (with the Dynamic LLM Router), local inference,
and the Client Tier prototype (Student Chat + Admin/Tutor dashboard, currently
driven by mock data until the backend is running).

Still outstanding:

* **Analytics endpoint** — the dashboard's telemetry charts expect
  `GET /api/admin/analytics`, which does not exist yet (mock data only).
* **Tutor dashboard** scoped to `tutor_assignments` at the query level (§16.1).
* **DUT4life identity provider** (strict auth mode) and admin endpoints for
  granting tutor privileges / managing assignments.
* **Evaluation harness** (accuracy / precision / recall / F1 against a
  ground-truth test set, §3.4 of the paper).
* Prompt-adjustment cycle (§17), real TSCC few-shot transcripts, and the
  code-repair pattern maintenance workflow (§20).
