import json
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
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    eval_result_columns = {row["name"] for row in conn.execute("PRAGMA table_info(eval_result)")}
    if "false_positive" not in eval_result_columns:
        conn.execute(
            "ALTER TABLE eval_result ADD COLUMN false_positive INTEGER NOT NULL DEFAULT 0"
        )

    session_columns = {row["name"] for row in conn.execute("PRAGMA table_info(session)")}
    if "citation_warnings" not in session_columns:
        conn.execute("ALTER TABLE session ADD COLUMN citation_warnings TEXT")

    eval_run_columns = {row["name"] for row in conn.execute("PRAGMA table_info(eval_run)")}
    if "repo_id" not in eval_run_columns:
        conn.execute("ALTER TABLE eval_run ADD COLUMN repo_id INTEGER REFERENCES repo(id)")

    # repo_id가 비어있는 기존 행(마이그레이션 이전에 생성된 eval_run)을 채운다.
    # WHERE repo_id IS NULL이라 매번 호출해도 안전하고, 이미 채워진 행은 건드리지 않는다.
    conn.execute(
        """
        UPDATE eval_run
        SET repo_id = (
            SELECT ec.repo_id
            FROM eval_result er
            JOIN eval_case ec ON ec.id = er.eval_case_id
            WHERE er.eval_run_id = eval_run.id
            LIMIT 1
        )
        WHERE repo_id IS NULL
        """
    )

    if "fpr" not in eval_run_columns:
        conn.execute("ALTER TABLE eval_run ADD COLUMN fpr REAL")
    if "citation_accuracy" not in eval_run_columns:
        conn.execute("ALTER TABLE eval_run ADD COLUMN citation_accuracy REAL")
    if "avg_cost_usd" not in eval_run_columns:
        conn.execute("ALTER TABLE eval_run ADD COLUMN avg_cost_usd REAL")
    if "avg_latency_ms" not in eval_run_columns:
        conn.execute("ALTER TABLE eval_run ADD COLUMN avg_latency_ms REAL")

    # notes(JSON)에만 있던 통계를 실제 컬럼으로 백필한다 — fpr이 비어있고 notes가
    # 있는 행만 대상이라 매번 호출해도 안전하고, 이미 채워진 행은 건드리지 않는다.
    for row in conn.execute(
        "SELECT id, notes FROM eval_run WHERE fpr IS NULL AND notes IS NOT NULL"
    ).fetchall():
        stats = json.loads(row["notes"])
        conn.execute(
            """
            UPDATE eval_run
            SET fpr = ?, citation_accuracy = ?, avg_cost_usd = ?, avg_latency_ms = ?
            WHERE id = ?
            """,
            (
                stats.get("fpr"), stats.get("citation_accuracy"),
                stats.get("avg_cost_usd"), stats.get("avg_latency_ms"),
                row["id"],
            ),
        )
