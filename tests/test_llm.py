import pytest

from repoview.agent.llm import FakeLLM, make_text_response, make_tool_call_response


def test_fake_llm_returns_prepared_responses_in_order():
    llm = FakeLLM([
        make_tool_call_response("search_code", {"pattern": "SELECT"}),
        make_text_response("최종 리뷰"),
    ])

    first = llm.call([{"role": "user", "content": "질문"}], [])
    second = llm.call([{"role": "user", "content": "질문"}], [])

    assert first.tool_calls[0].name == "search_code"
    assert first.tool_calls[0].arguments == {"pattern": "SELECT"}
    assert second.text == "최종 리뷰"
    assert second.tool_calls == []


def test_fake_llm_records_received_messages():
    llm = FakeLLM([make_text_response("답변")])
    llm.call([{"role": "user", "content": "질문"}], [])
    assert llm.received_messages[0][0]["content"] == "질문"


def test_fake_llm_raises_when_exhausted():
    llm = FakeLLM([make_text_response("한 번뿐")])
    llm.call([], [])
    with pytest.raises(AssertionError):
        llm.call([], [])


def test_tool_call_response_has_assistant_raw_message():
    response = make_tool_call_response("read_file", {"path": "a.java"})
    assert response.raw_message["role"] == "assistant"
    assert response.raw_message["tool_calls"][0]["function"]["name"] == "read_file"
