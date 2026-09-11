from repoview.tools.search import iter_code_files, search_code


def test_search_returns_file_and_line(mini_repo):
    result = search_code(mini_repo, "findOrdersByUserId")
    assert "src/main/java/com/example/UserService.java:" in result


def test_search_is_case_insensitive(mini_repo):
    assert "UserService.java" in search_code(mini_repo, "userservice")


def test_search_no_match_returns_message(mini_repo):
    assert search_code(mini_repo, "존재하지않는패턴XYZ") == "일치하는 결과가 없습니다."


def test_search_invalid_regex_returns_error_string(mini_repo):
    assert search_code(mini_repo, "[unclosed").startswith("ERROR:")


def test_search_respects_path_glob(mini_repo):
    result = search_code(mini_repo, "export", path_glob="frontend/**")
    assert "api.ts" in result
    assert "UserService.java" not in result


def test_search_respects_max_results(mini_repo):
    result = search_code(mini_repo, ".", max_results=3)
    hit_lines = [line for line in result.splitlines() if ":" in line and not line.startswith("(")]
    assert len(hit_lines) == 3


def test_iter_code_files_excludes_node_modules(mini_repo):
    paths = [p.as_posix() for p in iter_code_files(mini_repo)]
    assert not any("node_modules" in p for p in paths)


def test_iter_code_files_includes_java_and_ts(mini_repo):
    names = {p.name for p in iter_code_files(mini_repo)}
    assert "UserService.java" in names
    assert "api.ts" in names


def test_iter_code_files_skips_symlinks(mini_repo, monkeypatch):
    from pathlib import Path

    real_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda self: True if self.name == "UserService.java" else real_is_symlink(self),
    )

    names = {p.name for p in iter_code_files(mini_repo)}
    assert "UserService.java" not in names
    assert "api.ts" in names


def test_search_skips_symlinked_files(mini_repo, monkeypatch):
    from pathlib import Path

    real_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda self: True if self.name == "UserService.java" else real_is_symlink(self),
    )

    result = search_code(mini_repo, "findAllWithOrders")
    assert "UserService.java" not in result
