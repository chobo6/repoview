import os
import sqlite3
from collections import Counter
from pathlib import Path

from repoview.config import (
    CODE_EXTENSIONS,
    EXCLUDED_DIRS,
    NON_SOURCE_LANGUAGES,
    ROOT_CONFIG_FILENAMES,
)
from repoview.tools.search import iter_code_files

# (매니페스트 파일명, 파일 내용에 있어야 하는 문자열, 프레임워크 이름)
FRAMEWORK_MARKERS: list[tuple[str, str, str]] = [
    ("pom.xml", "spring-webmvc", "spring-mvc"),
    ("pom.xml", "spring-boot", "spring-boot"),
    ("pom.xml", "mybatis", "mybatis"),
    ("package.json", "colyseus", "colyseus"),
    ("package.json", '"next"', "nextjs"),
    ("package.json", '"react"', "react"),
    ("package.json", '"express"', "express"),
]
MANIFEST_NAMES = {"pom.xml", "package.json", "build.gradle"}
MANIFEST_MAX_DEPTH = 3


def detect_frameworks(root: Path) -> list[str]:
    root = Path(root)
    found: list[str] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIRS)
        depth = len(Path(dirpath).relative_to(root).parts)
        if depth >= MANIFEST_MAX_DEPTH:
            # 매니페스트는 이 깊이의 파일까지만 인정하므로 더 내려갈 필요가 없다.
            dirnames[:] = []

        for filename in sorted(filenames):
            if filename not in MANIFEST_NAMES:
                continue
            if depth + 1 > MANIFEST_MAX_DEPTH:
                continue
            manifest = Path(dirpath) / filename
            if manifest.is_symlink():
                # 심볼릭 링크는 레포 루트 밖을 가리킬 수 있어 내용을 읽지 않는다.
                continue
            text = manifest.read_text(encoding="utf-8", errors="replace")
            for marker_filename, marker, framework in FRAMEWORK_MARKERS:
                if filename == marker_filename and marker in text and framework not in found:
                    found.append(framework)

    return found


def index_repo(conn: sqlite3.Connection, name: str, root: Path) -> dict:
    root = Path(root).resolve()
    files = list(iter_code_files(root))

    rows = []
    language_votes: Counter[str] = Counter()

    for path in files:
        language = CODE_EXTENSIONS.get(path.suffix.lower()) or ROOT_CONFIG_FILENAMES[path.name]
        text = path.read_text(encoding="utf-8", errors="replace")
        rows.append(
            (
                path.relative_to(root).as_posix(),
                language,
                path.stat().st_size,
                len(text.splitlines()),
            )
        )
        if language not in NON_SOURCE_LANGUAGES:
            language_votes[language] += 1

    primary_language = language_votes.most_common(1)[0][0] if language_votes else None
    framework = ", ".join(detect_frameworks(root)) or None

    conn.execute(
        """
        INSERT INTO repo (name, root_path, primary_language, framework, file_count, indexed_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(name) DO UPDATE SET
            root_path        = excluded.root_path,
            primary_language = excluded.primary_language,
            framework        = excluded.framework,
            file_count       = excluded.file_count,
            indexed_at       = excluded.indexed_at
        """,
        (name, str(root), primary_language, framework, len(files)),
    )
    repo_id = conn.execute("SELECT id FROM repo WHERE name = ?", (name,)).fetchone()["id"]

    conn.execute("DELETE FROM repo_file WHERE repo_id = ?", (repo_id,))
    conn.executemany(
        "INSERT INTO repo_file (repo_id, path, language, size_bytes, line_count) VALUES (?, ?, ?, ?, ?)",
        [(repo_id, *row) for row in rows],
    )
    conn.commit()

    return {
        "repo_id": repo_id,
        "file_count": len(files),
        "primary_language": primary_language,
        "framework": framework or "",
    }
