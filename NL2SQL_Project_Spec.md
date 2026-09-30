# Project Specification: NL2SQL Microservice (FastAPI + Gradio + LangChain)

## Objective
Build a Natural Language to SQL (NL2SQL) microservice that allows users to ask plain English questions about database data. The application will translate these questions into secure, read-only SQL queries, execute them, and return the results. 

The architecture consists of a FastAPI backend to expose programmatic REST endpoints, and a Gradio web interface mounted directly onto the FastAPI app for UI access. The entire application will be containerized using Docker for deployment to GCP Cloud Run.

## Architecture Stack
* **Database:** PostgreSQL (Supabase).
* **LLM:** Groq API (e.g., `llama3-70b-8192` or `mixtral-8x7b-32768`) via `langchain-groq`.
* **Orchestration:** LangChain (`create_sql_query_chain`, `SQLDatabase`).
* **Backend Framework:** FastAPI.
* **Frontend Framework:** Gradio (mounted inside FastAPI).
* **Deployment:** Docker.

---

## 1. Directory Structure
Instruct the agent to create the following directory structure:

```text
nl2sql_project/
├── requirements.txt
├── .env
├── Dockerfile
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI entrypoint and Gradio mounting
│   ├── database.py          # Supabase connection setup via LangChain SQLDatabase
│   ├── chain.py             # LangChain create_sql_query_chain logic
│   └── ui.py                # Gradio interface definition
```

---

## 2. Environment Variables (`.env`)
The agent must expect these environment variables. *Do not hardcode these in the codebase.*

```env
GROQ_API_KEY="your_groq_api_key_here"
DATABASE_URL="postgresql+psycopg2://[db-user]:[password]@[supabase-url]:5432/[db-name]"
```

---

## 3. Dependencies (`requirements.txt`)
The agent should use the following packages:

```text
fastapi==0.111.0
uvicorn==0.30.1
gradio==4.36.1
langchain==0.2.5
langchain-community==0.2.5
langchain-groq==0.1.5
psycopg2-binary==2.9.9
SQLAlchemy==2.0.30
python-dotenv==1.0.1
```

---

## 4. Module Implementation Details

### `app/database.py`
**Goal:** Initialize the database connection for LangChain.
* Use `langchain_community.utilities.SQLDatabase`.
* Load the `DATABASE_URL` from the environment.
* Initialize the `SQLDatabase.from_uri()` instance. Provide a function `get_db()` to expose it.

### `app/chain.py`
**Goal:** Define the LLM and the LangChain SQL generation pipeline.
* Import `ChatGroq` from `langchain_groq`.
* Initialize the LLM using the model `llama3-70b-8192` with `temperature=0`.
* Import `create_sql_query_chain` from `langchain.chains`.
* Create a function `ask_database(question: str) -> dict` that:
  1. Calls the `create_sql_query_chain` passing the `ChatGroq` instance and the `SQLDatabase` instance.
  2. Invokes the chain to generate the SQL query string.
  3. Uses the `db.run(sql_query)` method to execute the generated query against the Supabase database.
  4. Returns a dictionary containing both the `sql_query` and the `result`.

### `app/main.py`
**Goal:** Expose the REST API and mount Gradio.
* Initialize the FastAPI `app`.
* Create a POST endpoint `/api/query` using Pydantic for request validation:
  ```python
  class QueryRequest(BaseModel):
      question: str
  ```
  The endpoint should call `ask_database()` and return the JSON response.
* Import the Gradio `demo` from `app.ui`.
* Use `gr.mount_gradio_app(app, demo, path="/")` to serve the UI on the root path.

### `app/ui.py`
**Goal:** Define the interactive Gradio Chat/Blocks interface.
* Create a Gradio `Blocks` interface.
* The layout should contain:
  1. A title using `gr.Markdown`.
  2. A `gr.Textbox` for the user's natural language question.
  3. A `gr.Button("Generate Data")`.
  4. Two output textboxes: one to show the generated `SQL Query`, and one to show the `Results`.
* Link the button click to a wrapper function that calls `ask_database()` and updates the output boxes.

---

## 5. Dockerization (`Dockerfile`)
**Goal:** Package the application for GCP Cloud Run.
* Use a slim python base image (e.g., `python:3.11-slim`).
* Set the working directory to `/app`.
* Copy `requirements.txt` and install them without cache.
* Copy the `app/` directory.
* Expose port `8080` (Cloud Run's default expected port).
* Command to run: `uvicorn app.main:app --host 0.0.0.0 --port 8080`.

---

## Execution Instructions for the Agent
1. Create the files exactly as mapped in the Directory Structure.
2. Ensure strict type hinting and error handling in the `app/chain.py` module (e.g., handling invalid SQL generation).
3. Do not include mock data generation scripts; assume the Supabase database already contains valid tables.
4. Ensure the `.env` file is added to a `.gitignore`.
