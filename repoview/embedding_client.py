import hashlib

from repoview.config import EMBEDDING_MODEL


class OpenAIEmbeddingClient:
    def __init__(self, model: str = EMBEDDING_MODEL) -> None:
        from openai import OpenAI

        self.model = model
        self._client = OpenAI()

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self._client.embeddings.create(input=texts, model=self.model)
        return [item.embedding for item in response.data]


class FakeEmbeddingClient:
    """테스트용. 텍스트의 SHA-256 해시에서 뽑은 결정론적 벡터를 반환한다.

    같은 텍스트는 항상 같은 벡터를 반환하므로, 정확히 일치하는 텍스트를
    쿼리했을 때 그 청크가 최상위 결과로 나오는지 검증하는 용도로 충분하다.
    실제 의미적 유사도는 검증하지 않는다 — 그건 eval의 역할이다.
    """

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim
        self.embed_calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls.append(list(texts))
        return [self._vector_for(text) for text in texts]

    def _vector_for(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [digest[i] / 255.0 for i in range(self.dim)]
