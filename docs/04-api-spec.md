# API 명세 — RepoView

Base URL: `http://localhost:8000/api`

## 1. 레포

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/repos` | 등록된 레포 목록 (name, language, framework, file_count, indexed_at) |
| GET | `/repos/{repo_id}` | 레포 상세 + 디렉토리 트리 요약 |

**응답 예시 — `GET /repos`**

```json
[
  {
    "id": 1,
    "name": "LocalQuest",
    "primary_language": "java",
    "framework": "spring-mvc",
    "file_count": 412,
    "chunk_count": 3180,
    "indexed_at": "2026-09-11T10:22:03"
  }
]
```

### 인덱싱을 API가 아닌 CLI로 둔 이유

인덱싱은 `python -m repoview.index --repo LocalQuest` 형태의 CLI로 실행한다. 레포당 1회만 도는 사전 준비 작업인데 API로 만들면 진행상황 폴링과 백그라운드 태스크 관리가 따라붙어 웹 앱의 책임("질문하고 리뷰 받기")과 섞인다. 데모에서도 인덱싱은 미리 끝나 있는 상태가 자연스럽다.

## 2. 세션 (질의)

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/sessions` | 세션 생성. body `{repo_id, question}` → `{session_id, status: "PENDING"}` |
| GET | `/sessions/{id}/stream` | SSE. 연결 시점에 에이전트 실행 시작, 스텝을 실시간 전송 |
| GET | `/sessions` | 과거 세션 목록 (`?repo_id=&phase=` 필터) |
| GET | `/sessions/{id}` | 세션 상세 + 전체 트레이스 (재생용) |

### POST로 생성만 하고 SSE 연결이 실행을 트리거하는 이유

POST 요청에서 바로 에이전트를 실행하면, 프론트가 SSE를 연결하기 전에 첫 스텝들이 지나가버리는 레이스가 생긴다. 생성과 실행을 분리하면 `EventSource`를 그대로 사용할 수 있고, 재연결 시에는 이미 `RUNNING`/`COMPLETED` 상태인 세션의 저장된 트레이스를 재생하면 된다. "과거 세션 다시 보기" 기능이 같은 코드 경로로 해결된다.

### SSE 이벤트 스키마

| 이벤트 | 페이로드 |
|---|---|
| `step_started` | `{step_no, type, tool_name, tool_args}` |
| `step_completed` | `{step_no, result_preview, latency_ms, error}` |
| `assistant_message` | `{text}` — 루프 중간에 모델이 낸 텍스트 |
| `done` | `{status, final_review, iteration_count, cost_usd}` |
| `error` | `{message}` |

**이벤트 예시**

```
event: step_started
data: {"step_no": 3, "type": "TOOL_CALL", "tool_name": "search_code", "tool_args": {"pattern": "SELECT .* FROM", "path": "src/main"}}

event: step_completed
data: {"step_no": 3, "result_preview": "12 matches in 5 files...", "latency_ms": 84, "error": null}
```

## 3. Eval

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/evals/runs` | eval 실행 목록 — Phase별 탐지율 비교표의 데이터 소스. `?repo_id=` 필터, `started_at DESC` 정렬 |
| GET | `/evals/runs/{id}` | 케이스별 상세 결과 (탐지 여부, 채점 근거, 해당 세션 링크) |

실행 자체는 CLI(`python -m repoview.eval --phase 4`)로 하고 결과 조회만 API로 제공한다. 면접 데모에서 "Phase 2는 30%, Phase 4는 80%" 같은 비교표를 화면에 보여주는 것이 이 프로젝트의 하이라이트이므로 조회는 웹에 둔다.

**같은 레포+Phase를 여러 번 돌린 기록을 숨기지 않는 이유**: 재실행할 때마다 LLM 응답 자체의 무작위성으로 탐지율이 흔들린다는 걸 실제로 겪었다(케이스 수가 적을수록 심함, `docs/TROUBLESHOOTING.md` #19 근처 참고). "최신 값만" 보여주면 이 변동성이 화면에서 사라져 수치를 실제보다 신뢰할 수 있는 것처럼 보이게 한다. 그래서 최신순 전체 이력을 그대로 보여주고, 값이 널뛰는 것 자체도 데모의 정직한 일부로 남긴다.

**응답 예시 — `GET /evals/runs?repo_id=1`**

```json
[
  {
    "id": 8,
    "repo_id": 1,
    "repo_name": "Songpyeon",
    "phase": 3,
    "model": "gpt-4o",
    "total_cases": 3,
    "passed_cases": 1,
    "detection_rate": 0.33,
    "fpr": 0.33,
    "citation_accuracy": 1.0,
    "avg_cost_usd": 0.0139,
    "avg_latency_ms": 6573,
    "started_at": "2026-09-12T14:02:11",
    "finished_at": "2026-09-12T14:03:47"
  }
]
```

`fpr`/`citation_accuracy`/`avg_cost_usd`/`avg_latency_ms`는 `eval_run`의 실제 컬럼이다(원래는 `notes` JSON에만 있어서 읽을 때마다 파싱이 필요했지만, 그 파싱을 까먹는 새 소비자가 생길 위험을 없애려고 컬럼으로 승격했다 — `docs/03-db-design.md` 참고). `notes`는 감사용 원본 JSON으로 남아있지만 그대로 클라이언트에 넘기지 않는다.

**응답 예시 — `GET /evals/runs/8`**

```json
{
  "id": 8,
  "repo_id": 1,
  "phase": 3,
  "model": "gpt-4o",
  "detection_rate": 0.33,
  "results": [
    {
      "eval_case_id": 19,
      "question": "방 목록 조회(/api/rooms) API에 성능 문제 있어?",
      "category": "n_plus_one",
      "is_planted": 1,
      "expected_finding": "...",
      "detected": 0,
      "false_positive": 0,
      "judge_reason": "...",
      "session_id": 44
    }
  ]
}
```

## 4. 공통 규약

- 에러 응답 형식: `{"error": {"code": "...", "message": "..."}}`
- 상태 코드: `400` 잘못된 요청 / `404` 없는 레포·세션 / `409` 이미 실행 중인 세션 / `500` 서버 오류
- 인증 없음 (로컬 단일 사용자)

## 5. 의도적으로 넣지 않은 것

레포 추가/삭제, 세션 삭제, 사용자 인증 — 레포 2개 고정 + 로컬 전용 도구라 불필요하다.
