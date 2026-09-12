import argparse
import json
import sqlite3
import time

from repoview.agent.llm import LLM, OpenAILLM
from repoview.agent.loop import run_session
from repoview.config import JUDGE_MODEL, MODEL_PRICING, OPENAI_MODEL, REPOS
from repoview.db import get_connection, init_db
from repoview.embedding_client import OpenAIEmbeddingClient
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
        session_id = None
        detected = 0
        false_positive = 0
        reason = ""
        try:
            result = run_session(
                conn, repo_id, case["question"], llm,
                model=model, phase=phase, embedding_client=embedding_client,
            )
            session_id = result.session_id
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
                detected = 0 if verdict else 1
                false_positive = 1 if verdict else 0
            else:
                file_match = (
                    case["expected_file_path"] is None
                    or matches_file(
                        citations, case["expected_file_path"],
                        case["expected_line_start"], case["expected_line_end"],
                    )
                )
                detected = 1 if (verdict and file_match) else 0

            total_cost += estimate_cost_usd(model, result.input_tokens, result.output_tokens)
            total_latency_ms += latency_ms
        except Exception as exc:
            reason = f"ERROR: {type(exc).__name__}: {exc}"

        if is_negative:
            negative_total += 1
            false_positives += false_positive
        else:
            positive_total += 1
            positive_passed += detected

        _record_eval_result(
            conn, eval_run_id, case["id"], session_id, detected, false_positive, reason,
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


def validate_judge_model(model: str, judge_model: str) -> None:
    if model == judge_model:
        raise ValueError(
            f"판정 모델({judge_model})이 평가 대상 모델({model})과 같습니다. "
            "REPOVIEW_JUDGE_MODEL을 다른 모델로 설정하세요."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase별 리뷰 탐지율을 측정한다")
    parser.add_argument("--repo", required=True, choices=sorted(REPOS), help="평가할 레포 이름")
    parser.add_argument(
        "--phase", type=int, required=True, choices=[2, 3],
        help="평가할 Phase (2=키워드 검색만, 3=RAG 포함)",
    )
    args = parser.parse_args()

    try:
        validate_judge_model(OPENAI_MODEL, JUDGE_MODEL)

        conn = get_connection()
        init_db(conn)

        row = conn.execute("SELECT id FROM repo WHERE name = ?", (args.repo,)).fetchone()
        if row is None:
            raise SystemExit(f"{args.repo}가 인덱싱되지 않았습니다. 먼저 python -m repoview.index를 실행하세요.")
        repo_id = row["id"]

        cases = list_eval_cases(conn, repo_id)

        llm = OpenAILLM(model=OPENAI_MODEL)
        judge_llm = OpenAILLM(model=JUDGE_MODEL)
        embedding_client = OpenAIEmbeddingClient() if args.phase == 3 else None

        stats = run_eval(
            conn, repo_id, cases, llm, judge_llm,
            model=OPENAI_MODEL, phase=args.phase, embedding_client=embedding_client,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    print(
        f"[Phase {args.phase}] {args.repo}: 탐지율 {stats['detection_rate']:.0%}, "
        f"오탐율 {stats['fpr']:.0%}, 인용 정확도 {stats['citation_accuracy']:.0%}, "
        f"평균 비용 ${stats['avg_cost_usd']:.4f}, 평균 지연 {stats['avg_latency_ms']:.0f}ms"
    )

    conn.close()


if __name__ == "__main__":
    main()
