import pytest

from repoview.agent.llm import FakeLLM, make_text_response, make_tool_call_response
from repoview.agent.loop import run_session
from repoview.indexer import index_repo


@pytest.fixture
def repo_id(conn, mini_repo):
    return index_repo(conn, "MiniRepo", mini_repo)["repo_id"]


def test_returns_final_review_when_model_stops_calling_tools(conn, repo_id):
    llm = FakeLLM([make_text_response("문제 없음")])
    result = run_session(conn, repo_id, "성능 문제 있어?", llm)
    assert result.status == "COMPLETED"
    assert result.final_review == "문제 없음"
    assert result.iteration_count == 1


def test_executes_tool_call_then_finishes(conn, repo_id):
    llm = FakeLLM([
        make_tool_call_response("search_code", {"pattern": "findOrdersByUserId"}),
        make_text_response("N+1 의심: UserService.java:13"),
    ])
    result = run_session(conn, repo_id, "성능 문제 있어?", llm)
    assert result.status == "COMPLETED"
    assert result.iteration_count == 2


def test_tool_result_is_appended_to_messages(conn, repo_id):
    llm = FakeLLM([
        make_tool_call_response("search_code", {"pattern": "findOrdersByUserId"}),
        make_text_response("끝"),
    ])
    run_session(conn, repo_id, "질문", llm)
    second_call_messages = llm.received_messages[1]
    tool_messages = [m for m in second_call_messages if m.get("role") == "tool"]
    assert tool_messages
    assert "UserService.java" in tool_messages[0]["content"]


def test_records_trace_steps(conn, repo_id):
    llm = FakeLLM([
        make_tool_call_response("read_file", {"path": "pom.xml"}),
        make_text_response("끝"),
    ])
    result = run_session(conn, repo_id, "질문", llm)
    rows = conn.execute(
        "SELECT type, tool_name FROM trace_step WHERE session_id = ? ORDER BY step_no",
        (result.session_id,),
    ).fetchall()
    assert [row["type"] for row in rows] == ["LLM_CALL", "TOOL_CALL", "LLM_CALL"]
    assert rows[1]["tool_name"] == "read_file"


def test_caps_at_max_iterations(conn, repo_id):
    llm = FakeLLM([
        make_tool_call_response("search_code", {"pattern": "a"}),
        make_tool_call_response("search_code", {"pattern": "b"}),
        make_text_response("상한 도달 후 요약"),
    ])
    result = run_session(conn, repo_id, "질문", llm, max_iterations=2)
    assert result.status == "CAPPED"
    assert result.final_review == "상한 도달 후 요약"


def test_tool_error_does_not_break_loop(conn, repo_id):
    llm = FakeLLM([
        make_tool_call_response("read_file", {"path": "../../../etc/passwd"}),
        make_text_response("경로 접근이 거부되었습니다"),
    ])
    result = run_session(conn, repo_id, "질문", llm)
    assert result.status == "COMPLETED"
    row = conn.execute(
        "SELECT tool_result FROM trace_step WHERE session_id = ? AND type = 'TOOL_CALL'",
        (result.session_id,),
    ).fetchone()
    assert row["tool_result"].startswith("ERROR:")


def test_session_row_is_updated_on_completion(conn, repo_id):
    llm = FakeLLM([make_text_response("완료")])
    result = run_session(conn, repo_id, "질문", llm)
    row = conn.execute("SELECT * FROM session WHERE id = ?", (result.session_id,)).fetchone()
    assert row["status"] == "COMPLETED"
    assert row["finished_at"] is not None
    assert row["input_tokens"] > 0


def test_long_tool_result_is_truncated_in_trace(conn, repo_id, monkeypatch):
    from repoview.agent import loop as loop_module

    monkeypatch.setattr(loop_module, "MAX_TRACE_RESULT_CHARS", 50)
    llm = FakeLLM([
        make_tool_call_response("read_file", {"path": "src/main/java/com/example/UserService.java"}),
        make_text_response("끝"),
    ])
    result = run_session(conn, repo_id, "질문", llm)
    row = conn.execute(
        "SELECT tool_result, tool_result_length FROM trace_step "
        "WHERE session_id = ? AND type = 'TOOL_CALL'",
        (result.session_id,),
    ).fetchone()
    assert len(row["tool_result"]) <= 50
    assert row["tool_result_length"] > 50
