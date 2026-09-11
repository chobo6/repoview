import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from repoview.agent.prompts import build_repo_overview, build_system_prompt
from repoview.config import CURRENT_PHASE, MAX_ITERATIONS
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
) -> SessionResult:
    limit = max_iterations or MAX_ITERATIONS
    repo = conn.execute("SELECT * FROM repo WHERE id = ?", (repo_id,)).fetchone()
    if repo is None:
        raise ValueError(f"레포를 찾을 수 없습니다: {repo_id}")

    repo_root = Path(repo["root_path"])
    system_prompt = build_system_prompt(build_repo_overview(conn, repo_id))

    session_id = _create_session(conn, repo_id, question, model, phase)

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]

    step_no = 0
    totals = {"input": 0, "output": 0}
    iteration = 0

    for iteration in range(1, limit + 1):
        response = _call_llm(llm, messages, TOOL_SCHEMAS, totals)
        step_no += 1
        _record_llm_step(conn, session_id, step_no, response)
        messages.append(response.raw_message)

        if not response.tool_calls:
            return _finish(
                conn, session_id, "COMPLETED", response.text, iteration, totals
            )

        for call in response.tool_calls:
            step_no += 1
            started = time.monotonic()
            result = dispatch(repo_root, call.name, call.arguments)
            latency_ms = int((time.monotonic() - started) * 1000)

            _record_tool_step(conn, session_id, step_no, call, result, latency_ms)
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": result}
            )

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

    return _finish(conn, session_id, "CAPPED", summary.text, iteration, totals)


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
    conn, session_id: int, step_no: int, call, result: str, latency_ms: int
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
            json.dumps(call.arguments, ensure_ascii=False),
            result[:MAX_TRACE_RESULT_CHARS],
            len(result),
            latency_ms,
            result if result.startswith("ERROR:") else None,
        ),
    )
    conn.commit()


def _finish(
    conn, session_id: int, status: str, final_review: str | None, iteration: int, totals: dict
) -> SessionResult:
    conn.execute(
        """
        UPDATE session
        SET status = ?, final_review = ?, iteration_count = ?,
            input_tokens = ?, output_tokens = ?, finished_at = datetime('now')
        WHERE id = ?
        """,
        (status, final_review, iteration, totals["input"], totals["output"], session_id),
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
