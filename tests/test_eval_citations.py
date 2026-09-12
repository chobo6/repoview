from repoview.eval_citations import extract_citations, matches_file


def test_extract_citations_finds_single_line_citation():
    text = "문제: 있음\n근거: `src/main/java/com/example/UserService.java:11`"

    citations = extract_citations(text)

    assert citations == [
        {"file_path": "src/main/java/com/example/UserService.java", "start_line": 11, "end_line": 11}
    ]


def test_extract_citations_finds_range_citation():
    citations = extract_citations("근거: `a/b/App.java:11-14`")

    assert citations[0]["start_line"] == 11
    assert citations[0]["end_line"] == 14


def test_extract_citations_finds_multiple_citations():
    text = "근거: `a.py:1`, 그리고 `b/c.ts:5-9`도 확인"

    citations = extract_citations(text)

    assert len(citations) == 2
    assert citations[0]["file_path"] == "a.py"
    assert citations[1]["file_path"] == "b/c.ts"


def test_extract_citations_returns_empty_list_when_no_citation():
    assert extract_citations("문제를 발견하지 못했습니다") == []


def test_matches_file_normalizes_backslashes():
    citations = [{"file_path": "src/main/App.java", "start_line": 1, "end_line": 1}]

    assert matches_file(citations, "src\\main\\App.java") is True


def test_matches_file_returns_false_when_absent():
    citations = [{"file_path": "a.py", "start_line": 1, "end_line": 1}]

    assert matches_file(citations, "b.py") is False
