# DB 설계 — RepoView (SQLite)

## 1. 테이블 개요

| 테이블 | 역할 |
|---|---|
| `repo` | 분석 대상 레포 메타데이터 |
| `repo_file` | 인덱싱된 파일 목록 |
| `session` | 질문 1건 = 세션 1건 |
| `trace_step` | 에이전트 루프의 각 스텝(LLM 호출 / 도구 호출) |
| `eval_case` | eval 정답 셋 |
| `eval_run` | eval 실행 1회 |
| `eval_result` | eval 케이스별 결과 |

## 2. 설계 판단

- **`session.phase` 컬럼**: PRD의 성공 기준이 "Phase별 탐지율 개선을 숫자로 보여주기"이므로, 세션마다 어느 단계 구현으로 돌렸는지 기록해야 나중에 비교표를 뽑을 수 있다.
- **`trace_step`을 단일 테이블로**: LLM 호출과 도구 호출을 `type`으로 구분해 한 테이블에 담는다. 테이블을 나누면 시간 순서 재구성이 번거롭고, 이 테이블의 목적이 "에이전트가 어떤 순서로 판단했는지 재생"이라 단일 시퀀스가 맞다.
- **`trace_step.tool_result` truncate**: `read_file` 결과가 파일 전체일 수 있어 8KB 초과분은 잘라 저장하고 원본 길이를 별도 컬럼에 남긴다. 트레이스 테이블이 무한정 커지는 것을 막는다.
- **`repo_file`을 테이블로 두는 이유**: 매 질문마다 파일시스템을 전체 스캔하면 `get_repo_overview`가 느려지고 언어 감지 결과를 캐싱할 곳이 없다. 파일시스템이 진실의 원천이므로 재인덱싱 시 해당 레포 행을 전부 삭제 후 재삽입한다.

## 3. DDL

```sql
CREATE TABLE repo (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL UNIQUE,
    root_path         TEXT    NOT NULL,
    primary_language  TEXT,
    framework         TEXT,
    file_count        INTEGER DEFAULT 0,
    chunk_count       INTEGER DEFAULT 0,
    indexed_at        TEXT,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE repo_file (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id     INTEGER NOT NULL REFERENCES repo(id) ON DELETE CASCADE,
    path        TEXT    NOT NULL,           -- 레포 루트 기준 상대경로
    language    TEXT,
    size_bytes  INTEGER,
    line_count  INTEGER
);
CREATE INDEX idx_repo_file_repo_path ON repo_file(repo_id, path);

CREATE TABLE session (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id         INTEGER NOT NULL REFERENCES repo(id),
    question        TEXT    NOT NULL,
    status          TEXT    NOT NULL,       -- RUNNING | COMPLETED | FAILED | CAPPED
    final_review    TEXT,
    iteration_count INTEGER DEFAULT 0,
    input_tokens    INTEGER DEFAULT 0,
    output_tokens   INTEGER DEFAULT 0,
    cost_usd        REAL    DEFAULT 0,
    model           TEXT    NOT NULL,
    phase           INTEGER NOT NULL,       -- 1~4: 어느 단계 구현으로 실행했는지
    error           TEXT,
    started_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    finished_at     TEXT
);
CREATE INDEX idx_session_repo_started ON session(repo_id, started_at);

CREATE TABLE trace_step (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id         INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    step_no            INTEGER NOT NULL,
    type               TEXT    NOT NULL,    -- LLM_CALL | TOOL_CALL
    tool_name          TEXT,
    tool_args          TEXT,                -- JSON 문자열
    tool_result        TEXT,                -- 8KB 초과 시 절단 저장
    tool_result_length INTEGER,             -- 절단 전 원본 길이
    assistant_text     TEXT,
    input_tokens       INTEGER,
    output_tokens      INTEGER,
    latency_ms         INTEGER,
    error              TEXT,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_trace_step_session ON trace_step(session_id, step_no);

CREATE TABLE eval_case (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id            INTEGER NOT NULL REFERENCES repo(id),
    question           TEXT    NOT NULL,
    expected_finding   TEXT    NOT NULL,    -- 기대하는 탐지 내용
    expected_file_path TEXT,
    expected_line_start INTEGER,
    expected_line_end   INTEGER,
    category           TEXT,                -- PERFORMANCE | SECURITY | ...
    is_planted         INTEGER NOT NULL DEFAULT 0,  -- 1: 의도적으로 심은 버그, 0: 실제 있었던 사례
    created_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE eval_run (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    phase          INTEGER NOT NULL,
    model          TEXT    NOT NULL,
    total_cases    INTEGER DEFAULT 0,
    passed_cases   INTEGER DEFAULT 0,
    detection_rate REAL    DEFAULT 0,
    notes          TEXT,
    started_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    finished_at    TEXT
);

CREATE TABLE eval_result (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    eval_run_id  INTEGER NOT NULL REFERENCES eval_run(id) ON DELETE CASCADE,
    eval_case_id INTEGER NOT NULL REFERENCES eval_case(id),
    session_id   INTEGER REFERENCES session(id),  -- 실제 실행된 세션
    detected     INTEGER NOT NULL DEFAULT 0,      -- 0 | 1
    judge_reason TEXT,                            -- 채점 근거
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_eval_result_run ON eval_result(eval_run_id);
```

## 4. 벡터 저장소 (Chroma, SQLite 외부, Phase 3부터)

- 레포별 컬렉션 1개 (`repo_localquest`, `repo_songpyeon`).
- 문서 = 코드 청크 텍스트. `iter_code_files`가 반환하는 파일들을 50줄 단위, 10줄 겹침으로 슬라이딩 윈도우 청킹한다(설계 근거는 `02-architecture.md` 4절).
- 임베딩 모델 = OpenAI `text-embedding-3-small`.
- 메타데이터 = `{file_path, start_line, end_line, language}`.
- `search_semantic` 도구가 이 메타데이터를 그대로 인용 근거(파일:라인)로 반환한다.
- 재임베딩 시 컬렉션을 통째로 지우고 다시 채운다(증분 업데이트 없음 — 근거는 `02-architecture.md` 4절).
- `repo.chunk_count`(1절 DDL에 이미 존재)를 `python -m repoview.embed` 실행 후 갱신해, 프론트에서 레포별 청크 수를 보여줄 수 있게 한다.
