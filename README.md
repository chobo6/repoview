# RepoView

두 개인 프로젝트(LocalQuest — Java/Spring, Songpyeon — Node/TS)를 대상으로, 자연어 질문에 `파일:라인` 인용이 달린 코드 리뷰를 작성해주는 로컬 전용 AI 에이전트 도구입니다. 포트폴리오/면접 데모용으로 만들었습니다.

## 이 프로젝트가 다른 "AI 코드리뷰 데모"와 다른 점

- **근거 없는 지적을 안 하려고 노력합니다.** 모든 리뷰는 `파일:라인` 인용을 포함해야 하고, 세션이 끝나면 그 인용이 실제로 존재하는 파일/라인인지, 에이전트가 진짜로 `read_file`로 읽은 파일인지 코드로 사후검증합니다. 검증에 실패한 인용은 화면에 경고 배지로 그대로 노출됩니다 — 감춰지지 않습니다.
- **측정 결과의 변동성을 숨기지 않습니다.** 같은 레포·같은 Phase를 여러 번 평가해도 LLM 응답의 비결정성 때문에 탐지율이 실행마다 흔들립니다. 이 프로젝트는 "최신 값 하나"만 보여주는 대신 재실행 기록을 전부 남기고, 점 스트립으로 흔들림 자체를 화면에 드러냅니다. "Phase 3(RAG)이 Phase 2보다 확실히 낫다"고 지금 표본으로는 통계적으로 단정할 수 없다는 것도 정직하게 인정합니다.
- **실시간으로 에이전트가 뭘 하는지 보여줍니다.** SSE 스트리밍으로 도구 호출("search_code 중...", "read_file 중...")이 실시간으로 화면에 나타납니다.

## 빠른 시작

```bash
# 1) 환경변수 설정 (최초 1회)
cp .env.example .env   # OPENAI_API_KEY, OPENAI_MODEL(예: gpt-4o) 채우기

# 2) 백엔드 설치
pip install -e ".[dev]"

# 3) 대상 레포 인덱싱 (레포 내용이 바뀔 때마다 다시 실행)
python -m repoview.index

# 4) (선택) 임베딩 — 의미 검색(RAG)을 쓰려면 필요, 비용 발생
python -m repoview.embed

# 5) API 서버
uvicorn repoview.api.app:app --reload   # :8000

# 6) 프론트엔드 (새 터미널, frontend/에서)
npm install
npm run dev   # :5173 — CORS가 이 포트로 고정되어 있음
```

`http://localhost:5173`에서 레포를 고르고 질문을 입력하면 됩니다. 질문마다 실제 OpenAI API를 호출하므로 비용이 발생합니다.

## 어떻게 동작하는가 — 서로 독립적인 4단계

한 요청 안에서 순차 실행되는 게 아니라 각자 별도 CLI/API로 실행됩니다.

1. **인덱싱** (`python -m repoview.index`) — 레포를 스캔해 언어/프레임워크를 감지하고 파일 목록을 SQLite에 저장합니다. 매번 파일시스템을 훑지 않도록 미리 만들어두는 가벼운 카탈로그입니다.
2. **임베딩** (`python -m repoview.embed`) — 코드를 50줄 단위(10줄 겹침)로 잘라 벡터로 바꿔 Chroma에 저장합니다. 정확한 함수명을 몰라도 의미가 비슷한 코드를 찾을 수 있게 해줍니다(RAG).
3. **질의** (`POST /api/sessions` → `GET /api/sessions/{id}/stream`) — 세션 생성과 실행이 분리되어 있습니다. SSE 연결 시점에 에이전트가 도구 호출↔LLM 판단을 반복하며(최대 6턴, 누적 토큰 5만 상한) 리뷰를 작성하고, 스텝마다 실시간으로 스트리밍됩니다. 종료 시 인용을 사후검증합니다.
4. **평가** (`python -m repoview.eval --repo <name> --phase <2|3>`) — 정답을 미리 아는 문제 세트를 풀게 하고, 평가 대상과 다른 LLM(judge)이 채점해 탐지율/오탐율/인용정확도/비용·지연을 측정합니다.

더 자세한 설계와 "왜 이렇게 했는가"는 [`docs/`](docs/) 아래 설계 문서(01~06)와 [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md)(실제 겪은 버그와 원인)에 있습니다.

## 기술 스택

- **백엔드**: Python 3.12, FastAPI, SQLite, Chroma(벡터 DB), OpenAI API
- **프론트엔드**: React 19, Vite (라우터·상태관리 라이브러리 없이 `useState`만)
- **테스트**: pytest, 실제 OpenAI를 절대 호출하지 않는 `FakeLLM`/`FakeEmbeddingClient` 주입 패턴, 186개 테스트

## 프로젝트 구조

```
repoview/
  agent/          에이전트 루프(loop.py), 프롬프트 구성, LLM 클라이언트
  api/             FastAPI 앱 — 세션 생성/SSE 스트림, eval 결과 조회
  tools/           list_directory / search_code / search_semantic / read_file
  db.py            SQLite 스키마 + 마이그레이션
  indexer.py       인덱싱
  embedder.py      청킹 + 임베딩
  eval.py          평가 러너 (LLM-as-judge)
  citation_check.py  세션 종료 시 인용 사후검증
frontend/          React 프론트엔드
tests/              백엔드 테스트 (mini_repo 픽스처 사용, 실제 레포에 의존 안 함)
docs/               설계 문서(01~06) + TROUBLESHOOTING.md + 구현 계획(plans/)
```

## 알려진 한계

- 하드코딩된 레포 2개만 지원 (범용 SaaS 아님, 의도된 범위)
- 인증 없음, 배포 안 됨 — 로컬 단일 사용자 전용
- 에이전트 자체의 탐지 정확도는 아직 평범한 수준(탐지율 44~67%, 오탐률 12~25%대) — 엔지니어링보다 프롬프트/전략 개선이 더 필요한 영역
- eval 표본이 아직 작다(레포당 17~19케이스, 3회 반복) — 수치의 신뢰구간이 넓다는 걸 스스로 인지하고 화면에도 그렇게 보여줌
- 프론트엔드는 자동 테스트 없음(수동 확인으로 대체)
