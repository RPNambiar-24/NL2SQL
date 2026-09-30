# NL2SQL Microservice

Ask questions about your database in plain English and get back the SQL query plus the results — no SQL knowledge required.

A FastAPI + Gradio microservice that translates natural-language questions into secure, read-only SQL queries using an LLM (Groq), executes them, and returns the results. Supports both an uploaded SQLite `.db` file (schema auto-detected) and a persistent PostgreSQL/Supabase backend, and is fully containerized with Docker.

## Features

- **Natural language to SQL** — ask a question, get a generated query and its result, not just raw text.
- **Two data sources, swappable at runtime**
  - Upload a `.db` / `.sqlite` file directly — its schema (tables, columns, types) is introspected automatically, no config needed.
  - Or connect to a PostgreSQL / Supabase database via `DATABASE_URL`.
- **Read-only by design** — every generated statement is checked against a guard that only allows `SELECT` / `WITH` / `EXPLAIN` before it ever touches the database.
- **Clean result formatting** — a single-value result (e.g. `COUNT(*)`) is shown as a bare number, not a raw `[(2,)]` tuple; multi-row results render as a readable table.
- **Dual interface** — a Gradio web UI for interactive use, plus a REST API for programmatic access, both served from one FastAPI app.
- **Reasoning-model aware** — works with Groq's current reasoning models (`gpt-oss`, `qwen3`, etc.) by stripping internal reasoning server-side and using a custom prompt pipeline instead of LangChain's legacy `create_sql_query_chain`, which isn't compatible with how reasoning models respond.
- **Containerized** — packaged with Docker for a consistent, portable runtime.

## Architecture

```
┌────────────┐      ┌──────────────────┐      ┌─────────────┐      ┌───────────────────┐
│   Client   │ ───▶ │  FastAPI+Gradio  │ ───▶ │  SQL engine  │ ───▶ │     Database       │
│ Browser/   │      │   (Docker)        │      │ (Groq LLM)   │      │ SQLite / Supabase  │
│   curl     │      └──────────────────┘      └─────────────┘      └───────────────────┘
└────────────┘
```

**Request flow (`chain.py`):**

1. **Build prompt** — the database schema (`CREATE TABLE` statements + sample rows) and dialect are pulled live and combined with the question into a custom prompt (no LangChain `create_sql_query_chain`, which binds a stop sequence that breaks reasoning models).
2. **Call the LLM** — via Groq, with `reasoning_format=hidden` applied automatically for reasoning-model families so internal reasoning traces don't leak into the output.
3. **Extract SQL** — strips any leftover `<think>` blocks or prose and locates the actual SQL statement in the response.
4. **Read-only guard** — rejects anything that isn't `SELECT` / `WITH` / `EXPLAIN` before execution.
5. **Execute + format** — runs the query directly via SQLAlchemy and formats the result (bare value, table, or "No results.").

## Project structure

```
nl2sql_project/
├── requirements.txt
├── .env
├── Dockerfile
├── app/
│   ├── main.py          # FastAPI entrypoint, mounts Gradio + REST endpoints
│   ├── database.py       # Swappable active-database manager (SQLite upload / Postgres)
│   ├── chain.py           # Prompt building, LLM call, SQL extraction, execution
│   └── ui.py               # Gradio interface (Q&A panel + database upload panel)
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Copy `.env` and fill in your values:

```env
GROQ_API_KEY=your_groq_api_key_here
GROQ_MODEL=openai/gpt-oss-120b
DATABASE_URL=postgresql+psycopg2://[db-user]:[password]@[supabase-url]:5432/[db-name]
```

> **Don't wrap values in quotes.** `python-dotenv` strips quotes automatically when running locally, but `docker run --env-file .env` does not — quoted values will break your API key.

`DATABASE_URL` is optional if you only plan to use uploaded `.db` files. Groq periodically deprecates models — if you hit a `model_decommissioned` or `model_not_found` error, check what your key currently has access to:

```bash
curl https://api.groq.com/openai/v1/models -H "Authorization: Bearer $GROQ_API_KEY"
```

and update `GROQ_MODEL` accordingly — no code change needed.

## Run locally

```bash
uvicorn app.main:app --reload --port 8080
```

- Web UI: <http://localhost:8080/>
- Health check: `GET /api/health`

## API reference

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/health` | GET | Liveness check |
| `/api/database` | GET | Currently active database source + detected schema |
| `/api/upload-db` | POST (multipart) | Upload a `.db` / `.sqlite` file and make it the active database |
| `/api/use-configured-database` | POST | Switch back to the configured `DATABASE_URL` |
| `/api/query` | POST `{"question": "..."}` | Ask a question, get back the generated SQL and result |

Example:

```bash
curl -X POST http://localhost:8080/api/upload-db -F "file=@/path/to/data.db"

curl -X POST http://localhost:8080/api/query \
  -H "Content-Type: application/json" \
  -d '{"question": "How many customers are there?"}'
```

## Docker

```bash
docker build -t nl2sql-service .
docker run -p 8080:8080 --env-file .env nl2sql-service
```

## Security notes

- Every generated statement is checked against a read-only guard before execution (blocks `INSERT` / `UPDATE` / `DELETE` / `DROP` / `ALTER` / `TRUNCATE` / `CREATE` / `GRANT` / `REVOKE` / `MERGE` / `CALL` / `EXEC`).
- For defense in depth with a real Postgres/Supabase database, also create a dedicated **read-only database role** so the database itself enforces the restriction regardless of what SQL is generated.
- `.env`, uploaded `.db` files, and `token.json`-style credentials are all gitignored — never commit real credentials.
- Uploaded `.db` files are stored under `/tmp` by default, which is **ephemeral on Cloud Run** — fine for demos, not for persistent storage across restarts or multiple instances.

## Tech stack

FastAPI · Gradio · LangChain (`langchain-community`) · Groq (`langchain-groq`) · SQLAlchemy · PostgreSQL/Supabase · Docker
