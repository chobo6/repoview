from pathlib import Path

from repoview.tools.files import list_directory, read_file
from repoview.tools.search import search_code

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "레포 내 디렉토리의 하위 항목을 나열한다. 구조를 더 깊이 파악할 때 사용한다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "레포 루트 기준 상대 경로. 비우면 루트를 나열한다.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "정규식으로 코드를 검색해 '파일:행: 내용' 형태로 반환한다. 관련 코드를 찾는 첫 수단으로 사용한다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "검색할 정규식 (대소문자 무시)"},
                    "path_glob": {
                        "type": "string",
                        "description": "검색 범위를 제한하는 glob. 예: 'src/main/**', '*.java'",
                    },
                    "max_results": {"type": "integer", "description": "최대 결과 수 (기본 20)"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "파일 내용을 행 번호와 함께 읽는다. 인용할 근거를 확보하려면 반드시 이 도구로 실제 내용을 확인해야 한다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "레포 루트 기준 상대 경로"},
                    "start_line": {"type": "integer", "description": "시작 행 (1부터)"},
                    "end_line": {"type": "integer", "description": "끝 행. 한 번에 최대 200행까지 반환된다."},
                },
                "required": ["path"],
            },
        },
    },
]


def dispatch(repo_root: Path, name: str, args: dict) -> str:
    """도구를 실행한다. 어떤 실패도 예외로 전파하지 않고 'ERROR: ...' 문자열로 반환한다."""
    try:
        if name == "list_directory":
            return list_directory(repo_root, args.get("path", ""))
        if name == "read_file":
            return read_file(
                repo_root,
                args["path"],
                _optional_int(args.get("start_line")),
                _optional_int(args.get("end_line")),
            )
        if name == "search_code":
            return search_code(
                repo_root,
                args["pattern"],
                args.get("path_glob"),
                _optional_int(args.get("max_results")),
            )
        return f"ERROR: 알 수 없는 도구입니다: {name}"
    except KeyError as exc:
        return f"ERROR: 필수 인자가 없습니다: {exc}"
    except (TypeError, ValueError) as exc:
        return f"ERROR: 인자가 올바르지 않습니다: {exc}"
    except Exception as exc:  # 도구 실패가 루프를 중단시키면 안 된다
        return f"ERROR: 도구 실행 실패: {exc}"


def _optional_int(value) -> int | None:
    if value is None:
        return None
    return int(value)
