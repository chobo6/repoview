from repoview import config


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


def test_max_iterations_default_is_six(monkeypatch):
    monkeypatch.delenv("REPOVIEW_MAX_ITERATIONS", raising=False)
    import importlib
    from repoview import config
    importlib.reload(config)
    assert config.MAX_ITERATIONS == 6


def test_max_session_tokens_default_is_fifty_thousand(monkeypatch):
    monkeypatch.delenv("REPOVIEW_MAX_SESSION_TOKENS", raising=False)
    import importlib
    from repoview import config
    importlib.reload(config)
    assert config.MAX_SESSION_TOKENS == 50_000
