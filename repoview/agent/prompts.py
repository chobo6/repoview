import sqlite3
from collections import Counter

TREE_DEPTH = 2
MAX_TREE_ENTRIES = 25


def build_repo_overview(conn: sqlite3.Connection, repo_id: int) -> str:
    repo = conn.execute("SELECT * FROM repo WHERE id = ?", (repo_id,)).fetchone()
    if repo is None:
        raise ValueError(f"레포를 찾을 수 없습니다: {repo_id}")

    paths = [
        row["path"]
        for row in conn.execute(
            "SELECT path FROM repo_file WHERE repo_id = ?", (repo_id,)
        )
    ]

    directory_counts: Counter[str] = Counter()
    for path in paths:
        parts = path.split("/")
        directory = "/".join(parts[:TREE_DEPTH]) if len(parts) > TREE_DEPTH else "/".join(parts[:-1]) or "."
        directory_counts[directory] += 1

    tree_lines = [
        f"  {directory}/ ({count}개 파일)"
        for directory, count in directory_counts.most_common(MAX_TREE_ENTRIES)
    ]

    if len(directory_counts) > MAX_TREE_ENTRIES:
        tree_lines.append(f"  ... (외 {len(directory_counts) - MAX_TREE_ENTRIES}개 디렉토리 더 있음)")

    language_counts = Counter(
        row["language"]
        for row in conn.execute(
            "SELECT language FROM repo_file WHERE repo_id = ?", (repo_id,)
        )
    )
    language_summary = ", ".join(
        f"{language} {count}개" for language, count in language_counts.most_common(6)
    )

    if len(language_counts) > 6:
        language_summary += f" 외 {len(language_counts) - 6}개 언어"

    return "\n".join(
        [
            f"[레포] {repo['name']}",
            f"주 언어: {repo['primary_language'] or '미상'}",
            f"프레임워크: {repo['framework'] or '미감지'}",
            f"전체 파일 수: {repo['file_count']}",
            f"언어 분포: {language_summary}",
            "주요 디렉토리:",
            *tree_lines,
        ]
    )


def build_system_prompt(overview: str) -> str:
    return f"""당신은 코드 리뷰 에이전트입니다. 주어진 도구로 실제 코드를 확인한 뒤에만 답변합니다.

{overview}

[도구 사용 지침]
- search_code로 먼저 관련 코드를 찾으세요. 정확한 함수명·키워드를 모르거나
  search_code 결과가 없거나 부족하면 search_semantic을 사용하세요.
- 후보를 좁힌 뒤 read_file로 실제 내용을 확인하세요.
- read_file은 한 번에 최대 200행을 반환합니다. 필요한 범위를 지정해 좁게 읽으세요.
- 구조를 더 봐야 하면 list_directory를 사용하세요. 레포 개요는 위에 이미 주어져 있습니다.
- 충분한 근거를 모았다면 더 도구를 호출하지 말고 최종 답변을 작성하세요.

[인용 규칙]
- 모든 지적에는 `파일경로:라인` 형식의 근거를 포함해야 합니다.
- read_file로 실제로 확인하지 않은 파일은 인용하지 마세요.
- 근거를 찾지 못했다면 추측하지 말고 찾지 못했다고 답하세요.

[출력 형식]
발견한 항목마다 다음을 작성하세요.
- 문제: 무엇이 문제인지
- 근거: `파일경로:라인`
- 영향: 실제로 어떤 상황에서 문제가 되는지
- 제안: 어떻게 고칠 수 있는지

문제를 찾지 못했다면 "해당 관점에서 문제를 발견하지 못했습니다"라고 명확히 답하세요."""
