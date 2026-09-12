from repoview.eval_citations import extract_citations, matches_file, normalize_path, ranges_overlap


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


def test_matches_file_normalizes_dot_slash_and_double_slash():
    # eval.py의 citation_accuracy와 citation_check.py의 인용 사후검증이 같은 입력에
    # 대해 다른 판정을 내리지 않도록, normalize_path를 공유해 "./" 및 중복 "//"도
    # 정규 경로와 같은 것으로 인식해야 한다.
    citations = [{"file_path": "./src/main/App.java", "start_line": 1, "end_line": 1}]

    assert matches_file(citations, "src//main/App.java") is True


def test_normalize_path_collapses_dot_slash_and_double_slash():
    assert normalize_path("./src/main/App.java") == "src/main/App.java"
    assert normalize_path("src//main/App.java") == "src/main/App.java"
    assert normalize_path("src\\main\\App.java") == "src/main/App.java"


def test_ranges_overlap_true_when_ranges_intersect():
    assert ranges_overlap(1, 10, 5, 15) is True
    assert ranges_overlap(1, 10, 10, 15) is True


def test_ranges_overlap_false_when_ranges_disjoint():
    assert ranges_overlap(1, 10, 11, 15) is False
