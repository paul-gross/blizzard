"""``blizzard runner takeover`` (issue #52).

Driven against a live daemon on a real unix socket: a real server, a real store, and
the CLI wired together. ``subprocess.call`` is monkeypatched so the interactive exec
never shells out — the point is the CLI's own protocol (open, exec, mark ended).
"""

from __future__ import annotations

import shlex
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import click
import httpx
import pytest
from click.testing import CliRunner

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.cli import runner as runner_group
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.leases import NewLease
from blizzard.runner.domain.takeover import TakeoverCommand
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore
from tests.runner_fakes import runner_store_errors
from tests.test_runner_status_cli import _init_runner, _serve_local_api

_NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)


def _store(root: Path) -> SqlAlchemyRunnerStore:
    return SqlAlchemyRunnerStore(create_engine_from_url(RunnerConfig.load(root).db_url), runner_store_errors())


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


def _seed_escalated_lease(store: SqlAlchemyRunnerStore) -> None:
    """A closed reference lease — the needs-human shape issue #291's bug reproduced
    against: every ``blizzard runner`` worker verb 404s once its lease is closed."""
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
def test_takeover_hands_the_resumed_session_a_worker_verb_that_reaches_the_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end proof of issue #291, over the real live socket: the resumed session's
    forwarded env authorizes a worker verb against the SAME closed reference lease the
    parked attempt held — no fresh lease, just the open-takeover fact widening the resolver."""
    root = _init_runner(tmp_path)
    store = _store(root)
    _seed_escalated_lease(store)
    assert store.active_lease("lease_1") is None  # the closed-lease shape the bug reproduced against

    reached: dict[str, httpx.Response] = {}
    # `BLIZZARD_RUNNER_URL` is unreachable here (`port=0` ephemeral binding), so this reaches
    # the daemon the same way the CLI's worker-verb commands do: over the UDS socket `--dir` names.
    transport = httpx.HTTPTransport(uds=str(RunnerConfig.socket_path_for(root)))

    def fake_call(command: str, shell: bool = False, cwd: str | None = None, env: dict[str, str] | None = None) -> int:
        assert env is not None
        with httpx.Client(transport=transport, base_url="http://runner", timeout=5.0) as client:
            reached["attachments"] = client.get(
                f"/api/leases/{env['BLIZZARD_LEASE_ID']}/attachments",
                headers={"X-Blizzard-Lease-Token": env["BLIZZARD_LEASE_TOKEN"]},
            )
        return 0

    monkeypatch.setattr(subprocess, "call", fake_call)

    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["takeover", "ch_1", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert reached["attachments"].status_code == 200, reached["attachments"].text
    assert reached["attachments"].json() == []
    # list_active_leases() is unaffected — no FILL slot consumed, no fresh mint.
    assert store.list_active_leases() == []
    assert store.open_takeover_for_chunk("ch_1") is None  # marked ended once the child exited


@pytest.mark.component
def test_takeover_execs_the_command_and_marks_it_ended(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _init_runner(tmp_path)
    store = _store(root)
    _seed_parked_lease(store)

    calls: list[tuple[str, bool, str]] = []
    child_envs: list[dict[str, str]] = []

    def fake_call(command: str, shell: bool = False, cwd: str | None = None, env: dict[str, str] | None = None) -> int:
        assert cwd is not None
        assert env is not None
        calls.append((command, shell, cwd))
        child_envs.append(env)
        return 0

    monkeypatch.setattr(subprocess, "call", fake_call)
    # A sentinel terminal var: the identity env must layer OVER the operator's env
    # (issue #258), not replace it, so the exec'd child still sees this.
    monkeypatch.setenv("OPERATOR_TERMINAL_SENTINEL", "still-here")

    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["takeover", "ch_1", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    # The composed command reasserts the daemon's permission mode (issue #258) — so
    # the taken-over session does not drop to per-tool approval prompts.
    assert calls == [("cd /ws/e1 && claude --resume sess-a --permission-mode bypassPermissions", True, "/ws/e1")]
    assert "taking over chunk ch_1 in /ws/e1" in result.output
    # The lease's worker identity rides the exec env (issue #258), layered over the
    # terminal env, so the session's `blizzard runner` verbs and heartbeat hook work.
    (child_env,) = child_envs
    assert child_env["OPERATOR_TERMINAL_SENTINEL"] == "still-here"
    assert child_env["BLIZZARD_CHUNK_ID"] == "ch_1"
    assert child_env["BLIZZARD_LEASE_ID"] == "lease_1"
    assert child_env["BLIZZARD_SESSION_ID"] == "sess-a"
    assert child_env["BLIZZARD_ENV_IDS"] == "e1"
    assert child_env["BLIZZARD_RUNNER_URL"].startswith("http://")
    lease_token = child_env["BLIZZARD_LEASE_TOKEN"]
    assert lease_token  # re-minted for the takeover — plaintext travels only in the env
    # The token never reaches a printable surface: not the CLI's output, not the command.
    assert lease_token not in result.output
    assert all(lease_token not in command for command, _, _ in calls)
    assert store.open_takeover_for_chunk("ch_1") is None  # marked ended once the child exited


@pytest.mark.component
def test_takeover_propagates_a_nonzero_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _init_runner(tmp_path)
    store = _store(root)
    _seed_parked_lease(store)

    monkeypatch.setattr(subprocess, "call", lambda command, shell=False, cwd=None, env=None: 7)

    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["takeover", "ch_1", "--dir", str(root)])

    assert result.exit_code == 7
    assert store.open_takeover_for_chunk("ch_1") is None  # still marked ended despite the nonzero exit


@pytest.mark.component
def test_takeover_ends_the_takeover_even_when_the_child_raises_keyboard_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``Ctrl-C`` into the interactive session must not strand the takeover open —
    the end-PATCH runs in a ``finally`` around the child, firing even for the
    ``BaseException`` `KeyboardInterrupt`, which the CLI's `httpx.HTTPError` handler misses."""
    root = _init_runner(tmp_path)
    store = _store(root)
    _seed_parked_lease(store)

    def fake_call(command: str, shell: bool = False, cwd: str | None = None, env: dict[str, str] | None = None) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(subprocess, "call", fake_call)

    with _serve_local_api(root):
        # click's own `main()` converts an uncaught KeyboardInterrupt into `Abort` —
        # assert the exit code, not the exception; the end-PATCH must fire before that.
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
    monkeypatch.setattr(subprocess, "call", lambda command, shell=False, cwd=None: calls.append(command) or 0)

    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["takeover", "ch_1", "--dir", str(root)])

    assert result.exit_code != 0
    assert "live worker attempt" in result.output
    assert calls == []  # never exec'd — the live worker was never superseded
    assert store.open_takeover_for_chunk("ch_1") is None


@pytest.mark.unit
def test_takeover_cli_still_declares_the_dir_flag_and_a_chunk_id_argument() -> None:
    """Pins the CLI-flag shape ``TakeoverCommand`` hard-codes against the
    REAL Click command object, not the composed string — a rename of ``--dir`` or the
    chunk id here would silently break the board command while other tests stay green."""
    takeover_cmd = runner_group.commands["takeover"]

    directory_param = next(p for p in takeover_cmd.params if p.name == "directory")
    assert directory_param.opts == ["--dir"]

    arguments = [p for p in takeover_cmd.params if isinstance(p, click.Argument)]
    assert [a.name for a in arguments] == ["chunk_id"]


@pytest.mark.unit
def test_composed_wrapped_command_parses_through_the_real_takeover_grammar() -> None:
    """``TakeoverCommand`` composes the string the board renders; this parses
    that exact string through the REAL Click command's own argument parsing, so a
    coordinated edit the grammar rejects fails here even if string-equality tests pass."""
    composed = TakeoverCommand("ch_1", "/var/lib/blizzard/runner dir").wrapped
    argv = shlex.split(composed)
    assert argv[:3] == ["blizzard", "runner", "takeover"]

    takeover_cmd = runner_group.commands["takeover"]
    with takeover_cmd.make_context("takeover", argv[3:]) as click_ctx:
        assert click_ctx.params["chunk_id"] == "ch_1"
        # The whitespace-bearing dir round-trips through quote -> shell-split -> parse.
        assert click_ctx.params["directory"] == "/var/lib/blizzard/runner dir"
