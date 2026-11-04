"""``blizzard runner store reset`` — a SQLite URL names its database file, and a store with no file cannot be set
aside and says why."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.runner.runtime import (
    StoreResetRefused,
    sqlite_store_file,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [("sqlite:////abs/data/runner.db", Path("/abs/data/runner.db")), ("sqlite+pysqlite:///rel.db?x=1", Path("rel.db"))],
)
def test_a_sqlite_url_names_its_database_file(url: str, expected: Path) -> None:
    assert sqlite_store_file(url) == expected


@pytest.mark.parametrize("url", ["postgresql://u@db/runner", "sqlite://", "sqlite:///:memory:"])
def test_a_store_with_no_file_cannot_be_set_aside_and_says_why(url: str) -> None:
    with pytest.raises(StoreResetRefused, match="not a SQLite file"):
        sqlite_store_file(url)
