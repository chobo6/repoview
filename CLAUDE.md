# CLAUDE.md

이 파일은 Claude Code(claude.ai/code)가 이 저장소에서 작업할 때 참고할 안내를 담고 있다.

## 명령어

백엔드 (Python 3.12, 레포 루트에서 실행):
- 설치: `pip install -e ".[dev]"`
- 전체 테스트 실행: `pytest`
- 파일 하나만 실행: `pytest tests/test_agent_loop.py`
- 테스트 하나만 실행: `pytest tests/test_agent_loop.py::test_name -v`
- 대상 레포 인덱싱 (API가 그 레포에 대해 답하려면 먼저 실행해야 함 — 실시간으로 스캔하지 않음): `python -m repoview.index` (`REPOS`에 있는 두 레포 전부) 또는 `python -m repoview.index --repo LocalQuest`
- eval_case 시드 (JSON → DB): `python -m repoview.eval_seed <file.json> --repo <name>` — `question` 기준으로 이미 있는 케이스는 건너뜀 (재실행해도 중복 안 생김)
- Phase별 탐지율 평가: `python -m repoview.eval --repo <name> --phase <2|3>` — LLM-as-judge + 인용 검증으로 탐지율/오탐율/인용 정확도/비용·지연 측정, `eval_run`/`eval_result`에 기록
- API 실행: `uvicorn repoview.api.app:app --reload` (`/api/*`를 :8000에서 서빙)
- 환경변수: `.env.example`을 `.env`로 복사하고 `OPENAI_API_KEY` + `OPENAI_MODEL`을 설정 — `OPENAI_MODEL`은 기본값이 빈 문자열이며 설정하지 않으면 `OpenAILLM`이 예외를 던진다. Eval Runner의 판정 모델은 `REPOVIEW_JUDGE_MODEL`(기본 `gpt-4o-mini`)이며, `OPENAI_MODEL`과 같으면 `validate_judge_model`이 크래시 대신 `SystemExit`로 막는다.

프론트엔드 (`frontend/`에서 실행):
- `npm install`, `npm run dev` (Vite, API가 :8000에 떠 있어야 함 — CORS는 `http://localhost:5173`으로 고정), `npm run build`, `npm run lint` (oxlint)

## 아키텍처

RepoView는 하드코딩된 두 레포(`repoview/config.py`의 `REPOS`: LocalQuest — Java/Spring, Songpyeon — Node/TS)에 대해 LLM 에이전트 루프를 돌려, 자유 형식의 코드 리뷰 질문에 file:line 인용이 달린 답변으로 답하는 로컬 전용 도구다.

네 단계가 각각 독립적으로 실행되며, 하나의 요청 안에서 순차 실행되지 않는다:
1. **인덱싱** (`python -m repoview.index` → `repoview/indexer.py`)은 레포의 파일들을 (`tools/search.py:iter_code_files`로, `EXCLUDED_DIRS`는 제외하고) 스캔해 언어/프레임워크를 감지하고, 해당 레포의 `repo`/`repo_file` 행(SQLite, `repoview/db.py`)을 통째로 교체한다. 프레임워크 감지는 매니페스트 파일(`pom.xml`, `package.json`)을 깊이 3까지 읽고, 심볼릭 링크로 걸린 매니페스트는 레포 루트 밖을 가리킬 수 있어 건너뛴다.
2. **임베딩** (Phase 3, `python -m repoview.embed`)은 코드를 50줄 슬라이딩 윈도우(10줄 겹침)로 청킹해 레포별 Chroma 컬렉션에 임베딩한다.
3. **질의** (`POST /api/sessions` → `repoview/agent/loop.py:run_session`)는 `agent/prompts.py`로 시스템 프롬프트를 빌드한다 — 레포 개요(언어/프레임워크 + 깊이 2 트리 + 루트 파일명)는 도구로 노출하는 대신 프롬프트 앞부분에 미리 주입해서, 모든 질문이 이를 공짜로 얻고 고정된 접두부가 캐싱 대상이 되게 한다. 이후 루프는 모델이 순수 텍스트를 반환하거나 `MAX_ITERATIONS`(`config.py`, 기본값 6) 또는 누적 토큰 상한(`MAX_SESSION_TOKENS`, 기본 50000)에 도달할 때까지 LLM 호출과 도구 디스패치(`repoview/tools/__init__.py:dispatch`)를 번갈아 수행한다 — 동일 `(tool_name, args)`가 3회 이상 반복되면 결과에 "다른 접근을 시도하라"는 안내를 주입한다(Phase 4). 세션 종료 시 `repoview/citation_check.py:verify_citations`가 리뷰의 `파일:라인` 인용을 사후검증해(존재하지 않는 파일/범위 밖 라인/미확인 인용) `session.citation_warnings`에 기록한다 — 검증 실패가 완료된 세션을 FAILED로 덮어쓰지 않도록 별도 UPDATE로 분리되어 있다. 모든 LLM 호출과 도구 호출은 `GET /api/sessions/{id}`로 재생할 수 있도록 `trace_step`에 기록된다.
4. **평가** (`python -m repoview.eval`, `repoview/eval.py:run_eval`)는 `eval_case` 행들을 주어진 Phase(2/3)로 3번 단계의 루프에 다시 태워, 평가 대상 모델과는 다른 별도의 LLM(`config.JUDGE_MODEL`)으로 리뷰를 판정하고, 그 인용을 `repo_file`과 규칙 기반으로 대조한 뒤, 탐지율/오탐율/인용 정확도/비용·지연을 `eval_run`(`repo_id`/`fpr`/`citation_accuracy`/`avg_cost_usd`/`avg_latency_ms` 실컬럼)/`eval_result`에 집계한다. 결과 조회는 `GET /api/evals/runs`, `GET /api/evals/runs/{id}`(프론트엔드 "Eval 결과" 탭)로 웹에서도 볼 수 있다. `run_eval`은 케이스 하나가 실패해도 나머지를 계속 진행한다(예외를 잡아 `detected=0`으로 기록) — 20~30개짜리 유상 API 실행이 전제이므로 이 격리가 중요하다. 같은 레포+Phase 재실행 기록은 최신순으로 전부 보존한다(재실행마다 탐지율이 흔들리는 걸 실제로 겪음, `docs/TROUBLESHOOTING.md` 참고) — "최신값만" 보여주지 말 것.

에이전트/도구 코드를 건드리기 전에 알아둘 크로스커팅 컨벤션:
- **도구 인자는 신뢰 경계다.** LLM이 `path` 인자를 생성하므로, 모든 도구는 이를 `tools/paths.py:resolve_safe_path`로 처리해 레포 루트를 벗어나는 것은 전부 거부한다. 새로 파일을 다루는 도구를 추가할 때 이 경로를 거치지 않으면 안 된다.
- **`dispatch`는 절대 예외를 던지지 않는다.** 도구 실패는 예외가 아니라 도구 결과 메시지 안에 `"ERROR: ..."` 문자열로 돌아온다 — 그래야 에이전트가 실패를 보고 다음 턴에 다른 접근을 시도할 수 있다. 새 도구를 추가할 때도 이 계약을 유지할 것.
- **LLM 클라이언트는 `Protocol`을 통해 교체 가능하다** (`agent/llm.py`). 테스트는 실제 OpenAI를 절대 호출하지 않고, 미리 준비된 `make_tool_call_response`/`make_text_response` 시퀀스를 반환하는 `FakeLLM`을 주입한다. 새로운 에이전트 루프 테스트를 작성할 때도 OpenAI SDK를 모킹하지 말고 이 패턴을 따를 것.
- **테스트는 `tests/fixtures/mini_repo/`**(작은 가짜 Java+TS 레포)를 사용한다 — 실제 LocalQuest/Songpyeon 체크아웃을 쓰지 않으므로, 도구/인덱서 테스트가 그 레포들의 존재나 불변성에 의존하지 않는다.

**구현은 설계 문서보다 뒤처져 있다 — 어느 브랜치에 있는지 먼저 확인할 것.** `docs/01-prd.md`부터 `docs/06-test-eval-design.md`까지는 Phase 1~4 전체 목표를 설명한다. `main`은 `config.CURRENT_PHASE = 3`(도구: `list_directory`/`search_code`/`read_file`/`search_semantic`) 위에 Eval Runner와 Phase 4 안전장치가 이미 merge되어 있다 — `POST /api/sessions`는 여전히 동기식이다(SSE 엔드포인트는 아직 없음). 확장자 없는 레포 루트 설정 파일(`Dockerfile`/`.dockerignore`/`.gitignore`/`.env.example`/`Makefile`/`.editorconfig`)은 `config.ROOT_CONFIG_FILENAMES`로 별도 인덱싱된다 — 하위 폴더의 동명 파일이나 이 목록 밖의 확장자 없는 파일은 여전히 인덱싱 대상이 아니다. 각 문서에는 대안이 왜 기각됐는지 설명하는 "판단 이유" 절이 있다(예: Postgres+pgvector 대신 SQLite, API 엔드포인트 대신 CLI 인덱싱, AST 기반 대신 슬라이딩 윈도우 청킹) — 인덱싱·스키마·에이전트 루프·eval 설계를 바꾸기 전에 관련 문서를 먼저 읽을 것. 이런 제약들은 코드만 봐서는 드러나지 않는다. 실제 발생한 버그와 원인은 `docs/TROUBLESHOOTING.md`에 기록되어 있다.

## Windows 환경 주의사항

- `taskkill //F //IM node.exe //T`처럼 **이름으로** 프로세스를 죽이면 이 프로젝트와 무관한 다른 Node 프로세스까지 전부 종료된다 — 반드시 `netstat -ano | grep :<port>`로 PID를 찾아 `taskkill //F //PID <pid> //T`로 지정할 것.
- 터미널에 찍히는 한글 출력이 깨져 보이면(`Ž����` 등) 대부분 실제 에러가 아니라 Windows 콘솔이 CP949라서 생기는 표시 문제다 — 파일로 리다이렉트한 뒤 `iconv -f cp949 -t utf-8`로 확인할 것.
- 새 git worktree를 만들면 `origin/main` 기준으로 생성되는데, 로컬 `main`에 아직 push 안 한 커밋이 있으면 그 워크트리가 구버전에서 시작된다 — 생성 직후 `git log --oneline -1`로 로컬 `main`과 비교하고 필요하면 `git merge main --ff-only`로 맞출 것.
