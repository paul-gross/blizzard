"""``RunnerAudit.held_workspace_bindings`` against a real runner store shared by two runners
(unit tier): a pool's held set names every runner's held binding in that workspace plus every
held binding naming no workspace, and only a binding's own runner's release frees it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blizzard.runner.store.internal.host_runner_audit import RunnerAudit
from tests.runner_fakes import SqlAlchemyRunnerStore, make_store, runner_store_errors

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)


def _two_runners(tmp_path: Path) -> tuple[SqlAlchemyRunnerStore, SqlAlchemyRunnerStore, RunnerAudit]:
    store_a = make_store(f"sqlite:///{tmp_path / 'runner.db'}", runner_id="r_a")
    store_b = SqlAlchemyRunnerStore(store_a._engine, runner_store_errors(), "r_b")
    return store_a, store_b, RunnerAudit(store_a.connections)


def _bind(store: SqlAlchemyRunnerStore, chunk_id: str, env: str, at: datetime, workspace_id: str | None) -> None:
    store.record_binding(
        chunk_id=chunk_id, environment_id=env, workdir=f"/w/{env}", bound_at=at, workspace_id=workspace_id
    )


def _held(audit: RunnerAudit, workspace_id: str) -> list[tuple[str, str, str | None]]:
    return [(b.chunk_id, b.environment_id, b.workspace_id) for b in audit.held_workspace_bindings(workspace_id)]


def test_the_pool_names_every_runners_held_binding_in_it_and_every_workspace_less_one(tmp_path: Path) -> None:
    store_a, store_b, audit = _two_runners(tmp_path)
    _bind(store_a, "ch_a", "e1", _T0, "ws1")
    _bind(store_b, "ch_b", "e2", _T0 + timedelta(seconds=1), "ws1")
    _bind(store_b, "ch_other", "e3", _T0 + timedelta(seconds=2), "ws2")
    _bind(store_a, "ch_unknown", "e4", _T0 + timedelta(seconds=3), None)

    assert _held(audit, "ws1") == [("ch_a", "e1", "ws1"), ("ch_b", "e2", "ws1"), ("ch_unknown", "e4", None)]
    assert _held(audit, "ws2") == [("ch_other", "e3", "ws2"), ("ch_unknown", "e4", None)]


def test_a_same_instant_rebind_after_the_runners_own_release_is_held(tmp_path: Path) -> None:
    store_a, _store_b, audit = _two_runners(tmp_path)
    _bind(store_a, "ch_1", "e1", _T0, "ws1")
    store_a.record_release(chunk_id="ch_1", environment_id="e1", released_at=_T0)
    _bind(store_a, "ch_1", "e1", _T0, "ws1")

    assert _held(audit, "ws1") == [("ch_1", "e1", "ws1")]


def test_another_runners_release_of_the_same_chunk_and_environment_does_not_free_a_binding(tmp_path: Path) -> None:
    store_a, store_b, audit = _two_runners(tmp_path)
    _bind(store_a, "ch_1", "e1", _T0, "ws1")

    store_b.record_release(chunk_id="ch_1", environment_id="e1", released_at=_T0 + timedelta(minutes=1))

    assert _held(audit, "ws1") == [("ch_1", "e1", "ws1")]


def test_only_the_runners_own_release_appended_after_the_bind_frees_it(tmp_path: Path) -> None:
    store_a, _store_b, audit = _two_runners(tmp_path)
    store_a.record_release(chunk_id="ch_stale", environment_id="e1", released_at=_T0)
    _bind(store_a, "ch_stale", "e1", _T0, "ws1")
    _bind(store_a, "ch_freed", "e2", _T0, "ws1")
    store_a.record_release(chunk_id="ch_freed", environment_id="e2", released_at=_T0)
    _bind(store_a, "ch_other_env", "e3", _T0, "ws1")
    store_a.record_release(chunk_id="ch_other_env", environment_id="e9", released_at=_T0)
    _bind(store_a, "ch_other_chunk", "e4", _T0, "ws1")
    store_a.record_release(chunk_id="ch_elsewhere", environment_id="e4", released_at=_T0)

    assert _held(audit, "ws1") == [
        ("ch_stale", "e1", "ws1"),
        ("ch_other_env", "e3", "ws1"),
        ("ch_other_chunk", "e4", "ws1"),
    ]
