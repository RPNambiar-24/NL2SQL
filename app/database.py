"""
database.py
-----------
Manages the LangChain SQLDatabase connection(s) used by the app.

Two sources are supported:
  1. A configured Postgres/Supabase database via DATABASE_URL (.env).
  2. A SQLite ".db" file uploaded at runtime through the UI or the
     /api/upload-db endpoint.

Whichever one is "active" is what ask_database() queries against. This
lets a user either point the service at Supabase permanently, or drop
in a SQLite file on the fly and have LangChain auto-introspect its
schema (tables, columns, types) with no manual config.
"""

import os
import shutil
from pathlib import Path
from threading import Lock
from typing import Optional

from dotenv import load_dotenv
from langchain_community.utilities import SQLDatabase

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

# Where uploaded .db files are stored. On Cloud Run this is ephemeral
# (wiped on restart/new instance) -- see README for persistence notes.
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/tmp/nl2sql_uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class _DatabaseManager:
    """
    Holds whichever SQLDatabase is currently "active" and is safe to
    read/write from multiple requests (FastAPI route + Gradio callback
    both touch this). A single process-wide instance is used.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._active_db: Optional[SQLDatabase] = None
        self._active_source: Optional[str] = None  # "postgres" | "sqlite:<filename>"

    def _connect_env_database(self) -> SQLDatabase:
        if not DATABASE_URL:
            raise RuntimeError(
                "No database is configured. Either set DATABASE_URL in your "
                ".env file, or upload a .db (SQLite) file first."
            )
        try:
            return SQLDatabase.from_uri(DATABASE_URL)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to connect to the database using DATABASE_URL: {exc}"
            ) from exc

    def get_active(self) -> SQLDatabase:
        """
        Returns the currently active SQLDatabase, connecting to the
        configured Postgres/Supabase database on first use if nothing
        has been uploaded yet.
        """
        with self._lock:
            if self._active_db is None:
                self._active_db = self._connect_env_database()
                self._active_source = "postgres"
            return self._active_db

    def get_active_source(self) -> str:
        with self._lock:
            return self._active_source or "unset"

    def load_sqlite_file(self, source_path: str, original_filename: str) -> SQLDatabase:
        """
        Copies an uploaded SQLite file into the upload directory, opens
        it as a SQLDatabase (which reflects the schema automatically),
        and makes it the active database. Raises on invalid/corrupt files.
        """
        safe_name = Path(original_filename).name or "uploaded.db"
        dest_path = UPLOAD_DIR / safe_name
        shutil.copyfile(source_path, dest_path)

        try:
            db = SQLDatabase.from_uri(f"sqlite:///{dest_path}")
            # Force a schema read now so bad/empty files fail fast, not on
            # the first question the user asks.
            table_names = db.get_usable_table_names()
            if not table_names:
                raise RuntimeError("The uploaded file has no tables.")
        except Exception as exc:
            dest_path.unlink(missing_ok=True)
            raise RuntimeError(f"Could not read '{safe_name}' as a SQLite database: {exc}") from exc

        with self._lock:
            self._active_db = db
            self._active_source = f"sqlite:{safe_name}"

        return db

    def reset_to_configured_database(self) -> SQLDatabase:
        """Switches back to the DATABASE_URL (Postgres/Supabase) connection."""
        db = self._connect_env_database()
        with self._lock:
            self._active_db = db
            self._active_source = "postgres"
        return db


_manager = _DatabaseManager()


def get_db() -> SQLDatabase:
    """Returns the currently active SQLDatabase (Postgres or uploaded SQLite)."""
    return _manager.get_active()


def get_active_source() -> str:
    """Returns a short label for whichever database is currently active."""
    return _manager.get_active_source()


def load_sqlite_upload(temp_path: str, original_filename: str) -> dict:
    """
    Loads an uploaded .db file as the active database. Returns a summary
    dict with the table names and a per-table column preview, so callers
    (UI/API) can show the user what schema was detected.
    """
    db = _manager.load_sqlite_file(temp_path, original_filename)
    tables = db.get_usable_table_names()
    return {
        "source": f"sqlite:{Path(original_filename).name}",
        "tables": sorted(tables),
        "schema_preview": db.get_table_info(),
    }


def use_configured_database() -> dict:
    """Switches the active database back to the configured DATABASE_URL."""
    db = _manager.reset_to_configured_database()
    tables = db.get_usable_table_names()
    return {
        "source": "postgres (DATABASE_URL)",
        "tables": sorted(tables),
        "schema_preview": db.get_table_info(),
    }
