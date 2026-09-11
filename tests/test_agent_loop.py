import chromadb
import pytest

from repoview.agent.llm import FakeLLM, make_text_response, make_tool_call_response
from repoview.agent.loop import run_session
from repoview.embedder import collection_name
from repoview.embedding_client import FakeEmbeddingClient
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


def test_llm_exception_marks_session_failed(conn, repo_id):
    class RaisingLLM:
        def call(self, messages, tools):
            raise RuntimeError("네트워크 오류 테스트")

    with pytest.raises(RuntimeError):
        run_session(conn, repo_id, "질문", RaisingLLM())

    row = conn.execute("SELECT * FROM session ORDER BY id DESC LIMIT 1").fetchone()
    assert row["status"] == "FAILED"
    assert "네트워크 오류 테스트" in row["error"]
    assert row["finished_at"] is not None


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


def test_search_semantic_works_when_repo_is_embedded(conn, repo_id, tmp_path, monkeypatch):
    import repoview.agent.loop as loop_module

    monkeypatch.setattr(loop_module, "CHROMA_PATH", tmp_path / "chroma")

    embedder = FakeEmbeddingClient()
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    collection = client.create_collection(name=collection_name("MiniRepo"))
    collection.add(
        ids=["src/main/java/com/example/UserService.java:1-17"],
        documents=["public List<User> findAllWithOrders() { ... }"],
        metadatas=[
            {
                "file_path": "src/main/java/com/example/UserService.java",
                "start_line": 1,
                "end_line": 17,
            }
        ],
        embeddings=embedder.embed(["public List<User> findAllWithOrders() { ... }"]),
    )

    llm = FakeLLM([
        make_tool_call_response(
            "search_semantic", {"query": "public List<User> findAllWithOrders() { ... }"}
        ),
        make_text_response("끝"),
    ])

    result = run_session(conn, repo_id, "질문", llm, embedding_client=embedder)

    row = conn.execute(
        "SELECT tool_result FROM trace_step WHERE session_id = ? AND type = 'TOOL_CALL'",
        (result.session_id,),
    ).fetchone()
    assert "UserService.java:1-17" in row["tool_result"]


def test_search_semantic_gracefully_errors_when_repo_not_embedded(conn, repo_id, tmp_path, monkeypatch):
    import repoview.agent.loop as loop_module

    monkeypatch.setattr(loop_module, "CHROMA_PATH", tmp_path / "chroma_never_written")

    llm = FakeLLM([
        make_tool_call_response("search_semantic", {"query": "아무 질문"}),
        make_text_response("끝"),
    ])

    result = run_session(conn, repo_id, "질문", llm, embedding_client=FakeEmbeddingClient())

    assert result.status == "COMPLETED"
    row = conn.execute(
        "SELECT tool_result FROM trace_step WHERE session_id = ? AND type = 'TOOL_CALL'",
        (result.session_id,),
    ).fetchone()
    assert row["tool_result"].startswith("ERROR:")


def test_run_session_without_embedding_client_still_works(conn, repo_id):
    # 기존 Phase 2 스타일 호출 — embedding_client를 아예 안 주는 경우도 여전히 동작해야 한다.
    llm = FakeLLM([make_text_response("완료")])
    result = run_session(conn, repo_id, "질문", llm)
    assert result.status == "COMPLETED"
