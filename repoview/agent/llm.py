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
    def __init__(self, model: str) -> None:
        if not model:
            raise ValueError("OPENAI_MODEL이 설정되지 않았습니다. .env를 확인하세요.")
        from openai import OpenAI

        self.model = model
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
