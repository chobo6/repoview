"""인덱싱 CLI: python -m repoview.index --repo LocalQuest"""

import argparse

from repoview.config import REPOS
from repoview.db import get_connection, init_db
from repoview.indexer import index_repo


def main() -> None:
    parser = argparse.ArgumentParser(description="레포를 인덱싱한다")
    parser.add_argument("--repo", choices=sorted(REPOS), help="인덱싱할 레포 이름 (생략 시 전체)")
    args = parser.parse_args()

    targets = [args.repo] if args.repo else sorted(REPOS)

    conn = get_connection()
    init_db(conn)

    for name in targets:
        root = REPOS[name]
        if not root.exists():
            print(f"[건너뜀] {name}: 경로가 없습니다 - {root}")
            continue
        summary = index_repo(conn, name, root)
        print(
            f"[완료] {name}: {summary['file_count']}개 파일, "
            f"언어={summary['primary_language']}, 프레임워크={summary['framework'] or '미감지'}"
        )

    conn.close()


if __name__ == "__main__":
    main()
