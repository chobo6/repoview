# 로컬 LLM(Ollama) 제공자 지원 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** OpenAI 외에 로컬 Ollama 모델(`qwen2.5:7b`)을 eval 비교 트랙과 질문하기 탭 양쪽에서 선택해서 쓸 수 있게 만든다.

**Architecture:** 기존 `openai` 파이썬 SDK가 제공하는 `base_url` 파라미터를 활용해 Ollama의 OpenAI 호환 엔드포인트(`/v1/chat/completions`)를 그대로 호출한다 — 새 HTTP 클라이언트나 새 의존성 없이 `OpenAILLM`에 `base_url` 파라미터 하나만 추가하고, 그 위에 얇은 팩토리 함수(`OllamaLLM`)와 화이트리스트 기반 선택 헬퍼(`build_llm`)를 얹는다. 클라이언트(프론트/CLI)는 항상 `ALLOWED_MODELS`에 등록된 모델명만 넘길 수 있고, 서버가 그 모델명으로 어떤 provider를 쓸지 결정한다 — 클라이언트가 임의 `base_url`을 지정하는 경로는 없다.

**Tech Stack:** 기존 `openai` 파이썬 SDK 재사용(신규 pip 의존성 없음), FastAPI, React. Ollama는 런타임 요구사항일 뿐 코드 의존성이 아니다(사용자가 `ollama serve`로 별도 실행).

**Spec:** `docs/07-local-llm-provider.md` (전체 설계 근거와 트레이드오프 서술)

## Global Constraints

- 클라이언트(프론트엔드, eval CLI)가 임의 모델명이나 `base_url`을 직접 지정하지 못한다 — 서버 쪽 `ALLOWED_MODELS` 화이트리스트에 있는 모델명만 허용한다(`docs/07-local-llm-provider.md` 3.2절).
- 기존 `FakeLLM` 기반 에이전트 루프/세션 테스트(`tests/test_agent_loop.py`, `tests/test_api.py`의 기존 테스트들)는 이 변경으로 전혀 영향받지 않고 그대로 통과해야 한다.
- `get_llm`(`repoview/api/app.py`)은 삭제하지 않는다 — 기존 테스트가 `app.dependency_overrides[get_llm] = lambda: FakeLLM(...)`로 완전히 교체하는 패턴을 쓰고 있어서, 시그니처/이름을 유지한 채 내부 구현만 바꾼다(`docs/07-local-llm-provider.md` 5절).
- 저지(judge) 모델은 provider 선택과 무관하게 항상 `OpenAILLM(JUDGE_MODEL)`로 고정한다.
- 새 pip/npm 의존성을 추가하지 않는다 — `openai` SDK의 `base_url` 파라미터만 재사용한다.
- 커밋 메시지는 한국어로 작성하고 `feat:`/`fix:` 같은 접두어를 붙이지 않는다.
- 대상 로컬 모델명은 정확히 `qwen2.5:7b`로 고정한다(스펙에서 이미 확정).

---

### Task 1: provider 플러밍 — `config.py` + `agent/llm.py`

**Files:**
- Modify: `repoview/config.py:16` (바로 다음 줄에 추가)
- Modify: `repoview/agent/llm.py:1-71` (import, `OpenAILLM.__init__`, 새 함수 2개 추가)
- Test: `tests/test_config.py` (파일 끝에 추가)
- Test: `tests/test_llm.py` (파일 끝에 추가)

**Interfaces:**
- Consumes: 없음(최하위 레이어).
- Produces:
  - `repoview.config.OLLAMA_BASE_URL: str`
  - `repoview.config.ALLOWED_MODELS: dict[str, str]` (값은 `"openai"` 또는 `"ollama"`)
  - `repoview.agent.llm.OpenAILLM(model: str, base_url: str | None = None)` — 기존 시그니처에 `base_url` 파라미터만 추가
  - `repoview.agent.llm.OllamaLLM(model: str) -> OpenAILLM` (함수, 클래스 아님)
  - `repoview.agent.llm.build_llm(model: str) -> LLM` — 이후 Task 2/3이 이 함수를 쓴다

- [ ] **Step 1: `config.py`에 `OLLAMA_BASE_URL`, `ALLOWED_MODELS` 추가**

`repoview/config.py:16` 바로 다음(`OPENAI_MODEL = os.getenv("OPENAI_MODEL", "")` 줄 다음)에 삽입:

```python
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")

ALLOWED_MODELS: dict[str, str] = {"qwen2.5:7b": "ollama"}
if OPENAI_MODEL:
    ALLOWED_MODELS[OPENAI_MODEL] = "openai"
```

- [ ] **Step 2: config 테스트 작성**

`tests/test_config.py` 끝에 추가:

```python
def test_allowed_models_always_includes_qwen_ollama():
    assert config.ALLOWED_MODELS.get("qwen2.5:7b") == "ollama"


def test_allowed_models_excludes_empty_openai_model(reloaded_config):
    reloaded = reloaded_config("OPENAI_MODEL")
    assert "" not in reloaded.ALLOWED_MODELS
```

- [ ] **Step 3: 테스트 실행 (실패 확인)**

Run: `pytest tests/test_config.py -v`
Expected: 새로 추가한 두 테스트가 `AttributeError: module 'repoview.config' has no attribute 'ALLOWED_MODELS'`로 FAIL. 나머지 기존 테스트는 그대로 PASS.

- [ ] **Step 4: `llm.py`에 `base_url` 파라미터 추가**

`repoview/agent/llm.py` 맨 위 import 블록(현재 1~4줄)에 한 줄 추가:

```python
import json
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from repoview.config import ALLOWED_MODELS, OLLAMA_BASE_URL
```

`OpenAILLM.__init__`(현재 28~34줄)을 아래로 교체:

```python
class OpenAILLM:
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

`call()` 메서드는 전혀 손대지 않는다.

- [ ] **Step 5: `OllamaLLM`, `build_llm` 추가**

`OpenAILLM` 클래스 정의 바로 다음(현재 `_parse_arguments` 함수 바로 위)에 삽입:

```python
def OllamaLLM(model: str) -> OpenAILLM:
    """Ollama의 OpenAI 호환 엔드포인트(/v1/chat/completions)로 향하는 OpenAILLM을 만든다.
    별도 클래스가 아닌 이유: 생성자 인자(base_url)만 다를 뿐 call()의 파싱·usage 집계
    로직이 100% 동일해서 서브클래싱/재구현할 이유가 없다."""
    return OpenAILLM(model, base_url=OLLAMA_BASE_URL)


def build_llm(model: str) -> "LLM":
    """ALLOWED_MODELS 화이트리스트에 있는 모델명만 받는다 — 호출자(API/eval CLI)가
    임의 모델명이나 base_url을 직접 지정하지 못하게 막는 지점이다."""
    provider = ALLOWED_MODELS.get(model)
    if provider is None:
        raise ValueError(f"허용되지 않은 모델입니다: {model}")
    if provider == "ollama":
        return OllamaLLM(model)
    return OpenAILLM(model)
```

- [ ] **Step 6: llm 테스트 작성**

`tests/test_llm.py` 맨 위 import에 추가:

```python
from repoview import config
from repoview.agent.llm import FakeLLM, OllamaLLM, OpenAILLM, build_llm, make_text_response, make_tool_call_response
```

파일 끝에 추가:

```python
def test_openai_llm_without_base_url_targets_openai_api(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    llm = OpenAILLM("gpt-4o")
    assert "api.openai.com" in str(llm._client.base_url)


def test_openai_llm_with_base_url_targets_that_url(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unused")
    llm = OpenAILLM("qwen2.5:7b", base_url="http://localhost:11434/v1")
    assert "11434" in str(llm._client.base_url)
    assert llm._client.api_key == "ollama"


def test_ollama_llm_factory_targets_ollama_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unused")
    llm = OllamaLLM("qwen2.5:7b")
    assert llm.model == "qwen2.5:7b"
    assert "11434" in str(llm._client.base_url)
    assert llm._client.api_key == "ollama"


def test_build_llm_returns_ollama_backed_client_for_qwen(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unused")
    llm = build_llm("qwen2.5:7b")
    assert llm.model == "qwen2.5:7b"
    assert "11434" in str(llm._client.base_url)


def test_build_llm_returns_plain_openai_client_for_openai_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    llm = build_llm(config.OPENAI_MODEL)
    assert llm.model == config.OPENAI_MODEL
    assert "api.openai.com" in str(llm._client.base_url)


def test_build_llm_raises_for_unknown_model():
    with pytest.raises(ValueError, match="허용되지 않은 모델"):
        build_llm("not-a-real-model")
```

- [ ] **Step 7: 전체 테스트 실행 (통과 확인)**

Run: `pytest tests/test_config.py tests/test_llm.py -v`
Expected: 전부 PASS. (참고: `config.OPENAI_MODEL`이 `.env`에 설정되어 있어야 `test_build_llm_returns_plain_openai_client_for_openai_model`이 의미 있게 검증된다 — 이 저장소는 이미 `.env`에 `OPENAI_MODEL`이 설정되어 있다.)

- [ ] **Step 8: 커밋**

```bash
git add repoview/config.py repoview/agent/llm.py tests/test_config.py tests/test_llm.py
git commit -m "$(cat <<'EOF'
Ollama용 LLM 클라이언트 추가 (OpenAI SDK의 base_url 재사용)

기존 openai SDK가 base_url을 바꿀 수 있게 해주는 점을 이용해,
Ollama의 OpenAI 호환 엔드포인트(/v1/chat/completions)를 그대로
호출하는 방식으로 새 HTTP 클라이언트 없이 로컬 모델을 지원한다.
build_llm은 ALLOWED_MODELS 화이트리스트에 있는 모델만 받아 호출자가
임의 모델명/base_url을 지정하지 못하게 한다.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `eval.py` — `--provider`/`--model` CLI 플래그

**Files:**
- Modify: `repoview/eval.py:1-13` (import), `repoview/eval.py:201-234` (main 함수)
- Test: `tests/test_eval.py` (파일 끝에 추가)
- Modify (문서 한 줄 추가): `docs/06-test-eval-design.md`

**Interfaces:**
- Consumes: `repoview.agent.llm.build_llm(model: str) -> LLM` (Task 1)
- Produces: `repoview.eval.resolve_model(provider: str, model: str | None) -> str` — CLI 인자 파싱과 분리된 순수 함수라 argparse 없이 테스트 가능

- [ ] **Step 1: `resolve_model` 테스트 작성**

`tests/test_eval.py` 맨 위 import에 `resolve_model` 추가:

```python
from repoview.eval import estimate_cost_usd, list_eval_cases, resolve_model, run_eval, validate_judge_model
```

파일 끝에 추가:

```python
def test_resolve_model_defaults_to_qwen_for_ollama_provider():
    assert resolve_model("ollama", None) == "qwen2.5:7b"


def test_resolve_model_defaults_to_openai_model_for_openai_provider():
    from repoview.config import OPENAI_MODEL

    assert resolve_model("openai", None) == OPENAI_MODEL


def test_resolve_model_respects_explicit_override():
    assert resolve_model("ollama", "custom-model") == "custom-model"
    assert resolve_model("openai", "custom-model") == "custom-model"
```

- [ ] **Step 2: 테스트 실행 (실패 확인)**

Run: `pytest tests/test_eval.py -k resolve_model -v`
Expected: `ImportError: cannot import name 'resolve_model'`로 FAIL.

- [ ] **Step 3: import 수정**

`repoview/eval.py:7`을 교체:

```python
from repoview.agent.llm import LLM, OpenAILLM, build_llm
```

- [ ] **Step 4: `resolve_model` 함수와 `main()` 수정**

`repoview/eval.py`의 `main()` 함수(현재 201~234줄)를 통째로 아래로 교체(바로 위에 `resolve_model` 함수를 새로 추가):

```python
def resolve_model(provider: str, model: str | None) -> str:
    if model:
        return model
    from repoview.config import OPENAI_MODEL

    return "qwen2.5:7b" if provider == "ollama" else OPENAI_MODEL


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase별 리뷰 탐지율을 측정한다")
    parser.add_argument("--repo", required=True, choices=sorted(REPOS), help="평가할 레포 이름")
    parser.add_argument(
        "--phase", type=int, required=True, choices=[2, 3],
        help="평가할 Phase (2=키워드 검색만, 3=RAG 포함)",
    )
    parser.add_argument(
        "--provider", choices=["openai", "ollama"], default="openai",
        help="평가 대상 LLM 제공자 (기본 openai)",
    )
    parser.add_argument(
        "--model", default=None,
        help="평가 대상 모델명. 생략 시 --provider openai는 OPENAI_MODEL, "
             "--provider ollama는 qwen2.5:7b를 사용한다.",
    )
    args = parser.parse_args()

    model = resolve_model(args.provider, args.model)

    try:
        validate_judge_model(OPENAI_MODEL, JUDGE_MODEL)

        conn = get_connection()
        init_db(conn)

        repo_id = get_repo_or_exit(conn, args.repo)

        cases = list_eval_cases(conn, repo_id)

        llm = build_llm(model)
        judge_llm = OpenAILLM(model=JUDGE_MODEL)
        embedding_client = OpenAIEmbeddingClient() if args.phase == 3 else None

        stats = run_eval(
            conn, repo_id, cases, llm, judge_llm,
            model=model, phase=args.phase, embedding_client=embedding_client,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
```

`build_llm`이 알 수 없는 모델에 `ValueError`를 던지면 기존의 `except ValueError as exc: raise SystemExit(...)` 블록이 그대로 잡아 처리한다 — 별도 에러 처리 불필요.

- [ ] **Step 5: 테스트 실행 (통과 확인)**

Run: `pytest tests/test_eval.py -v`
Expected: 전부 PASS (기존 테스트 포함, `resolve_model` 신규 3개 포함).

- [ ] **Step 6: 문서에 새 플래그 한 줄 추가**

`docs/06-test-eval-design.md`의 "### 4. 실행 및 비교" 절, 첫 번째 불릿 끝에 문장 하나 추가:

```
- `python -m repoview.eval --repo <name> --phase <2|3>` → `eval_run` 생성, 케이스별로 세션을 실행하고 결과를 저장한다. (PRD 초안엔 Phase 4까지 비교한다고 적혀 있었지만, "Phase 4"는 이후 실제 구현에서 "Agent 핵심 기능"이 아니라 "안전장치 라운드"를 가리키는 이름으로 굳어졌고 `--phase` 플래그는 `2`/`3`만 받는다 — 아래 "판단 이유" 참고.) `--provider ollama --model qwen2.5:7b`를 추가하면 로컬 모델로 평가할 수 있다(`docs/07-local-llm-provider.md` 참고) — 저지 모델은 이 선택과 무관하게 항상 OpenAI로 고정된다.
```

(기존 문장 끝에 마지막 문장만 이어붙이는 것 — 괄호 안 기존 문장은 그대로 둔다.)

- [ ] **Step 7: 커밋**

```bash
git add repoview/eval.py tests/test_eval.py docs/06-test-eval-design.md
git commit -m "$(cat <<'EOF'
eval CLI에 --provider/--model 플래그 추가 (Ollama 비교 지원)

resolve_model()을 argparse와 분리된 순수 함수로 둬서 CLI 파싱 없이
테스트 가능하게 했다. judge 모델은 provider 선택과 무관하게 항상
OpenAI로 고정된다.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

**참고 (수동 확인, 자동 테스트 범위 밖):** 이 저장소 환경에는 Ollama가 설치되어 있지 않다. 실제로 로컬 모델로 eval을 돌려보려면 별도로 `ollama pull qwen2.5:7b` 후 `ollama serve`를 띄운 상태에서 `python -m repoview.eval --repo LocalQuest --phase 2 --provider ollama`를 실행해야 한다 — 이건 구현 작업(이 태스크)의 자동 테스트 범위가 아니라 사용자가 직접 확인할 몫이다.

---

### Task 3: API — `POST /api/sessions`에 모델 선택 추가

**Files:**
- Modify: `repoview/api/app.py:13-19` (import), `:63-65` (SessionRequest), `:68-86` (get_db/get_llm/get_db_path 순서 변경 포함), `:114-131` (create_session)
- Test: `tests/test_api.py` (파일 끝에 추가)
- Modify (문서 한 줄): `docs/04-api-spec.md`

**Interfaces:**
- Consumes: `repoview.agent.llm.build_llm(model: str) -> LLM` (Task 1), `repoview.config.ALLOWED_MODELS` (Task 1)
- Produces: `POST /api/sessions` 요청 바디에 선택적 `model: str | None` 필드 — 이후 프론트(Task 4)가 이 필드를 채워 보낸다.

- [ ] **Step 1: import 수정**

`repoview/api/app.py:13`을 교체:

```python
from repoview.agent.llm import build_llm
```

`repoview/api/app.py:16`을 교체:

```python
from repoview.config import ALLOWED_MODELS, CURRENT_PHASE, DB_PATH, OPENAI_MODEL
```

- [ ] **Step 2: `SessionRequest`에 `model` 필드 추가**

`repoview/api/app.py:63-65`을 교체:

```python
class SessionRequest(BaseModel):
    repo_id: int
    question: str
    model: str | None = None
```

- [ ] **Step 3: `get_db_path`를 `get_llm`보다 앞으로 옮기고, `get_llm`을 세션별 모델 조회로 재구현**

`repoview/api/app.py:68-86`(현재 `get_db` ~ `get_db_path` 블록)을 통째로 교체:

```python
def get_db():
    """요청마다 연결을 열고 반드시 닫는다. 스키마 초기화는 startup에서 한 번만 한다."""
    conn = get_connection(get_db_path())
    try:
        yield conn
    finally:
        conn.close()


def get_db_path() -> Path:
    return DB_PATH


def get_llm(session_id: int, db_path: Path = Depends(get_db_path)):
    """session_id는 이 함수를 쓰는 라우트(stream_session)의 경로 파라미터와 이름이
    같아서 FastAPI가 자동으로 채워준다. 세션 생성 시 저장해둔 session.model을 읽어
    그 모델로 LLM을 구성한다 — 세션이 어떤 모델로 만들어졌든 실행도 항상 그 모델을
    쓰게 하기 위함이다. 테스트는 이 함수 자체를 완전히 교체(override)하므로
    아래 구현이 바뀌어도 기존 테스트는 영향받지 않는다."""
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT model FROM session WHERE id = ?", (session_id,)).fetchone()
    finally:
        conn.close()
    model = row["model"] if row else OPENAI_MODEL
    return build_llm(model)


def get_embedding_client():
    return OpenAIEmbeddingClient()
```

**중요:** `stream_session` 함수 자체는 건드리지 않는다 — `llm=Depends(get_llm)`은 그대로 둔다. FastAPI가 `get_llm`의 `session_id` 파라미터를 라우트의 경로 파라미터에서 자동으로 채워준다.

- [ ] **Step 4: `create_session`에 모델 검증 추가**

`repoview/api/app.py:114-131`을 교체:

```python
@app.post("/api/sessions")
def create_session(
    payload: SessionRequest,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="질문이 비어 있습니다")

    repo = conn.execute("SELECT id FROM repo WHERE id = ?", (payload.repo_id,)).fetchone()
    if repo is None:
        raise HTTPException(status_code=404, detail=f"레포를 찾을 수 없습니다: {payload.repo_id}")

    model = payload.model or OPENAI_MODEL
    if model not in ALLOWED_MODELS:
        raise HTTPException(status_code=400, detail=f"허용되지 않은 모델입니다: {model}")

    cursor = conn.execute(
        "INSERT INTO session (repo_id, question, status, model, phase) VALUES (?, ?, 'PENDING', ?, ?)",
        (payload.repo_id, question, model, CURRENT_PHASE),
    )
    conn.commit()
    return {"session_id": int(cursor.lastrowid), "status": "PENDING"}
```

- [ ] **Step 5: 테스트 작성**

`tests/test_api.py`의 `test_create_session_with_blank_question_returns_400` 함수(현재 81~84줄) 바로 다음에 추가:

```python
def test_create_session_rejects_unknown_model(client):
    repo_id = client.get("/api/repos").json()[0]["id"]
    response = client.post(
        "/api/sessions", json={"repo_id": repo_id, "question": "질문", "model": "not-a-real-model"}
    )
    assert response.status_code == 400


def test_create_session_stores_requested_model(client, conn):
    repo_id = client.get("/api/repos").json()[0]["id"]
    response = client.post(
        "/api/sessions", json={"repo_id": repo_id, "question": "질문", "model": "qwen2.5:7b"}
    )
    session_id = response.json()["session_id"]
    row = conn.execute("SELECT model FROM session WHERE id = ?", (session_id,)).fetchone()
    assert row["model"] == "qwen2.5:7b"
```

- [ ] **Step 6: 테스트 실행 (통과 확인)**

Run: `pytest tests/test_api.py -v`
Expected: 전부 PASS — 새 테스트 2개를 포함해 기존 테스트(특히 `test_create_session_returns_pending_status`, SSE 스트림 관련 테스트들)도 그대로 통과해야 한다. 하나라도 실패하면 Step 3의 `get_llm`/`get_db_path` 순서 변경이 기존 의존성 오버라이드와 충돌하는지부터 확인한다.

- [ ] **Step 7: API 문서에 필드 추가**

`docs/04-api-spec.md`의 "## 2. 세션 (질의)" 절, `POST /sessions` 표 행을 교체:

```
| POST | `/sessions` | 세션 생성. body `{repo_id, question, model?}` → `{session_id, status: "PENDING"}`. `model` 생략 시 `OPENAI_MODEL`. 허용 목록(`docs/07-local-llm-provider.md`)에 없는 모델이면 400. |
```

- [ ] **Step 8: 커밋**

```bash
git add repoview/api/app.py tests/test_api.py docs/04-api-spec.md
git commit -m "$(cat <<'EOF'
POST /api/sessions에서 모델(OpenAI/Ollama) 선택 가능하게 함

세션 생성 시 요청 바디의 model을 ALLOWED_MODELS로 검증해 저장하고,
실행 시점(get_llm)은 그 저장된 모델로 build_llm을 호출한다. 기존에는
get_llm이 session.model 값과 무관하게 고정된 모델로만 LLM을 만들던
잠재적 불일치가 있었는데 이번에 함께 바로잡았다. get_llm은 삭제하지
않고 시그니처를 유지한 채 내부 구현만 바꿔 기존 테스트의
dependency_overrides 패턴을 그대로 보존한다.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: 프론트엔드 — 질문하기 탭에 모델 드롭다운 추가

**Files:**
- Modify: `frontend/src/api.js:9-24` (`createSession`)
- Modify: `frontend/src/App.jsx:16-24`(상태), `:45-47`(제출 로직), `:111-117`(레포 드롭다운 옆에 모델 드롭다운 추가)

**Interfaces:**
- Consumes: `POST /api/sessions`의 선택적 `model` 필드 (Task 3)
- Produces: 없음(최상위 UI 레이어)

이 프로젝트의 기존 관행대로 프론트엔드는 자동 테스트 프레임워크가 없다(`docs/06-test-eval-design.md` 참고) — 수동 확인으로 대체한다.

- [ ] **Step 1: `api.js`의 `createSession` 확장**

`frontend/src/api.js:9-24`를 교체:

```javascript
export async function createSession(repoId, question, model) {
  const body = { repo_id: repoId, question }
  if (model) body.model = model

  const response = await fetch(`${BASE_URL}/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  let responseBody
  try {
    responseBody = await response.json()
  } catch {
    if (!response.ok) throw new Error('요청이 실패했습니다')
    throw new Error('응답을 해석하지 못했습니다')
  }
  if (!response.ok) throw new Error(responseBody.error?.message ?? '요청이 실패했습니다')
  return responseBody
}
```

(`model`이 빈 문자열/undefined면 요청 바디에서 아예 생략된다 — 서버가 `OPENAI_MODEL`을 기본값으로 쓴다.)

- [ ] **Step 2: `App.jsx`에 모델 상태 추가**

`frontend/src/App.jsx:24` (`const [costUsd, setCostUsd] = useState(null)`) 바로 다음 줄에 추가:

```javascript
  const [model, setModel] = useState('')
```

- [ ] **Step 3: 제출 로직에서 모델 전달**

`frontend/src/App.jsx:46`을 교체:

```javascript
      const created = await createSession(repoId, question, model)
```

- [ ] **Step 4: 모델 드롭다운 UI 추가**

`frontend/src/App.jsx:111-117`(레포 선택 `<select>` 블록) 바로 다음에 추가:

```jsx
              <select value={model} onChange={(e) => setModel(e.target.value)}>
                <option value="">OpenAI (gpt-4o)</option>
                <option value="qwen2.5:7b">Ollama 로컬 (qwen2.5:7b)</option>
              </select>
```

- [ ] **Step 5: 수동 확인**

Run: `cd frontend && npm run dev`

브라우저에서 `http://localhost:5173` 접속 후 확인:
1. 레포 선택 드롭다운 옆에 모델 드롭다운("OpenAI (gpt-4o)" / "Ollama 로컬 (qwen2.5:7b)")이 보이는지.
2. 기본값 "OpenAI (gpt-4o)" 상태로 평소처럼 질문을 제출했을 때 기존과 동일하게 동작하는지(백엔드가 떠 있어야 함, 실제 OpenAI 호출이라 비용 발생 — 꼭 필요하면 한 번만).
3. "Ollama 로컬" 선택 후 제출 시(Ollama가 안 떠 있다면) 세션이 결국 `FAILED` 상태로 표시되고 에러가 화면에 뜨는지(연결 실패가 조용히 무시되지 않는지) — Ollama를 설치·실행하지 않은 환경에서는 이 결과가 정상이다.

- [ ] **Step 6: 커밋**

```bash
git add frontend/src/api.js frontend/src/App.jsx
git commit -m "$(cat <<'EOF'
질문하기 탭에 모델(OpenAI/Ollama) 선택 드롭다운 추가

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## 구현 후 전체 확인

모든 태스크 완료 후:

```bash
pytest -q
```

Expected: 기존 테스트 전부(변경 전 기준 186개) + 이번에 추가한 테스트(Task 1: 8개, Task 2: 3개, Task 3: 2개 = 13개) 합쳐 총 199개 PASS, 0 FAIL.
