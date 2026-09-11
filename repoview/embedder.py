from pathlib import Path

from repoview.config import CHUNK_LINES, CHUNK_OVERLAP
from repoview.tools.search import iter_code_files


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
