import sqlite3

from repoview.db import get_connection, init_db

EXPECTED_TABLES = {
    "repo", "repo_file", "session", "trace_step",
    "eval_case", "eval_run", "eval_result",
}


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {row["name"] for row in rows}


def test_init_db_creates_all_tables(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    assert EXPECTED_TABLES <= table_names(conn)


def test_init_db_is_idempotent(tmp_path):
    db_file = tmp_path / "test.db"
    conn = get_connection(db_file)
    init_db(conn)
    init_db(conn)
    assert EXPECTED_TABLES <= table_names(conn)


def test_foreign_keys_are_enforced(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    try:
        conn.execute(
            "INSERT INTO repo_file (repo_id, path) VALUES (?, ?)", (999, "a.java")
        )
        conn.commit()
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised
