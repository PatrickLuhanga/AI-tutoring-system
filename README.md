# Hybrid AI Tutoring System — Data Tier + Orchestration Tier

This repository implements two of the four tiers described in
`AI_Tutoring_System_Architecture_and_Flow_UPDATED.docx` and the UML diagram:

```
Tier 1 — Hybrid Data Tier
├── Relational Store (PostgreSQL)   users, modules, enrollments, RBAC, telemetry, feedback, LLM config
└── Vector Store (pgvector)         curriculum chunks + code-repair patterns

Tier 2 — Orchestration Tier
├── API Gateway (Flask)             routing, authentication/RBAC, /api/chat, /api/feedback, /api/admin/*
├── Multi-Agent RAG workflow        Intent → Scaffolding → Retriever → Tutor → Guardrail
└── Dynamic LLM Router              local Ollama (Qwen3 4B) OR a cloud API, chosen at runtime
```

The inference tier is reached only through the Dynamic LLM Router; the Client Tier dashboards remain
out of scope.

---

## 1. What is implemented

| Architecture requirement | Implementation |
| --- | --- |
| Relational store: Students, Modules, Enrollments | `students`, `modules`, `enrollments` tables (`src/models.py`) |
| Tutor ↔ module RBAC enforced at DB level (§4.2) | `tutor_assignments` table |
| Telemetry & feedback: logs, ratings, failure flags, hint depth (§13, §14) | `telemetry_logs`, `hint_feedback` tables |
| Vector store: curriculum chunks (§8) | `curriculum_chunks` with `module_id` metadata filter |
| Vector store: code-repair patterns (§14) | `code_repair_patterns` |
| Local, lightweight embedding model (§10.3) | `all-MiniLM-L6-v2` via SentenceTransformers (384-dim) |
| Module scope as a metadata filter (§4.1, §8.3) | every vector row is tagged with `module_id` |
| Synthetic Java error corpus | 50 hand-authored patterns (`src/corpora/java_error_corpus.py`) |

The architecture document names `nomic-embed-text` (768-dim) as the working embedding choice. This
data tier defaults to `all-MiniLM-L6-v2` (384-dim) because it is smaller, fully local and free, and
still satisfies the "lightweight embedding model" requirement. **The embedding model and its
dimension are configuration, not code** — see [§6 Swapping the embedding model](#6-swapping-the-embedding-model).

### Tier 2 — Orchestration Tier

| Architecture requirement | Implementation |
| --- | --- |
| API Gateway: authenticate, route, enforce module scope (§3, §5.1) | `app.py` (entry point) + `src/app.py` + `src/auth.py` |
| Intent Agent: classify conceptual / debugging / problem-solving / bypass (§6) | `src/agents/intent_agent.py` |
| Scaffolding Engine: Socratic progression, defaults to questioning (§7) | `src/agents/scaffolding.py` + `src/prompts.py` |
| RAG Orchestrator: embed query, module-filtered top-3 search (§8) | `src/retriever.py` |
| Guardrail Agent: block complete-solution leaks, log failure flags (§11) | `src/agents/guardrail.py` |
| Tutor Agent: draft the Socratic response (§7-9) | `src/agents/tutor_agent.py` |
| Dynamic LLM Router: read active config from DB, route local/cloud (§10.2) | `src/llm_router.py` |
| Inference fault isolation: timeout + circuit breaker around Ollama | `src/inference/ollama_client.py` |
| Cloud API key stored encrypted in PostgreSQL, not `.env` | `llm_configs` table + `src/secrets_store.py` |
| Feedback routed straight to telemetry (§3, §12) | `POST /api/feedback` |
| Telemetry: intent, stage, hint depth, guardrail flags, retrieved ids (§13) | `telemetry_logs` written by `TutoringWorkflow` in `src/agents/workflow.py` |

---

## 2. Project layout

```
Project302/
├── app.py                       # Tier 2 — thin gateway entry point (no logic)
├── .env / .env.example          # database, gateway, LLM-router and guardrail settings
├── docker-compose.yml           # PostgreSQL 16 + pgvector
├── requirements.txt
├── frontend/                    # Tier 1 — React client (Vite)
├── academic content/            # source material (IPRT, PBDV, Resk, SPRI) — read-only
└── src/
    ├── config.py                # settings + module registry
    ├── db.py                    # engine, session scope, database creation
    ├── models.py                # all tables (relational + vector + llm_configs)
    ├── loaders.py               # content discovery + text extraction
    ├── embeddings.py            # all-MiniLM-L6-v2 wrapper
    ├── setup_database.py        # (1) schema initialisation + LLM config seed
    ├── ingest_curriculum.py     # (2) curriculum ingestion pipeline
    ├── ingest_code_patterns.py  # (3) code corpus ingestion
    ├── verify_data.py           # post-ingestion sanity check
    ├── corpora/
    │   └── java_error_corpus.py # the 50 synthetic Java errors
    │
    ├── app.py                   # Tier 2 — Flask application factory
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
    │   └── ollama_client.py     # Tier 4 boundary: timeout + circuit breaker
    ├── retriever.py             # module-scoped pgvector retrieval
    ├── llm_router.py            # Dynamic LLM Router + llm_configs service
    ├── prompts.py               # Socratic prompt templates
    └── secrets_store.py         # Fernet encryption for stored API keys
```

---

## 3. Prerequisites

- **Python 3.10 – 3.12** (3.12 recommended). The ML stack (`torch` / `sentence-transformers`) has
  the most reliable wheels on these versions.
- **PostgreSQL 14+ with the `pgvector` extension** — either:
  - Docker Desktop (easiest; `docker-compose.yml` uses `pgvector/pgvector:pg16`), **or**
  - an existing PostgreSQL server where you can run `CREATE EXTENSION vector`.

> **Port note:** the project defaults to host port **5433** (`POSTGRES_PORT=5433`) so it does not
> clash with a PostgreSQL instance already running on the standard 5432 port. Change it in `.env`
> if 5432 is free on your machine.

---

## 4. Setup

### 4.1 Create the virtual environment

**Windows (PowerShell)**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
```

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 4.2 Configure credentials

A working `.env` is already included for local development. Review it and change the password if you
like:

```dotenv
DATABASE_URL=postgresql+psycopg2://tutor_admin:tutor_password@localhost:5433/ai_tutoring
POSTGRES_PORT=5433
EMBEDDING_MODEL_NAME=all-MiniLM-L6-v2
EMBEDDING_DIM=384
```

Either set `DATABASE_URL` **or** the individual `POSTGRES_*` values. See `.env.example` for the full
documented list.

### 4.3 Start PostgreSQL + pgvector

```bash
docker compose up -d
docker compose ps          # wait until the container reports "healthy"
```

To reset the database volume completely:

```bash
docker compose down -v
```

If you use your **own** PostgreSQL instead of Docker, create the role and database once:

```sql
CREATE ROLE tutor_admin LOGIN PASSWORD 'tutor_password';
CREATE DATABASE ai_tutoring OWNER tutor_admin;
```

and run `CREATE EXTENSION vector;` while connected to `ai_tutoring` (the setup script does this for
you if the role has permission).

---

## 5. Running the pipeline

Run the four steps in order from the project root. The scripts are executed as modules
(`python -m src.<name>`).

### Step 1 — Initialise the schema

```bash
python -m src.setup_database
```

Creates the `vector` / `pg_trgm` extensions, all 8 tables, the HNSW similarity indexes, the JSONB
indexes and seeds the four modules. Expected tail:

```
Extensions ensured: vector, pg_trgm
Tables ensured (9)
Vector index ready: idx_curriculum_chunks_embedding (hnsw)
Vector index ready: idx_code_patterns_embedding (hnsw)
Seeded 4 modules
Seeded default LLM configuration (provider=local)
```

> Recreate everything from scratch (destructive):
> `python -m src.setup_database --drop`

### Step 2 — Ingest the curriculum

```bash
python -m src.ingest_curriculum
```

This walks `academic content/`, extracts text from PDF / DOCX / PPTX / HTML / CSV / TXT / Java /
XML, chunks each document with LangChain's `RecursiveCharacterTextSplitter`, embeds the chunks with
`all-MiniLM-L6-v2` and upserts them into `curriculum_chunks` (tagged with `module_id`).

Useful flags:

| Flag | Purpose |
| --- | --- |
| `--dry-run` | Extract + chunk only; no embeddings, no database writes |
| `--module IPRT` | Ingest a single module folder |
| `--max-files 20` | Stop after N files (useful for a quick test) |
| `--reset` | Delete all existing chunks first |
| `--content-dir PATH` | Override `ACADEMIC_CONTENT_DIR` |

The first run downloads the `all-MiniLM-L6-v2` model (~90 MB) into the local cache.

### Step 3 — Ingest the synthetic Java error corpus

```bash
python -m src.ingest_code_patterns
```

Inserts the 50 patterns from `src/corpora/java_error_corpus.py`. The script validates that every
entry has `broken_code`, `exception_thrown` and `conceptual_tutor_hint` before writing anything.

```bash
python -m src.ingest_code_patterns --dry-run   # validate + summarise by category
python -m src.ingest_code_patterns --reset     # wipe first
```

### Step 4 — Verify

```bash
python -m src.verify_data
python -m src.verify_data --module IPRT301 --query "NullPointerException on a null String"
```

Prints row counts and runs two module-filtered cosine-similarity searches (curriculum + code
patterns).

---

## 6. Swapping the embedding model

The dimension of the `vector` columns comes from `EMBEDDING_DIM`. To switch to the architecture
document's `nomic-embed-text` (768-dim) or any other model:

1. Set `EMBEDDING_MODEL_NAME` and `EMBEDDING_DIM` in `.env`.
2. Recreate the schema: `python -m src.setup_database --drop`.
3. Re-run both ingestion scripts.

The setup and ingestion scripts fail loudly if `EMBEDDING_DIM` disagrees with the model's real
output dimension, so a mismatch cannot go unnoticed.

> **Offline smoke tests only:** set `EMBEDDING_BACKEND=hash` to replace the model with a
> deterministic, dependency-free stub. It produces correctly-shaped vectors so the pipeline can be
> exercised without the ML stack, but the vectors carry **no semantic meaning** — never use it for
> real retrieval.

---

## 7. Module registry

The four top-level folders under `academic content/` map to the four modules in scope:

| Folder | `module_id` | Module | Language |
| --- | --- | --- | --- |
| `IPRT` | `IPRT301` | Internet Programming | Java |
| `PBDV` | `PBDV301` | Platform Based Development | Python |
| `RESK` | `RESK301` | Research Skills | N/A |
| `SPRI` | `SPRI301` | Social and Professional Issues | N/A |

Only files inside a module folder are ingested. Media (`.mp4`, `.f4v`), archives (`.zip`), Office
formats we do not parse (`.odp`, `.doc`) and binaries are skipped and reported.

Edit the registry in `src/config.py` (`MODULE_REGISTRY`) if the module codes change.

---

## 8. Schema reference

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
| `curriculum_chunks` | `module_id`, `source_file`, `section_title`, `chunk_index`, `chunk_text`, `embedding vector(384)`, `doc_metadata` |
| `code_repair_patterns` | `error_title` (unique), `exception_thrown`, `broken_code`, `conceptual_tutor_hint`, `embedding vector(384)` |

Example module-filtered similarity query (mirrors §8.4 of the architecture document):

```sql
SELECT section_title, chunk_text
FROM curriculum_chunks
WHERE module_id = 'IPRT301'
ORDER BY embedding <=> :query_vector
LIMIT 3;
```

Example code-repair query:

```sql
SELECT error_title, conceptual_tutor_hint
FROM code_repair_patterns
ORDER BY embedding <=> :query_vector
LIMIT 3;
```

---

## 9. Tier 2 — Orchestration Tier (Flask API Gateway)

Tier 2 turns the data tier into a running tutoring backend. It needs Tier 1 to already be set up
(schema + ingested content) and, for local inference, a running Ollama instance.

### 9.1 Prerequisites

- Tier 1 is complete: `python -m src.setup_database` has been run and content is ingested.
- **Ollama** installed and running (`ollama serve`) with at least one model pulled, e.g.
  `ollama pull qwen3:4b` (default) or any model you want to test. The admin can pick from whatever
  is actually downloaded.
- *(Optional)* a cloud API key if you intend to switch the router to a hosted model.

### 9.2 Install the gateway dependencies

```bash
pip install -r requirements.txt
```

### 9.3 Add the Tier 2 tables and the default LLM config

Tier 2 adds one table (`llm_configs`) and seeds a default **local** configuration. This command is
**non-destructive** — it creates anything missing and never overwrites an admin's runtime changes:

```bash
python -m src.setup_database
```

Expected additions to the summary:

```
Tables ensured (9)
Seeded default LLM configuration (provider=local)
```

> If you are upgrading an existing database, this step is all you need (no `--drop`).

### 9.4 Configure the gateway (`.env`)

The Tier 2 keys are documented in `.env.example`. The important ones:

| Variable | Default | Purpose |
| --- | --- | --- |
| `FLASK_HOST` / `FLASK_PORT` | `127.0.0.1` / `5000` | Where the API listens. |
| `ADMIN_API_KEY` | `change-me-admin-key` | **Change this.** Required in `X-Admin-Key` for `/api/admin/*`. |
| `LLM_PROVIDER` | `local` | First-boot provider (`local` or `cloud`). |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local inference endpoint. |
| `DEFAULT_LOCAL_MODEL` | `qwen3:4b` | Model used before an admin overrides it. |
| `LLM_CONFIG_SECRET_KEY` | *(empty)* | Master key that encrypts stored cloud API keys. Set a strong value. |
| `AUTH_MODE` | `dev` | `dev` trusts identity headers; `strict` requires the DUT4life provider. |
| `ENFORCE_ENROLLMENT` | `false` | Block students from modules they are not enrolled in. |
| `OLLAMA_CIRCUIT_FAILURE_THRESHOLD` | `3` | Consecutive Ollama failures before the breaker opens. |
| `OLLAMA_CIRCUIT_RESET_TIMEOUT` | `30` | Seconds the breaker stays open before a half-open probe. |
| `OLLAMA_HEALTH_TIMEOUT` | `5` | Short timeout for `?deep=1` health checks and the model dropdown. |
| `GUARDRAIL_*` | see below | Guardrail thresholds / action. |

### 9.5 Start the server

```bash
# From the project root, with the virtual environment active:
python app.py
```

`app.py` is a thin entry point - it only calls `src.app.create_app()` and runs
it. You can also use the module or a WSGI server directly:

```bash
python -m src.app
flask --app src.app run --host 127.0.0.1 --port 5000
waitress-serve --call app:create_app
```

The server logs `Gateway ready`. Confirm it is healthy:

```bash
curl http://127.0.0.1:5000/api/health
curl "http://127.0.0.1:5000/api/health?deep=1"     # also pings Ollama + shows the breaker
```

#### Fault tolerance: what happens when a tier is down

The Flask API and the React client are designed to survive a crashed Data or
Inference tier:

* **Ollama down / hung** - every call goes through
  `src/inference/ollama_client.py`, which applies a timeout and a circuit
  breaker. `POST /api/chat` returns `502` with a clean JSON body
  (`{"error": "The AI inference engine is temporarily unavailable. Please check
  your local Ollama runtime."}`); the API process stays up. After
  `OLLAMA_CIRCUIT_FAILURE_THRESHOLD` failures the breaker opens and the API
  fails fast instead of stacking up timeouts.
* **PostgreSQL down** - identity, module-scope, retrieval and telemetry all
  degrade instead of raising: the router falls back to `.env` defaults, retrieval
  returns no context, and telemetry is skipped. `/api/health` reports
  `"status": "degraded"` with `503`.
* **Any unhandled error** - a catch-all handler returns clean JSON, never an HTML
  traceback the client cannot parse.

### 9.6 Endpoints

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/chat` | dev identity | Run the full agent workflow for one student turn. |
| `POST` | `/api/feedback` | dev identity | Record a thumbs up/down + reason tag (straight to telemetry). |
| `GET` | `/api/modules` | none | Modules available to the client dropdown. |
| `GET` | `/api/health` | none | Liveness/readiness (`?deep=1` pings Ollama). |
| `GET` | `/api/admin/llm-config` | `X-Admin-Key` | Read the active provider/model (key never returned). |
| `POST` | `/api/admin/llm-config` | `X-Admin-Key` | Switch local ↔ cloud and/or rotate the API key. |
| `GET` | `/api/admin/ollama-models` | `X-Admin-Key` | List downloaded Ollama models for a dropdown. |

### 9.7 Example: ask a question

```bash
curl -X POST http://127.0.0.1:5000/api/chat \
  -H "Content-Type: application/json" \
  -H "X-User-Email: 22000000@dut4life.ac.za" \
  -H "X-User-Role: student" \
  -d '{
        "module_id": "IPRT301",
        "message": "Why does my Java code throw a NullPointerException?",
        "history": []
      }'
```

Response (trimmed):

```json
{
  "session_id": "...",
  "message_id": "...",
  "reply": "Let's work through it. Look at the line in the stack trace — which variable could be null?",
  "intent": { "label": "debugging", "confidence": 0.75, "source": "heuristic" },
  "scaffolding": { "stage": "hint", "hint_sequence_depth": 0 },
  "guardrail": { "flagged": false, "flags": [], "action": "pass" },
  "retrieval": { "chunks": [ ... ], "patterns": [ ... ] },
  "llm": { "provider": "local", "model": "qwen3:4b", "backend": "ollama", "latency_ms": 2140 },
  "telemetry_log_id": 1
}
```

Pass the returned `session_id` back on the next call to advance the Socratic progression
(`hint → student_attempt → feedback → further_guidance → explanation`).

### 9.8 Dynamic LLM Routing (local ↔ cloud)

The router reads the active configuration **from PostgreSQL before every generation**. Switch it at
runtime — no restart, no `.env` edit:

```bash
ADMIN="-H X-Admin-Key: change-me-admin-key -H Content-Type:application/json"

# Inspect the active config
curl $ADMIN http://127.0.0.1:5000/api/admin/llm-config

# Populate a dropdown with locally downloaded Ollama models
curl $ADMIN http://127.0.0.1:5000/api/admin/ollama-models

# Use a different local model
curl -X POST $ADMIN http://127.0.0.1:5000/api/admin/llm-config \
  -d '{"provider":"local","local_model":"qwen3:8b"}'

# Switch to a cloud API (OpenAI-compatible) and store the key encrypted
curl -X POST $ADMIN http://127.0.0.1:5000/api/admin/llm-config \
  -d '{"provider":"cloud","cloud_provider":"openai",
       "cloud_base_url":"https://api.openai.com/v1",
       "cloud_model":"gpt-4o-mini","api_key":"sk-..."}'

# Back to local (and optionally clear the stored key)
curl -X POST $ADMIN http://127.0.0.1:5000/api/admin/llm-config \
  -d '{"provider":"local","clear_api_key":true}'
```

Supported cloud flavours: `openai`, `openai_compatible`, `azure_openai`, `anthropic`.

**Key security:** keys are encrypted with Fernet using `LLM_CONFIG_SECRET_KEY` before they reach the
`llm_configs` table. `GET /api/admin/llm-config` only ever returns `has_api_key` and a masked
`api_key_masked` value (e.g. `....9876`).

### 9.9 Guardrail thresholds

The architecture fixes the guardrail *behaviour* (block complete-solution leakage and out-of-scope
output) but not the numbers (§11.1), so these are configuration:

| Variable | Default | Meaning |
| --- | --- | --- |
| `GUARDRAIL_MAX_CODE_LINES` | `8` | More code lines than this is treated as a solution leak. |
| `GUARDRAIL_MAX_WORDS` | `400` | Responses above this are flagged `too_long`. |
| `GUARDRAIL_MIN_CONTEXT_OVERLAP` | `0.08` | Below this lexical overlap with retrieved context is flagged `out_of_scope`. |
| `GUARDRAIL_ACTION` | `block` | `block` replaces a leak with a Socratic fallback; `truncate` strips code/answers. |

### 9.10 Authentication note

`AUTH_MODE=dev` reads the caller from the `X-User-Email` / `X-User-Role` headers (or the JSON body)
and links them to the `students` table when possible — perfect for local testing. Production uses
`AUTH_MODE=strict`, which expects a DUT4life token; the identity-provider call is isolated in
`src/auth.py::_verify_dut4life_token` and is the single place to wire up the real tenant (§4.4).
Admin routes always require the `X-Admin-Key` header.

---

## 10. Troubleshooting

| Symptom | Fix |
| --- | --- |
| `port is already allocated` | Another service owns the port. Change `POSTGRES_PORT` in `.env` (and update `DATABASE_URL`), then `docker compose up -d`. |
| `ModuleNotFoundError: pgvector` | `pip install -r requirements.txt` inside the activated virtual environment. |
| `Could not connect to PostgreSQL` | Is the container healthy (`docker compose ps`)? Are `.env` credentials correct? |
| `sentence-transformers is not installed` | Install requirements, or use `EMBEDDING_BACKEND=hash` for a plumbing-only smoke test. |
| Model download fails offline | Pre-download the model, or use `EMBEDDING_BACKEND=hash` for local testing. |
| Embedding dimension mismatch | Set `EMBEDDING_DIM` to the model's real dimension and re-run `setup_database --drop`. |
| Tier 2: `Could not ensure the llm_configs table exists` | Run `python -m src.setup_database` (non-destructive) to add the Tier 2 table. |
| Tier 2: `502 The tutoring model is unavailable` | Ollama is not running or the selected model is not pulled. Check `GET /api/admin/ollama-models` and `GET /api/health?deep=1`. |
| Tier 2: `401 Invalid or missing admin key` | Send the `X-Admin-Key` header matching `ADMIN_API_KEY`, and change it from the default. |
| Tier 2: `502 Ollama request ... failed` after switching models | That model is not downloaded. List models with `/api/admin/ollama-models` and select one. |
| Tier 2: `502 The active cloud provider has no API key` | POST an `api_key` to `/api/admin/llm-config` before switching to cloud. |
| Tier 2: stored key fails to decrypt | `LLM_CONFIG_SECRET_KEY` changed. Re-enter the cloud API key via the admin endpoint. |

---

## 11. Scope reminder

This repository now covers **Tier 1 (Hybrid Data Tier)** and **Tier 2 (Orchestration Tier)**:

- Tier 1 exposes data, schema and ingestion.
- Tier 2 exposes the Flask API Gateway, the multi-agent Socratic RAG workflow and the Dynamic LLM
  Router that drives local Ollama or a cloud API.

The **Client Tier** (student chat, tutor/admin dashboards) and the DUT4life identity provider are
intentionally out of scope. The gateway already exposes everything those clients need: `/api/chat`,
`/api/feedback`, `/api/modules`, and the admin configuration endpoints.
