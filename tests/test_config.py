from repoview import config


def test_chunk_overlap_is_smaller_than_chunk_lines():
    assert config.CHUNK_OVERLAP < config.CHUNK_LINES


def test_chroma_path_is_under_project_root_by_default():
    assert config.PROJECT_ROOT in config.CHROMA_PATH.parents or config.CHROMA_PATH.parent == config.PROJECT_ROOT


def test_embedding_model_is_set():
    assert config.EMBEDDING_MODEL
