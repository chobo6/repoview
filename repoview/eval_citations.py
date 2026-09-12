import posixpath
import re

# ponytail: URL(예: http://x.com:80)도 "path.ext:숫자" 형태면 오탐 매칭될 수 있는
# 나이브한 정규식이다. 실제 인용 정확도 수치가 이상하면 확장자 화이트리스트로 좁힐 것.
_CITATION_RE = re.compile(r"([\w./\\-]+\.\w+):(\d+)(?:-(\d+))?")


def normalize_path(path: str) -> str:
    """경로 비교에 쓰는 표준형으로 변환한다 — repoview 전체에서 파일 경로를
    비교하는 곳(eval.py, citation_check.py)이 전부 이 함수를 거쳐야 한다.
    독립적으로 `.replace("\\\\", "/")`만 하는 구현이 여러 곳에 생기면, 같은
    인용이 위치에 따라 다르게 판정되는(예: "./foo.py" 인식 여부가 갈리는)
    불일치가 생긴다."""
    return posixpath.normpath(path.replace("\\", "/"))


def ranges_overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    """두 줄 범위가 겹치는지 확인한다."""
    return start_a <= end_b and end_a >= start_b


def extract_citations(text: str) -> list[dict]:
    """리뷰 텍스트에서 `파일경로:시작줄(-끝줄)` 형태의 인용을 모두 추출한다."""
    citations = []
    for match in _CITATION_RE.finditer(text):
        file_path, start, end = match.groups()
        citations.append(
            {
                "file_path": file_path,
                "start_line": int(start),
                "end_line": int(end) if end else int(start),
            }
        )
    return citations


def matches_file(
    citations: list[dict],
    expected_file_path: str,
    expected_line_start: int | None = None,
    expected_line_end: int | None = None,
) -> bool:
    """추출된 인용 중 expected_file_path와 일치하고(있다면) 줄 범위도 겹치는 것이 있는지 확인한다."""
    normalized = normalize_path(expected_file_path)
    line_end = expected_line_end or expected_line_start
    for c in citations:
        if normalize_path(c["file_path"]) != normalized:
            continue
        if expected_line_start is None:
            return True
        if ranges_overlap(c["start_line"], c["end_line"], expected_line_start, line_end):
            return True
    return False
