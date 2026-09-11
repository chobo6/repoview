import sqlite3
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from repoview.agent.llm import OpenAILLM
from repoview.agent.loop import run_session
from repoview.agent.prompts import build_repo_overview
from repoview.config import CURRENT_PHASE, OPENAI_MODEL
from repoview.db import get_connection, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """스키마 초기화는 서버 기동 시 한 번만 한다."""
    conn = get_connection()
    init_db(conn)
    conn.close()
    yield


app = FastAPI(title="RepoView API", lifespan=lifespan)

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
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": str(exc.status_code), "message": exc.detail}},
    )


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
