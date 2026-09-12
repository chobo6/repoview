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


def test_eval_result_has_false_positive_column(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(eval_result)")}
    assert "false_positive" in columns


def test_false_positive_migration_is_safe_to_run_twice(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    init_db(conn)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(eval_result)")}
    assert "false_positive" in columns


def test_session_has_citation_warnings_column(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(session)")}
    assert "citation_warnings" in columns


def test_citation_warnings_migration_is_safe_to_run_twice(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    init_db(conn)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(session)")}
    assert "citation_warnings" in columns


def test_eval_run_has_repo_id_column(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(eval_run)")}
    assert "repo_id" in columns


def test_eval_run_repo_id_is_backfilled_from_existing_eval_results(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)

    conn.execute("INSERT INTO repo (id, name, root_path) VALUES (1, 'R', '/r')")
    conn.execute(
        "INSERT INTO eval_case (id, repo_id, question, expected_finding) VALUES (1, 1, 'q', 'f')"
    )
    conn.execute("INSERT INTO eval_run (id, phase, model) VALUES (1, 2, 'gpt-4o')")
    conn.execute(
        "INSERT INTO eval_result (eval_run_id, eval_case_id, detected) VALUES (1, 1, 0)"
    )
    conn.commit()

    # 위 INSERT들은 repo_id를 명시하지 않았으므로 eval_run.repo_id는 NULL이다.
    # init_db를 다시 태워 백필이 실제로 채우는지 확인한다.
    init_db(conn)

    row = conn.execute("SELECT repo_id FROM eval_run WHERE id = 1").fetchone()
    assert row["repo_id"] == 1
