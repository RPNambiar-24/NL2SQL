"""
main.py
-------
FastAPI entrypoint. Exposes REST endpoints for querying and for
uploading a SQLite database file, and mounts the Gradio Blocks UI
(from app.ui) at the root path "/".
"""

import shutil
import tempfile
from pathlib import Path

import gradio as gr
from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.chain import ask_database
from app.database import get_active_source, load_sqlite_upload, use_configured_database
from app.ui import demo

app = FastAPI(
    title="NL2SQL Microservice",
    description="Translates natural-language questions into read-only SQL queries and executes them.",
    version="1.0.0",
)


class QueryRequest(BaseModel):
    question: str


class QueryResponse(BaseModel):
    sql_query: str
    result: str


class DatabaseStatusResponse(BaseModel):
    source: str
    tables: list[str]
    schema_preview: str


@app.get("/api/health")
def health_check() -> dict:
    return {"status": "ok"}


@app.get("/api/database", response_model=DatabaseStatusResponse)
def database_status() -> DatabaseStatusResponse:
    """Returns which database is currently active and its detected schema."""
    from app.database import get_db  # local import avoids connecting at module load

    try:
        db = get_db()
        return DatabaseStatusResponse(
            source=get_active_source(),
            tables=sorted(db.get_usable_table_names()),
            schema_preview=db.get_table_info(),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/upload-db", response_model=DatabaseStatusResponse)
async def upload_database(file: UploadFile = File(...)) -> DatabaseStatusResponse:
    """
    Accepts a SQLite (.db/.sqlite/.sqlite3) file upload, introspects its
    schema, and makes it the active database for subsequent /api/query
    calls (and the Gradio UI).
    """
    if not file.filename.lower().endswith((".db", ".sqlite", ".sqlite3")):
        raise HTTPException(
            status_code=400,
            detail="File must be a SQLite database (.db, .sqlite, or .sqlite3).",
        )

    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file.filename).suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        summary = load_sqlite_upload(tmp_path, file.filename)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return DatabaseStatusResponse(**summary)


@app.post("/api/use-configured-database", response_model=DatabaseStatusResponse)
def switch_to_configured_database() -> DatabaseStatusResponse:
    """Switches the active database back to the configured DATABASE_URL."""
    try:
        summary = use_configured_database()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return DatabaseStatusResponse(**summary)


@app.post("/api/query", response_model=QueryResponse)
def query_database(request: QueryRequest) -> QueryResponse:
    """
    Accepts a natural-language question, converts it to SQL, executes
    it against whichever database is currently active, and returns
    both the generated query and its result.
    """
    response = ask_database(request.question)

    if response.get("error"):
        raise HTTPException(status_code=400, detail=response["error"])

    return QueryResponse(sql_query=response["sql_query"], result=response["result"])


# Mount the Gradio UI on the root path so the app is browsable directly,
# while /api/query and friends remain available for programmatic access.
app = gr.mount_gradio_app(app, demo, path="/")
