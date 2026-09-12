import json
import pytest
from fastapi.testclient import TestClient

from repoview.agent.llm import FakeLLM, make_text_response, make_tool_call_response
from repoview.api.app import app, get_db, get_embedding_client, get_llm
from repoview.embedding_client import FakeEmbeddingClient
from repoview.indexer import index_repo


@pytest.fixture
def client(conn, mini_repo, monkeypatch, tmp_path):
    import repoview.agent.loop as loop_module

    monkeypatch.setattr(loop_module, "CHROMA_PATH", tmp_path / "chroma")
    index_repo(conn, "MiniRepo", mini_repo)
    app.dependency_overrides[get_db] = lambda: conn
    app.dependency_overrides[get_llm] = lambda: FakeLLM([make_text_response("리뷰 결과")])
    app.dependency_overrides[get_embedding_client] = lambda: FakeEmbeddingClient()
    yield TestClient(app)
    app.dependency_overrides.clear()


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


def test_create_session_returns_review(client):
    repo_id = client.get("/api/repos").json()[0]["id"]
    response = client.post("/api/sessions", json={"repo_id": repo_id, "question": "성능 문제?"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "COMPLETED"
    assert body["final_review"] == "리뷰 결과"
    assert body["session_id"] > 0


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


def test_unhandled_exception_still_has_cors_header(client, monkeypatch):
    from repoview.api import app as app_module

    def boom(*args, **kwargs):
        raise RuntimeError("서버 내부 오류 테스트")

    monkeypatch.setattr(app_module, "run_session", boom)
    repo_id = client.get("/api/repos").json()[0]["id"]

    response = client.post(
        "/api/sessions",
        json={"repo_id": repo_id, "question": "질문"},
        headers={"Origin": "http://localhost:5173"},
    )

    assert response.status_code == 500
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_create_session_still_works_with_real_embedding_client_dependency_overridden(client):
    # get_embedding_client가 의존성 주입 체인에 실제로 연결돼 있는지 확인한다.
    # (세션 생성 자체가 200으로 끝나면, embedding_client가 run_session까지 전달되는
    # 배선이 끊어지지 않았다는 뜻이다 — 상세 검증은 Task 7의 test_agent_loop.py가 담당한다.)
    repo_id = client.get("/api/repos").json()[0]["id"]
    response = client.post("/api/sessions", json={"repo_id": repo_id, "question": "질문"})
    assert response.status_code == 200


def test_list_eval_runs_returns_runs_for_repo(client, conn):
    repo_id = client.get("/api/repos").json()[0]["id"]
    conn.execute(
        """
        INSERT INTO eval_run
            (repo_id, phase, model, total_cases, passed_cases, detection_rate, notes)
        VALUES (?, 2, 'gpt-4o', 1, 1, 1.0, ?)
        """,
        (repo_id, json.dumps({
            "fpr": 0.0, "citation_accuracy": 1.0,
            "avg_cost_usd": 0.0124, "avg_latency_ms": 5120,
        })),
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
