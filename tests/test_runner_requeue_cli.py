"""``blizzard runner requeue`` (issue #53).

Driven against a **live** daemon on a real unix socket, mirroring
``tests/test_runner_takeover_cli.py``'s ``_serve_local_api`` convention: a real server,
a real store, and the CLI wired together through the genuine ``build_hosted_app``
composition root (so ``RequeueService`` is wired exactly as ``host`` wires it).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.cli import runner as runner_group
from blizzard.runner.config import RunnerConfig
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore
from blizzard.runner.store.repository import NewLease
from tests.test_runner_status_cli import _init_runner, _serve_local_api

_NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)


def _store(root: Path) -> SqlAlchemyRunnerStore:
    return SqlAlchemyRunnerStore(create_engine_from_url(RunnerConfig.load(root).db_url))


def _seed_escalated_chunk(store: SqlAlchemyRunnerStore) -> None:
    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="runner-local",
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="escalated", closed_at=_NOW)


@pytest.mark.component
def test_requeue_over_an_escalated_chunk_reports_success(tmp_path: Path) -> None:
    root = _init_runner(tmp_path)
    store = _store(root)
    _seed_escalated_chunk(store)

    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["requeue", "ch_1", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert "requeued chunk ch_1" in result.output
    assert "ch_1" in store.pending_requeue_chunk_ids()


@pytest.mark.component
def test_requeue_refuses_while_a_takeover_is_open(tmp_path: Path) -> None:
    root = _init_runner(tmp_path)
    store = _store(root)
    _seed_escalated_chunk(store)
    store.record_takeover(
        takeover_id="tko_1",
        chunk_id="ch_1",
        lease_id=None,
        session_id="sess-a",
        workdir="/ws/e1",
        fence_epoch=None,
        opened_at=_NOW,
    )

    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["requeue", "ch_1", "--dir", str(root)])

    assert result.exit_code != 0
    assert "open takeover" in result.output
    assert store.pending_requeue_chunk_ids() == set()


@pytest.mark.component
def test_requeue_refuses_a_chunk_that_is_not_needs_human(tmp_path: Path) -> None:
    root = _init_runner(tmp_path)
    store = _store(root)
    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="runner-local",
            retries_max=2,
            created_at=_NOW,
        )
    )  # active, never closed — not needs_human

    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["requeue", "ch_1", "--dir", str(root)])

    assert result.exit_code != 0
    assert "not needs_human" in result.output
