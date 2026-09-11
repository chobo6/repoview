from repoview.agent.prompts import build_repo_overview, build_system_prompt
from repoview.indexer import index_repo


def test_overview_includes_language_and_framework(conn, mini_repo):
    summary = index_repo(conn, "MiniRepo", mini_repo)
    overview = build_repo_overview(conn, summary["repo_id"])
    assert "java" in overview
    assert "spring-mvc" in overview


def test_overview_includes_directory_tree(conn, mini_repo):
    summary = index_repo(conn, "MiniRepo", mini_repo)
    overview = build_repo_overview(conn, summary["repo_id"])
    assert "src/main" in overview


def test_overview_includes_file_count(conn, mini_repo):
    summary = index_repo(conn, "MiniRepo", mini_repo)
    overview = build_repo_overview(conn, summary["repo_id"])
    assert str(summary["file_count"]) in overview


def test_system_prompt_contains_citation_rule():
    prompt = build_system_prompt("개요")
    assert "파일경로:라인" in prompt
    assert "read_file" in prompt


def test_system_prompt_embeds_overview():
    assert "내가만든개요" in build_system_prompt("내가만든개요")
