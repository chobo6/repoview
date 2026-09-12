import os
import re
from collections.abc import Iterator
from fnmatch import fnmatch
from pathlib import Path

from repoview.config import (
    CODE_EXTENSIONS,
    EXCLUDED_DIRS,
    MAX_INDEXED_FILE_BYTES,
    MAX_SEARCH_RESULTS,
    ROOT_CONFIG_FILENAMES,
)

MAX_LINE_PREVIEW = 200


def iter_code_files(repo_root: Path) -> Iterator[Path]:
    root = Path(repo_root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIRS)
        is_root = Path(dirpath) == root
        for filename in sorted(filenames):
            path = Path(dirpath) / filename
            is_root_config = is_root and filename in ROOT_CONFIG_FILENAMES
            if path.suffix.lower() not in CODE_EXTENSIONS and not is_root_config:
                continue
            if path.is_symlink():
                # 심볼릭 링크는 레포 루트 밖을 가리킬 수 있어 read_file의
                # resolve_safe_path 방어를 거치지 않고 내용이 노출될 수 있다.
                continue
            try:
                if path.stat().st_size > MAX_INDEXED_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield path


def search_code(
    repo_root: Path,
    pattern: str,
    path_glob: str | None = None,
    max_results: int | None = None,
) -> str:
    limit = max_results if max_results is not None else MAX_SEARCH_RESULTS

    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        return f"ERROR: 잘못된 정규식입니다: {exc}"

    root = Path(repo_root)
    hits: list[str] = []

    for file_path in iter_code_files(root):
        relative = file_path.relative_to(root).as_posix()
        if path_glob and not _matches_glob(relative, path_glob):
            continue

        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for line_no, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                hits.append(f"{relative}:{line_no}: {line.strip()[:MAX_LINE_PREVIEW]}")
                if len(hits) >= limit:
                    return "\n".join(hits) + f"\n(상위 {limit}건만 표시)"

    return "\n".join(hits) if hits else "일치하는 결과가 없습니다."


def _matches_glob(relative_path: str, path_glob: str) -> bool:
    if fnmatch(relative_path, path_glob):
        return True
    # "frontend/**" 형태로 디렉토리 하위 전체를 지정하는 경우를 지원한다.
    if path_glob.endswith("/**"):
        return relative_path.startswith(path_glob[:-2])
    return False
