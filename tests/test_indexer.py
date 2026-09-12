from repoview.indexer import detect_frameworks, index_repo


def test_index_repo_creates_repo_row(conn, mini_repo):
    summary = index_repo(conn, "MiniRepo", mini_repo)
    row = conn.execute("SELECT * FROM repo WHERE id = ?", (summary["repo_id"],)).fetchone()
    assert row["name"] == "MiniRepo"
    assert row["indexed_at"] is not None


def test_index_repo_populates_files(conn, mini_repo):
    summary = index_repo(conn, "MiniRepo", mini_repo)
    paths = {
        row["path"]
        for row in conn.execute(
            "SELECT path FROM repo_file WHERE repo_id = ?", (summary["repo_id"],)
        )
    }
    assert "src/main/java/com/example/UserService.java" in paths
    assert not any("node_modules" in path for path in paths)


def test_index_repo_detects_primary_language(conn, mini_repo):
    summary = index_repo(conn, "MiniRepo", mini_repo)
    assert summary["primary_language"] == "java"


def test_index_repo_indexes_root_config_files_as_config_language(conn, mini_repo):
    summary = index_repo(conn, "MiniRepo", mini_repo)
    row = conn.execute(
        "SELECT language FROM repo_file WHERE repo_id = ? AND path = ?",
        (summary["repo_id"], "Dockerfile"),
    ).fetchone()
    assert row is not None
    assert row["language"] == "config"
    # config 파일이 java보다 많아져도 primary_language는 여전히 java여야 한다
    # (NON_SOURCE_LANGUAGES에 포함되어 집계에서 제외됨).
    assert summary["primary_language"] == "java"


def test_index_repo_detects_framework(conn, mini_repo):
    summary = index_repo(conn, "MiniRepo", mini_repo)
    assert "spring-mvc" in summary["framework"]


def test_reindex_replaces_files_without_duplicating(conn, mini_repo):
    first = index_repo(conn, "MiniRepo", mini_repo)
    second = index_repo(conn, "MiniRepo", mini_repo)
    assert first["repo_id"] == second["repo_id"]
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM repo_file WHERE repo_id = ?", (second["repo_id"],)
    ).fetchone()["c"]
    assert count == second["file_count"]


def test_records_line_counts(conn, mini_repo):
    summary = index_repo(conn, "MiniRepo", mini_repo)
    row = conn.execute(
        "SELECT line_count FROM repo_file WHERE repo_id = ? AND path LIKE '%UserService.java'",
        (summary["repo_id"],),
    ).fetchone()
    assert row["line_count"] > 5


def test_detect_frameworks_reads_pom(mini_repo):
    assert "spring-mvc" in detect_frameworks(mini_repo)


def test_detect_frameworks_skips_symlinked_manifest(mini_repo, monkeypatch):
    from pathlib import Path

    real_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda self: True if self.name == "pom.xml" else real_is_symlink(self),
    )

    assert "spring-mvc" not in detect_frameworks(mini_repo)
