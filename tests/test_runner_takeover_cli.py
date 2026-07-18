"""``blizzard runner takeover`` (issue #52).

Driven against a **live** daemon on a real unix socket, mirroring
``tests/test_runner_status_cli.py``'s ``_serve_local_api`` convention: a real server,
a real store, and the CLI wired together. ``subprocess.call`` is monkeypatched so the
interactive exec never actually shells out to a coding harness — the point here is the
CLI's own protocol (open, exec, then mark ended), not a real terminal session.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

import blizzard.runner.cli as runner_cli
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.cli import runner as runner_group
from blizzard.runner.config import RunnerConfig
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore
from blizzard.runner.store.repository import NewLease
from tests.test_runner_status_cli import _init_runner, _serve_local_api

_NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)


def _store(root: Path) -> SqlAlchemyRunnerStore:
    return SqlAlchemyRunnerStore(create_engine_from_url(RunnerConfig.load(root).db_url))


def _seed_parked_lease(store: SqlAlchemyRunnerStore) -> None:
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
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)


@pytest.mark.component
def test_takeover_execs_the_command_and_marks_it_ended(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _init_runner(tmp_path)
    store = _store(root)
    _seed_parked_lease(store)

    calls: list[tuple[str, bool, str]] = []

    def fake_call(command: str, shell: bool = False, cwd: str | None = None) -> int:
        assert cwd is not None
        calls.append((command, shell, cwd))
        return 0

    monkeypatch.setattr(runner_cli.subprocess, "call", fake_call)

    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["takeover", "ch_1", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert calls == [("cd /ws/e1 && claude --resume sess-a", True, "/ws/e1")]
    assert "taking over chunk ch_1 in /ws/e1" in result.output
    assert store.open_takeover_for_chunk("ch_1") is None  # marked ended once the child exited


@pytest.mark.component
def test_takeover_propagates_a_nonzero_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _init_runner(tmp_path)
    store = _store(root)
    _seed_parked_lease(store)

    monkeypatch.setattr(runner_cli.subprocess, "call", lambda command, shell=False, cwd=None: 7)

    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["takeover", "ch_1", "--dir", str(root)])

    assert result.exit_code == 7
    assert store.open_takeover_for_chunk("ch_1") is None  # still marked ended despite the nonzero exit


@pytest.mark.component
def test_takeover_ends_the_takeover_even_when_the_child_raises_keyboard_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``Ctrl-C`` into the interactive session must not strand the takeover open —
    the end-PATCH runs in a ``finally`` around the child, so it fires even though
    ``KeyboardInterrupt`` is a ``BaseException`` the CLI's own ``httpx.HTTPError``
    handler does not catch."""
    root = _init_runner(tmp_path)
    store = _store(root)
    _seed_parked_lease(store)

    def fake_call(command: str, shell: bool = False, cwd: str | None = None) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(runner_cli.subprocess, "call", fake_call)

    with _serve_local_api(root):
        # click's own `main()` converts an uncaught KeyboardInterrupt into `Abort` —
        # a clean exit(1) rather than a raw traceback — so the CLI-level assertion is
        # the exit code, not the exception class; the point under test is that the
        # end-PATCH still fired before that unwind reached click.
        result = CliRunner().invoke(runner_group, ["takeover", "ch_1", "--dir", str(root)])

    assert result.exit_code == 1
    assert store.open_takeover_for_chunk("ch_1") is None  # ended despite the interrupt — no stranded hold


@pytest.mark.component
def test_takeover_refuses_a_live_worker_without_force(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    )
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)

    calls: list[str] = []
    monkeypatch.setattr(
        runner_cli.subprocess, "call", lambda command, shell=False, cwd=None: calls.append(command) or 0
    )

    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["takeover", "ch_1", "--dir", str(root)])

    assert result.exit_code != 0
    assert "live worker attempt" in result.output
    assert calls == []  # never exec'd — the live worker was never superseded
    assert store.open_takeover_for_chunk("ch_1") is None
