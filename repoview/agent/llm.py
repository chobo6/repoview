import json
import uuid
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    raw_message: dict = field(default_factory=dict)


class LLM(Protocol):
    def call(self, messages: list[dict], tools: list[dict]) -> LLMResponse: ...


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

    def call(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        kwargs: dict = {"model": self.model, "messages": messages}
        if tools:
            kwargs["tools"] = tools

        completion = self._client.chat.completions.create(**kwargs)
        message = completion.choices[0].message

        tool_calls = [
            ToolCall(
                id=call.id,
                name=call.function.name,
                arguments=_parse_arguments(call.function.arguments),
            )
            for call in (message.tool_calls or [])
        ]

        usage = completion.usage
        return LLMResponse(
            text=message.content,
            tool_calls=tool_calls,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            raw_message=message.model_dump(exclude_none=True),
        )


def OllamaLLM(model: str) -> OpenAILLM:
    """Ollama의 OpenAI 호환 엔드포인트(/v1/chat/completions)로 향하는 OpenAILLM을 만든다.
    별도 클래스가 아닌 이유: 생성자 인자(base_url)만 다를 뿐 call()의 파싱·usage 집계
    로직이 100% 동일해서 서브클래싱/재구현할 이유가 없다."""
    from repoview.config import OLLAMA_BASE_URL

    return OpenAILLM(model, base_url=OLLAMA_BASE_URL)


def build_llm(model: str) -> "LLM":
    """ALLOWED_MODELS 화이트리스트에 있는 모델명만 받는다 — 호출자(API/eval CLI)가
    임의 모델명이나 base_url을 직접 지정하지 못하게 막는 지점이다.

    ALLOWED_MODELS는 함수 본문에서 매번 다시 임포트한다(모듈 최상단에서 값으로
    임포트하지 않는다) — repoview.config가 런타임에 리로드되는 경로(예: 테스트의
    reloaded_config 픽스처)가 있는데, 최상단 임포트는 리로드 이전의 오래된 dict
    객체를 계속 들고 있게 되어 리로드 후 값이 어긋날 수 있다. eval.py의
    resolve_model이 이미 같은 이유로 이 패턴을 쓰고 있다."""
    from repoview.config import ALLOWED_MODELS

    provider = ALLOWED_MODELS.get(model)
    if provider is None:
        raise ValueError(f"허용되지 않은 모델입니다: {model}")
    if provider == "ollama":
        return OllamaLLM(model)
    return OpenAILLM(model)


def _parse_arguments(raw: str | None) -> dict:
    """도구 인자는 항상 JSON으로 파싱한다. 문자열 매칭으로 처리하면 안 된다."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class FakeLLM:
    """테스트용. 준비된 응답을 순서대로 반환하고 받은 메시지를 기록한다."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.received_messages: list[list[dict]] = []

    def call(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        self.received_messages.append(list(messages))
        if not self._responses:
            raise AssertionError("FakeLLM: 준비된 응답을 모두 소진했습니다")
        return self._responses.pop(0)


def make_tool_call_response(
    name: str, arguments: dict, call_id: str | None = None
) -> LLMResponse:
    call_id = call_id or f"call_{uuid.uuid4().hex[:8]}"
    return LLMResponse(
        text=None,
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
        input_tokens=100,
        output_tokens=20,
        raw_message={
            "role": "assistant",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ],
        },
    )


def make_text_response(text: str) -> LLMResponse:
    return LLMResponse(
        text=text,
        tool_calls=[],
        input_tokens=100,
        output_tokens=50,
        raw_message={"role": "assistant", "content": text},
    )
