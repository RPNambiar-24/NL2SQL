"""
ui.py
-----
Gradio Blocks interface for the NL2SQL microservice. Mounted onto the
FastAPI app at "/" in main.py.

Adds a database upload panel: the user can drop in a SQLite ".db" file,
which is introspected automatically (tables + columns) and becomes the
active database for the Q&A panel below it. Without an upload, the
service falls back to the configured Postgres/Supabase DATABASE_URL.
"""

import gradio as gr

from app.chain import ask_database
from app.database import get_active_source, load_sqlite_upload, use_configured_database


def _handle_query(question: str) -> tuple[str, str]:
    """
    Wrapper called by the Gradio button. Calls ask_database() and maps
    its output onto the two Gradio output textboxes (sql_query, results).
    """
    if not question or not question.strip():
        return "", "Please enter a question."

    response = ask_database(question)

    if response.get("error"):
        sql_display = response.get("sql_query") or "(no SQL generated)"
        result_display = f"Error: {response['error']}"
        return sql_display, result_display

    return response["sql_query"], response["result"]


def _handle_upload(file_obj) -> tuple[str, str]:
    """
    Wrapper called when a .db file is uploaded. Loads it as the active
    database and returns a status message plus a schema preview so the
    user can confirm the right tables were detected.
    """
    if file_obj is None:
        return "No file selected.", ""

    filename = getattr(file_obj, "name", str(file_obj))

    if not filename.lower().endswith((".db", ".sqlite", ".sqlite3")):
        return (
            f"'{filename}' doesn't look like a SQLite file (.db/.sqlite/.sqlite3).",
            "",
        )

    try:
        summary = load_sqlite_upload(filename, filename)
    except Exception as exc:
        return f"Failed to load database: {exc}", ""

    status = f"Loaded '{summary['source']}' — detected tables: {', '.join(summary['tables'])}"
    return status, summary["schema_preview"]


def _handle_reset() -> tuple[str, str]:
    """Switches the active database back to the configured DATABASE_URL."""
    try:
        summary = use_configured_database()
    except Exception as exc:
        return f"Failed to switch to configured database: {exc}", ""

    status = f"Active source: {summary['source']} — tables: {', '.join(summary['tables'])}"
    return status, summary["schema_preview"]


with gr.Blocks(title="NL2SQL Microservice") as demo:
    gr.Markdown("# NL2SQL Microservice")
    gr.Markdown(
        "Ask a question in plain English. It will be translated into a "
        "read-only SQL query, executed against the active database, and "
        "the results will be shown below."
    )

    with gr.Accordion("Database source", open=True):
        gr.Markdown(
            "Upload a SQLite `.db` file to query it directly — its schema "
            "is detected automatically. Without an upload, questions run "
            "against the configured Postgres/Supabase database."
        )
        with gr.Row():
            db_file_input = gr.File(
                label="Upload a .db / .sqlite file",
                file_types=[".db", ".sqlite", ".sqlite3"],
            )
            with gr.Column():
                upload_button = gr.Button("Use uploaded database")
                reset_button = gr.Button("Use configured Postgres/Supabase database")

        db_status = gr.Textbox(label="Active database status", interactive=False)
        schema_preview = gr.Textbox(
            label="Detected schema",
            lines=8,
            interactive=False,
        )

    gr.Markdown("---")

    question_input = gr.Textbox(
        label="Question",
        placeholder="e.g. How many orders were placed last month?",
        lines=2,
    )

    generate_button = gr.Button("Generate Data")

    sql_output = gr.Textbox(label="SQL Query", lines=4, interactive=False)
    result_output = gr.Textbox(label="Results", lines=8, interactive=False)

    upload_button.click(
        fn=_handle_upload,
        inputs=[db_file_input],
        outputs=[db_status, schema_preview],
    )

    reset_button.click(
        fn=_handle_reset,
        inputs=[],
        outputs=[db_status, schema_preview],
    )

    generate_button.click(
        fn=_handle_query,
        inputs=[question_input],
        outputs=[sql_output, result_output],
    )
