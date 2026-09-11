import chromadb
import pytest

from repoview.embedding_client import FakeEmbeddingClient
from repoview.tools.semantic import search_semantic


@pytest.fixture
def populated_collection(tmp_path):
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    collection = client.create_collection(name="test_repo")
    embedder = FakeEmbeddingClient()

    chunks = [
        {"text": "def find_orders_by_user(user_id): ...", "file_path": "a.py", "start_line": 1, "end_line": 5},
        {"text": "class UnrelatedWidget: ...", "file_path": "b.py", "start_line": 10, "end_line": 15},
    ]
    collection.add(
        ids=[f"{c['file_path']}:{c['start_line']}-{c['end_line']}" for c in chunks],
        documents=[c["text"] for c in chunks],
        metadatas=[
            {"file_path": c["file_path"], "start_line": c["start_line"], "end_line": c["end_line"]}
            for c in chunks
        ],
        embeddings=embedder.embed([c["text"] for c in chunks]),
    )
    return collection, embedder


def test_search_semantic_returns_matching_chunk(populated_collection):
    collection, embedder = populated_collection

    result = search_semantic(collection, embedder, "def find_orders_by_user(user_id): ...")

    assert "a.py:1-5" in result


def test_search_semantic_without_collection_returns_error():
    result = search_semantic(None, FakeEmbeddingClient(), "아무 질문")
    assert result.startswith("ERROR:")


def test_search_semantic_respects_top_k(populated_collection):
    collection, embedder = populated_collection

    result = search_semantic(collection, embedder, "아무 텍스트", top_k=1)

    hit_lines = [line for line in result.splitlines() if line and not line.startswith("ERROR")]
    assert len(hit_lines) == 1


def test_search_semantic_empty_collection_returns_message(tmp_path):
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    collection = client.create_collection(name="empty_repo")

    result = search_semantic(collection, FakeEmbeddingClient(), "질문")

    assert result == "일치하는 결과가 없습니다."
