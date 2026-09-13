# TROUBLESHOOTING

실제 발생한 버그·장애의 근본 원인과 수정 방법을 기록한다. 커밋 로그(`git log`)에서
발견된 것과, 개발 세션 중 실제로 겪은 것을 시간 순으로 정리했다. 최신 구현이
아래 설명과 다르면 코드가 우선이다.

## Phase 1+2 (인덱싱 · 도구 · 에이전트 루프 · API)

### #1 빈 파일을 읽으면 ERROR가 반환됨

**증상**: `read_file` 도구로 빈 파일을 읽으면 정상 내용이 아니라 `ERROR:` 문자열이
돌아왔다.

**원인**: 빈 파일은 `splitlines()` 결과가 `[]`라 `total = len(lines)`가 0이 되고,
이후 로직이 "줄이 없음"을 "파일 없음"과 같은 에러 케이스로 취급했다.

**해결**: 빈 파일을 별도 케이스로 분기해 `"{path} (파일이 비어 있습니다)"`를
반환하도록 수정 (commit `68c7ebb`).

### #2 `end_line=0`이 무시되는 falsy-OR 버그

**증상**: 도구 인자로 `end_line=0`을 명시적으로 넘겨도 기본값으로 치환되어
의도한 범위로 읽히지 않았다.

**원인**: `end_line = end_line or total` 같은 `or` 기반 기본값 처리가 `0`을
"값 없음"과 구분하지 못했다(0은 falsy). `max_results`/`max_iterations`에도
같은 패턴이 있었다.

**해결**: `is not None` 명시적 비교로 전부 교체 (commit `68c7ebb`, `1a75cc6`).
**이후 비슷한 자리를 새로 만들 때도 `or` 기본값 대신 `is None` 체크를 쓸 것** —
정수 인자를 다루는 도구/CLI 코드에서 반복해서 나온 패턴이라 다시 나올 수 있다.

### #3 인덱싱이 제외 디렉토리까지 전부 순회해 느림 (LocalQuest 기준 10.4초 → 0.05초)

**증상**: `iter_code_files`/`detect_frameworks`가 `node_modules`, `.git` 같은
제외 대상 디렉토리 안까지 전부 걸어 들어가 인덱싱이 비정상적으로 느렸다.

**원인**: `EXCLUDED_DIRS`를 각 파일 경로에 대해 사후 필터링만 하고,
`os.walk`가 애초에 그 디렉토리 안으로 내려가는 것 자체를 막지 않았다.

**해결**: `os.walk`가 반환하는 `dirnames`를 제자리에서 가지치기
(`dirnames[:] = [...]`)해 제외 디렉토리 진입 자체를 차단 (commit `1a75cc6`).

### #4 처리되지 않은 예외가 CORS 헤더 없이 500을 반환

**증상**: 라우트 핸들러에서 처리 안 된 예외가 나면 500 응답에 CORS 헤더가 없어,
브라우저가 실제 에러 메시지 대신 CORS 에러로 표시했다 — 원인 파악이 어려웠다.

**원인**: CORS 미들웨어가 정상 응답 경로에만 적용되고, 예외 핸들러 경로는
그보다 먼저 응답을 끝내버렸다.

**해결**: CORS보다 먼저 등록되는 캐치올 예외 핸들러를 추가하고, 에러 응답
포맷을 공통 헬퍼로 통일 (commit `1a75cc6`).

### #5 LLM 호출 실패 시 세션이 RUNNING 상태로 영구히 멈음

**증상**: 에이전트 루프 중 LLM 호출이 실패하면 세션 레코드가 `RUNNING`에
멈춘 채로 남아, 그 세션이 실패했는지 그냥 오래 걸리는지 구분할 수 없었다.

**원인**: 예외 발생 시 세션 상태를 갱신하는 코드 경로가 없었다.

**해결**: 예외를 잡아 세션 상태를 `FAILED`로 기록한 뒤 예외는 그대로 다시
던지도록 수정 — 호출자가 실패를 알 수 있으면서도 DB 상태는 항상 최종 상태로
남는다 (commit `1a75cc6`). **Eval Runner의 케이스별 예외 격리(`run_eval`)도
같은 문제의 다른 형태**라 나중에 비슷하게 손봐야 했다 (아래 Eval Runner 절
참고).

### #6 심볼릭 링크로 레포 루트 밖 파일이 노출될 수 있음

**증상 (보안)**: 레포 안에 레포 루트 밖을 가리키는 심볼릭 링크가 있으면,
`search_code`/`detect_frameworks`가 `resolve_safe_path` 방어를 거치지 않고
그 링크의 실제 내용을 그대로 읽어 반환할 수 있었다.

**원인**: 두 함수 모두 파일 목록을 만들 때 심볼릭 링크 여부를 확인하지
않았다. `read_file`은 `resolve_safe_path`를 거치므로 원래 안전했지만, 검색/
프레임워크 감지 경로는 파일 시스템을 직접 순회해 이 방어를 우회했다.

**해결**: `iter_code_files`에서 심볼릭 링크를 만나면 건너뛰도록 수정
(commit `a75c388`). **"도구 인자는 신뢰 경계"라는 원칙만으로는 안 끝난다** —
파일 시스템을 직접 순회하는 모든 코드 경로에 이 방어가 있는지 새 도구를
추가할 때마다 확인할 것.

### #7 FastAPI 스레드풀 오프로드로 인한 `sqlite3.ProgrammingError`

**증상**: 세션 조회/생성 API가 간헐적으로 `sqlite3.ProgrammingError:
SQLite objects created in a thread can only be used in that same thread`로
실패했다. `TestClient`뿐 아니라 실제 Uvicorn 배포에서도 재현 가능한
구조적 문제였다.

**원인**: FastAPI가 동기 의존성/라우트 함수를 스레드풀로 오프로드하면서,
같은 요청 안에서도 커넥션을 **생성한** 스레드와 실제로 **쿼리를 실행하는**
스레드가 달라질 수 있었다.

**해결**: `get_connection()`에 `check_same_thread=False`를 추가 (commit
`8423141`). 커넥션을 여러 스레드가 동시에 쓰는 게 아니라 요청 하나가
순차적으로 이어받는 것뿐이라 안전하며, 기존 단일 스레드 사용처(인덱서, CLI,
기존 테스트)에는 동작 차이가 없다.

### #8 디렉토리/언어 목록이 조용히 잘림

**증상**: 시스템 프롬프트에 주입되는 레포 개요의 디렉토리 트리와 언어 요약이
각각 상위 N개로 잘리는데, 잘렸다는 표시가 없어 에이전트가 레포 규모를
실제보다 작게 오인할 수 있었다.

**원인**: `most_common(MAX_TREE_ENTRIES)`/`most_common(6)`로 자르기만 하고
초과분에 대한 안내가 없었다.

**해결**: 목록이 상한을 넘으면 `... (외 N개 디렉토리 더 있음)` 형태의 안내
문구를 덧붙이도록 수정 (commit `5befae1`).

### #9 프론트엔드가 `response.ok` 확인 전에 `.json()`을 호출

**증상**: API가 에러를 반환하면 프론트엔드에 의미 있는 에러 메시지 대신
원시 `SyntaxError`(JSON 파싱 실패)가 노출됐다.

**원인**: 에러 응답 바디가 기대한 JSON 형식이 아닐 수 있는데도 `.ok` 체크
없이 바로 `.json()`을 호출했다.

**해결**: `response.ok`를 먼저 확인하고 실패 시 별도 처리를 하도록 방어적으로
수정 (commit `1a75cc6`).

## Phase 3 (RAG · 임베딩)

### #10 압축된 빌드 산출물 청크가 임베딩 입력 한도를 넘겨 400 에러

**증상**: 실제 레포(LocalQuest 등)를 `embed_repo`로 임베딩하면 특정 청크에서
`text-embedding-3-small` 입력 토큰 한도를 넘겨 400 에러로 전체가 실패했다.

**원인**: 압축/난독화된 빌드 산출물처럼 줄바꿈이 거의 없는 파일은 50줄
슬라이딩 윈도우로 잘라도 한 청크가 여전히 매우 길 수 있었다.

**해결**: 임베딩 전에 그런 청크를 걸러내고, 배치도 고정 개수 대신 문자 수
기준(`_batched_by_chars`)으로 전환 (commit `3e28533`). **이 문제는 나중에
다른 원인(한글 청크)으로 다시 발생한다** — 아래 "이번 세션" 절 #17 참고.

### #11 초과 크기 청크를 통째로 버려서 인용이 사라짐

**증상**: `MAX_CHUNK_CHARS`를 넘는 청크를 통째로 버렸더니, 긴 한 줄이 낀
파일에서는 겹치는 두 청크(최대 약 80줄 분량)가 `search_semantic` 결과에서
통째로 사라졌다.

**원인**: "너무 길면 버린다"는 단순한 처리라 그 청크가 담고 있던 file:line
범위 자체가 검색 불가능해졌다.

**해결**: 버리는 대신 텍스트만 `MAX_CHUNK_CHARS`로 잘라내도록 변경 —
청크의 존재와 인용(file/line)은 항상 유지된다 (commit `72d71e1`).

### #12 임베딩 도중 실패하면 기존 컬렉션이 통째로 사라짐

**증상**: `embed_repo`가 중간에 실패(API 에러 등)하면 이미 지워버린 기존
Chroma 컬렉션이 복구되지 않아, 재시도 전까지 해당 레포의 검색이 완전히
막혔다.

**원인**: 기존 컬렉션을 먼저 삭제하고 나서 새로 채우는 "삭제 후 재생성"
방식이었다.

**해결**: 임시 staging 컬렉션에 전부 채운 뒤 `Collection.modify`로
원자적으로 이름을 교체하는 방식으로 변경 — 실패해도 기존 컬렉션이 그대로
남아 검색 가능하다 (commit `72d71e1`).

### #13 테스트가 실제 `chroma_data/` 디렉터리를 오염시킴

**증상**: `test_api.py`의 일부 테스트가 실행될 때마다 프로젝트의 실제
`chroma_data/` 디렉터리에 테스트용 컬렉션이 쌓였다.

**원인**: `client`/`test_get_session_includes_trace` 픽스처가 `CHROMA_PATH`를
격리하지 않고 기본 경로를 그대로 썼다.

**해결**: 해당 픽스처들이 `CHROMA_PATH`를 `tmp_path`로 monkeypatch하도록 수정
(commit `3e28533`).

## Eval Runner (PR #3, `worktree-eval-runner`)

### #14 `run_eval`의 예외 격리가 `run_session`에서 멈춤 → judge/DB 실패 시 전체 배치 중단

**증상**: 판정(judge) LLM 호출이나 DB 기록이 실패하면 `run_eval` 전체가
죽어, 이미 완료된 케이스들의 집계(`_finish_eval_run`)조차 기록되지 않았다.
20~30개짜리 유상 API 배치 중 하나만 흔들려도 전체를 다시 돌려야 하는
구조였다.

**원인**: 처음 구현(`8aac5a9`)에서는 `try/except`가 `run_session` 호출만
감싸고, 그 뒤에 이어지는 인용 추출·judge 호출·`_record_eval_result`는
보호 범위 밖에 있었다. CLAUDE.md가 명시한 "케이스 하나가 실패해도 나머지를
계속 진행한다"는 불변조건이 `run_session` 실패에만 적용되고 있었다.

**해결**: `try` 블록을 판정·인용검증·DB 기록까지 포함하는 케이스 전체로
확장 (commit `a906482`, 이번 세션의 코드 리뷰에서 발견·수정). 이 gap은
기존 테스트로는 안 잡혔다 — `test_run_eval_isolates_case_failure_and_still_finishes_run`이
`judge_llm`도 `FakeLLM([])`로 만들어뒀지만, `run_session`이 먼저 예외를
던져 `judge_llm`이 아예 호출되지 않았기 때문.

### #15 `_migrate()`가 컬럼 존재 여부와 무관하게 모든 `OperationalError`를 삼킴

**증상**: 마이그레이션이 "컬럼이 이미 있어서" 실패한 건지 "DB 락/디스크
문제로" 실패한 건지 구분이 안 됐고, 후자의 경우 나중에 `no such column`
같은 엉뚱한 에러로 나타났다.

**원인**: `ALTER TABLE ... ADD COLUMN`을 `try/except OperationalError: pass`로
감싸 "컬럼 이미 존재" 케이스만 넘기려 했지만, 이 예외 클래스는 다른 진짜
실패도 같이 삼켰다.

**해결**: `PRAGMA table_info(eval_result)`로 컬럼 존재 여부를 먼저 확인하고,
없을 때만 `ALTER TABLE`을 실행하도록 변경 (commit `a906482`) — 테스트
(`tests/test_db.py`)가 이미 쓰던 것과 같은 확인 방식으로 통일됐다.

### #16 인용이 파일만 맞으면 줄 번호가 틀려도 탐지 성공으로 기록됨

**증상**: 같은 파일의 엉뚱한 줄을 인용해도 `matches_file`이 파일 경로만
비교해 탐지 성공(`detected=1`)으로 잘못 기록될 수 있었다.

**원인**: `eval_case`가 `expected_line_start`/`expected_line_end`를 이미
저장하고 있었는데도 `matches_file`이 파일 경로만 비교했다.

**해결**: `matches_file`에 줄 범위 겹침 확인을 추가 (commit `a906482`).
`bf0697b`("judge YES인데 인용 파일이 다른 경우 미탐지로 처리되는지 검증하는
테스트 추가")이 파일 단위 검증은 먼저 테스트로 굳혀뒀고, 이번 세션에서 줄
단위까지 확장했다.

## 이번 세션 (실제 eval 셋 작성 · Phase 2/3 실행)

### #17 임베딩 배치가 분당 토큰(TPM) 한도를 초과 (429)

**증상**: `python -m repoview.embed` 실행 시 `openai.RateLimitError: ...
Request too large ... TPM: Limit 40000, Requested 103770`로 실패했다.

**원인**: `EMBED_BATCH_CHAR_BUDGET=400_000`자는 계정 티어가 높을 때 기준으로
잡힌 값이라, 새로 발급된 낮은 티어 계정(TPM 40,000)에서는 배치 하나가
한도를 훌쩍 넘었다.

**해결**: `EMBED_BATCH_CHAR_BUDGET`을 `100_000`으로 낮추고,
`OpenAIEmbeddingClient.embed()`에 `RateLimitError` 재시도(고정 15초 대기,
최대 5회)를 추가 (`repoview/embedder.py`, `repoview/embedding_client.py`).
**단, 이 재시도는 "요청 자체가 한도보다 큰" 경우엔 도움이 안 된다** — 그
경우엔 배치 크기 자체를 줄이는 것만 해법이다. 계정 티어가 높다면 처리량을
위해 상수를 다시 올려도 된다.

### #18 한글 주석이 많은 청크가 문자 수 캡은 통과했지만 토큰 한도(8192)를 초과 (400)

**증상**: #17을 고친 뒤에도 `openai.BadRequestError: Invalid 'input[43]':
maximum input length is 8192 tokens`로 Songpyeon 임베딩이 실패했다 (같은
회차 임베딩 로그 안에서 LocalQuest는 성공, Songpyeon만 실패).

**원인**: `MAX_CHUNK_CHARS=20_000`은 영문/코드 기준(약 3.8자/토큰)으로는
안전하지만, 한글은 토큰 밀도가 훨씬 높다(대략 1~1.5자/토큰). Songpyeon
소스에는 긴 한글 주석 블록이 많아, 문자 수 캡은 통과했는데도 실제 토큰
수는 8192를 넘는 청크가 있었다. #10과 같은 클래스의 문제가 다른 조건에서
재발한 것이다.

**해결**: 신규 pip 의존성(`tiktoken`) 추가 없이, CJK 최악의 경우까지 감안해
`MAX_CHUNK_CHARS`를 `6_000`으로 보수적으로 낮춤 (`repoview/embedder.py`).
문자 수 기반 근사의 근본적 한계이므로, 토큰 밀도가 이보다 더 높은 콘텐츠가
나오면 다시 낮춰야 할 수 있다.

### #19 eval_case 작성 실수 — 코드 수정 후 줄 번호가 밀렸는데 JSON에 반영 안 함

**증상**: Songpyeon Phase 3 결과에서 하드코딩 시크릿 케이스가 미탐지
(`detected=0`)로 나왔는데, 실제 리뷰 내용을 보면 RepoView가 정확한 위치를
인용해 정확히 찾아낸 상태였다 — Phase 3 탐지율이 Phase 2보다 오히려 낮게
나와 이상해서 `trace_step`을 직접 조회하다 발견했다.

**원인**: `session.ts`에 버그를 심으면서 설명 주석을 한 줄 추가로 넣었는데,
그 바람에 실제 버그 줄이 7번째에서 8번째로 밀렸다. `eval_cases/songpyeon.json`의
`expected_line_start`는 편집 전 줄 번호(7)로 남아있었고, #16에서 고친 줄
범위 검증이 (의도대로) 정확하게 "줄이 안 맞다"고 걸러냈다. RepoView나
`matches_file` 로직의 버그가 아니라 eval 데이터 자체의 오류였다.

**해결**: JSON과 이미 시드된 DB 행의 `expected_line_start`/`expected_line_end`를
8로 수정한 뒤 Songpyeon Phase 2/3을 다시 실행. **교훈**: 버그를 심은 뒤
파일을 다시 편집(주석 추가 등)하면 반드시 `grep -n`으로 실제 줄 번호를
재확인하고 나서 eval_case를 작성할 것 — 편집 전 기억에 의존하면 이런
오탐(정확히는 "미"탐)이 생긴다.

### #20 Windows 콘솔이 CP949라 한글 출력이 깨져 보임 (실제 버그 아님)

**증상**: `python -m repoview.eval` 등의 출력이 터미널에 `Ž���� 0%` 같은
글자로 깨져 보였다.

**원인**: 실제 버그가 아니라 Windows 콘솔 코드페이지(CP949)와 Python
stdout의 UTF-8 출력이 안 맞아서 생기는 표시 문제. 파일로 리다이렉트한 뒤
`iconv -f cp949 -t utf-8`로 보면 정상적으로 읽힌다.

**해결**: 코드 수정 없음 — 디버깅 중 "진짜 에러 메시지가 깨진 건지"
헷갈리지 않도록 알아두면 된다.

## 알려진 이슈 (미해결)

PR #3 코드 리뷰에서 발견된 항목 중 다음은 이후 작업(Phase 4, eval 셋
확장 라운드)에서 이미 고쳐졌다 — `estimate_cost_usd`는 이제 가격표에
없는 모델이면 stderr에 경고를 남기고서 `0.0`을 반환하고, `citation_accuracy`는
인용이 하나도 없으면 `1.0`이 아니라 `None`으로 계산되며(화면엔 "N/A(인용 없음)"),
`avg_cost_usd`/`avg_latency_ms`는 성공한 케이스 수(`completed_cases`)로만
나눠서 실패 케이스가 평균을 왜곡하지 않고, `MAX_ITERATIONS`는 6으로
올라갔다(Phase 4 판단 이유는 `docs/05-agent-design.md` §3 참고). 경로
정규화 중복도 `eval_citations.py:normalize_path`로 통합되어 `eval.py`/
`citation_check.py`가 공용으로 쓴다.

아직 안 고친 것:

- `repoview/eval.py`와 `repoview/eval_seed.py`에 "레포 미인덱싱" 조회+안내
  블록이 그대로 복붙되어 있다 (`get_repo_or_exit(conn, name)` 같은 공용
  헬퍼로 뽑아낼 것).
