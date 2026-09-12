import re
import sqlite3

from repoview.eval_citations import extract_citations

_READ_RESULT_RE = re.compile(r"^(.+?) \((\d+)-(\d+)행\)")


def verify_citations(
    conn: sqlite3.Connection, repo_id: int, session_id: int, review_text: str
) -> list[dict]:
    """리뷰의 `파일:라인` 인용 중 문제 있는 것만 골라 반환한다.

    문제 종류: 존재하지 않는 파일 / 파일 길이를 벗어난 라인 / read_file로
    확인하지 않은 인용. 문제 없으면 빈 리스트.
    """
    citations = extract_citations(review_text)
    if not citations:
        return []

    read_ranges = _read_file_ranges(conn, session_id)

    warnings = []
    for citation in citations:
        issue = _check_one(conn, repo_id, read_ranges, citation)
        if issue is not None:
            warnings.append({**citation, "issue": issue})
    return warnings


def _check_one(
    conn: sqlite3.Connection,
    repo_id: int,
    read_ranges: list[tuple[str, int, int]],
    citation: dict,
) -> str | None:
    normalized_path = citation["file_path"].replace("\\", "/")
    row = conn.execute(
        "SELECT line_count FROM repo_file WHERE repo_id = ? AND path = ?",
        (repo_id, normalized_path),
    ).fetchone()
    if row is None:
        return "존재하지 않는 파일"

    if row["line_count"] is not None and citation["start_line"] > row["line_count"]:
        return "파일 길이를 벗어난 라인"

    if not _was_read(read_ranges, normalized_path, citation["start_line"], citation["end_line"]):
        return "read_file로 확인하지 않은 인용"

    return None


def _was_read(
    read_ranges: list[tuple[str, int, int]], normalized_path: str, start_line: int, end_line: int
) -> bool:
    for path, read_start, read_end in read_ranges:
        if path.replace("\\", "/") != normalized_path:
            continue
        if read_start <= end_line and read_end >= start_line:
            return True
    return False


def _read_file_ranges(conn: sqlite3.Connection, session_id: int) -> list[tuple[str, int, int]]:
    rows = conn.execute(
        """
        SELECT tool_result FROM trace_step
        WHERE session_id = ? AND type = 'TOOL_CALL' AND tool_name = 'read_file'
        """,
        (session_id,),
    ).fetchall()

    ranges = []
    for row in rows:
        match = _READ_RESULT_RE.match(row["tool_result"] or "")
        if match:
            path, start, end = match.groups()
            ranges.append((path, int(start), int(end)))
    return ranges
