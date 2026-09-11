from repoview.config import DEFAULT_TOP_K
from repoview.tools.search import MAX_LINE_PREVIEW


def search_semantic(collection, embedding_client, query: str, top_k: int | None = None) -> str:
    if collection is None:
        return (
            "ERROR: 이 레포는 아직 임베딩되지 않았습니다. "
            "python -m repoview.embed로 먼저 임베딩하세요."
        )

    limit = top_k if top_k is not None else DEFAULT_TOP_K

    try:
        query_embedding = embedding_client.embed([query])[0]
    except Exception as exc:
        return f"ERROR: 임베딩 생성 실패: {exc}"

    results = collection.query(query_embeddings=[query_embedding], n_results=limit)

    ids = results["ids"][0]
    if not ids:
        return "일치하는 결과가 없습니다."

    lines = []
    for metadata, document in zip(results["metadatas"][0], results["documents"][0]):
        first_line = document.splitlines()[0] if document else ""
        snippet = first_line.strip()[:MAX_LINE_PREVIEW]
        lines.append(f"{metadata['file_path']}:{metadata['start_line']}-{metadata['end_line']}: {snippet}")

    return "\n".join(lines)
