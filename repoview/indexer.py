import sqlite3
from collections import Counter
from pathlib import Path

from repoview.config import (
    CODE_EXTENSIONS,
    EXCLUDED_DIRS,
    NON_SOURCE_LANGUAGES,
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

    for manifest in sorted(root.rglob("*")):
        if manifest.name not in MANIFEST_NAMES or not manifest.is_file():
            continue
        relative_parts = manifest.relative_to(root).parts
        if any(part in EXCLUDED_DIRS for part in relative_parts):
            continue
        if len(relative_parts) > MANIFEST_MAX_DEPTH:
            continue

        text = manifest.read_text(encoding="utf-8", errors="replace")
        for filename, marker, framework in FRAMEWORK_MARKERS:
            if manifest.name == filename and marker in text and framework not in found:
                found.append(framework)

    return found


def index_repo(conn: sqlite3.Connection, name: str, root: Path) -> dict:
    root = Path(root).resolve()
    files = list(iter_code_files(root))

    rows = []
    language_votes: Counter[str] = Counter()

    for path in files:
        language = CODE_EXTENSIONS[path.suffix.lower()]
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
