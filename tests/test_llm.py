import pytest

from repoview import config
from repoview.agent.llm import FakeLLM, OllamaLLM, OpenAILLM, build_llm, make_text_response, make_tool_call_response


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


def test_build_llm_sees_reloaded_allowed_models(monkeypatch):
    """ALLOWED_MODELS를 모듈 최상단에서 값으로 임포트하면 repoview.config가
    런타임에 리로드돼도(예: OPENAI_MODEL 변경) build_llm은 예전 dict를 계속
    들고 있어 새 모델을 거부한다 — 회귀 방지용."""
    import importlib

    from repoview import config

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini-reloaded-test")
    importlib.reload(config)
    try:
        llm = build_llm("gpt-4o-mini-reloaded-test")
        assert llm.model == "gpt-4o-mini-reloaded-test"
    finally:
        importlib.reload(config)
