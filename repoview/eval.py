import json
import sqlite3
import time

from repoview.agent.llm import LLM
from repoview.agent.loop import run_session
from repoview.config import MODEL_PRICING
from repoview.eval_citations import extract_citations, matches_file
from repoview.eval_judge import judge_case

NEGATIVE_CATEGORY = "negative"


def list_eval_cases(conn: sqlite3.Connection, repo_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM eval_case WHERE repo_id = ? ORDER BY id", (repo_id,)
    ).fetchall()


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        return 0.0
    input_price, output_price = pricing
    return (input_tokens / 1_000_000) * input_price + (output_tokens / 1_000_000) * output_price


def run_eval(
    conn: sqlite3.Connection,
    repo_id: int,
    cases: list,
    llm: LLM,
    judge_llm: LLM,
    *,
    model: str,
    phase: int,
    embedding_client=None,
) -> dict:
    if not cases:
        raise ValueError("eval_case가 없습니다. 먼저 python -m repoview.eval_seed로 시드하세요.")

    eval_run_id = _create_eval_run(conn, phase, model)

    citation_hits = 0
    citation_total = 0
    positive_total = 0
    positive_passed = 0
    negative_total = 0
    false_positives = 0
    total_cost = 0.0
    total_latency_ms = 0

    for case in cases:
        is_negative = case["category"] == NEGATIVE_CATEGORY

        started = time.monotonic()
        result = run_session(
            conn, repo_id, case["question"], llm,
            model=model, phase=phase, embedding_client=embedding_client,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        review = result.final_review or ""

        citations = extract_citations(review)
        citation_total += len(citations)
        citation_hits += sum(
            1 for c in citations if _citation_path_exists(conn, repo_id, c["file_path"])
        )

        verdict, reason = judge_case(
            judge_llm, case["question"], case["expected_finding"], review,
            is_negative=is_negative,
        )

        if is_negative:
            negative_total += 1
            detected = 0 if verdict else 1
            false_positive = 1 if verdict else 0
            false_positives += false_positive
        else:
            positive_total += 1
            file_match = (
                case["expected_file_path"] is None
                or matches_file(citations, case["expected_file_path"])
            )
            detected = 1 if (verdict and file_match) else 0
            positive_passed += detected
            false_positive = 0

        total_cost += estimate_cost_usd(model, result.input_tokens, result.output_tokens)
        total_latency_ms += latency_ms

        _record_eval_result(
            conn, eval_run_id, case["id"], result.session_id, detected, false_positive, reason,
        )

    stats = {
        "total_cases": positive_total,
        "passed_cases": positive_passed,
        "detection_rate": (positive_passed / positive_total) if positive_total else 0.0,
        "fpr": (false_positives / negative_total) if negative_total else 0.0,
        "citation_accuracy": (citation_hits / citation_total) if citation_total else 1.0,
        "avg_cost_usd": (total_cost / len(cases)) if cases else 0.0,
        "avg_latency_ms": (total_latency_ms / len(cases)) if cases else 0.0,
    }
    _finish_eval_run(conn, eval_run_id, stats)
    return stats


def _citation_path_exists(conn: sqlite3.Connection, repo_id: int, file_path: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM repo_file WHERE repo_id = ? AND path = ?",
        (repo_id, file_path.replace("\\", "/")),
    ).fetchone()
    return row is not None


def _create_eval_run(conn: sqlite3.Connection, phase: int, model: str) -> int:
    cursor = conn.execute(
        "INSERT INTO eval_run (phase, model) VALUES (?, ?)", (phase, model)
    )
    conn.commit()
    return int(cursor.lastrowid)


def _record_eval_result(
    conn: sqlite3.Connection,
    eval_run_id: int,
    eval_case_id: int,
    session_id: int,
    detected: int,
    false_positive: int,
    judge_reason: str,
) -> None:
    conn.execute(
        """
        INSERT INTO eval_result
            (eval_run_id, eval_case_id, session_id, detected, false_positive, judge_reason)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (eval_run_id, eval_case_id, session_id, detected, false_positive, judge_reason),
    )
    conn.commit()


def _finish_eval_run(conn: sqlite3.Connection, eval_run_id: int, stats: dict) -> None:
    conn.execute(
        """
        UPDATE eval_run
        SET total_cases = ?, passed_cases = ?, detection_rate = ?, notes = ?, finished_at = datetime('now')
        WHERE id = ?
        """,
        (
            stats["total_cases"],
            stats["passed_cases"],
            stats["detection_rate"],
            json.dumps(stats, ensure_ascii=False),
            eval_run_id,
        ),
    )
    conn.commit()
