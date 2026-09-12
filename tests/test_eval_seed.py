from repoview.eval_seed import seed_eval_cases
from repoview.indexer import index_repo


def test_seed_eval_cases_inserts_rows(conn, mini_repo):
    repo_id = index_repo(conn, "MiniRepo", mini_repo)["repo_id"]
    cases = [
        {
            "question": "성능 문제 있어?",
            "expected_finding": "N+1 쿼리",
            "expected_file_path": "src/main/java/com/example/UserService.java",
            "expected_line_start": 11,
            "expected_line_end": 14,
            "category": "n_plus_one",
            "is_planted": 1,
        }
    ]

    inserted = seed_eval_cases(conn, repo_id, cases)

    assert inserted == 1
    row = conn.execute("SELECT * FROM eval_case WHERE repo_id = ?", (repo_id,)).fetchone()
    assert row["question"] == "성능 문제 있어?"
    assert row["is_planted"] == 1


def test_seed_eval_cases_is_idempotent_on_question(conn, mini_repo):
    repo_id = index_repo(conn, "MiniRepo", mini_repo)["repo_id"]
    cases = [
        {"question": "중복 질문", "expected_finding": "아무거나", "category": "negative", "is_planted": 0}
    ]

    first = seed_eval_cases(conn, repo_id, cases)
    second = seed_eval_cases(conn, repo_id, cases)

    assert first == 1
    assert second == 0
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM eval_case WHERE repo_id = ?", (repo_id,)
    ).fetchone()["n"]
    assert count == 1


def test_seed_eval_cases_defaults_optional_fields_to_none(conn, mini_repo):
    repo_id = index_repo(conn, "MiniRepo", mini_repo)["repo_id"]
    cases = [{"question": "질문", "expected_finding": "설명", "category": "negative", "is_planted": 0}]

    seed_eval_cases(conn, repo_id, cases)

    row = conn.execute("SELECT * FROM eval_case WHERE repo_id = ?", (repo_id,)).fetchone()
    assert row["expected_file_path"] is None
    assert row["expected_line_start"] is None


import json
from pathlib import Path


def test_mini_repo_eval_cases_file_is_valid_json():
    path = Path(__file__).parent.parent / "eval_cases" / "mini_repo.json"
    cases = json.loads(path.read_text(encoding="utf-8"))

    assert len(cases) >= 2
    assert any(c["category"] == "negative" for c in cases)
    assert any(c.get("is_planted") == 1 for c in cases)
