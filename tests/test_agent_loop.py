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


def test_caps_when_cumulative_tokens_exceed_limit(conn, repo_id):
    # make_tool_call_response는 input_tokens=100, output_tokens=20 고정이라
    # 한 번만 호출해도 120토큰이 누적된다. max_session_tokens=100이면 그 자리에서
    # (다음 라운드로 못 넘어가고) 바로 상한을 넘겨 반복 루프를 break하고,
    # 기존 "반복 상한 도달" 요약 경로로 빠져 CAPPED가 되어야 한다.
    llm = FakeLLM([
        make_tool_call_response("search_code", {"pattern": "a"}),
        make_text_response("토큰 상한 도달 후 요약"),
    ])
    result = run_session(conn, repo_id, "질문", llm, max_iterations=10, max_session_tokens=100)
    assert result.status == "CAPPED"
    assert result.final_review == "토큰 상한 도달 후 요약"
    assert result.iteration_count == 1


def test_does_not_cap_when_under_token_limit(conn, repo_id):
    # 첫 라운드(120토큰 누적)가 상한(10,000)에 한참 못 미쳐 break하지 않고
    # 두 번째 라운드로 정상 진행되어 COMPLETED로 끝나야 한다.
    llm = FakeLLM([
        make_tool_call_response("search_code", {"pattern": "a"}),
        make_text_response("문제 없음"),
    ])
    result = run_session(conn, repo_id, "질문", llm, max_session_tokens=10_000)
    assert result.status == "COMPLETED"
    assert result.final_review == "문제 없음"


def test_injects_notice_after_third_identical_tool_call(conn, repo_id):
    llm = FakeLLM([
        make_tool_call_response("search_code", {"pattern": "x"}),
        make_tool_call_response("search_code", {"pattern": "x"}),
        make_tool_call_response("search_code", {"pattern": "x"}),
        make_text_response("끝"),
    ])
    result = run_session(conn, repo_id, "질문", llm, max_iterations=10)
    assert result.status == "COMPLETED"

    tool_results = [
        row["tool_result"]
        for row in conn.execute(
            "SELECT tool_result FROM trace_step WHERE session_id = ? AND type = 'TOOL_CALL' ORDER BY step_no",
            (result.session_id,),
        ).fetchall()
    ]
    assert len(tool_results) == 3
    assert "이미 동일한 검색을 수행했습니다" not in tool_results[0]
    assert "이미 동일한 검색을 수행했습니다" not in tool_results[1]
    assert "이미 동일한 검색을 수행했습니다" in tool_results[2]


def test_does_not_inject_notice_for_different_arguments(conn, repo_id):
    llm = FakeLLM([
        make_tool_call_response("search_code", {"pattern": "x"}),
        make_tool_call_response("search_code", {"pattern": "y"}),
        make_tool_call_response("search_code", {"pattern": "z"}),
        make_text_response("끝"),
    ])
    result = run_session(conn, repo_id, "질문", llm, max_iterations=10)
    tool_results = [
        row["tool_result"]
        for row in conn.execute(
            "SELECT tool_result FROM trace_step WHERE session_id = ? AND type = 'TOOL_CALL' ORDER BY step_no",
            (result.session_id,),
        ).fetchall()
    ]
    assert all("이미 동일한 검색을 수행했습니다" not in r for r in tool_results)


def test_citation_warnings_recorded_when_review_cites_nonexistent_file(conn, repo_id):
    llm = FakeLLM([
        make_text_response("문제: 있음\n근거: `no/such/file.py:1`\n영향: -\n제안: -")
    ])
    result = run_session(conn, repo_id, "질문", llm)
    row = conn.execute(
        "SELECT citation_warnings FROM session WHERE id = ?", (result.session_id,)
    ).fetchone()
    assert row["citation_warnings"] is not None
    assert "no/such/file.py" in row["citation_warnings"]
    assert "존재하지 않는 파일" in row["citation_warnings"]


def test_citation_warnings_recorded_when_review_cites_existing_but_unread_file(conn, repo_id):
    # mini_repo에 실제로 존재하는 파일을 read_file 없이 그냥 인용하는 경우 —
    # "read_file로 확인하지 않은 인용" 분기가 run_session을 통해서도 실제로 발동해야 한다.
    llm = FakeLLM([
        make_text_response(
            "문제: 있음\n근거: `src/main/java/com/example/UserService.java:1`\n영향: -\n제안: -"
        )
    ])
    result = run_session(conn, repo_id, "질문", llm)
    row = conn.execute(
        "SELECT citation_warnings FROM session WHERE id = ?", (result.session_id,)
    ).fetchone()
    assert row["citation_warnings"] is not None
    assert "read_file로 확인하지 않은 인용" in row["citation_warnings"]


def test_citation_warnings_is_none_when_no_issues(conn, repo_id):
    llm = FakeLLM([make_text_response("문제를 발견하지 못했습니다")])
    result = run_session(conn, repo_id, "질문", llm)
    row = conn.execute(
        "SELECT citation_warnings FROM session WHERE id = ?", (result.session_id,)
    ).fetchone()
    assert row["citation_warnings"] is None


def test_repeat_call_notice_and_citation_warnings_both_apply_in_same_session(conn, repo_id):
    # 반복 호출 감지(3회 이상 동일 호출 시 안내 주입)와 인용 사후검증은 서로 다른
    # 지점(도구 디스패치 vs 세션 종료)에서 동작하는 독립된 Phase 4 기능이다 — 한
    # 세션 안에서 둘 다 트리거돼도 서로 간섭 없이 각자 정상 동작해야 한다.
    llm = FakeLLM([
        make_tool_call_response("search_code", {"pattern": "x"}),
        make_tool_call_response("search_code", {"pattern": "x"}),
        make_tool_call_response("search_code", {"pattern": "x"}),
        make_text_response("문제: 있음\n근거: `no/such/file.py:1`\n영향: -\n제안: -"),
    ])
    result = run_session(conn, repo_id, "질문", llm, max_iterations=10)
    assert result.status == "COMPLETED"

    tool_results = [
        row["tool_result"]
        for row in conn.execute(
            "SELECT tool_result FROM trace_step WHERE session_id = ? AND type = 'TOOL_CALL' ORDER BY step_no",
            (result.session_id,),
        ).fetchall()
    ]
    assert len(tool_results) == 3
    assert "이미 동일한 검색을 수행했습니다" in tool_results[2]

    row = conn.execute(
        "SELECT citation_warnings FROM session WHERE id = ?", (result.session_id,)
    ).fetchone()
    assert row["citation_warnings"] is not None
    assert "no/such/file.py" in row["citation_warnings"]
    assert "존재하지 않는 파일" in row["citation_warnings"]


def test_verify_citations_exception_does_not_fail_completed_session(conn, repo_id, monkeypatch):
    # 인용 검증은 어드바이저리 기능이다 — verify_citations가 예외를 던져도
    # 이미 완료된 세션이 FAILED/final_review=None으로 덮어써지면 안 된다.
    from repoview.agent import loop as loop_module

    def _raise(*args, **kwargs):
        raise RuntimeError("citation 검사기 고장")

    monkeypatch.setattr(loop_module, "verify_citations", _raise)

    llm = FakeLLM([make_text_response("문제 없음: 리뷰 완료")])
    result = run_session(conn, repo_id, "질문", llm)

    assert result.status == "COMPLETED"
    assert result.final_review == "문제 없음: 리뷰 완료"

    row = conn.execute(
        "SELECT status, final_review, citation_warnings FROM session WHERE id = ?",
        (result.session_id,),
    ).fetchone()
    assert row["status"] == "COMPLETED"
    assert row["final_review"] == "문제 없음: 리뷰 완료"
    assert row["citation_warnings"] is None


def test_citation_warnings_write_failure_does_not_affect_session_status(conn, repo_id, monkeypatch):
    # citation_warnings 기록 자체가 실패해도(예: 마이그레이션 누락 시나리오) 이미 커밋된
    # status/final_review는 영향받지 않아야 한다 — 두 UPDATE가 분리되어 있는지 검증한다.
    from repoview.agent import loop as loop_module

    def _raise(*args, **kwargs):
        raise RuntimeError("citation_warnings 컬럼 기록 실패")

    monkeypatch.setattr(loop_module, "_record_citation_warnings", _raise)

    llm = FakeLLM([
        make_text_response("문제: 있음\n근거: `no/such/file.py:1`\n영향: -\n제안: -")
    ])
    result = run_session(conn, repo_id, "질문", llm)

    assert result.status == "COMPLETED"

    row = conn.execute(
        "SELECT status, final_review, citation_warnings FROM session WHERE id = ?",
        (result.session_id,),
    ).fetchone()
    assert row["status"] == "COMPLETED"
    assert row["final_review"] is not None
    assert row["citation_warnings"] is None
