from pathlib import Path

from repoview.embedder import chunk_file, chunk_repo


def test_chunk_file_returns_single_chunk_for_short_file(tmp_path):
    file_path = tmp_path / "short.py"
    file_path.write_text("\n".join(f"line {i}" for i in range(1, 11)), encoding="utf-8")

    chunks = chunk_file(file_path, tmp_path)

    assert len(chunks) == 1
    assert chunks[0]["file_path"] == "short.py"
    assert chunks[0]["start_line"] == 1
    assert chunks[0]["end_line"] == 10
    assert "line 1" in chunks[0]["text"]
    assert "line 10" in chunks[0]["text"]


def test_chunk_file_returns_empty_list_for_empty_file(tmp_path):
    file_path = tmp_path / "empty.py"
    file_path.write_text("", encoding="utf-8")

    assert chunk_file(file_path, tmp_path) == []


def test_chunk_file_splits_long_file_with_overlap(tmp_path):
    file_path = tmp_path / "long.py"
    file_path.write_text("\n".join(f"line {i}" for i in range(1, 121)), encoding="utf-8")

    chunks = chunk_file(file_path, tmp_path)

    assert [(c["start_line"], c["end_line"]) for c in chunks] == [
        (1, 50),
        (41, 90),
        (81, 120),
    ]
    # 겹치는 구간(41~50행)이 두 청크 모두에 실제로 들어있는지 확인한다.
    assert "line 45" in chunks[0]["text"]
    assert "line 45" in chunks[1]["text"]


def test_chunk_file_relative_path_uses_posix_separators(tmp_path):
    nested = tmp_path / "src" / "main" / "App.java"
    nested.parent.mkdir(parents=True)
    nested.write_text("class App {}", encoding="utf-8")

    chunks = chunk_file(nested, tmp_path)

    assert chunks[0]["file_path"] == "src/main/App.java"


def test_chunk_repo_uses_iter_code_files(mini_repo):
    chunks = chunk_repo(mini_repo)

    file_paths = {c["file_path"] for c in chunks}
    assert "src/main/java/com/example/UserService.java" in file_paths
    assert not any("node_modules" in path for path in file_paths)
