"""
chain.py
--------
Defines the Groq-backed LLM and the LangChain NL2SQL pipeline.

`ask_database()` is the single public entrypoint used by both the
FastAPI route and the Gradio UI. It:
  1. Builds a SQL query from the natural-language question.
  2. Guards against non-read-only statements before execution.
  3. Executes the query against the configured database.
  4. Returns both the generated SQL and the result (or an error message).
"""

import os
import re
from typing import TypedDict

from dotenv import load_dotenv
from langchain_groq import ChatGroq
from sqlalchemy import text as sql_text

from app.database import get_db

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not GROQ_API_KEY:
    raise RuntimeError(
        "GROQ_API_KEY is not set. Define it in your .env file."
    )

# Configurable via .env so a future Groq deprecation doesn't require a code
# change -- just update GROQ_MODEL and restart/redeploy. Groq periodically
# retires models; check https://console.groq.com/docs/deprecations if this
# starts failing with a "model_decommissioned" error.
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

# Statements that must never be allowed to reach the database, since this
# service is intended to be strictly read-only.
_DISALLOWED_STATEMENT_PATTERN = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|MERGE|CALL|EXEC)\b",
    re.IGNORECASE,
)


class QueryResult(TypedDict):
    sql_query: str
    result: str
    error: str | None


# Model families on Groq that are "reasoning" models -- these can emit
# <think>...</think> traces or free-form reasoning before their actual
# answer. For these, ask Groq's API to strip reasoning server-side via
# reasoning_format="hidden" so `.content` is just the final answer.
_REASONING_MODEL_PATTERN = re.compile(r"gpt-oss|qwen3|deepseek-r1", re.IGNORECASE)


def _get_llm() -> ChatGroq:
    """Initializes the Groq chat model used for SQL generation."""
    model_kwargs: dict = {}
    if _REASONING_MODEL_PATTERN.search(GROQ_MODEL):
        model_kwargs["reasoning_format"] = "hidden"

    return ChatGroq(
        model=GROQ_MODEL,
        temperature=0,
        api_key=GROQ_API_KEY,
        model_kwargs=model_kwargs,
    )


_SQL_GENERATION_TEMPLATE = """You are a {dialect} expert. Given an input question, write a single syntactically correct {dialect} SQL query that answers it.

Only use the following tables and columns:

{table_info}

Rules:
- Return ONLY the SQL query itself -- no explanation, no markdown code fences, no "SQLQuery:" prefix, no commentary before or after.
- The query must be read-only. Only use SELECT, WITH, or EXPLAIN -- never INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, GRANT, REVOKE, or MERGE.
- Unless the question asks for an aggregate (COUNT, SUM, AVG, MIN, MAX) that naturally returns a single row, limit results to at most {top_k} rows using LIMIT.
- Only use column and table names that appear in the schema above. Do not invent columns or tables.
- End the query with a semicolon.

Question: {question}

SQL query:"""


def _build_sql_prompt(db, question: str, top_k: int = 5) -> str:
    """
    Builds the SQL-generation prompt directly (rather than using LangChain's
    create_sql_query_chain, which binds a stop=["\\nSQLResult:"] sequence to
    every call). That stop sequence was designed for older, non-reasoning
    models; reasoning models like openai/gpt-oss-* can reference that same
    text while reasoning about the expected output format, which causes the
    API to cut the response off mid-reasoning with empty final content. This
    prompt has no stop sequence, so it works for both reasoning and
    non-reasoning models.
    """
    return _SQL_GENERATION_TEMPLATE.format(
        dialect=db.dialect,
        table_info=db.get_table_info(),
        top_k=top_k,
        question=question,
    )


def _extract_sql(raw_query: str) -> str:
    """
    Normalizes raw model output down to a bare SQL statement. Handles:
      - markdown code fences
      - a leading 'SQLQuery:' style label
      - reasoning-model output: models like openai/gpt-oss-* may prepend a
        <think>...</think> block, or plain prose, before the actual SQL.
    """
    query = raw_query.strip()

    # Strip <think>...</think> reasoning blocks that reasoning models may
    # emit before their actual answer.
    query = re.sub(r"<think>.*?</think>", "", query, flags=re.IGNORECASE | re.DOTALL).strip()

    # Strip a leading "SQLQuery:" style label if present.
    query = re.sub(r"^SQLQuery:\s*", "", query, flags=re.IGNORECASE)

    # Strip markdown code fences if the model wrapped the SQL in them.
    fence_match = re.search(r"```(?:sql)?\s*(.*?)```", query, re.IGNORECASE | re.DOTALL)
    if fence_match:
        query = fence_match.group(1).strip()

    # Locate the actual SQL statement rather than assuming the string starts
    # with it -- reasoning models often prepend explanatory prose even after
    # <think> tags and fences are removed.
    stmt_match = re.search(
        r"\b(SELECT|WITH|EXPLAIN|INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|MERGE)\b.*",
        query,
        re.IGNORECASE | re.DOTALL,
    )
    if stmt_match:
        query = stmt_match.group(0).strip()
    else:
        # No recognizable SQL keyword found anywhere in the output -- don't
        # let leftover prose (e.g. the model refusing in words) pass through
        # as if it were a query.
        return ""

    # Some chains append a trailing semicolon plus explanation text; keep
    # only up to the first semicolon-terminated statement if one exists.
    if ";" in query:
        query = query.split(";")[0].strip() + ";"

    return query.strip()


def _is_read_only(sql_query: str) -> bool:
    """Returns True only if the statement looks like a read-only query."""
    if _DISALLOWED_STATEMENT_PATTERN.match(sql_query):
        return False
    return True


def _format_result(columns: list, rows: list) -> str:
    """
    Formats query results for display instead of returning Python's raw
    str(list-of-tuples) form (e.g. "[(2,)]"):
      - No rows: a plain "No results." message.
      - A single row with a single column: just the bare value (e.g. "2"),
        which covers COUNT()/SUM()/single-value lookups.
      - Anything larger: a simple aligned text table with a header row.
    """
    if not rows:
        return "No results."

    if len(rows) == 1 and len(columns) == 1:
        return str(rows[0][0])

    str_rows = [[("" if v is None else str(v)) for v in row] for row in rows]
    widths = [
        max(len(str(col)), *(len(r[i]) for r in str_rows)) if str_rows else len(str(col))
        for i, col in enumerate(columns)
    ]

    def format_row(values: list) -> str:
        return " | ".join(str(v).ljust(widths[i]) for i, v in enumerate(values))

    header = format_row(list(columns))
    separator = "-+-".join("-" * w for w in widths)
    body = "\n".join(format_row(r) for r in str_rows)

    return f"{header}\n{separator}\n{body}"


def ask_database(question: str) -> QueryResult:
    """
    Translates a natural-language question into SQL, executes it against
    the configured read-only database, and returns both the query and
    the result.

    Never raises: any failure (LLM error, unsafe SQL, execution error) is
    captured and returned in the `error` field so callers (API/UI) can
    surface it cleanly instead of crashing.
    """
    if not question or not question.strip():
        return QueryResult(sql_query="", result="", error="Question must not be empty.")

    db = get_db()
    llm = _get_llm()

    # 1. Generate the SQL query from the natural-language question.
    try:
        prompt = _build_sql_prompt(db, question)
        response = llm.invoke(prompt)
        raw_sql = response.content if hasattr(response, "content") else str(response)
    except Exception as exc:
        return QueryResult(
            sql_query="",
            result="",
            error=f"Failed to generate SQL from the question: {exc}",
        )

    sql_query = _extract_sql(raw_sql)

    if not sql_query:
        preview = raw_sql.strip()[:300] if raw_sql else "(empty response)"
        return QueryResult(
            sql_query=raw_sql,
            result="",
            error=f"The model did not return a usable SQL query. Raw model output: {preview!r}",
        )

    # 2. Guard: only allow read-only statements to execute.
    if not _is_read_only(sql_query):
        return QueryResult(
            sql_query=sql_query,
            result="",
            error=(
                "Generated statement was rejected because it is not read-only. "
                "Only SELECT-style queries are permitted."
            ),
        )

    # 3. Execute the query directly via SQLAlchemy (rather than
    # SQLDatabase.run(), which returns a Python-repr string like "[(2,)]")
    # so the result can be formatted cleanly.
    try:
        with db._engine.connect() as conn:
            cursor_result = conn.execute(sql_text(sql_query))
            columns = list(cursor_result.keys())
            rows = cursor_result.fetchall()
    except Exception as exc:
        return QueryResult(
            sql_query=sql_query,
            result="",
            error=f"Failed to execute the generated SQL query: {exc}",
        )

    return QueryResult(sql_query=sql_query, result=_format_result(columns, rows), error=None)
