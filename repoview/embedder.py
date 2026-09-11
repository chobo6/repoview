from pathlib import Path

import chromadb
from chromadb.errors import NotFoundError

from repoview.config import CHUNK_LINES, CHUNK_OVERLAP
from repoview.tools.search import iter_code_files

MAX_CHUNK_CHARS = 20_000
EMBED_BATCH_CHAR_BUDGET = 400_000


def chunk_file(path: Path, root: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    if not lines:
        return []

    relative_path = path.relative_to(root).as_posix()
    step = CHUNK_LINES - CHUNK_OVERLAP

    chunks: list[dict] = []
    start = 0
    while True:
        end = min(start + CHUNK_LINES, len(lines))
        chunks.append(
            {
                "text": "\n".join(lines[start:end]),
                "file_path": relative_path,
                "start_line": start + 1,
                "end_line": end,
            }
        )
        if end >= len(lines):
            break
        start += step

    return chunks


def chunk_repo(root: Path) -> list[dict]:
    root = Path(root)
    chunks: list[dict] = []
    for path in iter_code_files(root):
        try:
            chunks.extend(chunk_file(path, root))
        except OSError:
            continue
    return chunks


def collection_name(repo_name: str) -> str:
    return f"repo_{repo_name.lower()}"


def embed_repo(
    conn,
    repo_id: int,
    name: str,
    root: Path,
    embedding_client,
    chroma_path: Path,
) -> dict:
    chunks = [c for c in chunk_repo(Path(root)) if len(c["text"]) <= MAX_CHUNK_CHARS]

    client = chromadb.PersistentClient(path=str(chroma_path))
    name_in_chroma = collection_name(name)
    try:
        client.delete_collection(name=name_in_chroma)
    except NotFoundError:
        pass
    collection = client.create_collection(name=name_in_chroma)

    for batch in _batched_by_chars(chunks, EMBED_BATCH_CHAR_BUDGET):
        texts = [chunk["text"] for chunk in batch]
        embeddings = embedding_client.embed(texts)
        collection.add(
            ids=[f"{c['file_path']}:{c['start_line']}-{c['end_line']}" for c in batch],
            documents=texts,
            metadatas=[
                {"file_path": c["file_path"], "start_line": c["start_line"], "end_line": c["end_line"]}
                for c in batch
            ],
            embeddings=embeddings,
        )

    conn.execute("UPDATE repo SET chunk_count = ? WHERE id = ?", (len(chunks), repo_id))
    conn.commit()

    return {"repo_id": repo_id, "chunk_count": len(chunks)}


def _batched_by_chars(items: list[dict], max_chars: int):
    batch: list[dict] = []
    batch_chars = 0
    for item in items:
        item_chars = len(item["text"])
        if batch and batch_chars + item_chars > max_chars:
            yield batch
            batch = []
            batch_chars = 0
        batch.append(item)
        batch_chars += item_chars
    if batch:
        yield batch
