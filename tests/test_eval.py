import pytest

from repoview.agent.llm import FakeLLM, make_text_response
from repoview.eval import estimate_cost_usd, list_eval_cases, run_eval
from repoview.eval_seed import seed_eval_cases
from repoview.indexer import index_repo


@pytest.fixture
def repo_id(conn, mini_repo):
    return index_repo(conn, "MiniRepo", mini_repo)["repo_id"]


@pytest.fixture
def seeded_cases(conn, repo_id):
    cases = [
        {
            "question": "성능 관점에서 UserService에 문제 있어?",
            "expected_finding": "findAllWithOrders가 사용자마다 findOrdersByUserId를 반복 호출해 N+1 쿼리가 발생한다",
            "expected_file_path": "src/main/java/com/example/UserService.java",
            "expected_line_start": 11,
            "expected_line_end": 14,
            "category": "n_plus_one",
            "is_planted": 1,
        },
        {
            "question": "OrderController에 보안 취약점 있어?",
            "expected_finding": "OrderController.list()는 단순 위임만 하므로 보안 문제가 없다",
            "category": "negative",
            "is_planted": 0,
        },
    ]
    seed_eval_cases(conn, repo_id, cases)
    return list_eval_cases(conn, repo_id)


def test_estimate_cost_usd_uses_pricing_table():
    cost = estimate_cost_usd("gpt-4o-mini", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == pytest.approx(0.15 + 0.60)


def test_estimate_cost_usd_defaults_to_zero_for_unknown_model():
    assert estimate_cost_usd("unknown-model", 1000, 1000) == 0.0


def test_run_eval_marks_positive_case_detected_when_judge_yes_and_citation_matches(
    conn, repo_id, seeded_cases
):
    positive = [c for c in seeded_cases if c["category"] != "negative"]
    llm = FakeLLM([
        make_text_response(
            "문제: N+1 쿼리\n근거: `src/main/java/com/example/UserService.java:11-14`\n"
            "영향: 느림\n제안: JOIN 사용"
        )
    ])
    judge_llm = FakeLLM([make_text_response("YES\n리뷰가 N+1 문제를 정확히 지적했다")])

    stats = run_eval(conn, repo_id, positive, llm, judge_llm, model="gpt-4o", phase=2)

    assert stats["total_cases"] == 1
    assert stats["passed_cases"] == 1
    assert stats["detection_rate"] == 1.0

    row = conn.execute("SELECT detected, false_positive FROM eval_result").fetchone()
    assert row["detected"] == 1
    assert row["false_positive"] == 0


def test_run_eval_marks_positive_case_undetected_when_citation_missing(conn, repo_id, seeded_cases):
    positive = [c for c in seeded_cases if c["category"] != "negative"]
    llm = FakeLLM([make_text_response("문제를 발견하지 못했습니다")])
    judge_llm = FakeLLM([make_text_response("NO\n리뷰가 문제를 언급하지 않았다")])

    stats = run_eval(conn, repo_id, positive, llm, judge_llm, model="gpt-4o", phase=2)

    assert stats["passed_cases"] == 0
    assert stats["detection_rate"] == 0.0


def test_run_eval_marks_positive_case_undetected_when_citation_points_at_wrong_file(
    conn, repo_id, seeded_cases
):
    positive = [c for c in seeded_cases if c["category"] != "negative"]
    llm = FakeLLM([
        make_text_response(
            "문제: N+1 쿼리\n근거: `src/main/java/com/example/OrderController.java:10-12`\n"
            "영향: 느림\n제안: JOIN 사용"
        )
    ])
    judge_llm = FakeLLM([make_text_response("YES\n리뷰가 N+1 문제를 정확히 지적했다")])

    stats = run_eval(conn, repo_id, positive, llm, judge_llm, model="gpt-4o", phase=2)

    assert stats["passed_cases"] == 0
    row = conn.execute("SELECT detected, false_positive FROM eval_result").fetchone()
    assert row["detected"] == 0
    assert row["false_positive"] == 0


def test_run_eval_marks_false_positive_on_negative_case(conn, repo_id, seeded_cases):
    negative = [c for c in seeded_cases if c["category"] == "negative"]
    llm = FakeLLM([
        make_text_response(
            "문제: 인증 우회 가능\n근거: `src/main/java/com/example/OrderController.java:10-12`\n"
            "영향: 심각\n제안: 인증 추가"
        )
    ])
    judge_llm = FakeLLM([make_text_response("YES\n리뷰가 실재하지 않는 보안 문제를 주장했다")])

    stats = run_eval(conn, repo_id, negative, llm, judge_llm, model="gpt-4o", phase=2)

    assert stats["fpr"] == 1.0
    row = conn.execute("SELECT detected, false_positive FROM eval_result").fetchone()
    assert row["false_positive"] == 1
    assert row["detected"] == 0


def test_run_eval_computes_citation_accuracy_against_repo_file(conn, repo_id, seeded_cases):
    positive = [c for c in seeded_cases if c["category"] != "negative"]
    llm = FakeLLM([
        make_text_response(
            "문제: N+1 쿼리\n근거: `src/main/java/com/example/UserService.java:11-14`, "
            "`no/such/file.py:1`\n영향: 느림\n제안: JOIN"
        )
    ])
    judge_llm = FakeLLM([make_text_response("YES\n확인됨")])

    stats = run_eval(conn, repo_id, positive, llm, judge_llm, model="gpt-4o", phase=2)

    assert stats["citation_accuracy"] == 0.5


def test_run_eval_writes_eval_run_row_and_links_session(conn, repo_id, seeded_cases):
    llm = FakeLLM([make_text_response("문제 없음")] * len(seeded_cases))
    judge_llm = FakeLLM([make_text_response("NO\n근거 없음")] * len(seeded_cases))

    run_eval(conn, repo_id, seeded_cases, llm, judge_llm, model="gpt-4o", phase=2)

    run_row = conn.execute("SELECT * FROM eval_run").fetchone()
    assert run_row["phase"] == 2
    assert run_row["model"] == "gpt-4o"
    assert run_row["total_cases"] == 1  # negative 케이스는 total_cases 집계에서 제외
    assert run_row["finished_at"] is not None

    result_rows = conn.execute("SELECT session_id FROM eval_result").fetchall()
    assert len(result_rows) == 2
    assert all(r["session_id"] is not None for r in result_rows)


def test_run_eval_raises_on_empty_case_list(conn, repo_id):
    with pytest.raises(ValueError):
        run_eval(conn, repo_id, [], FakeLLM([]), FakeLLM([]), model="gpt-4o", phase=2)
