# Getting Started — Hybrid AI Tutoring System

Step-by-step onboarding for a fresh collaborator. You need **nothing pre-installed
except** Git, Python, Node.js, Docker Desktop and Ollama. All commands below are
for **Windows PowerShell** (the team's standard shell).

**Repo:** https://github.com/PatrickLuhanga/AI-tutoring-system.git (branch: `main`)

---

## 0. Prerequisites (install once)

| Tool | Version | Check with |
| --- | --- | --- |
| Git | any recent | `git --version` |
| Python | **3.12 recommended** (3.10–3.12 supported) | `python --version` |
| Node.js | 18+ (v20/v22 fine) | `node --version` |
| Docker Desktop | with Compose v2 | `docker --version` |
| Ollama | latest | `ollama --version` |

> **Python note:** the ML stack (`torch` / `sentence-transformers`) has the most
> reliable wheels on 3.10–3.12. If you use a newer Python (3.13/3.14) and
> `pip install -r requirements.txt` fails to find wheels, install Python 3.12
> and recreate the venv.

---

## 1. Clone the repository

```powershell
git clone https://github.com/PatrickLuhanga/AI-tutoring-system.git
cd AI-tutoring-system
```

Confirm you are on `main`:

```powershell
git status        # should report "On branch main", clean tree
```

---

## 2. Environment variables

The repo contains **no secrets** — `.env` files are git-ignored. Duplicate the
examples and fill in the values below.

### 2.1 Backend (project root)

```powershell
Copy-Item .env.example .env
```

The defaults in `.env.example` work out of the box for local development. You
**must request three things from the project lead** if they differ from the
defaults:

| Key | Default in `.env.example` | Request from lead? |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | `tutor_password` | Only if the lead changed the DB password |
| `ADMIN_API_KEY` | `change-me-admin-key` | **Yes — the real shared admin key** (protects `/api/admin/*`) |
| `LLM_CONFIG_SECRET_KEY` | *(empty)* | **Yes — the encryption master key** used to decrypt stored cloud API keys; only needed if you plan to use a cloud provider |
| `DEFAULT_LOCAL_MODEL` | `qwen3:4b` | Set to `qwen3:8b` if you pulled `qwen3:8b` in §5 |

### 2.2 Frontend (`frontend/`)

```powershell
cd frontend
Copy-Item .env.example .env
cd ..
```

| Key | Value | Notes |
| --- | --- | --- |
| `VITE_USE_MOCK` | `true` → run the UI with **zero backend**; `false` → talk to Flask | Set `false` once the API is up (§7) |
| `VITE_ADMIN_KEY` | **must equal** `ADMIN_API_KEY` from 2.1 | Otherwise the Admin dashboard gets `401` |

> ⚠️ The `.env.example` defaults (`dev-admin-key` vs `change-me-admin-key`)
> **do not match**. If you want the Admin dashboard's LLM-router controls to
> work against a live backend, make these two values identical.

---

## 3. Backend setup (Python venv)

From the project root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
```

> If PowerShell blocks `Activate.ps1` (execution policy), run:
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`, then activate
> again.

You should see `(venv)` prepended to your prompt. Verify:

```powershell
python -c "import flask, sqlalchemy, pgvector, sentence_transformers; print('deps OK')"
```

---

## 4. Frontend setup

```powershell
cd frontend
npm install
cd ..
```

Verify:

```powershell
cd frontend; npm run build; cd ..
```

`npm run build` should finish with no TypeScript errors (`tsc -b && vite build`).

---

## 5. Infrastructure initialization

### 5.1 PostgreSQL 16 + pgvector (Docker, port **5433**)

Start Docker Desktop, then from the project root:

```powershell
docker compose up -d
docker compose ps
```

`docker compose ps` must show `ai_tutoring_pg ... (healthy)` with
`0.0.0.0:5433->5432/tcp`. Extra verification:

```powershell
docker ps --filter "name=ai_tutoring_pg"
docker exec ai_tutoring_pg pg_isready -U tutor_admin -d ai_tutoring
```

The last command should print `accepting connections`.

> Port 5433 is deliberate (avoids clashing with any PostgreSQL already on 5432).
> If 5433 is taken on your machine, change `POSTGRES_PORT` in `.env` **and**
> rebuild the container: `docker compose down; docker compose up -d`.

### 5.2 Ollama (local LLM, port 11434)

Ollama runs as a background service (Windows app or `ollama serve` in a terminal).

```powershell
ollama list
```

The working model for this project is **`qwen3:8b`**. If it is missing:

```powershell
ollama pull qwen3:8b
```

Verify the API is reachable:

```powershell
curl http://localhost:11434/api/tags
```

You should see a JSON payload containing `"name":"qwen3:8b"`.

> ⚠️ `.env.example` defaults to `qwen3:4b`, which is **not** pulled by default.
> Either `ollama pull qwen3:4b`, or set `DEFAULT_LOCAL_MODEL=qwen3:8b` in `.env`.
> The admin dashboard can also switch models at runtime.

### 5.3 Initialize the database + ingest course material

Run in order from the project root (venv active):

```powershell
python -m src.setup_database      # creates extensions, 9 tables, HNSW indexes, seeds 4 modules + default LLM config
python -m src.ingest_curriculum   # walks "academic content/", chunks, embeds, stores vectors
python -m src.ingest_code_patterns  # inserts the 50 Java code-repair patterns
python -m src.verify_data         # sanity check: row counts + 2 module-filtered similarity searches
```

Expected tails: `Tables ensured (9)`, `Seeded 4 modules`,
`Seeded default LLM configuration (provider=local)`.

> The first `ingest_curriculum` run downloads `all-MiniLM-L6-v2` (~90 MB).
> The full ingestion over all four modules takes a while — for a quick test use
> `python -m src.ingest_curriculum --module IPRT --max-files 20`.

---

## 6. Boot sequence (two terminals)

### Terminal 1 — Flask API Gateway (backend)

```powershell
# from the project root, venv active
python app.py
```

You should see `Gateway ready` and it listens on `http://127.0.0.1:5000`.
Health check (in a third terminal or browser):

```powershell
curl http://127.0.0.1:5000/api/health
curl "http://127.0.0.1:5000/api/health?deep=1"   # also pings Ollama + circuit breaker
```

### Terminal 2 — Vite dev server (frontend)

```powershell
cd frontend
npm run dev
```

Open **http://localhost:5173**. Vite proxies `/api` → `http://localhost:5000`,
so nothing extra to configure.

> For the UI to talk to Flask (not mocks), `frontend/.env` must have
> `VITE_USE_MOCK=false` — **restart `npm run dev` after changing it**.

### Smoke test the full pipeline

```powershell
curl -X POST http://127.0.0.1:5000/api/chat `
  -H "Content-Type: application/json" `
  -H "X-User-Email: 22000000@dut4life.ac.za" `
  -H "X-User-Role: student" `
  -d '{\"module_id\":\"IPRT301\",\"message\":\"Why does my Java code throw a NullPointerException?\",\"history\":[]}'
```

A successful reply includes `intent`, `scaffolding`, `guardrail`, `retrieval`
and `llm` audit blocks. Pass the returned `session_id` back on the next call to
advance the Socratic progression.

---

## 7. Troubleshooting quick reference

| Symptom | Fix |
| --- | --- |
| `port is already allocated` (Docker) | Change `POSTGRES_PORT` in `.env`, then `docker compose down; docker compose up -d` |
| `ModuleNotFoundError: pgvector` | You are outside the venv, or `pip install -r requirements.txt` didn't run |
| `Could not connect to PostgreSQL` | `docker compose ps` → container must be `(healthy)` |
| Chat returns 502 "inference engine unavailable" | Ollama not running, or model not pulled — `ollama list`, `curl http://localhost:11434/api/tags` |
| Admin dashboard returns 401 | `VITE_ADMIN_KEY` must equal backend `ADMIN_API_KEY` |
| Embedding dimension mismatch | `EMBEDDING_DIM` must match the model; change and re-run `python -m src.setup_database --drop` + re-ingest |
| UI shows "Mock data" badge | `VITE_USE_MOCK` is still `true` — set `false` and restart Vite |
