from pathlib import Path

from repoview.config import EXCLUDED_DIRS, MAX_FILE_LINES
from repoview.tools.paths import PathEscapeError, resolve_safe_path


def read_file(
    repo_root: Path,
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    try:
        target = resolve_safe_path(repo_root, path)
    except PathEscapeError as exc:
        return f"ERROR: {exc}"

    if not target.is_file():
        return f"ERROR: 파일을 찾을 수 없습니다: {path}"

    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    total = len(lines)

    # Special case for empty file
    if total == 0:
        return f"{path} (파일이 비어 있습니다)"

    start = max(1, start_line if start_line is not None else 1)
    end = min(total, end_line if end_line is not None else total)
    if end - start + 1 > MAX_FILE_LINES:
        end = start + MAX_FILE_LINES - 1

    if start > total:
        return f"ERROR: 시작 행이 파일 길이({total}행)를 넘습니다: {start}"

    body = "\n".join(
        f"{start + offset}: {line}" for offset, line in enumerate(lines[start - 1 : end])
    )
    footer = "" if end >= total else f"\n... ({total - end}행 더 있음, 총 {total}행)"
    return f"{path} ({start}-{end}행)\n{body}{footer}"


def list_directory(repo_root: Path, path: str = "") -> str:
    try:
        target = resolve_safe_path(repo_root, path)
    except PathEscapeError as exc:
        return f"ERROR: {exc}"

    if not target.is_dir():
        return f"ERROR: 디렉토리를 찾을 수 없습니다: {path}"

    entries = [
        f"{child.name}/" if child.is_dir() else child.name
        for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
        if child.name not in EXCLUDED_DIRS
    ]
    return "\n".join(entries) if entries else "(비어 있음)"
