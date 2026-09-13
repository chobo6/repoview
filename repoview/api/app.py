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


def _error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": str(status_code), "message": message}},
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """스키마 초기화는 서버 기동 시 한 번만 한다."""
    conn = get_connection()
    init_db(conn)
    conn.close()
    yield


app = FastAPI(title="RepoView API", lifespan=lifespan)


@app.middleware("http")
async def catch_unhandled_exceptions(request: Request, call_next):
    """CORSMiddleware보다 먼저 등록해야 CORS가 이 미들웨어를 감싸서
    처리되지 않은 예외에도 CORS 헤더가 붙는다 (Starlette는 미들웨어를
    등록의 역순으로 적용한다)."""
    try:
        return await call_next(request)
    except Exception as exc:
        return _error_response(500, f"{type(exc).__name__}: {exc}")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class SessionRequest(BaseModel):
    repo_id: int
    question: str


def get_db():
    """요청마다 연결을 열고 반드시 닫는다. 스키마 초기화는 startup에서 한 번만 한다."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def get_llm():
    return OpenAILLM(OPENAI_MODEL)


def get_embedding_client():
    return OpenAIEmbeddingClient()


def get_db_path() -> Path:
    return DB_PATH


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return _error_response(exc.status_code, exc.detail)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return _error_response(422, "요청 형식이 올바르지 않습니다")


@app.get("/api/repos")
def list_repos(conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    rows = conn.execute("SELECT * FROM repo ORDER BY name").fetchall()
    return [dict(row) for row in rows]


@app.get("/api/repos/{repo_id}")
def get_repo(repo_id: int, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    row = conn.execute("SELECT * FROM repo WHERE id = ?", (repo_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"레포를 찾을 수 없습니다: {repo_id}")
    return {**dict(row), "overview": build_repo_overview(conn, repo_id)}


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
    except Exception as exc:
        # run_session은 보통 실패 시 이미 session.status를 FAILED로 기록하지만,
        # 그 내부 try 블록에 들어가기도 전에 터지는 예외(레포 조회 실패 등)나
        # run_session 자체가 통째로 대체된 경우(테스트의 monkeypatch 등)엔
        # 아무도 상태를 못 바꾼다. 그러면 status가 영원히 RUNNING에 머물러
        # SSE 폴링 루프가 끝나는 조건을 절대 못 만나 무한 대기하게 된다.
        # 그래서 여기서 최후의 안전망으로 한 번 더 FAILED 처리한다 —
        # 이미 FAILED/COMPLETED로 끝났다면 WHERE status='RUNNING' 조건이
        # 막아주므로 덮어쓰지 않는다.
        conn.execute(
            "UPDATE session SET status = 'FAILED', error = ? WHERE id = ? AND status = 'RUNNING'",
            (str(exc), session_id),
        )
        conn.commit()
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


@app.get("/api/sessions")
def list_sessions(
    repo_id: int | None = None,
    phase: int | None = None,
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    query = "SELECT * FROM session WHERE 1 = 1"
    params: list = []
    if repo_id is not None:
        query += " AND repo_id = ?"
        params.append(repo_id)
    if phase is not None:
        query += " AND phase = ?"
        params.append(phase)
    query += " ORDER BY started_at DESC LIMIT 50"

    return [dict(row) for row in conn.execute(query, params)]


@app.get("/api/sessions/{session_id}")
def get_session(session_id: int, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    row = conn.execute("SELECT * FROM session WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"세션을 찾을 수 없습니다: {session_id}")

    trace = conn.execute(
        "SELECT * FROM trace_step WHERE session_id = ? ORDER BY step_no", (session_id,)
    ).fetchall()
    return {**dict(row), "trace": [dict(step) for step in trace]}


def _drop_notes(row: dict) -> dict:
    """notes는 eval.py가 감사용으로 남기는 원본 JSON 블롭이다 — fpr/citation_accuracy/
    avg_cost_usd/avg_latency_ms는 전부 eval_run의 실제 컬럼으로도 저장되므로(하위
    호환을 위해 마이그레이션이 notes에서도 백필한다), API 응답에서는 이 블롭만 뺀다."""
    row.pop("notes", None)
    return row


@app.get("/api/evals/runs")
def list_eval_runs(
    repo_id: int | None = None,
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    query = """
        SELECT eval_run.*, repo.name AS repo_name
        FROM eval_run
        LEFT JOIN repo ON repo.id = eval_run.repo_id
        WHERE 1 = 1
    """
    params: list = []
    if repo_id is not None:
        query += " AND eval_run.repo_id = ?"
        params.append(repo_id)
    query += " ORDER BY eval_run.started_at DESC"

    return [_drop_notes(dict(row)) for row in conn.execute(query, params)]


@app.get("/api/evals/runs/{run_id}")
def get_eval_run(run_id: int, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    run_row = conn.execute("SELECT * FROM eval_run WHERE id = ?", (run_id,)).fetchone()
    if run_row is None:
        raise HTTPException(status_code=404, detail=f"eval 실행을 찾을 수 없습니다: {run_id}")

    run = _drop_notes(dict(run_row))

    results = conn.execute(
        """
        SELECT
            eval_result.eval_case_id, eval_result.session_id,
            eval_result.detected, eval_result.false_positive, eval_result.judge_reason,
            eval_case.question, eval_case.category, eval_case.is_planted,
            eval_case.expected_finding
        FROM eval_result
        JOIN eval_case ON eval_case.id = eval_result.eval_case_id
        WHERE eval_result.eval_run_id = ?
        ORDER BY eval_result.id
        """,
        (run_id,),
    ).fetchall()
    run["results"] = [dict(r) for r in results]
    return run
