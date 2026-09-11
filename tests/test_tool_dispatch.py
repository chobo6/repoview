import chromadb

from repoview.embedding_client import FakeEmbeddingClient
from repoview.tools import TOOL_SCHEMAS, dispatch


def test_schemas_have_openai_function_shape():
    for schema in TOOL_SCHEMAS:
        assert schema["type"] == "function"
        function = schema["function"]
        assert isinstance(function["name"], str)
        assert isinstance(function["description"], str)
        assert function["parameters"]["type"] == "object"


def test_schema_names_match_dispatchable_tools():
    names = {schema["function"]["name"] for schema in TOOL_SCHEMAS}
    assert names == {"list_directory", "read_file", "search_code", "search_semantic"}


def test_dispatch_read_file(mini_repo):
    result = dispatch(mini_repo, "read_file", {"path": "pom.xml"})
    assert "spring-webmvc" in result


def test_dispatch_search_code(mini_repo):
    result = dispatch(mini_repo, "search_code", {"pattern": "findAllWithOrders"})
    assert "UserService.java" in result


def test_dispatch_unknown_tool_returns_error_string(mini_repo):
    assert dispatch(mini_repo, "없는도구", {}).startswith("ERROR:")


def test_dispatch_missing_required_argument_returns_error_string(mini_repo):
    assert dispatch(mini_repo, "read_file", {}).startswith("ERROR:")


def test_dispatch_never_raises_on_bad_arguments(mini_repo):
    result = dispatch(mini_repo, "search_code", {"pattern": "x", "max_results": "셋"})
    assert result.startswith("ERROR:")


def test_schema_names_include_search_semantic():
    names = {schema["function"]["name"] for schema in TOOL_SCHEMAS}
    assert "search_semantic" in names


def test_dispatch_search_semantic_without_collection_returns_error(mini_repo):
    result = dispatch(mini_repo, "search_semantic", {"query": "아무 질문"})
    assert result.startswith("ERROR:")


def test_dispatch_search_semantic_with_collection_returns_results(mini_repo, tmp_path):
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    collection = client.create_collection(name="test_repo")
    embedder = FakeEmbeddingClient()
    collection.add(
        ids=["a.py:1-5"],
        documents=["def target_function(): ..."],
        metadatas=[{"file_path": "a.py", "start_line": 1, "end_line": 5}],
        embeddings=embedder.embed(["def target_function(): ..."]),
    )

    result = dispatch(
        mini_repo,
        "search_semantic",
        {"query": "def target_function(): ..."},
        collection=collection,
        embedding_client=embedder,
    )

    assert "a.py:1-5" in result


def test_dispatch_read_file_ignores_new_kwargs(mini_repo):
    # collection/embedding_client가 주어져도 기존 도구 동작에는 영향이 없어야 한다.
    result = dispatch(mini_repo, "read_file", {"path": "pom.xml"}, collection=None, embedding_client=None)
    assert "spring-webmvc" in result
