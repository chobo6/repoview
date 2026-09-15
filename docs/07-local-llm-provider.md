# 로컬 LLM(Ollama) 제공자 지원 — 설계

## 1. 배경과 목적

OpenAI API 비용을 아끼면서 "로컬 오픈소스 모델은 이 프로젝트 수준의 작업(정확한 tool calling, 파일:라인 인용)에서 실제로 어느 정도 쓸 만한가"를 eval로 직접 측정해보기 위한 실험 트랙이다. 프로덕션 경로를 로컬 모델로 교체하는 것이 목적이 아니라, **OpenAI vs 로컬 모델 비교를 eval 비교표와 질문하기 탭 양쪽에서 선택 가능하게 만드는 것**이 목적이다.

개발 환경은 전용 GPU 없는 노트북(Intel Iris Xe 내장 그래픽, RAM 16GB)이라 CPU 추론이 전제다. 이 제약 때문에 처음부터 큰 모델을 상정하지 않고, 허용 모델 목록에 등록된 소수의 검증된 모델만 쓴다.

## 2. 범위

- 대상 로컬 모델: `qwen2.5:7b` (Ollama의 tool calling 공식 지원 모델 중 품질이 가장 나은 축)
- eval 비교 트랙(`eval.py --provider`)과 질문하기 탭(프론트엔드 모델 드롭다운) 양쪽에 적용
- 저지(judge) 모델은 이 변경과 무관하게 항상 OpenAI 고정 — 채점 신뢰도를 비교 대상 모델과 분리하는 기존 원칙 유지

## 3. 컴포넌트 설계

### 3.1 `repoview/agent/llm.py` — provider 확장

`OpenAILLM.__init__`에 `base_url: str | None = None` 파라미터를 추가한다. `base_url`이 주어지면 `OpenAI(base_url=base_url, api_key="ollama")`로 클라이언트를 생성하고(Ollama는 `/v1/chat/completions` OpenAI 호환 엔드포인트를 제공하며 API 키를 검증하지 않지만 SDK가 빈 문자열을 거부하므로 더미 값을 넣는다), 없으면 기존과 동일하게 `OpenAI()`(환경변수 `OPENAI_API_KEY` 사용)로 생성한다. `call()` 메서드의 tool_calls 파싱·usage 집계 로직은 전혀 손대지 않는다 — Ollama의 OpenAI 호환 응답이 같은 스키마를 따르기 때문이다.

```python
def __init__(self, model: str, base_url: str | None = None) -> None:
    if not model:
        raise ValueError("OPENAI_MODEL이 설정되지 않았습니다. .env를 확인하세요.")
    from openai import OpenAI

    self.model = model
    if base_url:
        self._client = OpenAI(base_url=base_url, api_key="ollama")
    else:
        self._client = OpenAI()
```

`OllamaLLM`은 별도 클래스가 아니라 팩토리 함수로 둔다(새 클래스를 만들 이유가 없다 — 생성자 인자만 다를 뿐 동작은 100% `OpenAILLM`과 같다):

```python
def OllamaLLM(model: str) -> OpenAILLM:
    from repoview.config import OLLAMA_BASE_URL
    return OpenAILLM(model, base_url=OLLAMA_BASE_URL)
```

### 3.2 `repoview/config.py` — 설정 및 허용 모델 목록

```python
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")

ALLOWED_MODELS: dict[str, str] = {
    OPENAI_MODEL: "openai",
    "qwen2.5:7b": "ollama",
}
```

`ALLOWED_MODELS`는 백엔드에서 실제로 생성을 허용할 `모델명 → provider` 매핑이다. **클라이언트(프론트엔드, eval CLI)가 임의의 모델명이나 `base_url`을 직접 지정하게 하지 않는다** — 요청받은 모델명이 이 목록에 없으면 세션 생성/평가 실행을 거부한다. 서버가 사용자 입력에 따라 임의 주소로 아웃바운드 요청을 보내는 것을 막기 위한 결정이다.

`OPENAI_MODEL`이 비어있는 배포 환경(예: Ollama만 쓰고 싶은 경우)을 감안해, `ALLOWED_MODELS` 생성 시 `OPENAI_MODEL`이 빈 문자열이면 그 항목은 제외한다.

### 3.3 provider 선택 헬퍼

`repoview/agent/llm.py`(또는 `config.py`)에 아래 헬퍼를 추가해 `eval.py`와 `api/app.py` 양쪽이 재사용한다:

```python
def build_llm(model: str) -> LLM:
    provider = ALLOWED_MODELS.get(model)
    if provider is None:
        raise ValueError(f"허용되지 않은 모델입니다: {model}")
    if provider == "ollama":
        return OllamaLLM(model)
    return OpenAILLM(model)
```

## 4. `eval.py` — CLI 확장

```
python -m repoview.eval --repo <name> --phase <2|3> [--provider openai|ollama] [--model <모델명>]
```

- `--provider` 기본값 `openai`. `openai`일 때 `--model` 생략 시 `OPENAI_MODEL` 사용, 지정 시 `ALLOWED_MODELS`에 있어야 함.
- `--provider ollama`일 때 `--model` 기본값 `qwen2.5:7b`.
- 최종적으로 `build_llm(model)`로 평가 대상 LLM을 만들고, `judge_llm = OpenAILLM(model=JUDGE_MODEL)`은 그대로 고정.
- `run_eval`에 넘기는 `model=` 인자도 실제 선택된 모델명(예: `"qwen2.5:7b"`)으로 바뀐다 — `eval_run.model` 컬럼과 비교표에 그대로 노출되도록.
- `estimate_cost_usd`는 기존 로직 그대로 둔다. `MODEL_PRICING`에 `qwen2.5:7b`가 없으므로 경고 로그만 찍고 비용 0으로 집계된다 — 별도 처리 불필요, 오히려 "로컬 모델은 $0"을 eval 비교표에서 그대로 보여주는 효과가 있다.

## 5. API — `POST /api/sessions`

요청 바디에 `model`(선택, 기본값 `OPENAI_MODEL`) 필드를 추가한다.

```json
{ "repo_id": 1, "question": "...", "model": "qwen2.5:7b" }
```

`model`이 `ALLOWED_MODELS`에 없으면 `400`(`{"error": {"code": "invalid_model", "message": "..."}}`)을 반환한다. 유효하면 `build_llm(model)`로 LLM을 구성해 세션 실행에 쓰고, 기존 `session.model` 컬럼(스키마 변경 없음)에 그대로 저장한다.

현재 `get_llm()`은 앱 전역에 고정된 `Depends`라서 요청마다 다른 모델을 만들 수 없다 — 게다가 실행 시점(`stream_session`)은 이미 `row["model"]`(세션 생성 시 저장된 값)을 읽어서 `_run_in_background`에 넘기고 있으면서도, 정작 `llm` 객체는 이 값과 무관하게 `Depends(get_llm)`이 고정으로 만든 걸 그대로 쓰고 있다 — 지금은 모델이 하나뿐이라 드러나지 않았을 뿐인 기존 불일치다. 이번 변경으로 이것도 함께 바로잡는다:

- `create_session`: `payload.model`(기본값 `OPENAI_MODEL`)이 `ALLOWED_MODELS`에 없으면 `HTTPException(status_code=400, detail=...)`을 던진다(기존 "레포를 찾을 수 없습니다" 등과 동일한 패턴 — 이 프로젝트는 검증 실패를 `_error_response`를 직접 호출하지 않고 `HTTPException`으로 던져 등록된 핸들러가 변환하게 한다). 유효하면 `session.model`에 저장.
- `stream_session`: **`get_llm` 의존성 함수는 삭제하지 않고 유지한다** — 기존 테스트(`tests/test_api.py`)가 `app.dependency_overrides[get_llm] = lambda: FakeLLM(...)`로 완전히 교체하는 방식으로 이미 여러 곳에서 쓰고 있어서, 이 시그니처를 없애면 기존 테스트가 전부 깨진다(Global Constraint 위반). 대신 `get_llm`의 **기본(운영) 구현만** 바꾼다 — 라우트의 `session_id` 경로 파라미터를 그대로 받아 DB에서 `model`을 조회한 뒤 `build_llm(model)`을 반환하도록 한다:

  ```python
  def get_llm(session_id: int, db_path: Path = Depends(get_db_path)):
      conn = get_connection(db_path)
      try:
          row = conn.execute("SELECT model FROM session WHERE id = ?", (session_id,)).fetchone()
      finally:
          conn.close()
      model = row["model"] if row else OPENAI_MODEL
      return build_llm(model)
  ```

  테스트는 이 함수를 완전히 교체(override)하므로 내부 구현 변경의 영향을 받지 않는다. 세션 조회를 위해 짧은 연결을 하나 더 여는 점(경량 PK 조회 1건)은 감수한다 — `stream_session` 본문이 어차피 곧이어 같은 세션 row를 다시 읽지만, 이 중복 조회를 없애려고 `get_llm`의 의존성 주입 구조 자체를 바꾸면(예: 다른 의존성 결과를 참조하게 만들면) 기존 테스트의 override 지점이 흔들릴 위험이 더 크다고 판단했다.

## 6. 프론트엔드

`frontend/src/App.jsx`의 "질문하기" 탭, 레포 선택 드롭다운 옆에 모델 드롭다운을 추가한다. 옵션은 하드코딩된 두 개로 고정(`ALLOWED_MODELS`와 동일한 목록을 프론트에도 상수로 둔다 — 별도 API로 목록을 조회할 만큼 자주 바뀌지 않는다):

```
OpenAI (gpt-4o)
Ollama 로컬 (qwen2.5:7b)
```

`frontend/src/api.js`의 `createSession(repoId, question)`을 `createSession(repoId, question, model)`로 확장하고, 선택된 모델을 요청 바디에 포함한다. 과거 세션 replay와 Eval 결과 탭은 이미 `model` 컬럼을 그대로 표시하고 있어 추가 변경이 필요 없다.

## 7. 에러 처리

Ollama 서버가 떠 있지 않으면 `openai` SDK가 연결 에러를 던진다. 이는 기존 `_run_in_background`의 안전망(예외를 잡아 세션을 `FAILED`로 기록하고 `finished_at`을 채움, `docs/04-api-spec.md` 2절)이 그대로 처리한다 — 별도 예외 처리를 추가하지 않는다.

## 8. 테스트

- `OllamaLLM`(팩토리 함수)이 올바른 `base_url`/`api_key`로 `OpenAI` 클라이언트를 구성하는지: 실제 Ollama 서버 없이, `openai.OpenAI` 생성자에 전달된 인자만 검증하는 단위 테스트.
- `build_llm`: 허용 목록에 있는 모델은 올바른 타입(OpenAI/Ollama 구성)을 반환하고, 없는 모델은 `ValueError`를 던지는지.
- `POST /api/sessions`에 `ALLOWED_MODELS`에 없는 모델을 보내면 `400`을 반환하는지(TestClient + FakeLLM 패턴 그대로 사용).
- `POST /api/sessions`에 유효한 모델을 보내면 해당 모델이 `session.model`에 저장되는지.
- 기존 FakeLLM 기반 에이전트 루프/세션 테스트는 이 변경으로 영향받지 않는다 — `build_llm`이 실제 생성 경로에서만 쓰이고 테스트는 여전히 `FakeLLM`을 직접 주입한다.

## 9. 비목표(Out of scope)

- Ollama 자동 설치/모델 pull 자동화 — 사용자가 직접 `ollama pull qwen2.5:7b`로 준비해야 한다.
- 로컬 모델의 임베딩(RAG) 지원 — 이번 범위는 LLM 호출(리뷰 생성)에 한정하고, `search_semantic`용 임베딩은 계속 OpenAI를 쓴다.
- 모델 목록을 API로 노출하거나 사용자가 임의 모델명을 입력하는 UI — 허용 목록은 코드에 고정한다.
