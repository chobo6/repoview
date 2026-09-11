from pathlib import Path

import chromadb

from repoview.embedder import chunk_file, chunk_repo, collection_name, embed_repo
from repoview.embedding_client import FakeEmbeddingClient
from repoview.indexer import index_repo


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


def test_embed_repo_populates_chroma_collection(conn, mini_repo, tmp_path):
    summary = index_repo(conn, "MiniRepo", mini_repo)
    embedder = FakeEmbeddingClient()
    chroma_path = tmp_path / "chroma"

    result = embed_repo(conn, summary["repo_id"], "MiniRepo", mini_repo, embedder, chroma_path)

    assert result["repo_id"] == summary["repo_id"]
    assert result["chunk_count"] > 0

    client = chromadb.PersistentClient(path=str(chroma_path))
    collection = client.get_collection(name=collection_name("MiniRepo"))
    assert collection.count() == result["chunk_count"]


def test_embed_repo_updates_chunk_count_in_db(conn, mini_repo, tmp_path):
    summary = index_repo(conn, "MiniRepo", mini_repo)
    embed_repo(conn, summary["repo_id"], "MiniRepo", mini_repo, FakeEmbeddingClient(), tmp_path / "chroma")

    row = conn.execute("SELECT chunk_count FROM repo WHERE id = ?", (summary["repo_id"],)).fetchone()
    assert row["chunk_count"] > 0


def test_re_embed_replaces_collection_without_duplicating(conn, mini_repo, tmp_path):
    summary = index_repo(conn, "MiniRepo", mini_repo)
    chroma_path = tmp_path / "chroma"

    first = embed_repo(conn, summary["repo_id"], "MiniRepo", mini_repo, FakeEmbeddingClient(), chroma_path)
    second = embed_repo(conn, summary["repo_id"], "MiniRepo", mini_repo, FakeEmbeddingClient(), chroma_path)

    assert first["chunk_count"] == second["chunk_count"]
    client = chromadb.PersistentClient(path=str(chroma_path))
    collection = client.get_collection(name=collection_name("MiniRepo"))
    assert collection.count() == second["chunk_count"]


def test_embed_repo_batches_embedding_calls(conn, mini_repo, tmp_path, monkeypatch):
    import repoview.embedder as embedder_module

    monkeypatch.setattr(embedder_module, "EMBED_BATCH_SIZE", 1)
    summary = index_repo(conn, "MiniRepo", mini_repo)
    embedder = FakeEmbeddingClient()

    result = embed_repo(conn, summary["repo_id"], "MiniRepo", mini_repo, embedder, tmp_path / "chroma")

    assert len(embedder.embed_calls) == result["chunk_count"]
    assert all(len(call) == 1 for call in embedder.embed_calls)
