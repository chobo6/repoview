from pathlib import Path


class PathEscapeError(ValueError):
    """LLM이 생성한 경로가 레포 루트를 벗어났을 때 발생한다."""


def resolve_safe_path(repo_root: Path, relative_path: str) -> Path:
    root = Path(repo_root).resolve()
    candidate = (root / relative_path).resolve()

    if candidate != root and root not in candidate.parents:
        raise PathEscapeError(f"레포 루트를 벗어나는 경로입니다: {relative_path}")

    return candidate
