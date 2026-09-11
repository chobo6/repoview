from pathlib import Path

import pytest

from repoview.db import get_connection, init_db


@pytest.fixture
def mini_repo() -> Path:
    return Path(__file__).parent / "fixtures" / "mini_repo"


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(tmp_path / "test.db")
    init_db(connection)
    yield connection
    connection.close()
