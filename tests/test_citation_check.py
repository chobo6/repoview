from repoview.citation_check import verify_citations
from repoview.indexer import index_repo


def _seed_session_with_read(conn, session_id, repo_id, path, start, end):
    """trace_step.session_id는 session(id)를 참조하는 외래키라, read_file
    트레이스를 넣으려면 먼저 session 행이 있어야 한다."""
    conn.execute(
        "INSERT INTO session (id, repo_id, question, status, model, phase) VALUES (?, ?, 'q', 'RUNNING', 'm', 2)",
        (session_id, repo_id),
    )
    conn.execute(
        """
        INSERT INTO trace_step (session_id, step_no, type, tool_name, tool_result)
        VALUES (?, 1, 'TOOL_CALL', 'read_file', ?)
        """,
        (session_id, f"{path} ({start}-{end}행)\n실제 파일 내용"),
    )
    conn.commit()


def test_citation_with_no_issues_returns_empty_list(conn, mini_repo):
    repo_id = index_repo(conn, "MiniRepo", mini_repo)["repo_id"]
    row = conn.execute(
        "SELECT path, line_count FROM repo_file WHERE repo_id = ? LIMIT 1", (repo_id,)
    ).fetchone()
    _seed_session_with_read(conn, session_id=1, repo_id=repo_id, path=row["path"], start=1, end=row["line_count"])

    review = f"문제: 없음\n근거: `{row['path']}:1-{row['line_count']}`\n영향: 없음\n제안: 없음"
    warnings = verify_citations(conn, repo_id, session_id=1, review_text=review)
    assert warnings == []


def test_citation_to_nonexistent_file_is_flagged(conn, mini_repo):
    # session/trace_step에 아무것도 없어도 verify_citations은 repo_file과
    # trace_step을 session_id로 SELECT만 하므로(외래키 삽입이 아니므로)
    # 존재하지 않는 session_id를 그냥 넘겨도 동작한다 — read_file 기록이 없다는
    # 뜻이 되어 오히려 "미확인 인용" 계열 검증과 자연스럽게 맞아떨어진다.
    repo_id = index_repo(conn, "MiniRepo", mini_repo)["repo_id"]

    review = "문제: 있음\n근거: `no/such/file.py:1`\n영향: -\n제안: -"
    warnings = verify_citations(conn, repo_id, session_id=999, review_text=review)
    assert len(warnings) == 1
    assert warnings[0]["file_path"] == "no/such/file.py"
    assert warnings[0]["issue"] == "존재하지 않는 파일"


def test_citation_not_actually_read_is_flagged(conn, mini_repo):
    repo_id = index_repo(conn, "MiniRepo", mini_repo)["repo_id"]
    row = conn.execute(
        "SELECT path, line_count FROM repo_file WHERE repo_id = ? LIMIT 1", (repo_id,)
    ).fetchone()
    # read_file을 한 번도 호출하지 않은(트레이스가 없는) 세션인 상황을 재현한다.

    review = f"문제: 있음\n근거: `{row['path']}:1`\n영향: -\n제안: -"
    warnings = verify_citations(conn, repo_id, session_id=999, review_text=review)
    assert len(warnings) == 1
    assert warnings[0]["issue"] == "read_file로 확인하지 않은 인용"


def test_citation_beyond_file_length_is_flagged(conn, mini_repo):
    repo_id = index_repo(conn, "MiniRepo", mini_repo)["repo_id"]
    row = conn.execute(
        "SELECT path, line_count FROM repo_file WHERE repo_id = ? LIMIT 1", (repo_id,)
    ).fetchone()
    beyond = row["line_count"] + 100
    _seed_session_with_read(conn, session_id=2, repo_id=repo_id, path=row["path"], start=1, end=row["line_count"])

    review = f"문제: 있음\n근거: `{row['path']}:{beyond}`\n영향: -\n제안: -"
    warnings = verify_citations(conn, repo_id, session_id=2, review_text=review)
    assert len(warnings) == 1
    assert warnings[0]["issue"] == "파일 길이를 벗어난 라인"


def test_review_with_no_citations_returns_empty_list(conn, mini_repo):
    repo_id = index_repo(conn, "MiniRepo", mini_repo)["repo_id"]
    warnings = verify_citations(conn, repo_id, session_id=999, review_text="문제를 발견하지 못했습니다")
    assert warnings == []
