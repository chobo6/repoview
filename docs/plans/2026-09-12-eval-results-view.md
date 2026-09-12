# Eval 결과 조회 API + 프론트엔드 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `python -m repoview.eval`로 이미 쌓인 eval 실행 기록을 웹에서 조회할 수 있게 한다 — 레포별 Phase 비교표(API `GET /evals/runs`)와 케이스별 상세(`GET /evals/runs/{id}`), 그리고 프론트엔드 탭 화면.

**Architecture:** `eval_run`에 `repo_id` 컬럼을 추가해 지금까지 `eval_result→eval_case` 경유로만 가능했던 "이 실행이 어느 레포 것인지" 조회를 직접 컬럼 조회로 바꾼다. API는 기존 `list_sessions`/`get_session` 패턴을 그대로 따르는 읽기 전용 GET 2개만 추가한다(실행 자체는 여전히 CLI). 프론트엔드는 새 의존성 없이 `App.jsx`에 탭 상태만 추가하고, eval 화면 자체는 별도 컴포넌트(`EvalResults.jsx`)로 분리한다.

**Tech Stack:** 기존 스택 그대로 (FastAPI, sqlite3, React 19, 순수 `fetch`). 신규 의존성 없음.

**Spec:** `docs/03-db-design.md`(`eval_run` DDL), `docs/04-api-spec.md` 3절 — 2026-09-12 커밋 `6b41f9e`에서 확정됨.

## Global Constraints

- 백엔드 테스트는 FastAPI `TestClient` + `app.dependency_overrides` 패턴을 쓴다 (`tests/test_api.py`의 기존 `client` fixture 재사용) — 실제 OpenAI 호출 없음.
- 한국어 커밋 메시지, `feat:`/`fix:` 같은 접두사 없음.
- `repoview/db.py`의 `_migrate()` 패턴(PRAGMA table_info로 컬럼 존재 확인 후 ALTER TABLE)을 그대로 따를 것 — try/except로 예외를 삼키지 말 것.
- 프론트엔드는 새 npm 의존성을 추가하지 않는다 — 라우터 없이 `useState` 탭 토글로 처리.
- 같은 레포+Phase를 여러 번 실행한 기록을 "최신 것만"으로 걸러내지 않는다 — 전체 이력을 `started_at DESC`로 보여준다 (`docs/04-api-spec.md` 3절 "판단 이유" 참고).

---

### Task 1: `eval_run.repo_id` 컬럼과 백필

**Files:**
- Modify: `repoview/db.py` (`_migrate()`)
- Modify: `repoview/eval.py` (`_create_eval_run`, `run_eval`의 호출부)
- Test: `tests/test_db.py`, `tests/test_eval.py`

**Interfaces:**
- Consumes: 없음 (독립 작업)
- Produces: `eval_run.repo_id`(INTEGER, nullable, `repo(id)` 참조) 컬럼. `_create_eval_run(conn, repo_id, phase, model) -> int` — Task 2가 `GET /evals/runs`에서 이 컬럼을 직접 SELECT/필터링에 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_db.py`의 `test_citation_warnings_migration_is_safe_to_run_twice` 다음에 추가:

```python
def test_eval_run_has_repo_id_column(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(eval_run)")}
    assert "repo_id" in columns


def test_eval_run_repo_id_is_backfilled_from_existing_eval_results(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)

    conn.execute("INSERT INTO repo (id, name, root_path) VALUES (1, 'R', '/r')")
    conn.execute(
        "INSERT INTO eval_case (id, repo_id, question, expected_finding) VALUES (1, 1, 'q', 'f')"
    )
    conn.execute("INSERT INTO eval_run (id, phase, model) VALUES (1, 2, 'gpt-4o')")
    conn.execute(
        "INSERT INTO eval_result (eval_run_id, eval_case_id, detected) VALUES (1, 1, 0)"
    )
    conn.commit()

    # 위 INSERT들은 repo_id를 명시하지 않았으므로 eval_run.repo_id는 NULL이다.
    # init_db를 다시 태워 백필이 실제로 채우는지 확인한다.
    init_db(conn)

    row = conn.execute("SELECT repo_id FROM eval_run WHERE id = 1").fetchone()
    assert row["repo_id"] == 1
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_db.py -k "eval_run_has_repo_id or eval_run_repo_id_is_backfilled" -v`
Expected: FAIL — `assert "repo_id" in columns`에서 실패 (컬럼 없음).

- [ ] **Step 3: `repoview/db.py`의 `_migrate()` 수정**

`_migrate` 함수 끝(현재 `session_columns`/`citation_warnings` 블록 다음)에 추가:

```python
    eval_run_columns = {row["name"] for row in conn.execute("PRAGMA table_info(eval_run)")}
    if "repo_id" not in eval_run_columns:
        conn.execute("ALTER TABLE eval_run ADD COLUMN repo_id INTEGER REFERENCES repo(id)")

    # repo_id가 비어있는 기존 행(마이그레이션 이전에 생성된 eval_run)을 채운다.
    # WHERE repo_id IS NULL이라 매번 호출해도 안전하고, 이미 채워진 행은 건드리지 않는다.
    conn.execute(
        """
        UPDATE eval_run
        SET repo_id = (
            SELECT ec.repo_id
            FROM eval_result er
            JOIN eval_case ec ON ec.id = er.eval_case_id
            WHERE er.eval_run_id = eval_run.id
            LIMIT 1
        )
        WHERE repo_id IS NULL
        """
    )
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_db.py -v`
Expected: PASS (전체)

- [ ] **Step 5: `repoview/eval.py`의 `_create_eval_run`과 호출부 수정 — 실패하는 테스트 먼저**

`tests/test_eval.py`의 `test_run_eval_writes_eval_run_row_and_links_session` 다음에 추가:

```python
def test_run_eval_records_repo_id_on_eval_run(conn, repo_id, seeded_cases):
    llm = FakeLLM([make_text_response("문제 없음")] * len(seeded_cases))
    judge_llm = FakeLLM([make_text_response("NO\n근거 없음")] * len(seeded_cases))

    run_eval(conn, repo_id, seeded_cases, llm, judge_llm, model="gpt-4o", phase=2)

    run_row = conn.execute("SELECT repo_id FROM eval_run").fetchone()
    assert run_row["repo_id"] == repo_id
```

- [ ] **Step 6: 테스트 실패 확인**

Run: `pytest tests/test_eval.py -k records_repo_id -v`
Expected: FAIL — `assert None == repo_id` (아직 repo_id를 안 저장하므로 NULL).

- [ ] **Step 7: `repoview/eval.py` 수정**

`_create_eval_run` 함수(현재 141~146번째 줄)를 교체:

```python
def _create_eval_run(conn: sqlite3.Connection, repo_id: int, phase: int, model: str) -> int:
    cursor = conn.execute(
        "INSERT INTO eval_run (repo_id, phase, model) VALUES (?, ?, ?)", (repo_id, phase, model)
    )
    conn.commit()
    return int(cursor.lastrowid)
```

`run_eval` 안의 호출부(현재 50번째 줄)를 교체:

```python
    eval_run_id = _create_eval_run(conn, repo_id, phase, model)
```

(`run_eval`은 이미 `repo_id`를 파라미터로 받고 있으므로 그대로 전달하면 된다.)

- [ ] **Step 8: 테스트 통과 확인**

Run: `pytest tests/test_eval.py -v`
Expected: PASS (전체)

- [ ] **Step 9: 전체 테스트 스위트 확인**

Run: `pytest -q`
Expected: PASS (전체)

- [ ] **Step 10: 커밋**

```bash
git add repoview/db.py repoview/eval.py tests/test_db.py tests/test_eval.py
git commit -m "$(cat <<'EOF'
eval_run에 repo_id 컬럼 추가하고 기존 행 백필

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `GET /api/evals/runs`, `GET /api/evals/runs/{id}`

**Files:**
- Modify: `repoview/api/app.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: Task 1의 `eval_run.repo_id` 컬럼.
- Produces: 프론트엔드가 Task 3에서 호출할 두 엔드포인트. `GET /api/evals/runs?repo_id=<int>` → `list[dict]`(각 dict에 `id, repo_id, repo_name, phase, model, total_cases, passed_cases, detection_rate, fpr, citation_accuracy, avg_cost_usd, avg_latency_ms, started_at, finished_at`). `GET /api/evals/runs/{id}` → 위 필드 전부 + `results: list[dict]`(각 `eval_case_id, session_id, detected, false_positive, judge_reason, question, category, is_planted, expected_finding`).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_api.py` 상단에 `import json` 추가(아직 없다면), 파일 끝에 추가:

```python
def test_list_eval_runs_returns_runs_for_repo(client, conn):
    repo_id = client.get("/api/repos").json()[0]["id"]
    conn.execute(
        """
        INSERT INTO eval_run
            (repo_id, phase, model, total_cases, passed_cases, detection_rate, notes)
        VALUES (?, 2, 'gpt-4o', 1, 1, 1.0, ?)
        """,
        (repo_id, json.dumps({
            "fpr": 0.0, "citation_accuracy": 1.0,
            "avg_cost_usd": 0.0124, "avg_latency_ms": 5120,
        })),
    )
    conn.commit()

    response = client.get(f"/api/evals/runs?repo_id={repo_id}")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["repo_name"] == "MiniRepo"
    assert body[0]["detection_rate"] == 1.0
    assert body[0]["fpr"] == 0.0
    assert body[0]["citation_accuracy"] == 1.0
    assert body[0]["avg_cost_usd"] == 0.0124
    assert body[0]["avg_latency_ms"] == 5120
    assert "notes" not in body[0]


def test_list_eval_runs_without_filter_returns_all(client, conn):
    repo_id = client.get("/api/repos").json()[0]["id"]
    conn.execute(
        "INSERT INTO eval_run (repo_id, phase, model, notes) VALUES (?, 2, 'gpt-4o', '{}')",
        (repo_id,),
    )
    conn.commit()

    response = client.get("/api/evals/runs")
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_get_eval_run_detail_includes_case_results(client, conn):
    repo_id = client.get("/api/repos").json()[0]["id"]
    cursor = conn.execute(
        """
        INSERT INTO eval_case (repo_id, question, expected_finding, category)
        VALUES (?, 'OrderController에 문제 있어?', '문제 없음', 'negative')
        """,
        (repo_id,),
    )
    eval_case_id = cursor.lastrowid
    cursor = conn.execute(
        "INSERT INTO eval_run (repo_id, phase, model, notes) VALUES (?, 2, 'gpt-4o', '{}')",
        (repo_id,),
    )
    run_id = cursor.lastrowid
    conn.execute(
        """
        INSERT INTO eval_result (eval_run_id, eval_case_id, session_id, detected, judge_reason)
        VALUES (?, ?, 42, 1, '문제 없다고 정확히 판단함')
        """,
        (run_id, eval_case_id),
    )
    conn.commit()

    response = client.get(f"/api/evals/runs/{run_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == run_id
    assert len(body["results"]) == 1
    result = body["results"][0]
    assert result["question"] == "OrderController에 문제 있어?"
    assert result["category"] == "negative"
    assert result["is_planted"] == 0
    assert result["detected"] == 1
    assert result["judge_reason"] == "문제 없다고 정확히 판단함"
    assert result["session_id"] == 42


def test_get_missing_eval_run_returns_404(client):
    response = client.get("/api/evals/runs/9999")
    assert response.status_code == 404
    assert "error" in response.json()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_api.py -k eval_run -v`
Expected: FAIL — `404 Not Found` (라우트가 아직 없음, FastAPI 기본 404).

- [ ] **Step 3: `repoview/api/app.py` 수정**

파일 최상단 import 블록(1번째 줄)에 `import json` 추가:

```python
import json
import sqlite3
from contextlib import asynccontextmanager
```

파일 끝(`get_session` 함수 다음)에 추가:

```python
def _expand_eval_run_notes(row: dict) -> dict:
    notes = json.loads(row.pop("notes", None) or "{}")
    row["fpr"] = notes.get("fpr")
    row["citation_accuracy"] = notes.get("citation_accuracy")
    row["avg_cost_usd"] = notes.get("avg_cost_usd")
    row["avg_latency_ms"] = notes.get("avg_latency_ms")
    return row


@app.get("/api/evals/runs")
def list_eval_runs(
    repo_id: int | None = None,
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    query = """
        SELECT eval_run.*, repo.name AS repo_name
        FROM eval_run
        JOIN repo ON repo.id = eval_run.repo_id
        WHERE 1 = 1
    """
    params: list = []
    if repo_id is not None:
        query += " AND eval_run.repo_id = ?"
        params.append(repo_id)
    query += " ORDER BY eval_run.started_at DESC"

    return [_expand_eval_run_notes(dict(row)) for row in conn.execute(query, params)]


@app.get("/api/evals/runs/{run_id}")
def get_eval_run(run_id: int, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    run_row = conn.execute("SELECT * FROM eval_run WHERE id = ?", (run_id,)).fetchone()
    if run_row is None:
        raise HTTPException(status_code=404, detail=f"eval 실행을 찾을 수 없습니다: {run_id}")

    run = _expand_eval_run_notes(dict(run_row))

    results = conn.execute(
        """
        SELECT
            eval_result.eval_case_id, eval_result.session_id,
            eval_result.detected, eval_result.false_positive, eval_result.judge_reason,
            eval_case.question, eval_case.category, eval_case.is_planted,
            eval_case.expected_finding
        FROM eval_result
        JOIN eval_case ON eval_case.id = eval_result.eval_case_id
        WHERE eval_result.eval_run_id = ?
        ORDER BY eval_result.id
        """,
        (run_id,),
    ).fetchall()
    run["results"] = [dict(r) for r in results]
    return run
```

(`_expand_eval_run_notes`가 두 엔드포인트에서 공유되므로, `list_eval_runs`보다 먼저 정의해야 한다 — 위 순서 그대로 넣으면 된다.)

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_api.py -v`
Expected: PASS (전체)

- [ ] **Step 5: 전체 테스트 스위트 확인**

Run: `pytest -q`
Expected: PASS (전체)

- [ ] **Step 6: 커밋**

```bash
git add repoview/api/app.py tests/test_api.py
git commit -m "$(cat <<'EOF'
GET /api/evals/runs, GET /api/evals/runs/{id} 구현

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: 프론트엔드 — Eval 결과 탭

**Files:**
- Modify: `frontend/src/api.js`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/App.css`
- Create: `frontend/src/EvalResults.jsx`

**Interfaces:**
- Consumes: Task 2의 `GET /api/evals/runs`(옵션 `repo_id`), `GET /api/evals/runs/{id}`. 기존 `frontend/src/api.js`의 `fetchSession(sessionId)`(이미 존재, 그대로 재사용).
- Produces: 없음 (최상위 UI 컴포넌트, 다른 파일이 이걸 가져다 쓰지 않는다).

- [ ] **Step 1: `frontend/src/api.js`에 fetch 함수 2개 추가**

파일 끝에 추가:

```javascript
export async function fetchEvalRuns(repoId) {
  const response = await fetch(`${BASE_URL}/evals/runs?repo_id=${repoId}`)
  if (!response.ok) throw new Error('eval 결과를 불러오지 못했습니다')
  return response.json()
}

export async function fetchEvalRun(runId) {
  const response = await fetch(`${BASE_URL}/evals/runs/${runId}`)
  if (!response.ok) throw new Error('eval 상세 결과를 불러오지 못했습니다')
  return response.json()
}
```

- [ ] **Step 2: `frontend/src/EvalResults.jsx` 신규 생성**

```jsx
import { useEffect, useState } from 'react'
import { fetchEvalRun, fetchEvalRuns, fetchSession } from './api'

function formatPercent(value) {
  return value == null ? '-' : `${Math.round(value * 100)}%`
}

function EvalResults({ repos }) {
  const [repoId, setRepoId] = useState(repos[0]?.id ?? null)
  const [runs, setRuns] = useState([])
  const [selectedRun, setSelectedRun] = useState(null)
  const [session, setSession] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!repoId) return
    setSelectedRun(null)
    setSession(null)
    fetchEvalRuns(repoId)
      .then(setRuns)
      .catch((err) => setError(err.message))
  }, [repoId])

  const handleSelectRun = async (runId) => {
    setSession(null)
    try {
      setSelectedRun(await fetchEvalRun(runId))
    } catch (err) {
      setError(err.message)
    }
  }

  const handleViewSession = async (sessionId) => {
    if (!sessionId) return
    try {
      setSession(await fetchSession(sessionId))
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="eval-results">
      <select value={repoId ?? ''} onChange={(e) => setRepoId(Number(e.target.value))}>
        {repos.map((repo) => (
          <option key={repo.id} value={repo.id}>
            {repo.name}
          </option>
        ))}
      </select>

      {error && <p className="error">{error}</p>}

      <table className="eval-table">
        <thead>
          <tr>
            <th>Phase</th>
            <th>모델</th>
            <th>탐지율</th>
            <th>오탐율</th>
            <th>인용정확도</th>
            <th>비용</th>
            <th>지연</th>
            <th>실행시각</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr
              key={run.id}
              className={selectedRun?.id === run.id ? 'selected' : ''}
              onClick={() => handleSelectRun(run.id)}
            >
              <td>{run.phase}</td>
              <td>{run.model}</td>
              <td>{formatPercent(run.detection_rate)}</td>
              <td>{formatPercent(run.fpr)}</td>
              <td>{formatPercent(run.citation_accuracy)}</td>
              <td>${run.avg_cost_usd?.toFixed(4) ?? '-'}</td>
              <td>{run.avg_latency_ms ? `${Math.round(run.avg_latency_ms)}ms` : '-'}</td>
              <td>{run.started_at}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {selectedRun && (
        <section className="eval-detail">
          <h3>케이스별 결과</h3>
          <ul>
            {selectedRun.results.map((result) => (
              <li key={result.eval_case_id} className={result.false_positive ? 'false-positive' : ''}>
                <p>
                  <strong>{result.question}</strong> ({result.category}
                  {result.is_planted ? ', 심은 버그' : ''})
                </p>
                <p>탐지: {result.detected ? 'O' : 'X'} · 오탐: {result.false_positive ? 'O' : 'X'}</p>
                <p className="meta">{result.judge_reason}</p>
                {result.session_id && (
                  <button type="button" onClick={() => handleViewSession(result.session_id)}>
                    세션 보기
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {session && (
        <section className="result">
          <h3>세션 리뷰</h3>
          <pre className="review">{session.final_review}</pre>
        </section>
      )}
    </div>
  )
}

export default EvalResults
```

- [ ] **Step 3: `frontend/src/App.jsx` 수정 — 탭 토글 추가**

파일 최상단 import를 교체:

```jsx
import { useEffect, useState } from 'react'
import { createSession, fetchRepos, fetchSession } from './api'
import EvalResults from './EvalResults'
import './App.css'
```

`function App() {` 안, `const [error, setError] = useState(null)` 다음 줄에 탭 상태 추가:

```jsx
  const [tab, setTab] = useState('ask')
```

`return (` 블록의 `<main className="app">` 바로 다음(`<h1>RepoView</h1>` 다음)에 탭 버튼 추가:

```jsx
      <nav className="tabs">
        <button type="button" className={tab === 'ask' ? 'active' : ''} onClick={() => setTab('ask')}>
          질문하기
        </button>
        <button type="button" className={tab === 'evals' ? 'active' : ''} onClick={() => setTab('evals')}>
          Eval 결과
        </button>
      </nav>
```

기존 `<form onSubmit={handleSubmit}>`부터 세션 결과를 보여주는 `{session && (...)}` 블록까지 전부를 `{tab === 'ask' && (` ... `)}`로 감싸고, 그 뒤에 eval 탭을 추가한다. 즉 현재:

```jsx
      <form onSubmit={handleSubmit}>
        ...
      </form>

      {error && <p className="error">{error}</p>}

      {session && (
        <section className="result">
          ...
        </section>
      )}
    </main>
```

을 다음으로 교체:

```jsx
      {tab === 'ask' && (
        <>
          <form onSubmit={handleSubmit}>
            <select value={repoId ?? ''} onChange={(e) => setRepoId(Number(e.target.value))}>
              {repos.map((repo) => (
                <option key={repo.id} value={repo.id}>
                  {repo.name} ({repo.primary_language}, {repo.file_count}개 파일)
                </option>
              ))}
            </select>

            <textarea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="예: 이 프로젝트에서 성능상 문제가 될 만한 부분 찾아줘"
              rows={3}
            />

            <button type="submit" disabled={loading}>
              {loading ? '분석 중…' : '리뷰 요청'}
            </button>
          </form>

          {error && <p className="error">{error}</p>}

          {session && (
            <section className="result">
              <h2>리뷰 결과</h2>
              <p className="meta">
                상태 {session.status} · {session.iteration_count}턴 · 토큰{' '}
                {session.input_tokens + session.output_tokens}
              </p>
              <pre className="review">{session.final_review}</pre>

              <h3>에이전트 트레이스</h3>
              <ol className="trace">
                {session.trace.map((step) => (
                  <li key={step.id} className={step.error ? 'step error' : 'step'}>
                    <strong>{step.type === 'TOOL_CALL' ? step.tool_name : 'LLM 판단'}</strong>
                    {step.tool_args && <code>{step.tool_args}</code>}
                    {step.tool_result && <pre>{step.tool_result.slice(0, 500)}</pre>}
                    {step.assistant_text && <p>{step.assistant_text}</p>}
                  </li>
                ))}
              </ol>
            </section>
          )}
        </>
      )}

      {tab === 'evals' && <EvalResults repos={repos} />}
    </main>
```

(내부 폼/결과 마크업 자체는 바뀌지 않는다 — `{tab === 'ask' && (<>...</>)}`로 감싸고 뒤에 eval 탭 분기만 추가하는 것.)

- [ ] **Step 4: `frontend/src/App.css`에 최소 스타일 추가**

파일 끝에 추가:

```css
.tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}

.tabs button.active {
  font-weight: bold;
  text-decoration: underline;
}

.eval-table {
  width: 100%;
  border-collapse: collapse;
  margin-bottom: 16px;
}

.eval-table th,
.eval-table td {
  border: 1px solid #ddd;
  padding: 6px 8px;
  text-align: left;
  font-size: 14px;
}

.eval-table tr {
  cursor: pointer;
}

.eval-table tr.selected {
  background: #eef6ff;
}

.eval-detail li {
  border-bottom: 1px solid #eee;
  padding: 8px 0;
}

.eval-detail li.false-positive {
  background: #fdecea;
}
```

- [ ] **Step 5: 수동으로 동작 확인**

Run (레포 루트에서): `uvicorn repoview.api.app:app --reload`
Run (다른 터미널, `frontend/`에서): `npm run dev`

브라우저로 `http://localhost:5173` 접속 → "Eval 결과" 탭 클릭 → 레포 선택 시 표가 뜨는지, 행 클릭 시 케이스별 상세가 펼쳐지는지, "세션 보기" 클릭 시 리뷰 텍스트가 뜨는지 확인. (자동 테스트 없음 — 이 프로젝트 프론트엔드는 테스트 프레임워크가 없는 기존 컨벤션.)

- [ ] **Step 6: 커밋**

```bash
git add frontend/src/api.js frontend/src/App.jsx frontend/src/App.css frontend/src/EvalResults.jsx
git commit -m "$(cat <<'EOF'
프론트엔드에 Eval 결과 탭 추가

질문하기/Eval 결과 탭 토글(새 의존성 없이 useState). Eval 결과 탭은
레포별 Phase 비교표 → 행 클릭 시 케이스별 상세 → 세션 보기로 리뷰 확인.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## 완료 기준

- [ ] `pytest -q`가 전부 통과한다 (실제 API 키 없이).
- [ ] `eval_run.repo_id`가 새 실행에는 직접 저장되고, 기존 실행에는 백필되어 있다.
- [ ] `GET /api/evals/runs?repo_id=`가 그 레포의 실행 이력을 최신순으로 반환하고, `notes` JSON 필드가 최상위로 펼쳐져 있다.
- [ ] `GET /api/evals/runs/{id}`가 케이스별 상세(질문/카테고리/탐지여부/판정근거/세션ID)를 반환하고, 없는 id는 404를 반환한다.
- [ ] 프론트엔드 "Eval 결과" 탭에서 레포 선택 → 비교표 → 케이스 상세 → 세션 리뷰까지 수동으로 확인된다.
- [ ] 기존 Phase 1~4, Eval Runner 테스트가 전부 그대로 통과한다.

## 이 계획 이후로 미룬 것

| 항목 | 이유 |
|---|---|
| eval 실행 자체를 API로 트리거 | `docs/04-api-spec.md`가 이미 "실행은 CLI, 조회만 API"로 결정 — 진행상황 폴링/백그라운드 태스크가 필요해져 범위가 커짐 |
| "최신 실행만" 필터/하이라이트 | 재실행마다 탐지율이 흔들리는 걸 실제로 겪어서, 전체 이력을 그대로 보여주기로 함(`docs/04-api-spec.md` 3절) — 나중에 필요하면 프론트에서 클라이언트 사이드로 추가 가능 |
| 차트/그래프 시각화 | 지금은 표로 충분 — 케이스 수가 적어(레포당 6개) 그래프가 표보다 나을 게 없음 |
