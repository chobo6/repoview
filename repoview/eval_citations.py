import re

# ponytail: URL(예: http://x.com:80)도 "path.ext:숫자" 형태면 오탐 매칭될 수 있는
# 나이브한 정규식이다. 실제 인용 정확도 수치가 이상하면 확장자 화이트리스트로 좁힐 것.
_CITATION_RE = re.compile(r"([\w./\\-]+\.\w+):(\d+)(?:-(\d+))?")


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


def matches_file(citations: list[dict], expected_file_path: str) -> bool:
    """추출된 인용 중 expected_file_path와 일치하는 것이 있는지 확인한다."""
    normalized = expected_file_path.replace("\\", "/")
    return any(c["file_path"].replace("\\", "/") == normalized for c in citations)
