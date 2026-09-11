# Eval Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `python -m repoview.eval --repo <레포> --phase <2|3>`로 eval_case를 순회 실행해 LLM-as-judge와 인용 검증으로 채점하고, 탐지율·오탐율·인용 정확도·비용/지연을 `eval_run`/`eval_result`에 기록한다.

**Architecture:** eval_case는 별도 CLI(`repoview/eval_seed.py`)로 JSON 파일에서 시드한다. eval 오케스트레이션(`repoview/eval.py`의 `run_eval`)은 케이스마다 기존 `run_session`(Phase 2/3 공용, `embedding_client` 유무로 갈림)을 그대로 재사용해 리뷰를 생성하고, 순수 함수인 인용 추출기(`repoview/eval_citations.py`)로 리뷰 본문에서 `파일:라인` 인용을 뽑아 `repo_file`과 대조하고, LLM-as-judge(`repoview/eval_judge.py`)로 문제 발견 여부를 판정한다. 판정 결과는 `eval_result`에 케이스별로, 집계 지표는 `eval_run`에 기록한다.

**Tech Stack:** 기존 스택 그대로(Python 3.12, sqlite3, openai SDK, pytest). 신규 의존성 없음 — 판정 모델 호출도 기존 `agent/llm.py`의 `LLM` Protocol(`OpenAILLM`/`FakeLLM`)을 그대로 재사용한다.

**Spec:** `docs/06-test-eval-design.md`(B절: eval 셋 구성·채점·지표·실행), `docs/03-db-design.md`(`eval_case`/`eval_run`/`eval_result` 스키마), `docs/01-prd.md`(2절 Phase 범위, 32번째 줄 성공 기준), `docs/plans/2026-09-11-phase3-rag.md`("이 계획에서 다루지 않는 것" — 이 계획이 이어받는 배경)

## Global Constraints

- 모든 자동화 테스트는 평가 대상 모델과 판정 모델 양쪽 다 `FakeLLM`을 주입해 실제 OpenAI API 키 없이 전부 통과해야 한다 (기존 `FakeLLM`/`FakeEmbeddingClient` 원칙과 동일, `docs/06-test-eval-design.md` "Fake LLM을 쓰는 이유").
- 판정 모델(`JUDGE_MODEL`)은 평가 대상 모델과 반드시 분리한다 — 같은 모델이 자기 답을 채점하면 편향이 생긴다 (`docs/06-test-eval-design.md` B-2절).
- 최종 판정 기준: judge가 YES **AND** 인용이 `expected_file_path`와 일치. 위치까지 맞아야 탐지로 인정한다 — 엄격한 기준이 지표의 신뢰도를 만든다 (`docs/06-test-eval-design.md` B-2절).
- 네거티브 케이스 없이는 "일단 전부 문제라고 지적하기" 전략이 탐지율 100%를 받으므로, 오탐율(FPR)을 별도로 반드시 계산한다 (`docs/06-test-eval-design.md` B-1절).
- 신규 pip 의존성을 추가하지 않는다 — 기존 `openai` SDK를 재사용한다.
- 한국어 커밋 메시지, ORM 미사용, 도구/CLI 실패는 크래시 대신 명확한 메시지로 안내 등 Phase 1~3의 기존 제약을 유지한다.
- `CURRENT_PHASE = 3`까지만 main에 구현되어 있고 Phase 4(반복 감지·실시간 인용 사후검증)는 아직 없으므로, 이 계획의 eval CLI `--phase`는 `2`와 `3`만 지원한다.

## 이 계획에서 다루지 않는 것

| 항목 | 이유 |
|---|---|
| 실제 LocalQuest/Songpyeon 20~30개 eval 셋 전체 작성(버그 직접 심기 포함) | 두 외부 레포를 직접 조사·수정해야 하는 별도 분량의 작업. Task 7에서 조사 범위와 절차만 제시하고, 실제 데이터 채워넣기는 후속 작업으로 남긴다. |
| Phase 4 에이전트 루프 강화(반복 호출 감지, 실시간 인용 사후검증) | `docs/05-agent-design.md` 4절 기준 별도 계획 대상. Eval Runner의 "규칙 기반 인용 대조"는 이것 없이도 동작한다. |
| train/test 70/30 자동 분리, 케이스당 3회 평균 | `docs/06-test-eval-design.md` B-4·B-5절에 명시된 절차이지만, eval 셋 자체가 아직 없는 지금 단계에서는 과설계다. 데이터가 쌓이면 별도로 다룬다. |

## File Structure

| 파일 | 책임 |
|---|---|
| `repoview/db.py` (수정) | `eval_result`에 `false_positive` 컬럼 마이그레이션 추가 |
| `repoview/config.py` (수정) | `JUDGE_MODEL`, `MODEL_PRICING` 추가 |
| `repoview/eval_citations.py` (신규) | 리뷰 텍스트에서 `파일:라인` 인용 추출(`extract_citations`), 특정 파일 인용 여부 확인(`matches_file`) — 순수 함수 |
| `repoview/eval_judge.py` (신규) | LLM-as-judge 프롬프트 생성·판정 파싱(`judge_case`) |
| `repoview/eval_seed.py` (신규) | JSON 파일에서 `eval_case` 시드 삽입 CLI |
| `eval_cases/mini_repo.json` (신규) | 테스트/데모용 예시 eval 케이스 (mini_repo 기준) |
| `repoview/eval.py` (신규) | eval 오케스트레이션(`run_eval`) + 집계 + CLI 진입점(`python -m repoview.eval`) |

---

### Task 1: DB 마이그레이션과 설정값

**Files:**
- Modify: `repoview/db.py`, `repoview/config.py`
- Test: `tests/test_db.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: 없음
- Produces: `eval_result.false_positive` 컬럼, `repoview.config.JUDGE_MODEL`, `repoview.config.MODEL_PRICING`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_db.py` 끝에 추가:

```python
def test_eval_result_has_false_positive_column(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(eval_result)")}
    assert "false_positive" in columns


def test_false_positive_migration_is_safe_to_run_twice(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    init_db(conn)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(eval_result)")}
    assert "false_positive" in columns
```

`tests/test_config.py` 끝에 추가:

```python
def test_judge_model_is_set():
    assert config.JUDGE_MODEL


def test_model_pricing_contains_known_models_with_positive_rates():
    assert "gpt-4o-mini" in config.MODEL_PRICING
    for input_price, output_price in config.MODEL_PRICING.values():
        assert input_price > 0
        assert output_price > 0
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_db.py tests/test_config.py -v`
Expected: FAIL — `false_positive` 컬럼 없음, `AttributeError: module 'repoview.config' has no attribute 'JUDGE_MODEL'`

- [ ] **Step 3: `repoview/db.py` 수정**

`init_db` 함수를 다음으로 교체:

```python
def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    try:
        conn.execute(
            "ALTER TABLE eval_result ADD COLUMN false_positive INTEGER NOT NULL DEFAULT 0"
        )
    except sqlite3.OperationalError:
        pass
```

- [ ] **Step 4: `repoview/config.py` 끝에 추가**

```python
JUDGE_MODEL = os.getenv("REPOVIEW_JUDGE_MODEL", "gpt-4o-mini")

MODEL_PRICING: dict[str, tuple[float, float]] = {
    # (입력 $/1M 토큰, 출력 $/1M 토큰)
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
}
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `pytest tests/test_db.py tests/test_config.py -v`
Expected: PASS

- [ ] **Step 6: 전체 테스트 회귀 확인**

Run: `pytest -v`
Expected: 기존 104개 + 신규 4개 = 108 passed

- [ ] **Step 7: 커밋**

```bash
git add repoview/db.py repoview/config.py tests/test_db.py tests/test_config.py
git commit -m "$(cat <<'EOF'
eval_result에 false_positive 컬럼 추가, 판정 모델·비용 설정값 추가

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: 인용 추출기

**Files:**
- Create: `repoview/eval_citations.py`
- Test: `tests/test_eval_citations.py`

**Interfaces:**
- Consumes: 없음 (순수 함수, DB/네트워크 없음)
- Produces: `repoview.eval_citations.extract_citations(text: str) -> list[dict]` (각 dict는 `file_path`, `start_line`, `end_line` 키), `repoview.eval_citations.matches_file(citations: list[dict], expected_file_path: str) -> bool`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_eval_citations.py`:

```python
from repoview.eval_citations import extract_citations, matches_file


def test_extract_citations_finds_single_line_citation():
    text = "문제: 있음\n근거: `src/main/java/com/example/UserService.java:11`"

    citations = extract_citations(text)

    assert citations == [
        {"file_path": "src/main/java/com/example/UserService.java", "start_line": 11, "end_line": 11}
    ]


def test_extract_citations_finds_range_citation():
    citations = extract_citations("근거: `a/b/App.java:11-14`")

    assert citations[0]["start_line"] == 11
    assert citations[0]["end_line"] == 14


def test_extract_citations_finds_multiple_citations():
    text = "근거: `a.py:1`, 그리고 `b/c.ts:5-9`도 확인"

    citations = extract_citations(text)

    assert len(citations) == 2
    assert citations[0]["file_path"] == "a.py"
    assert citations[1]["file_path"] == "b/c.ts"


def test_extract_citations_returns_empty_list_when_no_citation():
    assert extract_citations("문제를 발견하지 못했습니다") == []


def test_matches_file_normalizes_backslashes():
    citations = [{"file_path": "src/main/App.java", "start_line": 1, "end_line": 1}]

    assert matches_file(citations, "src\\main\\App.java") is True


def test_matches_file_returns_false_when_absent():
    citations = [{"file_path": "a.py", "start_line": 1, "end_line": 1}]

    assert matches_file(citations, "b.py") is False
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_eval_citations.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'repoview.eval_citations'`

- [ ] **Step 3: 구현**

`repoview/eval_citations.py`:

```python
import re

# ponytail: URL(예: http://x.com:80)도 "path.ext:숫자" 형태면 오탐 매칭될 수 있는
# 나이브한 정규식이다. 실제 인용 정확도 수치가 이상하면 확장자 화이트리스트로 좁힐 것.
_CITATION_RE = re.compile(r"([\w./\\-]+\.\w+):(\d+)(?:-(\d+))?")


def extract_citations(text: str) -> list[dict]:
    """리뷰 텍스트에서 `파일경로:시작줄(-끝줄)` 형태의 인용을 모두 추출한다."""
    citations = []
    for match in _CITATION_RE.finditer(text):
        file_path, start, end = match.groups()
        citations.append(
            {
                "file_path": file_path,
                "start_line": int(start),
                "end_line": int(end) if end else int(start),
            }
        )
    return citations


def matches_file(citations: list[dict], expected_file_path: str) -> bool:
    """추출된 인용 중 expected_file_path와 일치하는 것이 있는지 확인한다."""
    normalized = expected_file_path.replace("\\", "/")
    return any(c["file_path"].replace("\\", "/") == normalized for c in citations)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_eval_citations.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: 커밋**

```bash
git add repoview/eval_citations.py tests/test_eval_citations.py
git commit -m "$(cat <<'EOF'
리뷰 텍스트에서 파일:라인 인용을 추출하는 함수 구현

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: LLM-as-judge 판정기

**Files:**
- Create: `repoview/eval_judge.py`
- Test: `tests/test_eval_judge.py`

**Interfaces:**
- Consumes: `LLM` Protocol/`FakeLLM` (`repoview/agent/llm.py`, Phase 1+2에서 이미 구현됨)
- Produces: `repoview.eval_judge.judge_case(judge_llm, question: str, expected_finding: str, review: str, *, is_negative: bool) -> tuple[bool, str]`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_eval_judge.py`:

```python
from repoview.agent.llm import FakeLLM, make_text_response
from repoview.eval_judge import judge_case


def test_judge_case_parses_yes_verdict_and_reason():
    judge_llm = FakeLLM([make_text_response("YES\nN+1 쿼리를 정확히 지적했다")])

    verdict, reason = judge_case(
        judge_llm, "성능 문제 있어?", "N+1 쿼리", "리뷰 본문...", is_negative=False
    )

    assert verdict is True
    assert reason == "N+1 쿼리를 정확히 지적했다"


def test_judge_case_parses_no_verdict():
    judge_llm = FakeLLM([make_text_response("NO\n문제를 언급하지 않았다")])

    verdict, _ = judge_case(judge_llm, "질문", "기대 결과", "리뷰", is_negative=False)

    assert verdict is False


def test_judge_case_is_case_insensitive_on_verdict():
    judge_llm = FakeLLM([make_text_response("yes\n이유")])

    verdict, _ = judge_case(judge_llm, "질문", "기대 결과", "리뷰", is_negative=False)

    assert verdict is True


def test_judge_case_builds_different_prompt_for_negative_case():
    judge_llm = FakeLLM([make_text_response("NO\n주장 없음")])

    judge_case(judge_llm, "질문", "존재하지 않는 문제", "리뷰", is_negative=True)

    sent_prompt = judge_llm.received_messages[0][0]["content"]
    assert "실제로는 존재하지 않는" in sent_prompt


def test_judge_case_handles_missing_reason_line():
    judge_llm = FakeLLM([make_text_response("YES")])

    verdict, reason = judge_case(judge_llm, "질문", "기대", "리뷰", is_negative=False)

    assert verdict is True
    assert reason == ""
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_eval_judge.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'repoview.eval_judge'`

- [ ] **Step 3: 구현**

`repoview/eval_judge.py`:

```python
from repoview.agent.llm import LLM


def build_judge_prompt(
    question: str, expected_finding: str, review: str, *, is_negative: bool
) -> str:
    if is_negative:
        return f"""아래 코드 리뷰가 실제로는 존재하지 않는 다음 문제를 있다고 잘못 주장했는지 판정하세요.

[질문] {question}
[실제로는 존재하지 않는 문제] {expected_finding}
[리뷰 결과]
{review}

리뷰가 이 문제가 있다고 주장했다면 "YES", 없다고 판단했거나 언급하지 않았다면 "NO"라고
첫 줄에 정확히 쓰고, 다음 줄에 판단 이유를 한 문장으로 쓰세요."""

    return f"""아래 코드 리뷰가 다음 문제를 실제로 발견했는지 판정하세요.

[질문] {question}
[찾아야 할 문제] {expected_finding}
[리뷰 결과]
{review}

리뷰가 이 문제를 정확히 발견했다면 "YES", 발견하지 못했다면 "NO"라고 첫 줄에 정확히 쓰고,
다음 줄에 판단 이유를 한 문장으로 쓰세요."""


def judge_case(
    judge_llm: LLM,
    question: str,
    expected_finding: str,
    review: str,
    *,
    is_negative: bool,
) -> tuple[bool, str]:
    prompt = build_judge_prompt(question, expected_finding, review, is_negative=is_negative)
    response = judge_llm.call([{"role": "user", "content": prompt}], [])
    return _parse_verdict(response.text or "")


def _parse_verdict(text: str) -> tuple[bool, str]:
    lines = text.strip().splitlines()
    if not lines:
        return False, ""
    verdict = lines[0].strip().upper().startswith("YES")
    reason = "\n".join(line.strip() for line in lines[1:]).strip()
    return verdict, reason
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_eval_judge.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: 커밋**

```bash
git add repoview/eval_judge.py tests/test_eval_judge.py
git commit -m "$(cat <<'EOF'
LLM-as-judge 프롬프트 생성과 판정 파싱 구현

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: eval_case 시드 CLI와 데모 케이스

**Files:**
- Create: `repoview/eval_seed.py`, `eval_cases/mini_repo.json`
- Test: `tests/test_eval_seed.py`

**Interfaces:**
- Consumes: `eval_case` 테이블 (Phase 1+2에서 이미 생성됨)
- Produces: `repoview.eval_seed.seed_eval_cases(conn, repo_id: int, cases: list[dict]) -> int` (삽입된 개수 반환, `question` 기준으로 이미 있는 케이스는 건너뜀)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_eval_seed.py`:

```python
from repoview.eval_seed import seed_eval_cases
from repoview.indexer import index_repo


def test_seed_eval_cases_inserts_rows(conn, mini_repo):
    repo_id = index_repo(conn, "MiniRepo", mini_repo)["repo_id"]
    cases = [
        {
            "question": "성능 문제 있어?",
            "expected_finding": "N+1 쿼리",
            "expected_file_path": "src/main/java/com/example/UserService.java",
            "expected_line_start": 11,
            "expected_line_end": 14,
            "category": "n_plus_one",
            "is_planted": 1,
        }
    ]

    inserted = seed_eval_cases(conn, repo_id, cases)

    assert inserted == 1
    row = conn.execute("SELECT * FROM eval_case WHERE repo_id = ?", (repo_id,)).fetchone()
    assert row["question"] == "성능 문제 있어?"
    assert row["is_planted"] == 1


def test_seed_eval_cases_is_idempotent_on_question(conn, mini_repo):
    repo_id = index_repo(conn, "MiniRepo", mini_repo)["repo_id"]
    cases = [
        {"question": "중복 질문", "expected_finding": "아무거나", "category": "negative", "is_planted": 0}
    ]

    first = seed_eval_cases(conn, repo_id, cases)
    second = seed_eval_cases(conn, repo_id, cases)

    assert first == 1
    assert second == 0
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM eval_case WHERE repo_id = ?", (repo_id,)
    ).fetchone()["n"]
    assert count == 1


def test_seed_eval_cases_defaults_optional_fields_to_none(conn, mini_repo):
    repo_id = index_repo(conn, "MiniRepo", mini_repo)["repo_id"]
    cases = [{"question": "질문", "expected_finding": "설명", "category": "negative", "is_planted": 0}]

    seed_eval_cases(conn, repo_id, cases)

    row = conn.execute("SELECT * FROM eval_case WHERE repo_id = ?", (repo_id,)).fetchone()
    assert row["expected_file_path"] is None
    assert row["expected_line_start"] is None
```

`tests/test_eval_seed.py` 끝에 데모 JSON 파일 검증 테스트도 추가:

```python
import json
from pathlib import Path


def test_mini_repo_eval_cases_file_is_valid_json():
    path = Path(__file__).parent.parent / "eval_cases" / "mini_repo.json"
    cases = json.loads(path.read_text(encoding="utf-8"))

    assert len(cases) >= 2
    assert any(c["category"] == "negative" for c in cases)
    assert any(c.get("is_planted") == 1 for c in cases)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_eval_seed.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'repoview.eval_seed'`, `eval_cases/mini_repo.json`도 아직 없어 FileNotFoundError

- [ ] **Step 3: `repoview/eval_seed.py` 구현**

```python
"""eval_case 시드 CLI: python -m repoview.eval_seed eval_cases/mini_repo.json --repo MiniRepo"""

import argparse
import json

from repoview.db import get_connection, init_db


def seed_eval_cases(conn, repo_id: int, cases: list[dict]) -> int:
    inserted = 0
    for case in cases:
        existing = conn.execute(
            "SELECT 1 FROM eval_case WHERE repo_id = ? AND question = ?",
            (repo_id, case["question"]),
        ).fetchone()
        if existing:
            continue

        conn.execute(
            """
            INSERT INTO eval_case
                (repo_id, question, expected_finding, expected_file_path,
                 expected_line_start, expected_line_end, category, is_planted)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                repo_id,
                case["question"],
                case["expected_finding"],
                case.get("expected_file_path"),
                case.get("expected_line_start"),
                case.get("expected_line_end"),
                case.get("category"),
                1 if case.get("is_planted") else 0,
            ),
        )
        inserted += 1

    conn.commit()
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="JSON 파일에서 eval_case를 시드한다")
    parser.add_argument("cases_file", help="eval case JSON 파일 경로")
    parser.add_argument("--repo", required=True, help="레포 이름")
    args = parser.parse_args()

    conn = get_connection()
    init_db(conn)

    row = conn.execute("SELECT id FROM repo WHERE name = ?", (args.repo,)).fetchone()
    if row is None:
        raise SystemExit(f"{args.repo}가 인덱싱되지 않았습니다. 먼저 python -m repoview.index를 실행하세요.")

    with open(args.cases_file, encoding="utf-8") as f:
        cases = json.load(f)

    count = seed_eval_cases(conn, row["id"], cases)
    print(f"[완료] {args.repo}: {count}개 eval_case 시드 (건너뜀 {len(cases) - count}개)")

    conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: `eval_cases/mini_repo.json` 작성**

레포 루트에 `eval_cases/` 디렉토리를 만들고 `eval_cases/mini_repo.json`:

```json
[
  {
    "question": "성능 관점에서 UserService에 문제 있어?",
    "expected_finding": "findAllWithOrders가 사용자마다 findOrdersByUserId를 반복 호출해 N+1 쿼리가 발생한다",
    "expected_file_path": "src/main/java/com/example/UserService.java",
    "expected_line_start": 11,
    "expected_line_end": 14,
    "category": "n_plus_one",
    "is_planted": 1
  },
  {
    "question": "OrderController에 보안 취약점 있어?",
    "expected_finding": "OrderController.list()는 UserService에 단순 위임만 하며 특별한 보안 문제가 없다",
    "category": "negative",
    "is_planted": 0
  }
]
```

이 파일은 `tests/fixtures/mini_repo`(Java+TS 가짜 레포)를 대상으로 한 데모/회귀 테스트용이다. `UserService.java:11-14`의 반복 호출 루프가 N+1 쿼리 패턴의 실제 근거다.

- [ ] **Step 5: 테스트 통과 확인**

Run: `pytest tests/test_eval_seed.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: 전체 테스트 회귀 확인**

Run: `pytest -v`
Expected: 이전 누적 + 신규 4개 통과

- [ ] **Step 7: 커밋**

```bash
git add repoview/eval_seed.py eval_cases/mini_repo.json tests/test_eval_seed.py
git commit -m "$(cat <<'EOF'
eval_case JSON 시드 CLI와 mini_repo 데모 케이스 구현

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: eval 오케스트레이션 (`run_eval`)

**Files:**
- Create: `repoview/eval.py` (이번 태스크에서는 `run_eval`과 헬퍼만, CLI는 Task 6)
- Test: `tests/test_eval.py`

**Interfaces:**
- Consumes: `run_session` (`repoview/agent/loop.py`), `extract_citations`/`matches_file` (Task 2), `judge_case` (Task 3), `seed_eval_cases` (Task 4), `MODEL_PRICING` (Task 1)
- Produces: `repoview.eval.list_eval_cases(conn, repo_id: int) -> list[sqlite3.Row]`, `repoview.eval.estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float`, `repoview.eval.run_eval(conn, repo_id: int, cases: list, llm, judge_llm, *, model: str, phase: int, embedding_client=None) -> dict`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_eval.py`:

```python
import pytest

from repoview.agent.llm import FakeLLM, make_text_response
from repoview.eval import estimate_cost_usd, list_eval_cases, run_eval
from repoview.eval_seed import seed_eval_cases
from repoview.indexer import index_repo


@pytest.fixture
def repo_id(conn, mini_repo):
    return index_repo(conn, "MiniRepo", mini_repo)["repo_id"]


@pytest.fixture
def seeded_cases(conn, repo_id):
    cases = [
        {
            "question": "성능 관점에서 UserService에 문제 있어?",
            "expected_finding": "findAllWithOrders가 사용자마다 findOrdersByUserId를 반복 호출해 N+1 쿼리가 발생한다",
            "expected_file_path": "src/main/java/com/example/UserService.java",
            "expected_line_start": 11,
            "expected_line_end": 14,
            "category": "n_plus_one",
            "is_planted": 1,
        },
        {
            "question": "OrderController에 보안 취약점 있어?",
            "expected_finding": "OrderController.list()는 단순 위임만 하므로 보안 문제가 없다",
            "category": "negative",
            "is_planted": 0,
        },
    ]
    seed_eval_cases(conn, repo_id, cases)
    return list_eval_cases(conn, repo_id)


def test_estimate_cost_usd_uses_pricing_table():
    cost = estimate_cost_usd("gpt-4o-mini", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == pytest.approx(0.15 + 0.60)


def test_estimate_cost_usd_defaults_to_zero_for_unknown_model():
    assert estimate_cost_usd("unknown-model", 1000, 1000) == 0.0


def test_run_eval_marks_positive_case_detected_when_judge_yes_and_citation_matches(
    conn, repo_id, seeded_cases
):
    positive = [c for c in seeded_cases if c["category"] != "negative"]
    llm = FakeLLM([
        make_text_response(
            "문제: N+1 쿼리\n근거: `src/main/java/com/example/UserService.java:11-14`\n"
            "영향: 느림\n제안: JOIN 사용"
        )
    ])
    judge_llm = FakeLLM([make_text_response("YES\n리뷰가 N+1 문제를 정확히 지적했다")])

    stats = run_eval(conn, repo_id, positive, llm, judge_llm, model="gpt-4o", phase=2)

    assert stats["total_cases"] == 1
    assert stats["passed_cases"] == 1
    assert stats["detection_rate"] == 1.0

    row = conn.execute("SELECT detected, false_positive FROM eval_result").fetchone()
    assert row["detected"] == 1
    assert row["false_positive"] == 0


def test_run_eval_marks_positive_case_undetected_when_citation_missing(conn, repo_id, seeded_cases):
    positive = [c for c in seeded_cases if c["category"] != "negative"]
    llm = FakeLLM([make_text_response("문제를 발견하지 못했습니다")])
    judge_llm = FakeLLM([make_text_response("NO\n리뷰가 문제를 언급하지 않았다")])

    stats = run_eval(conn, repo_id, positive, llm, judge_llm, model="gpt-4o", phase=2)

    assert stats["passed_cases"] == 0
    assert stats["detection_rate"] == 0.0


def test_run_eval_marks_false_positive_on_negative_case(conn, repo_id, seeded_cases):
    negative = [c for c in seeded_cases if c["category"] == "negative"]
    llm = FakeLLM([
        make_text_response(
            "문제: 인증 우회 가능\n근거: `src/main/java/com/example/OrderController.java:10-12`\n"
            "영향: 심각\n제안: 인증 추가"
        )
    ])
    judge_llm = FakeLLM([make_text_response("YES\n리뷰가 실재하지 않는 보안 문제를 주장했다")])

    stats = run_eval(conn, repo_id, negative, llm, judge_llm, model="gpt-4o", phase=2)

    assert stats["fpr"] == 1.0
    row = conn.execute("SELECT detected, false_positive FROM eval_result").fetchone()
    assert row["false_positive"] == 1
    assert row["detected"] == 0


def test_run_eval_computes_citation_accuracy_against_repo_file(conn, repo_id, seeded_cases):
    positive = [c for c in seeded_cases if c["category"] != "negative"]
    llm = FakeLLM([
        make_text_response(
            "문제: N+1 쿼리\n근거: `src/main/java/com/example/UserService.java:11-14`, "
            "`no/such/file.py:1`\n영향: 느림\n제안: JOIN"
        )
    ])
    judge_llm = FakeLLM([make_text_response("YES\n확인됨")])

    stats = run_eval(conn, repo_id, positive, llm, judge_llm, model="gpt-4o", phase=2)

    assert stats["citation_accuracy"] == 0.5


def test_run_eval_writes_eval_run_row_and_links_session(conn, repo_id, seeded_cases):
    llm = FakeLLM([make_text_response("문제 없음")] * len(seeded_cases))
    judge_llm = FakeLLM([make_text_response("NO\n근거 없음")] * len(seeded_cases))

    run_eval(conn, repo_id, seeded_cases, llm, judge_llm, model="gpt-4o", phase=2)

    run_row = conn.execute("SELECT * FROM eval_run").fetchone()
    assert run_row["phase"] == 2
    assert run_row["model"] == "gpt-4o"
    assert run_row["total_cases"] == 1  # negative 케이스는 total_cases 집계에서 제외
    assert run_row["finished_at"] is not None

    result_rows = conn.execute("SELECT session_id FROM eval_result").fetchall()
    assert len(result_rows) == 2
    assert all(r["session_id"] is not None for r in result_rows)


def test_run_eval_raises_on_empty_case_list(conn, repo_id):
    with pytest.raises(ValueError):
        run_eval(conn, repo_id, [], FakeLLM([]), FakeLLM([]), model="gpt-4o", phase=2)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_eval.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'repoview.eval'`

- [ ] **Step 3: 구현**

`repoview/eval.py`:

```python
import json
import sqlite3
import time

from repoview.agent.llm import LLM
from repoview.agent.loop import run_session
from repoview.config import MODEL_PRICING
from repoview.eval_citations import extract_citations, matches_file
from repoview.eval_judge import judge_case

NEGATIVE_CATEGORY = "negative"


def list_eval_cases(conn: sqlite3.Connection, repo_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM eval_case WHERE repo_id = ? ORDER BY id", (repo_id,)
    ).fetchall()


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        return 0.0
    input_price, output_price = pricing
    return (input_tokens / 1_000_000) * input_price + (output_tokens / 1_000_000) * output_price


def run_eval(
    conn: sqlite3.Connection,
    repo_id: int,
    cases: list,
    llm: LLM,
    judge_llm: LLM,
    *,
    model: str,
    phase: int,
    embedding_client=None,
) -> dict:
    if not cases:
        raise ValueError("eval_case가 없습니다. 먼저 python -m repoview.eval_seed로 시드하세요.")

    eval_run_id = _create_eval_run(conn, phase, model)

    citation_hits = 0
    citation_total = 0
    positive_total = 0
    positive_passed = 0
    negative_total = 0
    false_positives = 0
    total_cost = 0.0
    total_latency_ms = 0

    for case in cases:
        is_negative = case["category"] == NEGATIVE_CATEGORY

        started = time.monotonic()
        result = run_session(
            conn, repo_id, case["question"], llm,
            model=model, phase=phase, embedding_client=embedding_client,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        review = result.final_review or ""

        citations = extract_citations(review)
        citation_total += len(citations)
        citation_hits += sum(
            1 for c in citations if _citation_path_exists(conn, repo_id, c["file_path"])
        )

        verdict, reason = judge_case(
            judge_llm, case["question"], case["expected_finding"], review,
            is_negative=is_negative,
        )

        if is_negative:
            negative_total += 1
            detected = 0 if verdict else 1
            false_positive = 1 if verdict else 0
            false_positives += false_positive
        else:
            positive_total += 1
            file_match = (
                case["expected_file_path"] is None
                or matches_file(citations, case["expected_file_path"])
            )
            detected = 1 if (verdict and file_match) else 0
            positive_passed += detected
            false_positive = 0

        total_cost += estimate_cost_usd(model, result.input_tokens, result.output_tokens)
        total_latency_ms += latency_ms

        _record_eval_result(
            conn, eval_run_id, case["id"], result.session_id, detected, false_positive, reason,
        )

    stats = {
        "total_cases": positive_total,
        "passed_cases": positive_passed,
        "detection_rate": (positive_passed / positive_total) if positive_total else 0.0,
        "fpr": (false_positives / negative_total) if negative_total else 0.0,
        "citation_accuracy": (citation_hits / citation_total) if citation_total else 1.0,
        "avg_cost_usd": (total_cost / len(cases)) if cases else 0.0,
        "avg_latency_ms": (total_latency_ms / len(cases)) if cases else 0.0,
    }
    _finish_eval_run(conn, eval_run_id, stats)
    return stats


def _citation_path_exists(conn: sqlite3.Connection, repo_id: int, file_path: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM repo_file WHERE repo_id = ? AND path = ?",
        (repo_id, file_path.replace("\\", "/")),
    ).fetchone()
    return row is not None


def _create_eval_run(conn: sqlite3.Connection, phase: int, model: str) -> int:
    cursor = conn.execute(
        "INSERT INTO eval_run (phase, model) VALUES (?, ?)", (phase, model)
    )
    conn.commit()
    return int(cursor.lastrowid)


def _record_eval_result(
    conn: sqlite3.Connection,
    eval_run_id: int,
    eval_case_id: int,
    session_id: int,
    detected: int,
    false_positive: int,
    judge_reason: str,
) -> None:
    conn.execute(
        """
        INSERT INTO eval_result
            (eval_run_id, eval_case_id, session_id, detected, false_positive, judge_reason)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (eval_run_id, eval_case_id, session_id, detected, false_positive, judge_reason),
    )
    conn.commit()


def _finish_eval_run(conn: sqlite3.Connection, eval_run_id: int, stats: dict) -> None:
    conn.execute(
        """
        UPDATE eval_run
        SET total_cases = ?, passed_cases = ?, detection_rate = ?, notes = ?, finished_at = datetime('now')
        WHERE id = ?
        """,
        (
            stats["total_cases"],
            stats["passed_cases"],
            stats["detection_rate"],
            json.dumps(stats, ensure_ascii=False),
            eval_run_id,
        ),
    )
    conn.commit()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_eval.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: 전체 테스트 회귀 확인**

Run: `pytest -v`
Expected: 이전 누적 + 신규 7개 통과

- [ ] **Step 6: 커밋**

```bash
git add repoview/eval.py tests/test_eval.py
git commit -m "$(cat <<'EOF'
케이스별 세션 실행·판정·인용 검증을 수행하는 eval 오케스트레이션 구현

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: eval CLI 진입점

**Files:**
- Modify: `repoview/eval.py`
- Test: `tests/test_eval.py`에 추가

**Interfaces:**
- Consumes: `run_eval`/`list_eval_cases` (Task 5), `OpenAILLM` (`repoview/agent/llm.py`), `OpenAIEmbeddingClient` (`repoview/embedding_client.py`), `REPOS`/`JUDGE_MODEL`/`OPENAI_MODEL` (`repoview/config.py`)
- Produces: `repoview.eval.validate_judge_model(model: str, judge_model: str) -> None` (다르면 통과, 같으면 `ValueError`), CLI `python -m repoview.eval --repo <이름> --phase <2|3>`

기존 `index.py`/`embed.py`와 마찬가지로 CLI의 `main()`/argparse 배선 자체는 이 저장소에서 단위 테스트하지 않는다(두 파일 모두 선례가 없음) — 대신 안전장치인 `validate_judge_model`만 TDD로 검증한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_eval.py` 끝에 추가:

```python
from repoview.eval import validate_judge_model


def test_validate_judge_model_passes_when_different():
    validate_judge_model("gpt-4o", "gpt-4o-mini")  # 예외 없이 통과해야 함


def test_validate_judge_model_raises_when_same():
    with pytest.raises(ValueError):
        validate_judge_model("gpt-4o", "gpt-4o")
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_eval.py -v`
Expected: FAIL — `ImportError: cannot import name 'validate_judge_model' from 'repoview.eval'`

- [ ] **Step 3: `repoview/eval.py` 수정**

파일 상단 임포트에 추가:

```python
import argparse

from repoview.agent.llm import OpenAILLM
from repoview.config import JUDGE_MODEL, OPENAI_MODEL, REPOS
from repoview.db import get_connection, init_db
from repoview.embedding_client import OpenAIEmbeddingClient
```

(`from repoview.agent.llm import LLM`은 그대로 두고 `OpenAILLM`을 추가로 임포트한다.)

파일 끝에 추가:

```python
def validate_judge_model(model: str, judge_model: str) -> None:
    if model == judge_model:
        raise ValueError(
            f"판정 모델({judge_model})이 평가 대상 모델({model})과 같습니다. "
            "REPOVIEW_JUDGE_MODEL을 다른 모델로 설정하세요."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase별 리뷰 탐지율을 측정한다")
    parser.add_argument("--repo", required=True, choices=sorted(REPOS), help="평가할 레포 이름")
    parser.add_argument(
        "--phase", type=int, required=True, choices=[2, 3],
        help="평가할 Phase (2=키워드 검색만, 3=RAG 포함)",
    )
    args = parser.parse_args()

    validate_judge_model(OPENAI_MODEL, JUDGE_MODEL)

    conn = get_connection()
    init_db(conn)

    row = conn.execute("SELECT id FROM repo WHERE name = ?", (args.repo,)).fetchone()
    if row is None:
        raise SystemExit(f"{args.repo}가 인덱싱되지 않았습니다. 먼저 python -m repoview.index를 실행하세요.")
    repo_id = row["id"]

    cases = list_eval_cases(conn, repo_id)

    llm = OpenAILLM(model=OPENAI_MODEL)
    judge_llm = OpenAILLM(model=JUDGE_MODEL)
    embedding_client = OpenAIEmbeddingClient() if args.phase == 3 else None

    stats = run_eval(
        conn, repo_id, cases, llm, judge_llm,
        model=OPENAI_MODEL, phase=args.phase, embedding_client=embedding_client,
    )

    print(
        f"[Phase {args.phase}] {args.repo}: 탐지율 {stats['detection_rate']:.0%}, "
        f"오탐율 {stats['fpr']:.0%}, 인용 정확도 {stats['citation_accuracy']:.0%}, "
        f"평균 비용 ${stats['avg_cost_usd']:.4f}, 평균 지연 {stats['avg_latency_ms']:.0f}ms"
    )

    conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_eval.py -v`
Expected: PASS (기존 7개 + 신규 2개 = 9 passed)

- [ ] **Step 5: 전체 테스트 회귀 확인**

Run: `pytest -v`
Expected: 모두 통과. 정확한 총 개수는 실행 결과를 신뢰한다.

- [ ] **Step 6: 커밋**

```bash
git add repoview/eval.py tests/test_eval.py
git commit -m "$(cat <<'EOF'
eval CLI 진입점과 판정 모델 분리 가드 구현

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: 실제 레포 eval 셋 작성 (수동/조사 작업)

**Files:**
- 코드 변경 없음 — 조사 후 `eval_cases/localquest.json`, `eval_cases/songpyeon.json` 작성 및 시드

**Interfaces:**
- Consumes: `seed_eval_cases` (Task 4), 실제 `LocalQuest`/`Songpyeon` 체크아웃(`repoview/config.py`의 `REPOS` 경로)

이 태스크는 두 외부 레포를 직접 조사해야 하므로 TDD 사이클이 아니라 조사·작성 절차다. `docs/06-test-eval-design.md` B-1절이 요구하는 3가지 케이스 유형을 모두 포함해야 한다.

- [ ] **Step 1: 실제 사례 케이스 확보 (`is_planted=0`)**

`docs/06-test-eval-design.md`가 이미 지목한 실제 트러블슈팅 사례들의 정확한 `file_path:line`을 각 레포에서 `grep`/`read_file`로 확인한다:
- LocalQuest: 인코딩 깨짐, 관리자 공지 404
- Songpyeon: CJS·ESM 이중 로드, 재접속 시 `onAuth` 미재호출 취약점, `.dockerignore` 재귀 매칭 누락

확인한 각 사례를 `question`(사용자가 물어볼 법한 자연어 질문), `expected_finding`, `expected_file_path`, `expected_line_start/end`, `category`(예: `encoding`, `404`, `dual_module_load`, `auth_reconnect`, `dockerignore`), `is_planted: 0`으로 JSON에 기록한다.

- [ ] **Step 2: 심은 버그 케이스 작성 (`is_planted=1`)**

두 레포에 실제로 다음 패턴의 버그를 최소 1곳씩 심고(각 레포의 별도 브랜치에서, 이 계획 범위 밖의 결정이므로 레포 소유자 확인 후 진행), 심은 위치를 `expected_file_path`/`expected_line_start/end`로 기록한다: N+1 쿼리, 하드코딩된 시크릿, 인덱스 없는 대용량 조회, 동기 블로킹 호출.

- [ ] **Step 3: 네거티브 케이스 작성**

문제가 없는 파일을 골라 "여기 성능/보안 문제 있어?" 형태의 질문을 만들고 `category: "negative"`, `is_planted: 0`으로 기록한다. `docs/06-test-eval-design.md` B-1절 기준 최소 전체 케이스의 20~30%를 네거티브로 채운다.

- [ ] **Step 4: 목표 수량 확인 및 시드**

레포당 20~30개(실제+심은 버그+네거티브 합산)를 목표로 `eval_cases/localquest.json`, `eval_cases/songpyeon.json`을 완성한 뒤:

```bash
python -m repoview.index
python -m repoview.embed
python -m repoview.eval_seed eval_cases/localquest.json --repo LocalQuest
python -m repoview.eval_seed eval_cases/songpyeon.json --repo Songpyeon
```

- [ ] **Step 5: Phase 2/3 비교 실행**

```bash
python -m repoview.eval --repo LocalQuest --phase 2
python -m repoview.eval --repo LocalQuest --phase 3
python -m repoview.eval --repo Songpyeon --phase 2
python -m repoview.eval --repo Songpyeon --phase 3
```

네 번의 출력(탐지율/오탐율/인용 정확도/비용/지연)을 기록해 PRD 32번째 줄의 "Phase별 탐지율 개선"을 수치로 보여준다.

- [ ] **Step 6: 결과 보고**

코드 변경이 없으므로 커밋하지 않는다. 대신 다음 보고에 Step 5의 4개 실행 결과와, 실제 eval 셋 케이스 수·구성을 포함한다.

---

## 완료 기준

- [ ] `pytest`가 전부 통과한다 (실제 API 키 없이).
- [ ] `python -m repoview.eval_seed eval_cases/mini_repo.json --repo MiniRepo`(인덱싱된 MiniRepo 대상)로 데모 케이스가 시드된다.
- [ ] `run_eval`이 양성/음성 케이스 모두에서 `detected`/`false_positive`를 올바르게 기록하고, 인용 정확도·비용·지연을 집계한다.
- [ ] `validate_judge_model`이 판정 모델과 평가 모델이 같을 때 명확히 막는다.
- [ ] 기존 Phase 1~3 테스트가 전부 그대로 통과한다 — 하위 호환이 깨지지 않았다.

## 이 계획 이후로 미룬 것

| 항목 | 이유 |
|---|---|
| 실제 LocalQuest/Songpyeon 20~30개 eval 셋 전체 | Task 7에서 절차만 제시 — 두 외부 레포 조사·버그 심기는 별도 분량. |
| Phase 4 에이전트 루프 강화(반복 감지, 실시간 인용 사후검증) | `docs/05-agent-design.md` 4절 기준 별도 계획. eval CLI의 `--phase`는 이 계획에서 2·3만 지원. |
| train/test 70/30 분리, 케이스당 3회 평균 | eval 셋 규모가 아직 없어 지금 단계에서는 과설계. 데이터가 쌓인 뒤 별도로 다룬다. |
