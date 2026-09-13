import json

import pytest
from fastapi.testclient import TestClient

from repoview.agent.llm import FakeLLM, make_text_response, make_tool_call_response
from repoview.api.app import app, get_db, get_db_path, get_embedding_client, get_llm
from repoview.embedding_client import FakeEmbeddingClient
from repoview.indexer import index_repo


@pytest.fixture
def client(conn, mini_repo, monkeypatch, tmp_path):
    import repoview.agent.loop as loop_module

    monkeypatch.setattr(loop_module, "CHROMA_PATH", tmp_path / "chroma")
    index_repo(conn, "MiniRepo", mini_repo)
    app.dependency_overrides[get_db] = lambda: conn
    app.dependency_overrides[get_db_path] = lambda: tmp_path / "test.db"
    app.dependency_overrides[get_llm] = lambda: FakeLLM([make_text_response("리뷰 결과")])
    app.dependency_overrides[get_embedding_client] = lambda: FakeEmbeddingClient()
    yield TestClient(app)
    app.dependency_overrides.clear()


def _consume_stream(client, session_id, headers=None):
    """SSE 스트림을 끝(done 또는 error)까지 소비하고 [(이벤트명, payload), ...]를 반환한다.
    TestClient의 동기 client.get()은 StreamingResponse가 완전히 끝날 때까지 블로킹하므로,
    이 함수가 반환하는 시점엔 이미 세션 실행이 끝나 있다."""
    response = client.get(f"/api/sessions/{session_id}/stream", headers=headers)
    events = []
    event_name = None
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event_name = line[len("event: "):]
        elif line.startswith("data: "):
            events.append((event_name, json.loads(line[len("data: "):])))
    return events, response


def test_list_repos(client):
    response = client.get("/api/repos")
    assert response.status_code == 200
    assert response.json()[0]["name"] == "MiniRepo"


def test_get_repo_detail(client):
    repo_id = client.get("/api/repos").json()[0]["id"]
    response = client.get(f"/api/repos/{repo_id}")
    assert response.status_code == 200
    assert "overview" in response.json()


def test_get_missing_repo_returns_404(client):
    response = client.get("/api/repos/9999")
    assert response.status_code == 404
    assert "error" in response.json()


def test_create_session_returns_pending_status(client, conn):
    repo_id = client.get("/api/repos").json()[0]["id"]
    response = client.post("/api/sessions", json={"repo_id": repo_id, "question": "성능 문제?"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "PENDING"
    assert body["session_id"] > 0

    row = conn.execute("SELECT status FROM session WHERE id = ?", (body["session_id"],)).fetchone()
    assert row["status"] == "PENDING"
    trace_count = conn.execute(
        "SELECT COUNT(*) AS c FROM trace_step WHERE session_id = ?", (body["session_id"],)
    ).fetchone()["c"]
    assert trace_count == 0


def test_create_session_with_unknown_repo_returns_404(client):
    response = client.post("/api/sessions", json={"repo_id": 9999, "question": "질문"})
    assert response.status_code == 404


def test_create_session_with_blank_question_returns_400(client):
    repo_id = client.get("/api/repos").json()[0]["id"]
    response = client.post("/api/sessions", json={"repo_id": repo_id, "question": "   "})
    assert response.status_code == 400


def test_get_session_includes_trace(conn, mini_repo, monkeypatch, tmp_path):
    import repoview.agent.loop as loop_module

    monkeypatch.setattr(loop_module, "CHROMA_PATH", tmp_path / "chroma")
    index_repo(conn, "MiniRepo", mini_repo)
    app.dependency_overrides[get_db] = lambda: conn
    app.dependency_overrides[get_db_path] = lambda: tmp_path / "test.db"
    app.dependency_overrides[get_llm] = lambda: FakeLLM([
        make_tool_call_response("search_code", {"pattern": "class"}),
        make_text_response("끝"),
    ])
    app.dependency_overrides[get_embedding_client] = lambda: FakeEmbeddingClient()
    client = TestClient(app)

    repo_id = client.get("/api/repos").json()[0]["id"]
    session_id = client.post(
        "/api/sessions", json={"repo_id": repo_id, "question": "질문"}
    ).json()["session_id"]

    events, _ = _consume_stream(client, session_id)
    assert events[-1][0] == "done"

    response = client.get(f"/api/sessions/{session_id}")
    assert response.status_code == 200
    trace = response.json()["trace"]
    assert any(step["type"] == "TOOL_CALL" for step in trace)

    app.dependency_overrides.clear()


def test_list_sessions(client):
    repo_id = client.get("/api/repos").json()[0]["id"]
    client.post("/api/sessions", json={"repo_id": repo_id, "question": "질문"})
    response = client.get("/api/sessions")
    assert response.status_code == 200
    assert len(response.json()) >= 1


def test_stream_emits_error_event_and_keeps_cors_header_when_run_session_crashes(client, conn, monkeypatch):
    from repoview.api import app as app_module

    def boom(*args, **kwargs):
        raise RuntimeError("서버 내부 오류 테스트")

    monkeypatch.setattr(app_module, "run_session", boom)
    repo_id = client.get("/api/repos").json()[0]["id"]
    session_id = client.post(
        "/api/sessions", json={"repo_id": repo_id, "question": "질문"}
    ).json()["session_id"]

    events, response = _consume_stream(
        client, session_id, headers={"Origin": "http://localhost:5173"}
    )

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert events[-1][0] == "error"
    assert events[-1][1]["message"] == "서버 내부 오류 테스트"

    row = conn.execute(
        "SELECT status, finished_at FROM session WHERE id = ?", (session_id,)
    ).fetchone()
    assert row["status"] == "FAILED"
    assert row["finished_at"] is not None


def test_stream_still_works_with_real_embedding_client_dependency_overridden(client):
    # get_embedding_client이 스트림 엔드포인트 → 백그라운드 실행 → run_session까지
    # 배선이 끊어지지 않았는지 확인한다.
    repo_id = client.get("/api/repos").json()[0]["id"]
    session_id = client.post(
        "/api/sessions", json={"repo_id": repo_id, "question": "질문"}
    ).json()["session_id"]
    events, response = _consume_stream(client, session_id)
    assert response.status_code == 200
    assert events[-1][0] == "done"


def test_list_eval_runs_returns_runs_for_repo(client, conn):
    repo_id = client.get("/api/repos").json()[0]["id"]
    conn.execute(
        """
        INSERT INTO eval_run
            (repo_id, phase, model, total_cases, passed_cases, detection_rate,
             fpr, citation_accuracy, avg_cost_usd, avg_latency_ms)
        VALUES (?, 2, 'gpt-4o', 1, 1, 1.0, 0.0, 1.0, 0.0124, 5120)
        """,
        (repo_id,),
    )
    conn.commit()

    response = client.get(f"/api/evals/runs?repo_id={repo_id}")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["repo_name"] == "MiniRepo"
    assert body[0]["detection_rate"] == 1.0
    assert body[0]["fpr"] == 0.0
    assert body[0]["citation_accuracy"] == 1.0
    assert body[0]["avg_cost_usd"] == 0.0124
    assert body[0]["avg_latency_ms"] == 5120
    assert "notes" not in body[0]


def test_list_eval_runs_without_filter_returns_all(client, conn):
    repo_id = client.get("/api/repos").json()[0]["id"]
    conn.execute(
        "INSERT INTO eval_run (repo_id, phase, model, notes) VALUES (?, 2, 'gpt-4o', '{}')",
        (repo_id,),
    )
    conn.commit()

    response = client.get("/api/evals/runs")
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_list_eval_runs_includes_rows_with_null_repo_id(client, conn):
    # repo_id 백필이 실패할 수 있는 행(예: 매칭되는 eval_result가 없는 오래된 행)도
    # 조용히 숨겨지면 안 된다 — INNER JOIN이면 이런 행이 사라진다.
    conn.execute(
        "INSERT INTO eval_run (repo_id, phase, model, notes) VALUES (NULL, 2, 'gpt-4o', '{}')"
    )
    conn.commit()

    response = client.get("/api/evals/runs")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["repo_id"] is None
    assert body[0]["repo_name"] is None


def test_get_eval_run_detail_includes_case_results(client, conn):
    repo_id = client.get("/api/repos").json()[0]["id"]

    # Create a session for the eval_result to reference
    cursor = conn.execute(
        """
        INSERT INTO session (repo_id, question, status, model, phase)
        VALUES (?, 'test', 'COMPLETED', 'gpt-4o', 2)
        """,
        (repo_id,),
    )
    session_id = cursor.lastrowid

    cursor = conn.execute(
        """
        INSERT INTO eval_case (repo_id, question, expected_finding, category)
        VALUES (?, 'OrderController에 문제 있어?', '문제 없음', 'negative')
        """,
        (repo_id,),
    )
    eval_case_id = cursor.lastrowid
    cursor = conn.execute(
        "INSERT INTO eval_run (repo_id, phase, model, notes) VALUES (?, 2, 'gpt-4o', '{}')",
        (repo_id,),
    )
    run_id = cursor.lastrowid
    conn.execute(
        """
        INSERT INTO eval_result (eval_run_id, eval_case_id, session_id, detected, judge_reason)
        VALUES (?, ?, ?, 1, '문제 없다고 정확히 판단함')
        """,
        (run_id, eval_case_id, session_id),
    )
    conn.commit()

    response = client.get(f"/api/evals/runs/{run_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == run_id
    assert len(body["results"]) == 1
    result = body["results"][0]
    assert result["question"] == "OrderController에 문제 있어?"
    assert result["category"] == "negative"
    assert result["is_planted"] == 0
    assert result["detected"] == 1
    assert result["judge_reason"] == "문제 없다고 정확히 판단함"
    assert result["session_id"] == session_id


def test_get_missing_eval_run_returns_404(client):
    response = client.get("/api/evals/runs/9999")
    assert response.status_code == 404
    assert "error" in response.json()


def test_stream_missing_session_returns_404(client):
    response = client.get("/api/sessions/9999/stream")
    assert response.status_code == 404


def test_stream_emits_done_event_with_cost_usd(client, conn):
    repo_id = client.get("/api/repos").json()[0]["id"]
    session_id = client.post(
        "/api/sessions", json={"repo_id": repo_id, "question": "질문"}
    ).json()["session_id"]
    conn.execute("UPDATE session SET model = 'gpt-4o' WHERE id = ?", (session_id,))
    conn.commit()

    events, _ = _consume_stream(client, session_id)
    done_events = [payload for name, payload in events if name == "done"]
    assert len(done_events) == 1
    assert done_events[0]["status"] == "COMPLETED"
    assert done_events[0]["final_review"] == "리뷰 결과"
    assert done_events[0]["cost_usd"] == pytest.approx(0.00075)


def test_stream_emits_step_started_and_completed_for_tool_call(conn, mini_repo, monkeypatch, tmp_path):
    import repoview.agent.loop as loop_module

    monkeypatch.setattr(loop_module, "CHROMA_PATH", tmp_path / "chroma")
    index_repo(conn, "MiniRepo", mini_repo)
    app.dependency_overrides[get_db] = lambda: conn
    app.dependency_overrides[get_db_path] = lambda: tmp_path / "test.db"
    app.dependency_overrides[get_llm] = lambda: FakeLLM([
        make_tool_call_response("search_code", {"pattern": "class"}),
        make_text_response("끝"),
    ])
    app.dependency_overrides[get_embedding_client] = lambda: FakeEmbeddingClient()
    client = TestClient(app)

    repo_id = client.get("/api/repos").json()[0]["id"]
    session_id = client.post(
        "/api/sessions", json={"repo_id": repo_id, "question": "질문"}
    ).json()["session_id"]

    events, _ = _consume_stream(client, session_id)
    names = [name for name, _ in events]
    assert "step_started" in names
    assert "step_completed" in names
    started = next(payload for name, payload in events if name == "step_started")
    assert started["tool_name"] == "search_code"
    assert started["tool_args"] == {"pattern": "class"}

    app.dependency_overrides.clear()


def test_reconnect_to_completed_session_replays_trace_without_rerunning(client):
    repo_id = client.get("/api/repos").json()[0]["id"]
    session_id = client.post(
        "/api/sessions", json={"repo_id": repo_id, "question": "질문"}
    ).json()["session_id"]
    _consume_stream(client, session_id)  # 첫 연결 — 실제로 run_session을 실행시킨다

    events, response = _consume_stream(client, session_id)  # 재연결
    assert response.status_code == 200
    assert events[-1][0] == "done"

    # run_session이 두 번 실행됐다면 LLM_CALL 트레이스가 2개였을 것이다.
    # client 픽스처는 FakeLLM 응답을 1개만 준비하므로, 두 번째 실행은
    # 응답 고갈로 실패했거나 존재해선 안 된다 — 정확히 1개여야 한다.
    trace = client.get(f"/api/sessions/{session_id}").json()["trace"]
    assert sum(1 for s in trace if s["type"] == "LLM_CALL") == 1


# 주의: FastAPI TestClient는 두 스레드의 client.get() 호출을 하나의 공유 이벤트루프에
# 태스크로 올리고, claim 로직(SELECT→UPDATE→commit)엔 await 지점이 없어 실제로는
# 인터리빙되지 않는다 — 즉 이 테스트는 원자적 CAS(UPDATE ... WHERE status='PENDING')와
# 비원자적(check-then-act) 구현을 구분하지 못한다. 프로덕션의 SQL 레벨 원자성 자체는
# 별도로 코드 리뷰에서 검증됨(진짜 다중 프로세스 동시성에서만 의미 있는 방어선이라
# 이 harness로는 재현이 안 됨). 제대로 검증하려면 실제 uvicorn 서버+소켓 기반 테스트가
# 필요한데, 이 프로젝트 규모 대비 과하다고 판단해 보류함.
def test_concurrent_stream_connections_only_trigger_one_execution(client):
    import threading

    repo_id = client.get("/api/repos").json()[0]["id"]
    session_id = client.post(
        "/api/sessions", json={"repo_id": repo_id, "question": "질문"}
    ).json()["session_id"]

    results = []

    def _connect():
        results.append(client.get(f"/api/sessions/{session_id}/stream"))

    threads = [threading.Thread(target=_connect) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(r.status_code == 200 for r in results)
    trace = client.get(f"/api/sessions/{session_id}").json()["trace"]
    assert sum(1 for s in trace if s["type"] == "LLM_CALL") == 1
