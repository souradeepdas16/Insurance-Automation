"""SQLite database for case management, documents, and settings."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.paths import APP_DIR

DB_PATH = APP_DIR / "data" / "insurance.db"


def _get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db():
    conn = _get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create tables if they don't exist."""
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS cases (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT    NOT NULL,
                status        TEXT    NOT NULL DEFAULT 'created',
                folder_path   TEXT    NOT NULL,
                created_at    TEXT    NOT NULL,
                completed_at  TEXT,
                error_message TEXT
            );

            CREATE TABLE IF NOT EXISTS documents (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id         INTEGER NOT NULL,
                original_name   TEXT    NOT NULL,
                classified_name TEXT,
                doc_type        TEXT,
                file_path       TEXT    NOT NULL,
                created_at      TEXT    NOT NULL,
                FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE
            );
        """
        )

        # Always keep cases_folder pointing next to the current exe/project root.
        # Using UPSERT (not INSERT OR IGNORE) so that copying the dist folder
        # to a different machine always resolves to the correct location.
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("cases_folder", str(APP_DIR / "cases")),
        )


# ── Settings ──────────────────────────────────────────────────────────────────


def get_setting(key: str) -> str | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def get_all_settings() -> dict[str, str]:
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


# ── Cases ─────────────────────────────────────────────────────────────────────


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def create_case(name: str, folder_path: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO cases (name, status, folder_path, created_at) VALUES (?, ?, ?, ?)",
            (name, "created", folder_path, now),
        )
        case_id = cur.lastrowid
    return get_case(case_id)


def get_case(case_id: int) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if not row:
            return None
        case = _row_to_dict(row)
        docs = conn.execute(
            "SELECT * FROM documents WHERE case_id = ? ORDER BY id", (case_id,)
        ).fetchall()
        case["documents"] = [_row_to_dict(d) for d in docs]
        return case


def list_cases() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM cases ORDER BY created_at DESC").fetchall()
        cases = []
        for row in rows:
            case = _row_to_dict(row)
            doc_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM documents WHERE case_id = ?",
                (case["id"],),
            ).fetchone()["cnt"]
            case["document_count"] = doc_count
            cases.append(case)
        return cases


def update_case_status(
    case_id: int, status: str, error_message: str | None = None
) -> None:
    with get_db() as conn:
        if status == "completed":
            conn.execute(
                "UPDATE cases SET status = ?, completed_at = ?, error_message = NULL WHERE id = ?",
                (status, datetime.now(timezone.utc).isoformat(), case_id),
            )
        elif status == "failed":
            conn.execute(
                "UPDATE cases SET status = ?, error_message = ? WHERE id = ?",
                (status, error_message, case_id),
            )
        else:
            conn.execute("UPDATE cases SET status = ? WHERE id = ?", (status, case_id))


def reset_stuck_processing() -> int:
    """Reset any cases left in 'processing' state (e.g. after a server crash)."""
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE cases SET status = 'failed', error_message = 'Server restarted during processing' "
            "WHERE status = 'processing'",
        )
        return cur.rowcount


def delete_case(case_id: int) -> bool:
    with get_db() as conn:
        cur = conn.execute("DELETE FROM cases WHERE id = ?", (case_id,))
        return cur.rowcount > 0


# ── Documents ─────────────────────────────────────────────────────────────────


def add_document(case_id: int, original_name: str, file_path: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO documents (case_id, original_name, file_path, created_at) "
            "VALUES (?, ?, ?, ?)",
            (case_id, original_name, file_path, now),
        )
        doc_id = cur.lastrowid
    with get_db() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return _row_to_dict(row)


def get_document_by_id(doc_id: int) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return _row_to_dict(row) if row else None


def get_documents_by_case(case_id: int) -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM documents WHERE case_id = ? ORDER BY id", (case_id,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def update_document_classification(
    doc_id: int, doc_type: str, classified_name: str
) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE documents SET doc_type = ?, classified_name = ? WHERE id = ?",
            (doc_type, classified_name, doc_id),
        )


def reset_document_classifications(case_id: int) -> None:
    """Clear doc_type and classified_name for all documents of a case,
    so a re-run starts from a clean state."""
    with get_db() as conn:
        conn.execute(
            "UPDATE documents SET doc_type = NULL, classified_name = NULL WHERE case_id = ?",
            (case_id,),
        )


def delete_document(doc_id: int) -> bool:
    """Delete a single document record from the database."""
    with get_db() as conn:
        cur = conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        return cur.rowcount > 0
