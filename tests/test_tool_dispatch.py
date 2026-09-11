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
    assert names == {"list_directory", "read_file", "search_code"}


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
