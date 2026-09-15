import importlib

import pytest

from repoview import config


@pytest.fixture
def reloaded_config(monkeypatch):
    """env var를 지운 채 config를 리로드해 순수 기본값을 확인하고, 테스트가 끝나면
    monkeypatch가 되돌린 실제 환경 기준으로 config를 다시 리로드해 다른 테스트로
    상태가 새지 않게 한다."""

    def _reload(*env_names):
        for name in env_names:
            monkeypatch.delenv(name, raising=False)
        importlib.reload(config)
        return config

    yield _reload
    importlib.reload(config)


def test_chunk_overlap_is_smaller_than_chunk_lines():
    assert config.CHUNK_OVERLAP < config.CHUNK_LINES


def test_chroma_path_is_under_project_root_by_default():
    assert config.PROJECT_ROOT in config.CHROMA_PATH.parents or config.CHROMA_PATH.parent == config.PROJECT_ROOT


def test_embedding_model_is_set():
    assert config.EMBEDDING_MODEL


def test_judge_model_is_set():
    assert config.JUDGE_MODEL


def test_model_pricing_contains_known_models_with_positive_rates():
    assert "gpt-4o-mini" in config.MODEL_PRICING
    for input_price, output_price in config.MODEL_PRICING.values():
        assert input_price > 0
        assert output_price > 0


def test_max_iterations_default_is_six(reloaded_config):
    assert reloaded_config("REPOVIEW_MAX_ITERATIONS").MAX_ITERATIONS == 6


def test_max_session_tokens_default_is_fifty_thousand(reloaded_config):
    assert reloaded_config("REPOVIEW_MAX_SESSION_TOKENS").MAX_SESSION_TOKENS == 50_000


def test_allowed_models_always_includes_qwen_ollama():
    assert config.ALLOWED_MODELS.get("qwen2.5:7b") == "ollama"


def test_allowed_models_excludes_empty_openai_model(reloaded_config):
    reloaded = reloaded_config("OPENAI_MODEL")
    assert "" not in reloaded.ALLOWED_MODELS
