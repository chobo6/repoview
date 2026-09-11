from repoview.embedding_client import FakeEmbeddingClient


def test_fake_embedding_client_returns_one_vector_per_text():
    client = FakeEmbeddingClient()
    vectors = client.embed(["hello", "world"])
    assert len(vectors) == 2
    assert all(isinstance(v, list) for v in vectors)


def test_fake_embedding_client_is_deterministic():
    client = FakeEmbeddingClient()
    assert client.embed(["same text"]) == client.embed(["same text"])


def test_fake_embedding_client_different_text_gives_different_vector():
    client = FakeEmbeddingClient()
    v1 = client.embed(["text a"])[0]
    v2 = client.embed(["text b"])[0]
    assert v1 != v2


def test_fake_embedding_client_records_calls():
    client = FakeEmbeddingClient()
    client.embed(["a", "b"])
    client.embed(["c"])
    assert client.embed_calls == [["a", "b"], ["c"]]
