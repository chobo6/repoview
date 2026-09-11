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


def test_overview_shows_truncation_note_for_many_directories(conn):
    # Create a repo with more than MAX_TREE_ENTRIES (25) distinct directories
    cursor = conn.execute(
        "INSERT INTO repo (name, root_path, primary_language, framework, file_count) VALUES (?, ?, ?, ?, ?)",
        ("ManyDirs", "/tmp", "java", None, 30),
    )
    repo_id = cursor.lastrowid

    # Insert 30 files in distinct directories
    for i in range(30):
        conn.execute(
            "INSERT INTO repo_file (repo_id, path, language, line_count) VALUES (?, ?, ?, ?)",
            (repo_id, f"dir{i}/file.java", "java", 10),
        )
    conn.commit()

    overview = build_repo_overview(conn, repo_id)
    # Should show truncation note for directories
    assert "외 5개 디렉토리 더 있음" in overview


def test_overview_shows_truncation_note_for_many_languages(conn):
    # Create a repo with more than 6 languages
    cursor = conn.execute(
        "INSERT INTO repo (name, root_path, primary_language, framework, file_count) VALUES (?, ?, ?, ?, ?)",
        ("ManyLangs", "/tmp", "python", None, 8),
    )
    repo_id = cursor.lastrowid

    languages = ["python", "javascript", "java", "go", "rust", "cpp", "csharp", "kotlin"]
    for i, lang in enumerate(languages):
        conn.execute(
            "INSERT INTO repo_file (repo_id, path, language, line_count) VALUES (?, ?, ?, ?)",
            (repo_id, f"file{i}.ext", lang, 10),
        )
    conn.commit()

    overview = build_repo_overview(conn, repo_id)
    # Should show truncation note for languages (8 languages, max 6, so 2 more)
    assert "외 2개 언어" in overview


def test_system_prompt_includes_search_strategy_guidance():
    prompt = build_system_prompt("개요")
    assert "search_code" in prompt
    assert "search_semantic" in prompt
