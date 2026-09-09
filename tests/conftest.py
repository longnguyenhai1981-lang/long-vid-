from __future__ import annotations

import pytest

from app.storage.database import init_database


@pytest.fixture()
def engine(tmp_path):
    """An isolated, temporary SQLite database for one test. Never the default DB."""
    return init_database(tmp_path / "motily_test.db")
