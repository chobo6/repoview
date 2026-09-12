# SSE 스트리밍 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `POST /api/sessions`는 세션 생성만 하고 즉시 반환하며, `GET /api/sessions/{id}/stream`(SSE)에 연결하는 시점에 실제 에이전트 실행이 트리거되어 스텝이 실시간으로 스트리밍되게 만든다.

**Architecture:** `run_session`(`repoview/agent/loop.py`)은 이미 스텝마다 `trace_step`에 즉시 commit하므로 이 커밋을 그대로 이벤트 소스로 재사용한다(콜백 기반 실시간 push가 아니라 DB 폴링). SSE 엔드포인트가 연결 시 `UPDATE ... WHERE status='PENDING'`으로 원자적으로 실행을 선점해 백그라운드 스레드(`asyncio.to_thread`)로 `run_session`을 돌리고, 그 연결 자신은 0.2초 간격으로 `trace_step`을 폴링해 새 행을 SSE 이벤트로 변환한다.

**Tech Stack:** FastAPI `StreamingResponse` (신규 의존성 없음, `sse-starlette` 등 불필요), 브라우저 `EventSource`.

**Spec:** `docs/04-api-spec.md` 2절("구현 노트 — DB 폴링 기반" 서브섹션 포함), `docs/05-agent-design.md` §8

## Global Constraints

- 기존 `run_session` 호출자(`repoview/eval.py`, `tests/test_agent_loop.py`의 기존 테스트들)는 이 변경으로 전혀 영향받지 않는다 — `session_id`를 생략하면 지금과 100% 동일하게 동작해야 한다.
- 새 pip/npm 의존성 추가 금지. FastAPI `StreamingResponse`와 브라우저 내장 `EventSource`만 사용한다.
- 폴링 간격은 0.2초(`POLL_INTERVAL_S = 0.2`), sqlite `busy_timeout`은 5000ms(5초)로 고정한다 — 두 값 모두 스펙 문서에 이미 확정되어 있다.
- 커밋 메시지는 한국어로 작성하고 `feat:`/`fix:` 같은 접두어를 붙이지 않는다.
- SSE 이벤트 포맷: `f"event: {event명}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"`. 이벤트명과 페이로드 필드는 `docs/04-api-spec.md` 2절의 스키마(`step_started`, `step_completed`, `assistant_message`, `done`, `error`)를 그대로 따른다.

---

### Task 1: `get_connection()`에 busy_timeout 추가

SSE 폴링 커넥션과 백그라운드 실행 커넥션이 같은 sqlite 파일에 동시 접근하게 되므로, 그 전에 먼저 안전장치를 넣어둔다. 이후 태스크들이 이 위에서 안전하게 동작한다.

**Files:**
- Modify: `repoview/db.py:104-108` (`get_connection` 함수)
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `get_connection(db_path)`가 반환하는 커넥션은 `PRAGMA busy_timeout`이 5000으로 설정되어 있다. (이후 태스크가 그대로 재사용)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_db.py` 파일 끝에 추가:

```python
def test_get_connection_sets_busy_timeout(tmp_path):
    conn = get_connection(tmp_path / "busy.db")
    value = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert value == 5000
    conn.close()
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `pytest tests/test_db.py::test_get_connection_sets_busy_timeout -v`
Expected: FAIL (`assert 0 == 5000` — sqlite 기본 busy_timeout은 0)

- [ ] **Step 3: 최소 구현**

`repoview/db.py`의 `get_connection` 함수(104번째 줄 부근)를 다음으로 교체:

```python
def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_db.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 전체 스위트 확인 후 커밋**

Run: `pytest -q`
Expected: 기존 테스트 전부 그대로 PASS (busy_timeout 추가는 관측 가능한 동작 변화가 없음 — 락 경합 시 재시도 유예 시간만 늘어남)

```bash
git add repoview/db.py tests/test_db.py
git commit -m "$(cat <<'EOF'
sqlite 커넥션에 busy_timeout 5초 추가

SSE 폴링 커넥션과 세션 실행 커넥션이 같은 db 파일에 동시 접근하게 될
예정이라, 그 전에 락 경합 시 즉시 에러 대신 짧게 재시도하도록 안전장치를
먼저 넣는다.
EOF
)"
```

---

### Task 2: `run_session`에 `session_id` 옵션 파라미터 추가

세션 생성(행 INSERT)과 실행을 분리하려면, `run_session`이 "이미 있는 세션 ID를 이어받아 실행"하는 모드를 지원해야 한다.

**Files:**
- Modify: `repoview/agent/loop.py:30-52` (`run_session` 함수 시그니처와 본문 앞부분)
- Test: `tests/test_agent_loop.py`

**Interfaces:**
- Consumes: 없음 (Task 1과 독립)
- Produces: `run_session(conn, repo_id, question, llm, *, ..., session_id: int | None = None, ...)`. `session_id`가 주어지면 새 `session` 행을 만들지 않고 그 ID를 그대로 사용한다. Task 3이 이 파라미터로 "먼저 PENDING으로 만들어둔 세션"을 이어받아 실행한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_agent_loop.py` 파일 끝에 추가:

```python
def test_run_session_uses_provided_session_id_without_creating_new_row(conn, repo_id):
    cursor = conn.execute(
        "INSERT INTO session (repo_id, question, status, model, phase) VALUES (?, 'q', 'RUNNING', '', 3)",
        (repo_id,),
    )
    conn.commit()
    existing_id = cursor.lastrowid

    llm = FakeLLM([make_text_response("문제 없음")])
    result = run_session(conn, repo_id, "질문", llm, session_id=existing_id)

    assert result.session_id == existing_id
    count = conn.execute("SELECT COUNT(*) AS c FROM session").fetchone()["c"]
    assert count == 1
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `pytest tests/test_agent_loop.py::test_run_session_uses_provided_session_id_without_creating_new_row -v`
Expected: FAIL (`TypeError: run_session() got an unexpected keyword argument 'session_id'`)

- [ ] **Step 3: 최소 구현**

`repoview/agent/loop.py`의 `run_session` 시그니처(30~41번째 줄)를 다음으로 교체:

```python
def run_session(
    conn: sqlite3.Connection,
    repo_id: int,
    question: str,
    llm,
    *,
    model: str = "",
    phase: int = CURRENT_PHASE,
    max_iterations: int | None = None,
    max_session_tokens: int | None = None,
    embedding_client=None,
    session_id: int | None = None,
) -> SessionResult:
```

바로 아래, `session_id = _create_session(conn, repo_id, question, model, phase)` 줄(52번째 줄)을 다음으로 교체:

```python
    if session_id is None:
        session_id = _create_session(conn, repo_id, question, model, phase)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_agent_loop.py -v`
Expected: 전부 PASS (기존 테스트들은 `session_id`를 안 넘기므로 `if session_id is None:` 분기를 그대로 타 동작 변화 없음)

- [ ] **Step 5: 전체 스위트 확인 후 커밋**

Run: `pytest -q`
Expected: 전부 PASS

```bash
git add repoview/agent/loop.py tests/test_agent_loop.py
git commit -m "$(cat <<'EOF'
run_session에 session_id 옵션 파라미터 추가

세션 생성과 실행을 분리하려면 "이미 만들어둔 세션 행을 이어받아 실행"하는
모드가 필요하다. session_id를 안 넘기는 기존 호출자(eval.py, 기존
테스트)는 동작이 전혀 바뀌지 않는다.
EOF
)"
```

---

### Task 3: 세션 생성/실행 분리 + SSE 스트림 엔드포인트

이 태스크가 기능의 핵심이다. `POST /api/sessions`가 실행을 멈추는 순간과 `GET /sessions/{id}/stream`이 실행을 시작하는 순간은 서로 없어서는 안 되는 짝이라 하나의 태스크로 묶는다 — 둘 중 하나만 있으면 세션이 영원히 PENDING에 머문다.

**중요 — 테스트에서 DB 경로가 갈라지는 문제**: 새 스트림 엔드포인트는 `Depends(get_db)`(요청마다 여닫는 커넥션)를 쓰지 않고 자기 전용 커넥션을 직접 연다. `get_connection()`을 인자 없이 호출하면 `config.DB_PATH`(실제 프로덕션 DB 파일)를 열어버려서, 테스트가 격리된 `tmp_path/test.db` 대신 실제 DB 파일을 건드리는 사고가 난다. 이를 막기 위해 **오버라이드 가능한 `db_path` 의존성**을 새로 만든다 (아래 Step 3 참고) — 이러면 기존 `client` 픽스처가 `get_llm`/`get_embedding_client`를 오버라이드하는 것과 똑같은 방식으로 `db_path`도 테스트용 경로로 바꿔치기할 수 있다.

**Files:**
- Modify: `repoview/api/app.py` (imports, `create_session`, 새 `get_db_path`/`stream_session`/`_run_in_background`/`_sse`)
- Modify: `tests/test_api.py` (`client` 픽스처, 기존 동기식 계약에 의존하던 테스트들, 신규 테스트)

**Interfaces:**
- Consumes: `run_session(..., session_id=...)` (Task 2), `get_connection(db_path)`의 `busy_timeout`(Task 1)
- Produces: `POST /api/sessions` → `{"session_id": int, "status": "PENDING"}`. `GET /api/sessions/{id}/stream` → `text/event-stream`, 이벤트명 `step_started`/`step_completed`/`assistant_message`/`done`/`error` (스키마는 `docs/04-api-spec.md` 2절과 동일).

- [ ] **Step 1: 실패하는 테스트부터 작성 — POST 계약 변경**

`tests/test_api.py`의 5번째 줄 import를 다음으로 교체(`get_db_path` 추가):

```python
from repoview.api.app import app, get_db, get_db_path, get_embedding_client, get_llm
```

파일 맨 위 `import pytest` 다음 줄에 `import json`을 추가.

`client` 픽스처(10~20번째 줄)를 다음으로 교체:

```python
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
```

(`conn` 픽스처가 이미 `tmp_path / "test.db"`를 여는 것과 동일한 경로 표현식을 써야 같은 파일을 가리킨다 — `conftest.py`의 `conn` 픽스처 참고.)

`test_create_session_returns_review` 테스트(42~49번째 줄)를 다음으로 교체:

```python
def test_create_session_returns_pending_status(client):
    repo_id = client.get("/api/repos").json()[0]["id"]
    response = client.post("/api/sessions", json={"repo_id": repo_id, "question": "성능 문제?"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "PENDING"
    assert body["session_id"] > 0
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `pytest tests/test_api.py::test_create_session_returns_pending_status -v`
Expected: FAIL — `ImportError: cannot import name 'get_db_path'` (아직 `app.py`에 없음)

- [ ] **Step 3: `repoview/api/app.py` 구현**

파일 맨 위 import 블록(1~15번째 줄)을 다음으로 교체:

```python
import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from repoview.agent.llm import OpenAILLM
from repoview.agent.loop import run_session
from repoview.agent.prompts import build_repo_overview
from repoview.config import CURRENT_PHASE, DB_PATH, OPENAI_MODEL
from repoview.db import get_connection, init_db
from repoview.embedding_client import OpenAIEmbeddingClient
from repoview.eval import estimate_cost_usd

POLL_INTERVAL_S = 0.2
```

`get_embedding_client()` 함수 정의 바로 아래(현재 74~76번째 줄 부근)에 추가:

```python
def get_db_path() -> Path:
    return DB_PATH
```

`create_session` 함수(현재 102~133번째 줄) 전체를 다음으로 교체:

```python
@app.post("/api/sessions")
def create_session(
    payload: SessionRequest,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="질문이 비어 있습니다")

    repo = conn.execute("SELECT id FROM repo WHERE id = ?", (payload.repo_id,)).fetchone()
    if repo is None:
        raise HTTPException(status_code=404, detail=f"레포를 찾을 수 없습니다: {payload.repo_id}")

    cursor = conn.execute(
        "INSERT INTO session (repo_id, question, status, model, phase) VALUES (?, ?, 'PENDING', ?, ?)",
        (payload.repo_id, question, OPENAI_MODEL, CURRENT_PHASE),
    )
    conn.commit()
    return {"session_id": int(cursor.lastrowid), "status": "PENDING"}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _run_in_background(
    db_path: Path,
    session_id: int,
    repo_id: int,
    question: str,
    model: str,
    phase: int,
    llm,
    embedding_client,
) -> None:
    conn = get_connection(db_path)
    try:
        run_session(
            conn, repo_id, question, llm,
            model=model, phase=phase, session_id=session_id, embedding_client=embedding_client,
        )
    except Exception:
        pass  # run_session이 실패 시 이미 session.status를 FAILED로 기록한다 — 여기선 더 할 일 없음
    finally:
        conn.close()


@app.get("/api/sessions/{session_id}/stream")
async def stream_session(
    session_id: int,
    db_path: Path = Depends(get_db_path),
    llm=Depends(get_llm),
    embedding_client=Depends(get_embedding_client),
) -> StreamingResponse:
    conn = get_connection(db_path)
    row = conn.execute("SELECT * FROM session WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        conn.close()
        raise HTTPException(status_code=404, detail=f"세션을 찾을 수 없습니다: {session_id}")

    claim = conn.execute(
        "UPDATE session SET status = 'RUNNING' WHERE id = ? AND status = 'PENDING'", (session_id,)
    )
    conn.commit()
    if claim.rowcount == 1:
        asyncio.create_task(
            asyncio.to_thread(
                _run_in_background,
                db_path, session_id, row["repo_id"], row["question"], row["model"], row["phase"],
                llm, embedding_client,
            )
        )

    async def event_generator():
        last_step = 0
        try:
            while True:
                new_rows = conn.execute(
                    "SELECT * FROM trace_step WHERE session_id = ? AND step_no > ? ORDER BY step_no",
                    (session_id, last_step),
                ).fetchall()
                for trace_row in new_rows:
                    last_step = trace_row["step_no"]
                    if trace_row["type"] == "TOOL_CALL":
                        yield _sse("step_started", {
                            "step_no": trace_row["step_no"],
                            "type": trace_row["type"],
                            "tool_name": trace_row["tool_name"],
                            "tool_args": json.loads(trace_row["tool_args"]) if trace_row["tool_args"] else {},
                        })
                        yield _sse("step_completed", {
                            "step_no": trace_row["step_no"],
                            "result_preview": (trace_row["tool_result"] or "")[:500],
                            "latency_ms": trace_row["latency_ms"],
                            "error": trace_row["error"],
                        })
                    elif trace_row["type"] == "LLM_CALL" and trace_row["assistant_text"]:
                        yield _sse("assistant_message", {"text": trace_row["assistant_text"]})

                session_row = conn.execute(
                    "SELECT status, final_review, iteration_count, model, input_tokens, output_tokens, error "
                    "FROM session WHERE id = ?",
                    (session_id,),
                ).fetchone()
                if session_row["status"] not in ("PENDING", "RUNNING"):
                    if session_row["status"] == "FAILED":
                        yield _sse("error", {"message": session_row["error"] or "알 수 없는 오류"})
                    else:
                        cost = estimate_cost_usd(
                            session_row["model"], session_row["input_tokens"], session_row["output_tokens"]
                        )
                        yield _sse("done", {
                            "status": session_row["status"],
                            "final_review": session_row["final_review"],
                            "iteration_count": session_row["iteration_count"],
                            "cost_usd": cost,
                        })
                    return
                await asyncio.sleep(POLL_INTERVAL_S)
        finally:
            conn.close()

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

- [ ] **Step 4: POST 테스트 통과 확인**

Run: `pytest tests/test_api.py::test_create_session_returns_pending_status -v`
Expected: PASS

- [ ] **Step 5: 나머지 깨진 테스트를 새 계약에 맞게 재작성**

`tests/test_api.py`에서 아래 4개 테스트가 지금 이 시점엔 실패한다(POST가 더 이상 실행을 안 하므로). 각각을 교체한다.

`test_get_session_includes_trace`(현재 63~86번째 줄)를 다음으로 교체:

```python
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
```

이 테스트가 쓰는 `_consume_stream` 헬퍼를 `client` 픽스처 정의 바로 다음(현재 21번째 줄 뒤, `test_list_repos` 앞)에 추가:

```python
def _consume_stream(client, session_id):
    """SSE 스트림을 끝(done 또는 error)까지 소비하고 [(이벤트명, payload), ...]를 반환한다.
    TestClient의 동기 client.get()은 StreamingResponse가 완전히 끝날 때까지 블로킹하므로,
    이 함수가 반환하는 시점엔 이미 세션 실행이 끝나 있다."""
    response = client.get(f"/api/sessions/{session_id}/stream")
    events = []
    event_name = None
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event_name = line[len("event: "):]
        elif line.startswith("data: "):
            events.append((event_name, json.loads(line[len("data: "):])))
    return events, response
```

`test_unhandled_exception_still_has_cors_header`(현재 97~113번째 줄)를 다음으로 교체 — 이제 `run_session`은 백그라운드 스레드에서 실행되고 예외가 HTTP 응답을 직접 500으로 만들지 않으므로(대신 `error` 이벤트가 됨), 원래 테스트가 검증하던 "예외가 나도 CORS 헤더가 붙는다"는 스트림 응답 자체에 대해 확인하도록 목적을 옮긴다:

```python
def test_stream_emits_error_event_and_keeps_cors_header_when_run_session_crashes(client, monkeypatch):
    from repoview.api import app as app_module

    def boom(*args, **kwargs):
        raise RuntimeError("서버 내부 오류 테스트")

    monkeypatch.setattr(app_module, "run_session", boom)
    repo_id = client.get("/api/repos").json()[0]["id"]
    session_id = client.post(
        "/api/sessions", json={"repo_id": repo_id, "question": "질문"}
    ).json()["session_id"]

    response = client.get(
        f"/api/sessions/{session_id}/stream",
        headers={"Origin": "http://localhost:5173"},
    )

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "event: error" in response.text
```

`test_create_session_still_works_with_real_embedding_client_dependency_overridden`(현재 116~122번째 줄)를 다음으로 교체 — `embedding_client`는 이제 POST가 아니라 스트림 엔드포인트가 배선하므로:

```python
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
```

`test_list_sessions`(현재 89~94번째 줄)는 **수정하지 않는다** — POST가 `PENDING` 행이라도 만들면 여전히 통과한다(그냥 "세션이 최소 1개 존재하는지"만 확인하는 테스트라 상태값과 무관).

- [ ] **Step 6: 신규 동작에 대한 테스트 추가**

`tests/test_api.py` 파일 끝에 추가:

```python
def test_stream_missing_session_returns_404(client):
    response = client.get("/api/sessions/9999/stream")
    assert response.status_code == 404


def test_stream_emits_done_event_with_cost_usd(client):
    repo_id = client.get("/api/repos").json()[0]["id"]
    session_id = client.post(
        "/api/sessions", json={"repo_id": repo_id, "question": "질문"}
    ).json()["session_id"]

    events, _ = _consume_stream(client, session_id)
    done_events = [payload for name, payload in events if name == "done"]
    assert len(done_events) == 1
    assert done_events[0]["status"] == "COMPLETED"
    assert done_events[0]["final_review"] == "리뷰 결과"
    assert done_events[0]["cost_usd"] >= 0


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
```

- [ ] **Step 7: 전체 스위트 통과 확인**

Run: `pytest -q`
Expected: 전부 PASS (`tests/test_api.py`가 새로 늘어난 테스트 포함 전부 통과)

- [ ] **Step 8: 커밋**

```bash
git add repoview/api/app.py tests/test_api.py
git commit -m "$(cat <<'EOF'
세션 생성/실행을 분리하고 GET /sessions/{id}/stream(SSE) 추가

POST /api/sessions는 이제 status=PENDING 행만 만들고 즉시 반환한다.
실행은 GET /sessions/{id}/stream 연결 시 원자적 UPDATE(CAS)로 한 번만
트리거되어 백그라운드 스레드에서 돈다. 스트림 연결 자신은 이미 스텝마다
commit되는 trace_step을 0.2초 간격으로 폴링해 이벤트로 변환한다 —
run_session의 루프 구조 자체는 건드리지 않는다.
EOF
)"
```

---

### Task 4: 프론트엔드 — EventSource로 실시간 트레이스 표시

**Files:**
- Modify: `frontend/src/api.js:1` (`BASE_URL` export)
- Modify: `frontend/src/App.jsx` (`handleSubmit`, 렌더링)
- Modify: `frontend/src/App.css` (라이브 스텝 목록 스타일)

**Interfaces:**
- Consumes: `POST /api/sessions` → `{session_id, status: "PENDING"}`, `GET /sessions/{id}/stream` SSE 이벤트(Task 3)
- Produces: 없음 (최상위 UI)

이 프로젝트 프론트엔드는 테스트 프레임워크가 없는 기존 컨벤션이므로(다른 컴포넌트들도 자동 테스트 없음), 이 태스크는 TDD 대신 구현 후 브라우저로 수동 확인한다.

- [ ] **Step 1: `api.js`에서 `BASE_URL` export**

`frontend/src/api.js` 1번째 줄을 다음으로 교체:

```js
export const BASE_URL = 'http://localhost:8000/api'
```

- [ ] **Step 2: `App.jsx`의 `handleSubmit`을 스트리밍 방식으로 재작성**

`frontend/src/App.jsx` 1번째 줄 import를 다음으로 교체:

```jsx
import { useEffect, useState } from 'react'
import { BASE_URL, createSession, fetchRepos, fetchSession } from './api'
```

`const [tab, setTab] = useState('ask')` 줄 바로 다음에 상태 하나 추가:

```jsx
  const [liveSteps, setLiveSteps] = useState([])
```

`handleSubmit` 함수 전체(현재 24~40번째 줄)를 다음으로 교체:

```jsx
  const handleSubmit = async (event) => {
    event.preventDefault()
    if (!repoId || !question.trim()) return

    setLoading(true)
    setError(null)
    setSession(null)
    setLiveSteps([])

    try {
      const created = await createSession(repoId, question)
      const source = new EventSource(`${BASE_URL}/sessions/${created.session_id}/stream`)

      source.addEventListener('step_started', (e) => {
        const data = JSON.parse(e.data)
        setLiveSteps((prev) => [...prev, `${data.tool_name}(${JSON.stringify(data.tool_args)})`])
      })

      source.addEventListener('assistant_message', (e) => {
        const data = JSON.parse(e.data)
        setLiveSteps((prev) => [...prev, data.text])
      })

      source.addEventListener('done', async () => {
        source.close()
        setSession(await fetchSession(created.session_id))
        setLoading(false)
      })

      source.addEventListener('error', (e) => {
        source.close()
        setError(e.data ? JSON.parse(e.data).message : '스트림 연결이 끊어졌습니다')
        setLoading(false)
      })
    } catch (err) {
      setError(err.message)
      setLoading(false)
    }
  }
```

(`EventSource`의 `'error'` 이벤트명은 서버가 보낸 `event: error`뿐 아니라 브라우저가 순수 네트워크 문제로 자체 발생시키는 경우와 이름이 겹친다 — `e.data` 존재 여부로 "서버가 보낸 진짜 에러 페이로드"와 "연결 자체 문제"를 구분한다.)

- [ ] **Step 3: 로딩 중 라이브 트레이스 표시**

`{error && <p className="error">{error}</p>}` 줄(현재 78번째 줄) 바로 다음에 추가:

```jsx
          {loading && liveSteps.length > 0 && (
            <ul className="live-steps">
              {liveSteps.map((line, i) => (
                <li key={i}>{line}</li>
              ))}
            </ul>
          )}
```

- [ ] **Step 4: 스타일 추가**

`frontend/src/App.css` 파일 끝에 추가:

```css
.live-steps {
  list-style: none;
  padding: 0;
  margin: 12px 0;
  display: grid;
  gap: 4px;
  font-size: 13px;
  color: #555;
}

.live-steps li {
  padding: 4px 8px;
  background: #f6f6f6;
  border-radius: 4px;
}
```

- [ ] **Step 5: 빌드/린트 확인**

Run (in `frontend/`): `npm run lint && npm run build`
Expected: 에러 없음 (기존 경고 2개는 이 태스크와 무관하므로 그대로 남아있어도 무방)

- [ ] **Step 6: 브라우저로 수동 확인**

1. `uvicorn repoview.api.app:app --port 8000` 실행 (백엔드)
2. `cd frontend && npm run dev` 실행 (반드시 포트 5173 — 다른 포트로 뜨면 CORS가 막는다. 이미 5173을 다른 프로세스가 점유 중이면 PID로 찾아 종료할 것: `netstat -ano | grep :5173` → `taskkill //F //PID <pid> //T`)
3. LocalQuest 또는 Songpyeon 선택 후 질문 입력, "리뷰 요청" 클릭
4. 확인할 것:
   - 버튼이 "분석 중…"으로 바뀌고, 도구 호출/텍스트가 실시간으로 하나씩 목록에 나타나는지 (Playwright로 확인 가능 — `browser_navigate` → 질문 입력/제출 → `browser_snapshot`을 짧은 간격으로 반복 호출해 목록이 늘어나는지 확인)
   - 완료되면 기존과 동일한 "리뷰 결과" 섹션(트레이스 전체, 인용 경고 배지 포함)이 나타나는지
   - 브라우저 개발자 도구 Network 탭에서 `/stream` 요청이 하나만 나가는지 (같은 질문으로 두 번 연속 제출했을 때 각각 새 세션이 생기는 것이지, 같은 세션에 중복 연결이 일어나지 않는지)
5. 확인 후 두 서버 프로세스를 PID로 종료 (`netstat -ano`로 PID 찾아 `taskkill //F //PID <pid> //T`, 이름으로 죽이지 말 것)

- [ ] **Step 7: 커밋**

```bash
git add frontend/src/api.js frontend/src/App.jsx frontend/src/App.css
git commit -m "$(cat <<'EOF'
프론트엔드에서 EventSource로 실시간 에이전트 트레이스 표시

세션 생성 후 SSE 스트림에 연결해 도구 호출/모델 텍스트를 실시간으로
누적 표시하고, done 이벤트를 받으면 기존 fetchSession으로 전체 결과
(인용 경고 배지 포함)로 화면을 교체한다.
EOF
)"
```
