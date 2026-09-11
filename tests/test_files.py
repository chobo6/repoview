from repoview.tools.files import list_directory, read_file

JAVA_PATH = "src/main/java/com/example/UserService.java"


def test_read_file_includes_line_numbers(mini_repo):
    result = read_file(mini_repo, JAVA_PATH)
    assert "1: package com.example;" in result


def test_read_file_respects_line_range(mini_repo):
    result = read_file(mini_repo, JAVA_PATH, start_line=3, end_line=5)
    assert "3: public class UserService {" in result
    assert "1: package com.example;" not in result


def test_read_file_missing_file_returns_error_string(mini_repo):
    result = read_file(mini_repo, "없는파일.java")
    assert result.startswith("ERROR:")


def test_read_file_path_escape_returns_error_string(mini_repo):
    result = read_file(mini_repo, "../../../etc/passwd")
    assert result.startswith("ERROR:")


def test_read_file_caps_at_max_lines(tmp_path):
    big = tmp_path / "big.txt"
    big.write_text("\n".join(f"line {i}" for i in range(1, 501)), encoding="utf-8")
    result = read_file(tmp_path, "big.txt")
    assert "200: line 200" in result
    assert "201: line 201" not in result
    assert "더 있음" in result


def test_list_directory_lists_entries(mini_repo):
    result = list_directory(mini_repo, "src/main")
    assert "java/" in result
    assert "resources/" in result


def test_list_directory_excludes_node_modules(mini_repo):
    result = list_directory(mini_repo, "")
    assert "node_modules" not in result


def test_list_directory_missing_dir_returns_error_string(mini_repo):
    assert list_directory(mini_repo, "없는디렉토리").startswith("ERROR:")
