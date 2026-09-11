import pytest

from repoview.tools.paths import PathEscapeError, resolve_safe_path


def test_resolves_normal_relative_path(mini_repo):
    result = resolve_safe_path(mini_repo, "pom.xml")
    assert result == (mini_repo / "pom.xml").resolve()


def test_empty_path_resolves_to_repo_root(mini_repo):
    assert resolve_safe_path(mini_repo, "") == mini_repo.resolve()


def test_nested_path_is_allowed(mini_repo):
    result = resolve_safe_path(mini_repo, "src/main/java/com/example/UserService.java")
    assert result.name == "UserService.java"


def test_parent_traversal_is_rejected(mini_repo):
    with pytest.raises(PathEscapeError):
        resolve_safe_path(mini_repo, "../../../etc/passwd")


def test_sneaky_traversal_inside_path_is_rejected(mini_repo):
    with pytest.raises(PathEscapeError):
        resolve_safe_path(mini_repo, "src/../../outside.txt")


def test_absolute_path_is_rejected(mini_repo, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    with pytest.raises(PathEscapeError):
        resolve_safe_path(mini_repo, str(outside))
