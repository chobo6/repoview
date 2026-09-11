import sqlite3
from pathlib import Path

from repoview.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS repo (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT    NOT NULL UNIQUE,
    root_path        TEXT    NOT NULL,
    primary_language TEXT,
    framework        TEXT,
    file_count       INTEGER DEFAULT 0,
    chunk_count      INTEGER DEFAULT 0,
    indexed_at       TEXT,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS repo_file (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id    INTEGER NOT NULL REFERENCES repo(id) ON DELETE CASCADE,
    path       TEXT    NOT NULL,
    language   TEXT,
    size_bytes INTEGER,
    line_count INTEGER
);
CREATE INDEX IF NOT EXISTS idx_repo_file_repo_path ON repo_file(repo_id, path);

CREATE TABLE IF NOT EXISTS session (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id         INTEGER NOT NULL REFERENCES repo(id),
    question        TEXT    NOT NULL,
    status          TEXT    NOT NULL,
    final_review    TEXT,
    iteration_count INTEGER DEFAULT 0,
    input_tokens    INTEGER DEFAULT 0,
    output_tokens   INTEGER DEFAULT 0,
    cost_usd        REAL    DEFAULT 0,
    model           TEXT    NOT NULL,
    phase           INTEGER NOT NULL,
    error           TEXT,
    started_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    finished_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_session_repo_started ON session(repo_id, started_at);

CREATE TABLE IF NOT EXISTS trace_step (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id         INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    step_no            INTEGER NOT NULL,
    type               TEXT    NOT NULL,
    tool_name          TEXT,
    tool_args          TEXT,
    tool_result        TEXT,
    tool_result_length INTEGER,
    assistant_text     TEXT,
    input_tokens       INTEGER,
    output_tokens      INTEGER,
    latency_ms         INTEGER,
    error              TEXT,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_trace_step_session ON trace_step(session_id, step_no);

CREATE TABLE IF NOT EXISTS eval_case (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id             INTEGER NOT NULL REFERENCES repo(id),
    question            TEXT    NOT NULL,
    expected_finding    TEXT    NOT NULL,
    expected_file_path  TEXT,
    expected_line_start INTEGER,
    expected_line_end   INTEGER,
    category            TEXT,
    is_planted          INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS eval_run (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    phase          INTEGER NOT NULL,
    model          TEXT    NOT NULL,
    total_cases    INTEGER DEFAULT 0,
    passed_cases   INTEGER DEFAULT 0,
    detection_rate REAL    DEFAULT 0,
    notes          TEXT,
    started_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    finished_at    TEXT
);

CREATE TABLE IF NOT EXISTS eval_result (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    eval_run_id  INTEGER NOT NULL REFERENCES eval_run(id) ON DELETE CASCADE,
    eval_case_id INTEGER NOT NULL REFERENCES eval_case(id),
    session_id   INTEGER REFERENCES session(id),
    detected     INTEGER NOT NULL DEFAULT 0,
    judge_reason TEXT,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_eval_result_run ON eval_result(eval_run_id);
"""


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
