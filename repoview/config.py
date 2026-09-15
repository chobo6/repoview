import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("REPOVIEW_DB") or PROJECT_ROOT / "repoview.db")

REPOS: dict[str, Path] = {
    "LocalQuest": Path(r"C:\Users\hong\OneDrive\Desktop\workspace\human\LocalQuest"),
    "Songpyeon": Path(r"C:\Users\hong\OneDrive\Desktop\workspace\songpyeon"),
}

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")

ALLOWED_MODELS: dict[str, str] = {"qwen2.5:7b": "ollama"}
if OPENAI_MODEL:
    ALLOWED_MODELS[OPENAI_MODEL] = "openai"

MAX_ITERATIONS = int(os.getenv("REPOVIEW_MAX_ITERATIONS", "6"))
MAX_SESSION_TOKENS = int(os.getenv("REPOVIEW_MAX_SESSION_TOKENS", "50000"))
MAX_FILE_LINES = 200
MAX_SEARCH_RESULTS = 20
MAX_INDEXED_FILE_BYTES = 1_000_000
CURRENT_PHASE = 3

EXCLUDED_DIRS = {
    ".git", ".idea", ".vscode", "__pycache__", ".pytest_cache",
    "node_modules", "target", "build", "dist", ".venv", "venv",
}

CODE_EXTENSIONS: dict[str, str] = {
    ".java": "java", ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".py": "python",
    ".jsp": "jsp", ".sql": "sql", ".xml": "xml", ".json": "json",
    ".yml": "yaml", ".yaml": "yaml", ".css": "css", ".html": "html",
    ".md": "markdown",
}

# 확장자가 없는 레포 루트 설정 파일 — Path.suffix가 빈 문자열이라 CODE_EXTENSIONS로는
# 못 잡는다. 파일명으로 직접 매칭하며, 레포 루트에 있을 때만 인덱싱 대상이다
# (iter_code_files 참고). docker-compose.yml처럼 이미 확장자가 있는 파일은 여기 없어도 된다.
ROOT_CONFIG_FILENAMES: dict[str, str] = {
    "Dockerfile": "config", ".dockerignore": "config",
    ".gitignore": "config", ".env.example": "config",
    "Makefile": "config", ".editorconfig": "config",
}

# primary_language 집계에서 제외할 언어 (설정/문서 파일이 본체 언어를 가리는 것 방지)
NON_SOURCE_LANGUAGES = {"json", "yaml", "markdown", "xml", "css", "html", "config"}

CHUNK_LINES = 50
CHUNK_OVERLAP = 10
EMBEDDING_MODEL = os.getenv("REPOVIEW_EMBEDDING_MODEL", "text-embedding-3-small")
DEFAULT_TOP_K = 5
CHROMA_PATH = Path(os.getenv("REPOVIEW_CHROMA_PATH") or PROJECT_ROOT / "chroma_data")

JUDGE_MODEL = os.getenv("REPOVIEW_JUDGE_MODEL", "gpt-4o-mini")

MODEL_PRICING: dict[str, tuple[float, float]] = {
    # (입력 $/1M 토큰, 출력 $/1M 토큰)
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
}
