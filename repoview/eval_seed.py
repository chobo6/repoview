"""eval_case 시드 CLI: python -m repoview.eval_seed eval_cases/mini_repo.json --repo MiniRepo"""

import argparse
import json

from repoview.db import get_connection, get_repo_or_exit, init_db


def seed_eval_cases(conn, repo_id: int, cases: list[dict]) -> int:
    inserted = 0
    for case in cases:
        existing = conn.execute(
            "SELECT 1 FROM eval_case WHERE repo_id = ? AND question = ?",
            (repo_id, case["question"]),
        ).fetchone()
        if existing:
            continue

        conn.execute(
            """
            INSERT INTO eval_case
                (repo_id, question, expected_finding, expected_file_path,
                 expected_line_start, expected_line_end, category, is_planted)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                repo_id,
                case["question"],
                case["expected_finding"],
                case.get("expected_file_path"),
                case.get("expected_line_start"),
                case.get("expected_line_end"),
                case.get("category"),
                1 if case.get("is_planted") else 0,
            ),
        )
        inserted += 1

    conn.commit()
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="JSON 파일에서 eval_case를 시드한다")
    parser.add_argument("cases_file", help="eval case JSON 파일 경로")
    parser.add_argument("--repo", required=True, help="레포 이름")
    args = parser.parse_args()

    conn = get_connection()
    init_db(conn)

    repo_id = get_repo_or_exit(conn, args.repo)

    with open(args.cases_file, encoding="utf-8") as f:
        cases = json.load(f)

    count = seed_eval_cases(conn, repo_id, cases)
    print(f"[완료] {args.repo}: {count}개 eval_case 시드 (건너뜀 {len(cases) - count}개)")

    conn.close()


if __name__ == "__main__":
    main()
