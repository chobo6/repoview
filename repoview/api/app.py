import sqlite3
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from repoview.agent.llm import OpenAILLM
from repoview.agent.loop import run_session
from repoview.agent.prompts import build_repo_overview
from repoview.config import CURRENT_PHASE, OPENAI_MODEL
from repoview.db import get_connection, init_db


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
    llm=Depends(get_llm),
) -> dict:
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="질문이 비어 있습니다")

    repo = conn.execute("SELECT id FROM repo WHERE id = ?", (payload.repo_id,)).fetchone()
    if repo is None:
        raise HTTPException(status_code=404, detail=f"레포를 찾을 수 없습니다: {payload.repo_id}")

    result = run_session(
        conn, payload.repo_id, question, llm, model=OPENAI_MODEL, phase=CURRENT_PHASE
    )
    return {
        "session_id": result.session_id,
        "status": result.status,
        "final_review": result.final_review,
        "iteration_count": result.iteration_count,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
    }


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
