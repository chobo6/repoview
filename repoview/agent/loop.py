import json
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import chromadb
from chromadb.errors import NotFoundError

from repoview.agent.prompts import build_repo_overview, build_system_prompt
from repoview.citation_check import verify_citations
from repoview.config import CHROMA_PATH, CURRENT_PHASE, MAX_ITERATIONS, MAX_SESSION_TOKENS
from repoview.embedder import collection_name
from repoview.tools import TOOL_SCHEMAS, dispatch

MAX_TRACE_RESULT_CHARS = 8_000


@dataclass
class SessionResult:
    session_id: int
    status: str
    final_review: str | None
    iteration_count: int
    input_tokens: int
    output_tokens: int


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
    limit = max_iterations if max_iterations is not None else MAX_ITERATIONS
    token_limit = max_session_tokens if max_session_tokens is not None else MAX_SESSION_TOKENS
    repo = conn.execute("SELECT * FROM repo WHERE id = ?", (repo_id,)).fetchone()
    if repo is None:
        raise ValueError(f"레포를 찾을 수 없습니다: {repo_id}")

    repo_root = Path(repo["root_path"])
    system_prompt = build_system_prompt(build_repo_overview(conn, repo_id))
    collection = _resolve_collection(repo["name"], embedding_client)

    if session_id is None:
        session_id = _create_session(conn, repo_id, question, model, phase)

    step_no = 0
    totals = {"input": 0, "output": 0}
    iteration = 0

    try:
        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ]

        call_counts: dict[tuple[str, str], int] = {}

        for iteration in range(1, limit + 1):
            response = _call_llm(llm, messages, TOOL_SCHEMAS, totals)
            step_no += 1
            _record_llm_step(conn, session_id, step_no, response)
            messages.append(response.raw_message)

            if not response.tool_calls:
                return _finish(
                    conn, repo_id, session_id, "COMPLETED", response.text, iteration, totals
                )

            for call in response.tool_calls:
                step_no += 1
                started = time.monotonic()
                result = dispatch(
                    repo_root,
                    call.name,
                    call.arguments,
                    collection=collection,
                    embedding_client=embedding_client,
                )
                latency_ms = int((time.monotonic() - started) * 1000)

                args_json = json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)
                call_key = (call.name, args_json)
                call_counts[call_key] = call_counts.get(call_key, 0) + 1
                if call_counts[call_key] >= 3:
                    result += "\n\n이미 동일한 검색을 수행했습니다. 다른 접근을 시도하거나 결론을 내리세요."

                _record_tool_step(conn, session_id, step_no, call, args_json, result, latency_ms)
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": result}
                )

            # 이 검사는 라운드가 끝난 뒤에만 수행되고 그 뒤에 요약 호출이 한 번 더 붙으므로,
            # 하드 상한이 아니라 트리거 임계값이다 — 실제 토큰 사용량은 한 라운드 + 요약 호출 1회만큼
            # token_limit을 넘어설 수 있다 (의도된 설계).
            if totals["input"] + totals["output"] > token_limit:
                break

        # 반복 상한 도달: 도구 없이 한 번 더 호출해 지금까지 찾은 내용을 정리시킨다.
        messages.append(
            {
                "role": "user",
                "content": "탐색 한도에 도달했습니다. 더 이상 도구를 호출하지 말고, "
                "지금까지 확인한 근거만으로 결론을 정리해 주세요.",
            }
        )
        summary = _call_llm(llm, messages, [], totals)
        step_no += 1
        _record_llm_step(conn, session_id, step_no, summary)

        return _finish(conn, repo_id, session_id, "CAPPED", summary.text, iteration, totals)
    except Exception as exc:
        _finish(
            conn, repo_id, session_id, "FAILED", None, iteration, totals,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise


def _resolve_collection(repo_name: str, embedding_client):
    """embedding_client가 없으면 Chroma를 아예 열지 않는다 — Phase 2 스타일 호출에서
    불필요한 파일시스템 접근을 피하기 위해서다. 컬렉션이 없으면 None을 반환하고,
    search_semantic이 이를 "아직 임베딩되지 않음" 에러로 안내한다.
    """
    if embedding_client is None:
        return None

    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    try:
        return client.get_collection(name=collection_name(repo_name))
    except NotFoundError:
        return None


def _call_llm(llm, messages: list[dict], tools: list[dict], totals: dict):
    response = llm.call(messages, tools)
    totals["input"] += response.input_tokens
    totals["output"] += response.output_tokens
    return response


def _create_session(
    conn: sqlite3.Connection, repo_id: int, question: str, model: str, phase: int
) -> int:
    cursor = conn.execute(
        "INSERT INTO session (repo_id, question, status, model, phase) VALUES (?, ?, 'RUNNING', ?, ?)",
        (repo_id, question, model, phase),
    )
    conn.commit()
    return int(cursor.lastrowid)


def _record_llm_step(conn, session_id: int, step_no: int, response) -> None:
    conn.execute(
        """
        INSERT INTO trace_step
            (session_id, step_no, type, assistant_text, input_tokens, output_tokens)
        VALUES (?, ?, 'LLM_CALL', ?, ?, ?)
        """,
        (
            session_id,
            step_no,
            response.text,
            response.input_tokens,
            response.output_tokens,
        ),
    )
    conn.commit()


def _record_tool_step(
    conn, session_id: int, step_no: int, call, args_json: str, result: str, latency_ms: int
) -> None:
    conn.execute(
        """
        INSERT INTO trace_step
            (session_id, step_no, type, tool_name, tool_args,
             tool_result, tool_result_length, latency_ms, error)
        VALUES (?, ?, 'TOOL_CALL', ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            step_no,
            call.name,
            args_json,
            result[:MAX_TRACE_RESULT_CHARS],
            len(result),
            latency_ms,
            result if result.startswith("ERROR:") else None,
        ),
    )
    conn.commit()


def _finish(
    conn,
    repo_id: int,
    session_id: int,
    status: str,
    final_review: str | None,
    iteration: int,
    totals: dict,
    error: str | None = None,
) -> SessionResult:
    # 인용 검증/기록을 상태 커밋보다 먼저 수행한다 — 반대 순서(예전 구현)였을 때는
    # SSE 폴링이 status만 반영된 순간을 관측해 citation_warnings를 아직 NULL인 채로
    # 읽어가는 레이스가 있었다. 상태 UPDATE를 마지막에 커밋하면, 외부에서 터미널
    # 상태를 관측하는 시점엔 citation_warnings도 이미 반영되어 있음이 보장된다.
    # 인용 검증/기록 자체가 실패해도(어드바이저리 기능, 예: 마이그레이션 누락) 이미
    # 완료된 세션의 status/final_review 커밋은 항상 뒤따라야 하므로 별도로 감싼다.
    if final_review:
        try:
            warnings = verify_citations(conn, repo_id, session_id, final_review)
            if warnings:
                _record_citation_warnings(conn, session_id, warnings)
        except Exception as exc:
            print(
                f"[경고] 인용 사후검증 실패 (session_id={session_id}): {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )

    conn.execute(
        """
        UPDATE session
        SET status = ?, final_review = ?, iteration_count = ?,
            input_tokens = ?, output_tokens = ?, error = ?, finished_at = datetime('now')
        WHERE id = ?
        """,
        (status, final_review, iteration, totals["input"], totals["output"], error, session_id),
    )
    conn.commit()

    return SessionResult(
        session_id=session_id,
        status=status,
        final_review=final_review,
        iteration_count=iteration,
        input_tokens=totals["input"],
        output_tokens=totals["output"],
    )


def _record_citation_warnings(conn, session_id: int, warnings: list[dict]) -> None:
    conn.execute(
        "UPDATE session SET citation_warnings = ? WHERE id = ?",
        (json.dumps(warnings, ensure_ascii=False), session_id),
    )
    conn.commit()
