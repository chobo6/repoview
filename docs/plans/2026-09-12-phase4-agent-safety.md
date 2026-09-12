# Phase 4 에이전트 안전장치 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 에이전트 루프에 세션 예산 종료조건(반복 6턴/토큰 50,000), 동일 도구 호출 반복 감지, 인용 사후검증(할루시네이션 방지) 세 가지 안전장치를 추가한다.

**Architecture:** 셋 다 `repoview/agent/loop.py`의 `run_session` 루프 안팎에서 동작한다. 반복/토큰 상한은 기존 `MAX_ITERATIONS` CAPPED 처리 경로를 그대로 재사용해 새 종료 조건 하나만 얹는다. 반복 호출 감지는 루프 지역 변수(딕셔너리)로 카운팅해 `tool_result`에 안내문을 주입한다. 인용 사후검증은 세션이 끝난 뒤(COMPLETED/CAPPED 확정 시점) `eval_citations.py`의 기존 인용 추출 함수를 재사용하는 새 순수 함수 모듈(`repoview/citation_check.py`)로 처리하고, 결과를 `session.citation_warnings`(JSON, nullable) 컬럼에 기록한다. 새 도구·API·프론트엔드 변경은 없다.

**Tech Stack:** 기존 스택 그대로 (Python 3.12, sqlite3, pytest, `FakeLLM`). 신규 의존성 없음.

**Spec:** `docs/05-agent-design.md` 3·4·7절, `docs/03-db-design.md`(session 테이블) — 2026-09-12 커밋 `4c0ff6f`에서 예산 제약에 맞춰 구체화됨.

## Global Constraints

- 모든 자동화 테스트는 `FakeLLM`을 주입해 실제 OpenAI API 키 없이 전부 통과해야 한다 — OpenAI SDK를 모킹하지 말 것 (`repoview/agent/llm.py`의 `FakeLLM`/`make_text_response`/`make_tool_call_response` 사용).
- `dispatch`는 절대 예외를 던지지 않는다 — 도구 실패는 `"ERROR: ..."` 문자열로 돌아온다는 기존 계약을 건드리지 않는다.
- 한국어 커밋 메시지, `feat:`/`fix:` 같은 접두사 없음.
- `MAX_ITERATIONS` 기본값은 **6**, 누적 토큰 상한은 **50,000** — 문서 원안(15턴/200K)에서 예산 제약으로 낮춘 값이니 임의로 다시 올리지 말 것.
- 인용 사후검증은 이번 스코프에서 **기록만** 한다 — UI 배지, 자동 재생성, SSE는 범위 밖.
- `repoview/db.py`의 `_migrate()` 패턴(PRAGMA table_info로 컬럼 존재 확인 후 ALTER TABLE)을 그대로 따를 것 — try/except로 예외를 삼키지 말 것.

---

### Task 1: 세션 예산 설정값과 토큰 상한 종료조건

**Files:**
- Modify: `repoview/config.py`
- Modify: `repoview/agent/loop.py:1-14` (import), `repoview/agent/loop.py:28-100` (`run_session`)
- Test: `tests/test_config.py`, `tests/test_agent_loop.py`

**Interfaces:**
- Consumes: 없음 (독립 작업)
- Produces: `repoview.config.MAX_ITERATIONS`(int, 신규 기본값 6), `repoview.config.MAX_SESSION_TOKENS`(int, 신규, 기본값 50000). `run_session(..., max_session_tokens: int | None = None)` — 기존 `max_iterations` 파라미터와 같은 방식의 테스트용 오버라이드.

- [ ] **Step 1: 실패하는 설정값 테스트 작성**

`tests/test_config.py` 끝에 추가:

```python
def test_max_iterations_default_is_six(monkeypatch):
    monkeypatch.delenv("REPOVIEW_MAX_ITERATIONS", raising=False)
    import importlib
    from repoview import config
    importlib.reload(config)
    assert config.MAX_ITERATIONS == 6


def test_max_session_tokens_default_is_fifty_thousand(monkeypatch):
    monkeypatch.delenv("REPOVIEW_MAX_SESSION_TOKENS", raising=False)
    import importlib
    from repoview import config
    importlib.reload(config)
    assert config.MAX_SESSION_TOKENS == 50_000
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_config.py -k "max_iterations_default_is_six or max_session_tokens_default_is_fifty_thousand" -v`
Expected: FAIL — `test_max_iterations_default_is_six`는 현재 값 3과 달라서, `test_max_session_tokens_default_is_fifty_thousand`는 `AttributeError: module 'repoview.config' has no attribute 'MAX_SESSION_TOKENS'`로 실패.

- [ ] **Step 3: `repoview/config.py` 수정**

`MAX_ITERATIONS` 줄을 다음으로 교체:

```python
MAX_ITERATIONS = int(os.getenv("REPOVIEW_MAX_ITERATIONS", "6"))
MAX_SESSION_TOKENS = int(os.getenv("REPOVIEW_MAX_SESSION_TOKENS", "50000"))
```

(기존 `MAX_ITERATIONS = int(os.getenv("REPOVIEW_MAX_ITERATIONS", "3"))` 한 줄을 위 두 줄로 교체 — 위치는 `repoview/config.py`의 기존 `MAX_ITERATIONS` 줄 그대로.)

- [ ] **Step 4: 설정값 테스트 통과 확인**

Run: `pytest tests/test_config.py -v`
Expected: PASS (전체)

- [ ] **Step 5: 토큰 상한 CAPPED 실패 테스트 작성**

`tests/test_agent_loop.py`의 `test_caps_at_max_iterations` 근처에 추가 (기존 임포트 `from repoview.agent.llm import FakeLLM, make_text_response, make_tool_call_response` 그대로 사용):

```python
def test_caps_when_cumulative_tokens_exceed_limit(conn, repo_id):
    # make_tool_call_response는 input_tokens=100, output_tokens=20 고정이라
    # 한 번만 호출해도 120토큰이 누적된다. max_session_tokens=100이면 그 자리에서
    # (다음 라운드로 못 넘어가고) 바로 상한을 넘겨 반복 루프를 break하고,
    # 기존 "반복 상한 도달" 요약 경로로 빠져 CAPPED가 되어야 한다.
    llm = FakeLLM([
        make_tool_call_response("search_code", {"pattern": "a"}),
        make_text_response("토큰 상한 도달 후 요약"),
    ])
    result = run_session(conn, repo_id, "질문", llm, max_iterations=10, max_session_tokens=100)
    assert result.status == "CAPPED"
    assert result.final_review == "토큰 상한 도달 후 요약"
    assert result.iteration_count == 1


def test_does_not_cap_when_under_token_limit(conn, repo_id):
    # 첫 라운드(120토큰 누적)가 상한(10,000)에 한참 못 미쳐 break하지 않고
    # 두 번째 라운드로 정상 진행되어 COMPLETED로 끝나야 한다.
    llm = FakeLLM([
        make_tool_call_response("search_code", {"pattern": "a"}),
        make_text_response("문제 없음"),
    ])
    result = run_session(conn, repo_id, "질문", llm, max_session_tokens=10_000)
    assert result.status == "COMPLETED"
    assert result.final_review == "문제 없음"
```

- [ ] **Step 6: 테스트 실패 확인**

Run: `pytest tests/test_agent_loop.py -k "token" -v`
Expected: FAIL — `run_session() got an unexpected keyword argument 'max_session_tokens'`

- [ ] **Step 7: `repoview/agent/loop.py` 수정**

import 줄(파일 11번째 줄)을 교체:

```python
from repoview.config import CHROMA_PATH, CURRENT_PHASE, MAX_ITERATIONS, MAX_SESSION_TOKENS
```

`run_session` 시그니처(현재 28~38번째 줄)를 교체:

```python
def run_session(
    conn: sqlite3.Connection,
    repo_id: int,
    question: str,
    llm,
    *,
    model: str = "",
    phase: int = CURRENT_PHASE,
    max_iterations: int | None = None,
    max_session_tokens: int | None = None,
    embedding_client=None,
) -> SessionResult:
    limit = max_iterations if max_iterations is not None else MAX_ITERATIONS
    token_limit = max_session_tokens if max_session_tokens is not None else MAX_SESSION_TOKENS
```

(`limit = ...` 다음 줄에 `token_limit = ...`을 추가하는 것 — 그 아래 `repo = conn.execute(...)`부터는 그대로.)

for 루프 안, 도구 호출을 처리하는 `for call in response.tool_calls:` 블록이 끝난 직후(현재 86번째 줄 `)` 다음, 87번째 줄 빈 줄)에 다음을 추가:

```python
            if totals["input"] + totals["output"] > token_limit:
                break
```

(들여쓰기는 `for iteration in range(1, limit + 1):`의 본문 레벨과 동일 — 즉 `for call in response.tool_calls:` 블록과 같은 들여쓰기.)

- [ ] **Step 8: 테스트 통과 확인**

Run: `pytest tests/test_agent_loop.py -v`
Expected: PASS (전체 — 기존 `test_caps_at_max_iterations`를 포함해 하나도 깨지지 않아야 한다. 반복 상한 도달 시의 기존 흐름은 `break` 없이 `for` 문이 자연 종료되는 것이므로 그대로 유지된다.)

- [ ] **Step 9: 전체 테스트 스위트 확인**

Run: `pytest -q`
Expected: PASS (전체)

- [ ] **Step 10: 커밋**

```bash
git add repoview/config.py repoview/agent/loop.py tests/test_config.py tests/test_agent_loop.py
git commit -m "$(cat <<'EOF'
MAX_ITERATIONS 기본값을 6으로, 누적 토큰 상한(50,000)을 새 종료조건으로 추가

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: 동일 도구 호출 반복 감지

**Files:**
- Modify: `repoview/agent/loop.py:60-86` (`run_session`의 for 루프 본문)
- Test: `tests/test_agent_loop.py`

**Interfaces:**
- Consumes: Task 1이 끝난 뒤의 `run_session` 루프 구조 (같은 파일, 독립적으로 추가만 함 — Task 1의 토큰 상한 `break`문과는 순서만 맞으면 됨)
- Produces: 없음 (외부에서 호출하는 함수/시그니처 변화 없음, `run_session` 내부 동작만 바뀜)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_agent_loop.py`에 추가:

```python
def test_injects_notice_after_third_identical_tool_call(conn, repo_id):
    llm = FakeLLM([
        make_tool_call_response("search_code", {"pattern": "x"}),
        make_tool_call_response("search_code", {"pattern": "x"}),
        make_tool_call_response("search_code", {"pattern": "x"}),
        make_text_response("끝"),
    ])
    result = run_session(conn, repo_id, "질문", llm, max_iterations=10)
    assert result.status == "COMPLETED"

    tool_results = [
        row["tool_result"]
        for row in conn.execute(
            "SELECT tool_result FROM trace_step WHERE session_id = ? AND type = 'TOOL_CALL' ORDER BY step_no",
            (result.session_id,),
        ).fetchall()
    ]
    assert len(tool_results) == 3
    assert "이미 동일한 검색을 수행했습니다" not in tool_results[0]
    assert "이미 동일한 검색을 수행했습니다" not in tool_results[1]
    assert "이미 동일한 검색을 수행했습니다" in tool_results[2]


def test_does_not_inject_notice_for_different_arguments(conn, repo_id):
    llm = FakeLLM([
        make_tool_call_response("search_code", {"pattern": "x"}),
        make_tool_call_response("search_code", {"pattern": "y"}),
        make_tool_call_response("search_code", {"pattern": "z"}),
        make_text_response("끝"),
    ])
    result = run_session(conn, repo_id, "질문", llm, max_iterations=10)
    tool_results = [
        row["tool_result"]
        for row in conn.execute(
            "SELECT tool_result FROM trace_step WHERE session_id = ? AND type = 'TOOL_CALL' ORDER BY step_no",
            (result.session_id,),
        ).fetchall()
    ]
    assert all("이미 동일한 검색을 수행했습니다" not in r for r in tool_results)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_agent_loop.py -k "identical_tool_call or different_arguments" -v`
Expected: FAIL — 세 번째 호출 결과에 안내문이 없어서 `assert ... in tool_results[2]`가 실패.

- [ ] **Step 3: `repoview/agent/loop.py` 수정**

`run_session` 안, `messages` 초기화 직후(`for iteration in range(1, limit + 1):` 바로 위)에 카운터를 추가:

```python
        call_counts: dict[tuple[str, str], int] = {}
```

`for call in response.tool_calls:` 블록(현재 71~86번째 줄)을 교체:

```python
            for call in response.tool_calls:
                step_no += 1
                started = time.monotonic()
                result = dispatch(
                    repo_root,
                    call.name,
                    call.arguments,
                    collection=collection,
                    embedding_client=embedding_client,
                )
                latency_ms = int((time.monotonic() - started) * 1000)

                call_key = (call.name, json.dumps(call.arguments, sort_keys=True, ensure_ascii=False))
                call_counts[call_key] = call_counts.get(call_key, 0) + 1
                if call_counts[call_key] >= 3:
                    result += "\n\n이미 동일한 검색을 수행했습니다. 다른 접근을 시도하거나 결론을 내리세요."

                _record_tool_step(conn, session_id, step_no, call, result, latency_ms)
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": result}
                )
```

(`json`은 이미 파일 최상단에서 import 되어 있다 — 추가 import 불필요.)

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_agent_loop.py -v`
Expected: PASS (전체)

- [ ] **Step 5: 전체 테스트 스위트 확인**

Run: `pytest -q`
Expected: PASS (전체)

- [ ] **Step 6: 커밋**

```bash
git add repoview/agent/loop.py tests/test_agent_loop.py
git commit -m "$(cat <<'EOF'
동일 도구 호출을 3회 이상 반복하면 안내 문구를 tool_result에 주입

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: DB 마이그레이션 — `session.citation_warnings` 컬럼

**Files:**
- Modify: `repoview/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: 없음 (독립 작업, Task 1·2와 병행 가능)
- Produces: `session` 테이블의 `citation_warnings` 컬럼(TEXT, nullable) — Task 5가 여기에 JSON 문자열을 기록한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_db.py`의 `test_false_positive_migration_is_safe_to_run_twice` 다음에 추가:

```python
def test_session_has_citation_warnings_column(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(session)")}
    assert "citation_warnings" in columns


def test_citation_warnings_migration_is_safe_to_run_twice(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    init_db(conn)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(session)")}
    assert "citation_warnings" in columns
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_db.py -k citation_warnings -v`
Expected: FAIL — `assert "citation_warnings" in columns`에서 실패 (컬럼 없음)

- [ ] **Step 3: `repoview/db.py`의 `_migrate()` 수정**

`_migrate` 함수(현재 116~121번째 줄)를 교체:

```python
def _migrate(conn: sqlite3.Connection) -> None:
    eval_result_columns = {row["name"] for row in conn.execute("PRAGMA table_info(eval_result)")}
    if "false_positive" not in eval_result_columns:
        conn.execute(
            "ALTER TABLE eval_result ADD COLUMN false_positive INTEGER NOT NULL DEFAULT 0"
        )

    session_columns = {row["name"] for row in conn.execute("PRAGMA table_info(session)")}
    if "citation_warnings" not in session_columns:
        conn.execute("ALTER TABLE session ADD COLUMN citation_warnings TEXT")
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_db.py -v`
Expected: PASS (전체)

- [ ] **Step 5: 전체 테스트 스위트 확인**

Run: `pytest -q`
Expected: PASS (전체)

- [ ] **Step 6: 커밋**

```bash
git add repoview/db.py tests/test_db.py
git commit -m "$(cat <<'EOF'
session에 citation_warnings 컬럼 추가 (Phase 4 인용 사후검증 결과 저장용)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: 인용 사후검증 순수 함수 (`repoview/citation_check.py`)

**Files:**
- Create: `repoview/citation_check.py`
- Test: `tests/test_citation_check.py`

**Interfaces:**
- Consumes: `repoview.eval_citations.extract_citations(text: str) -> list[dict]` (기존 함수, 각 dict는 `{"file_path": str, "start_line": int, "end_line": int}`). `repo_file` 테이블(컬럼 `repo_id`, `path`, `line_count`). `trace_step` 테이블(컬럼 `session_id`, `type`, `tool_name`, `tool_result`) — Task 3과는 별도로 이미 존재하는 컬럼만 읽는다.
- Produces: `verify_citations(conn: sqlite3.Connection, repo_id: int, session_id: int, review_text: str) -> list[dict]` — 문제 있는 인용만 담은 리스트(빈 리스트면 문제 없음). 각 항목은 `{"file_path": str, "start_line": int, "end_line": int, "issue": str}`. Task 5가 이 반환값을 `json.dumps`해서 `session.citation_warnings`에 저장한다(빈 리스트면 `None` 저장).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_citation_check.py` 신규 생성:

```python
from repoview.citation_check import verify_citations
from repoview.indexer import index_repo


def _seed_session_with_read(conn, session_id, repo_id, path, start, end):
    """trace_step.session_id는 session(id)를 참조하는 외래키라, read_file
    트레이스를 넣으려면 먼저 session 행이 있어야 한다."""
    conn.execute(
        "INSERT INTO session (id, repo_id, question, status, model, phase) VALUES (?, ?, 'q', 'RUNNING', 'm', 2)",
        (session_id, repo_id),
    )
    conn.execute(
        """
        INSERT INTO trace_step (session_id, step_no, type, tool_name, tool_result)
        VALUES (?, 1, 'TOOL_CALL', 'read_file', ?)
        """,
        (session_id, f"{path} ({start}-{end}행)\n실제 파일 내용"),
    )
    conn.commit()


def test_citation_with_no_issues_returns_empty_list(conn, mini_repo):
    repo_id = index_repo(conn, "MiniRepo", mini_repo)["repo_id"]
    row = conn.execute(
        "SELECT path, line_count FROM repo_file WHERE repo_id = ? LIMIT 1", (repo_id,)
    ).fetchone()
    _seed_session_with_read(conn, session_id=1, repo_id=repo_id, path=row["path"], start=1, end=row["line_count"])

    review = f"문제: 없음\n근거: `{row['path']}:1-{row['line_count']}`\n영향: 없음\n제안: 없음"
    warnings = verify_citations(conn, repo_id, session_id=1, review_text=review)
    assert warnings == []


def test_citation_to_nonexistent_file_is_flagged(conn, mini_repo):
    # session/trace_step에 아무것도 없어도 verify_citations은 repo_file과
    # trace_step을 session_id로 SELECT만 하므로(외래키 삽입이 아니므로)
    # 존재하지 않는 session_id를 그냥 넘겨도 동작한다 — read_file 기록이 없다는
    # 뜻이 되어 오히려 "미확인 인용" 계열 검증과 자연스럽게 맞아떨어진다.
    repo_id = index_repo(conn, "MiniRepo", mini_repo)["repo_id"]

    review = "문제: 있음\n근거: `no/such/file.py:1`\n영향: -\n제안: -"
    warnings = verify_citations(conn, repo_id, session_id=999, review_text=review)
    assert len(warnings) == 1
    assert warnings[0]["file_path"] == "no/such/file.py"
    assert warnings[0]["issue"] == "존재하지 않는 파일"


def test_citation_not_actually_read_is_flagged(conn, mini_repo):
    repo_id = index_repo(conn, "MiniRepo", mini_repo)["repo_id"]
    row = conn.execute(
        "SELECT path, line_count FROM repo_file WHERE repo_id = ? LIMIT 1", (repo_id,)
    ).fetchone()
    # read_file을 한 번도 호출하지 않은(트레이스가 없는) 세션인 상황을 재현한다.

    review = f"문제: 있음\n근거: `{row['path']}:1`\n영향: -\n제안: -"
    warnings = verify_citations(conn, repo_id, session_id=999, review_text=review)
    assert len(warnings) == 1
    assert warnings[0]["issue"] == "read_file로 확인하지 않은 인용"


def test_citation_beyond_file_length_is_flagged(conn, mini_repo):
    repo_id = index_repo(conn, "MiniRepo", mini_repo)["repo_id"]
    row = conn.execute(
        "SELECT path, line_count FROM repo_file WHERE repo_id = ? LIMIT 1", (repo_id,)
    ).fetchone()
    beyond = row["line_count"] + 100
    _seed_session_with_read(conn, session_id=2, repo_id=repo_id, path=row["path"], start=1, end=row["line_count"])

    review = f"문제: 있음\n근거: `{row['path']}:{beyond}`\n영향: -\n제안: -"
    warnings = verify_citations(conn, repo_id, session_id=2, review_text=review)
    assert len(warnings) == 1
    assert warnings[0]["issue"] == "파일 길이를 벗어난 라인"


def test_review_with_no_citations_returns_empty_list(conn, mini_repo):
    repo_id = index_repo(conn, "MiniRepo", mini_repo)["repo_id"]
    warnings = verify_citations(conn, repo_id, session_id=999, review_text="문제를 발견하지 못했습니다")
    assert warnings == []
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_citation_check.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'repoview.citation_check'`

- [ ] **Step 3: `repoview/citation_check.py` 구현**

```python
import re
import sqlite3

from repoview.eval_citations import extract_citations

_READ_RESULT_RE = re.compile(r"^(.+?) \((\d+)-(\d+)행\)")


def verify_citations(
    conn: sqlite3.Connection, repo_id: int, session_id: int, review_text: str
) -> list[dict]:
    """리뷰의 `파일:라인` 인용 중 문제 있는 것만 골라 반환한다.

    문제 종류: 존재하지 않는 파일 / 파일 길이를 벗어난 라인 / read_file로
    확인하지 않은 인용. 문제 없으면 빈 리스트.
    """
    citations = extract_citations(review_text)
    if not citations:
        return []

    read_ranges = _read_file_ranges(conn, session_id)

    warnings = []
    for citation in citations:
        issue = _check_one(conn, repo_id, read_ranges, citation)
        if issue is not None:
            warnings.append({**citation, "issue": issue})
    return warnings


def _check_one(
    conn: sqlite3.Connection,
    repo_id: int,
    read_ranges: list[tuple[str, int, int]],
    citation: dict,
) -> str | None:
    normalized_path = citation["file_path"].replace("\\", "/")
    row = conn.execute(
        "SELECT line_count FROM repo_file WHERE repo_id = ? AND path = ?",
        (repo_id, normalized_path),
    ).fetchone()
    if row is None:
        return "존재하지 않는 파일"

    if row["line_count"] is not None and citation["start_line"] > row["line_count"]:
        return "파일 길이를 벗어난 라인"

    if not _was_read(read_ranges, normalized_path, citation["start_line"], citation["end_line"]):
        return "read_file로 확인하지 않은 인용"

    return None


def _was_read(
    read_ranges: list[tuple[str, int, int]], normalized_path: str, start_line: int, end_line: int
) -> bool:
    for path, read_start, read_end in read_ranges:
        if path.replace("\\", "/") != normalized_path:
            continue
        if read_start <= end_line and read_end >= start_line:
            return True
    return False


def _read_file_ranges(conn: sqlite3.Connection, session_id: int) -> list[tuple[str, int, int]]:
    rows = conn.execute(
        """
        SELECT tool_result FROM trace_step
        WHERE session_id = ? AND type = 'TOOL_CALL' AND tool_name = 'read_file'
        """,
        (session_id,),
    ).fetchall()

    ranges = []
    for row in rows:
        match = _READ_RESULT_RE.match(row["tool_result"] or "")
        if match:
            path, start, end = match.groups()
            ranges.append((path, int(start), int(end)))
    return ranges
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_citation_check.py -v`
Expected: PASS (전체 5개)

- [ ] **Step 5: 전체 테스트 스위트 확인**

Run: `pytest -q`
Expected: PASS (전체)

- [ ] **Step 6: 커밋**

```bash
git add repoview/citation_check.py tests/test_citation_check.py
git commit -m "$(cat <<'EOF'
인용 사후검증 순수 함수 verify_citations 구현

파일 존재 여부·라인 범위·read_file로 실제 확인했는지를 대조해
문제 있는 인용만 반환한다. eval_citations.extract_citations를 재사용.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `run_session`에 인용 사후검증 연결

**Files:**
- Modify: `repoview/agent/loop.py`
- Test: `tests/test_agent_loop.py`

**Interfaces:**
- Consumes: Task 3의 `session.citation_warnings` 컬럼, Task 4의 `verify_citations(conn, repo_id, session_id, review_text) -> list[dict]`.
- Produces: 없음 (외부 시그니처 변화 없음 — `run_session`/`SessionResult`는 그대로. `session.citation_warnings`가 채워지는 것은 DB를 직접 조회해 확인).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_agent_loop.py`에 추가 (파일 상단에 `from repoview.citation_check import verify_citations`는 필요 없음 — DB로 직접 확인):

```python
def test_citation_warnings_recorded_when_review_cites_unread_file(conn, repo_id):
    llm = FakeLLM([
        make_text_response("문제: 있음\n근거: `no/such/file.py:1`\n영향: -\n제안: -")
    ])
    result = run_session(conn, repo_id, "질문", llm)
    row = conn.execute(
        "SELECT citation_warnings FROM session WHERE id = ?", (result.session_id,)
    ).fetchone()
    assert row["citation_warnings"] is not None
    assert "no/such/file.py" in row["citation_warnings"]


def test_citation_warnings_is_none_when_no_issues(conn, repo_id):
    llm = FakeLLM([make_text_response("문제를 발견하지 못했습니다")])
    result = run_session(conn, repo_id, "질문", llm)
    row = conn.execute(
        "SELECT citation_warnings FROM session WHERE id = ?", (result.session_id,)
    ).fetchone()
    assert row["citation_warnings"] is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_agent_loop.py -k citation_warnings -v`
Expected: FAIL — `citation_warnings`가 항상 `None`이라 첫 번째 테스트가 실패 (컬럼은 Task 3에서 이미 만들어졌지만 아직 아무도 채우지 않음).

- [ ] **Step 3: `repoview/agent/loop.py` 수정**

파일 상단 import에 추가:

```python
from repoview.citation_check import verify_citations
```

(`from repoview.config import ...` 줄 위나 아래, 다른 `from repoview...` import들과 같은 위치에 추가.)

`_finish` 함수(현재 184~210번째 줄)를 교체 — `repo_id` 인자와 인용 검증 로직 추가:

```python
def _finish(
    conn,
    repo_id: int,
    session_id: int,
    status: str,
    final_review: str | None,
    iteration: int,
    totals: dict,
    error: str | None = None,
) -> SessionResult:
    citation_warnings = None
    if final_review:
        warnings = verify_citations(conn, repo_id, session_id, final_review)
        if warnings:
            citation_warnings = json.dumps(warnings, ensure_ascii=False)

    conn.execute(
        """
        UPDATE session
        SET status = ?, final_review = ?, iteration_count = ?,
            input_tokens = ?, output_tokens = ?, error = ?,
            citation_warnings = ?, finished_at = datetime('now')
        WHERE id = ?
        """,
        (
            status, final_review, iteration, totals["input"], totals["output"], error,
            citation_warnings, session_id,
        ),
    )
    conn.commit()
    return SessionResult(
        session_id=session_id,
        status=status,
        final_review=final_review,
        iteration_count=iteration,
        input_tokens=totals["input"],
        output_tokens=totals["output"],
    )
```

`_finish`를 호출하는 세 곳을 전부 `repo_id`를 넘기도록 수정한다 (`run_session` 안에서 `repo_id`는 이미 함수 파라미터로 존재 — 그대로 전달):

1. 도구 호출 없이 정상 종료하는 지점 (`if not response.tool_calls:` 블록 안):
   ```python
                return _finish(
                    conn, repo_id, session_id, "COMPLETED", response.text, iteration, totals
                )
   ```
2. 반복/토큰 상한 도달 후 요약 반환하는 지점 (`for` 루프 다음, `return _finish(conn, session_id, "CAPPED", ...)` 줄):
   ```python
        return _finish(conn, repo_id, session_id, "CAPPED", summary.text, iteration, totals)
   ```
3. 예외 처리(`except Exception as exc:`) 블록 안 — 이 경로는 `final_review=None`이라 `verify_citations`가 호출조차 안 되지만(위 `if final_review:` 가드), 시그니처는 맞춰야 하므로 동일하게 `repo_id`를 추가:
   ```python
        _finish(
            conn, repo_id, session_id, "FAILED", None, iteration, totals,
            error=f"{type(exc).__name__}: {exc}",
        )
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_agent_loop.py -v`
Expected: PASS (전체)

- [ ] **Step 5: 전체 테스트 스위트 확인**

Run: `pytest -q`
Expected: PASS (전체 — `repoview/eval.py`의 `run_session` 호출부는 위치 인자를 안 바꿨으므로(`repo_id`는 `_finish`의 새 파라미터일 뿐 `run_session`의 시그니처는 그대로) 영향 없음)

- [ ] **Step 6: 커밋**

```bash
git add repoview/agent/loop.py tests/test_agent_loop.py
git commit -m "$(cat <<'EOF'
run_session 종료 시 인용 사후검증을 실행해 session.citation_warnings에 기록

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## 완료 기준

- [ ] `pytest`가 전부 통과한다 (실제 API 키 없이).
- [ ] `MAX_ITERATIONS` 기본값이 6, `MAX_SESSION_TOKENS` 기본값이 50,000이다.
- [ ] 누적 토큰이 상한을 넘으면 세션이 `CAPPED`로 끝난다.
- [ ] 동일 `(tool_name, args)` 조합이 3회 이상 반복되면 그 `tool_result`에 안내 문구가 들어간다.
- [ ] 세션 종료 시 인용에 문제(존재하지 않는 파일/범위 밖 라인/미확인 파일)가 있으면 `session.citation_warnings`에 JSON으로 기록되고, 문제 없으면 `NULL`이다.
- [ ] 기존 Phase 1~3, Eval Runner 테스트가 전부 그대로 통과한다 — 하위 호환이 깨지지 않았다.

## 이 계획 이후로 미룬 것

| 항목 | 이유 |
|---|---|
| `citation_warnings`를 프론트엔드에 배지로 표시 | 이번 라운드는 백엔드 기록까지만 (예산 우선, 화면 작업은 나중에 한 번에) — `docs/05-agent-design.md` 4절 "판단 이유" 참고 |
| 검증 실패 시 자동 재생성 | 추가 LLM 호출이 필요해 비용 증가 — 이번엔 기록만 |
| SSE 스트리밍 | 이번 브레인스토밍 스코프 밖으로 명시적으로 제외 |
| `.dockerignore`류 확장자 없는 루트 파일 인덱싱(`CODE_EXTENSIONS` 확장) | 별도 스코프 결정 필요 — `docs/TROUBLESHOOTING.md` "알려진 이슈" 참고 |
