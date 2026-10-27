"""The plain-SQL runner-store seam refuses names the contract does not pin and a store off its head (unit tier)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tests import runner_store_sql
from tests.runner_store_sql import insert, select, utc_text

pytestmark = pytest.mark.unit


def _store(path: Path, revision: str | None = None) -> Path:
    head = json.loads((Path(runner_store_sql.__file__).parent.parent / runner_store_sql.CONTRACT_PATH).read_text())
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
    conn.execute("INSERT INTO alembic_version VALUES (?)", (revision or head["head_revision"],))
    conn.execute(
        "CREATE TABLE outbound_buffer (runner_id TEXT, seq INTEGER, kind TEXT, payload TEXT, created_at DATETIME)"
    )
    conn.commit()
    conn.close()
    return path


def test_a_pinned_insert_reads_back(tmp_path: Path) -> None:
    store = _store(tmp_path / "runner.db")
    row = {"runner_id": "rn_1", "seq": 1, "kind": "k", "payload": "{}", "created_at": utc_text()}
    insert(store, "outbound_buffer", [row])
    assert select(store, "outbound_buffer", ["runner_id", "seq"], {"runner_id": "rn_1"}, "seq") == [
        {"runner_id": "rn_1", "seq": 1}
    ]


def test_an_unpinned_identifier_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path / "runner.db")
    with pytest.raises(ValueError, match="not pinned"):
        insert(store, "outbound_buffer", [{"nope": 1}])
    with pytest.raises(ValueError, match="not pinned"):
        select(store, "no_such_table", ["x"])


def test_a_store_off_the_contract_head_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path / "runner.db", revision="20261011_1000_runner_scope")
    with pytest.raises(ValueError, match="contract head"):
        select(store, "outbound_buffer", ["seq"])


def test_timestamps_are_naive_utc_text() -> None:
    assert len(utc_text()) == len("2026-01-01 00:00:00.000000") and "+" not in utc_text()
