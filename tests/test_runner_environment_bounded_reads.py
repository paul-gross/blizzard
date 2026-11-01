"""The runner store's chunk-set environment-binding reads are bounded by the chunks asked about: rows read stay flat
as rebinds deepen, statements follow the batch count, and each plural answers what its singular does (component tier)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blizzard.foundation.store import batching
from tests.runner_fakes import SqlAlchemyRunnerStore, make_store
from tests.support import count_queries, count_rows_read

pytestmark = pytest.mark.component

_T0 = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> SqlAlchemyRunnerStore:
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _bind(
    store: SqlAlchemyRunnerStore, chunk_id: str, n: int, *, project_id: str | None = "p", at: int | None = None
) -> None:
    store.record_binding(
        chunk_id=chunk_id,
        environment_id=f"env-{chunk_id}-{n}",
        workdir=f"/ws/{chunk_id}/{n}",
        bound_at=_T0 + timedelta(seconds=n if at is None else at),
        workspace_id="ws",
        project_id=project_id,
    )


def _release(store: SqlAlchemyRunnerStore, chunk_id: str, n: int) -> None:
    store.record_release(chunk_id=chunk_id, environment_id=f"env-{chunk_id}-{n}", released_at=_T0 + timedelta(hours=1))


def _seed(store: SqlAlchemyRunnerStore, chunks: list[str], depth: int) -> None:
    for chunk_id in chunks:
        for n in range(depth):
            _bind(store, chunk_id, n)
            if n < depth - 1:
                _release(store, chunk_id, n)


@pytest.mark.parametrize("read", ["chunk_project_ids", "newest_bindings_for"])
def test_rows_read_equal_the_chunk_count_at_two_rebind_depths(tmp_path: Path, read: str) -> None:
    chunks = ["ch_1", "ch_2", "ch_3"]
    measured: list[int] = []
    for depth in (1, 4):
        directory = tmp_path / str(depth)
        directory.mkdir()
        store = _store(directory)
        _seed(store, chunks, depth)
        measured.append(count_rows_read(store.engine, lambda store=store: getattr(store, read)(chunks)))
    assert measured == [len(chunks), len(chunks)]


def test_rows_read_by_chunk_project_ids_are_exactly_the_chunk_count(tmp_path: Path) -> None:
    store = _store(tmp_path)
    chunks = ["ch_1", "ch_2", "ch_3"]
    _seed(store, chunks, 4)
    assert count_rows_read(store.engine, lambda: store.chunk_project_ids(chunks)) == len(chunks)


def test_a_newer_null_project_binding_never_hides_an_older_project(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _bind(store, "ch_1", 0, project_id="proj-a")
    _bind(store, "ch_1", 1, project_id=None)
    _bind(store, "ch_2", 0, project_id=None)
    assert store.chunk_project_ids(["ch_1", "ch_2", "ch_unknown"]) == {"ch_1": "proj-a"}


def test_another_runners_binding_on_the_same_chunk_is_invisible(tmp_path: Path) -> None:
    db = f"sqlite:///{tmp_path / 'runner.db'}"
    mine = make_store(db)
    theirs = make_store(db, runner_id="rn_other")
    _bind(mine, "ch_1", 0, project_id="mine")
    _bind(theirs, "ch_1", 1, project_id="theirs")
    assert mine.chunk_project_ids(["ch_1"]) == {"ch_1": "mine"}
    assert mine.newest_bindings_for(["ch_1"])["ch_1"].environment_id == "env-ch_1-0"


def test_empty_and_duplicate_ids(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _bind(store, "ch_1", 0)
    assert count_queries(store.engine, lambda: store.held_bindings_for([])) == 0
    assert count_queries(store.engine, lambda: store.newest_bindings_for([])) == 0
    assert store.chunk_project_ids([]) == {}
    assert store.chunk_project_ids(["ch_1", "ch_1"]) == {"ch_1": "p"}
    assert len(store.held_bindings_for(["ch_1", "ch_1"])["ch_1"]) == 1


def test_plurals_equal_the_singulars_per_chunk(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # ch_1: released then rebound at the same instant; ch_2: released only; ch_3: two held.
    _bind(store, "ch_1", 0, at=0)
    _release(store, "ch_1", 0)
    _bind(store, "ch_1", 1, at=0)
    _bind(store, "ch_2", 0)
    _release(store, "ch_2", 0)
    _bind(store, "ch_3", 0, at=5)
    _bind(store, "ch_3", 1, at=2)
    chunks = ["ch_1", "ch_2", "ch_3", "ch_none"]
    held = store.held_bindings_for(chunks)
    newest = store.newest_bindings_for(chunks)
    for chunk_id in chunks:
        assert held.get(chunk_id, []) == store.bindings_for_chunk(chunk_id)
        assert newest.get(chunk_id) == store.newest_binding_for_chunk(chunk_id)
    assert "ch_2" not in held and newest["ch_2"].environment_id == "env-ch_2-0"
    assert [b.environment_id for b in held["ch_3"]] == ["env-ch_3-1", "env-ch_3-0"]


@pytest.mark.parametrize("read", ["chunk_project_ids", "held_bindings_for", "newest_bindings_for"])
def test_statements_equal_the_batch_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, read: str) -> None:
    store = _store(tmp_path)
    chunks = [f"ch_{i}" for i in range(5)]
    _seed(store, chunks, 1)
    monkeypatch.setattr(batching, "BATCH_SIZE", 2)
    assert count_queries(store.engine, lambda store=store: getattr(store, read)(chunks)) == 3
