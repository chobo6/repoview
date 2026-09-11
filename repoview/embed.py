"""임베딩 CLI: python -m repoview.embed --repo LocalQuest"""

import argparse
from pathlib import Path

from repoview.config import CHROMA_PATH, REPOS
from repoview.db import get_connection, init_db
from repoview.embedder import embed_repo
from repoview.embedding_client import OpenAIEmbeddingClient


def main() -> None:
    parser = argparse.ArgumentParser(description="레포를 임베딩한다")
    parser.add_argument("--repo", choices=sorted(REPOS), help="임베딩할 레포 이름 (생략 시 전체)")
    args = parser.parse_args()

    targets = [args.repo] if args.repo else sorted(REPOS)

    conn = get_connection()
    init_db(conn)
    embedding_client = OpenAIEmbeddingClient()

    for name in targets:
        row = conn.execute("SELECT id, root_path FROM repo WHERE name = ?", (name,)).fetchone()
        if row is None:
            print(f"[건너뜀] {name}: 먼저 python -m repoview.index로 인덱싱하세요")
            continue

        summary = embed_repo(conn, row["id"], name, Path(row["root_path"]), embedding_client, CHROMA_PATH)
        print(f"[완료] {name}: {summary['chunk_count']}개 청크 임베딩")

    conn.close()


if __name__ == "__main__":
    main()
