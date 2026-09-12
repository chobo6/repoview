import hashlib
import time

from repoview.config import EMBEDDING_MODEL

EMBED_MAX_RETRIES = 5
EMBED_RETRY_SECONDS = 15


class OpenAIEmbeddingClient:
    def __init__(self, model: str = EMBEDDING_MODEL) -> None:
        from openai import OpenAI

        self.model = model
        self._client = OpenAI()

    def embed(self, texts: list[str]) -> list[list[float]]:
        from openai import RateLimitError

        for attempt in range(EMBED_MAX_RETRIES):
            try:
                response = self._client.embeddings.create(input=texts, model=self.model)
                return [item.embedding for item in response.data]
            except RateLimitError:
                if attempt == EMBED_MAX_RETRIES - 1:
                    raise
                # ponytail: 분당 토큰(TPM) 누적 한도에 걸린 경우 대비 — 고정 대기 후
                # 재시도. 요청 자체가 한도보다 큰 경우는 EMBED_BATCH_CHAR_BUDGET을
                # 낮춰야 해결되며, 이 재시도는 그 경우를 돕지 못한다.
                time.sleep(EMBED_RETRY_SECONDS)
        raise AssertionError("unreachable")


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
