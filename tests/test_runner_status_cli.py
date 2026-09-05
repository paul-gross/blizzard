"""``blizzard runner status`` — the machine-local view (issue #51).

Driven against a live daemon on a real unix socket: a real server, a real store, and
the CLI wired together, doubled only at the hub seam — the verb is a pure client of
the local API and renders fully with the hub unreachable, which a stubbed transport
would assert nothing about."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import uvicorn
from click.testing import CliRunner

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.app import build_hosted_app
from blizzard.runner.cli import runner as runner_group
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.leases import NewLease
from blizzard.runner.listeners import Listeners, Uds
from tests.runner_fakes import SqlAlchemyRunnerStore, runner_store_errors

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _store(root: Path) -> SqlAlchemyRunnerStore:
    return SqlAlchemyRunnerStore(create_engine_from_url(RunnerConfig.load(root).db_url), runner_store_errors())


def _init_runner(tmp_path: Path) -> Path:
    root = tmp_path / "runner"
    result = CliRunner().invoke(runner_group, ["init", str(root)])
    assert result.exit_code == 0, result.output
    return root


@contextmanager
def _serve_local_api(root: Path) -> Iterator[tuple[Path, str]]:
    config = RunnerConfig.load(root, port=0)
    app = build_hosted_app(config).app
    sockets = Listeners.of(config).bound()
    tcp_url = f"http://{sockets[1].getsockname()[0]}:{sockets[1].getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=lambda: server.run(sockets=sockets), daemon=True)
    thread.start()
    try:
        _await_socket(config.socket_path)
        yield config.socket_path, tcp_url
    finally:
        server.should_exit = True
        thread.join(timeout=10.0)
        Uds(config.socket_path).unlink()


def _await_socket(path: Path, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    transport = httpx.HTTPTransport(uds=str(path))
    with httpx.Client(transport=transport, base_url="http://runner") as client:
        while time.monotonic() < deadline:
            try:
                if client.get("/api/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
    raise AssertionError(f"runner local API never came up on {path}")


def _no_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if the CLI verb reaches for the hub — it must never."""

    def explode(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("`runner status` contacted the hub; it must be a pure client of the local API")

    monkeypatch.setattr(httpx, "post", explode)


@pytest.mark.component
def test_status_renders_the_full_view_with_the_hub_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _init_runner(tmp_path)
    _no_hub(monkeypatch)
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
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    store.record_ask(
        lease_id="lease_1",
        chunk_id="ch_1",
        question_id="qn_1",
        question="which branch?",
        options=["main", "dev"],
        session_id="sess-a",
        asked_at=_NOW,
    )

    # A second, already-escalated chunk with its takeover command.
    store.record_lease(
        NewLease(
            lease_id="lease_2",
            chunk_id="ch_2",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="runner-local",
            retries_max=2,
            # The session stamps (issue #144) — so `status` renders which lineage parked
            # and the resume command an operator can paste lands in its configuration.
            session_name="code",
            resolved_model="opus",
            resolved_effort="high",
            created_at=_NOW,
        )
    )
    store.record_spawn("lease_2", pid=200, process_start_time="start-200", session_id="sess-b", spawned_at=_NOW)
    store.record_binding(chunk_id="ch_2", environment_id="e2", workdir="/ws/e2", bound_at=_NOW)
    store.record_closure(
        lease_id="lease_2",
        chunk_id="ch_2",
        node_id="nd_build",
        reason="escalated",
        closed_at=_NOW + timedelta(minutes=1),
    )

    # A third chunk under an open operator takeover — status renders regardless of
    # how the takeover (issue #52) got left open.
    store.record_binding(chunk_id="ch_3", environment_id="e3", workdir="/ws/e3", bound_at=_NOW)
    store.record_takeover(
        takeover_id="tko_1",
        chunk_id="ch_3",
        lease_id=None,
        session_id="sess-c",
        workdir="/ws/e3",
        fence_epoch=None,
        opened_at=_NOW + timedelta(minutes=2),
    )

    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["status", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    out = result.output
    assert "runner runner-local" in out
    assert "running" in out  # not paused
    assert "capacity: 1/1 used, 0 free" in out  # only lease_1 is active; lease_2 closed
    assert "hub: unreachable" in out  # never synced — honest, not assumed
    assert "held environments (3):" in out
    assert "e1" in out and "e2" in out and "e3" in out
    assert "open asks (1):" in out
    assert "which branch?" in out
    assert "escalations (1):" in out
    assert "ch_2" in out
    # The literal takeover command, carrying the parked session's own configuration
    # (issue #144) rather than whatever a fresh resolution would produce now.
    assert "resume: cd /ws/e2 && claude --resume sess-b --model opus --effort high" in out
    assert "session=code (opus, high)" in out  # which lineage parked, not just its id
    assert "open takeovers (1):" in out
    assert "chunk ch_3" in out and "takeover=tko_1" in out


@pytest.mark.component
def test_status_renders_empty_sections_on_a_fresh_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _init_runner(tmp_path)
    _no_hub(monkeypatch)
    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["status", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert "leases (0):" in result.output
    assert "held environments (0):" in result.output
    assert "open asks (0):" in result.output
    assert "escalations (0):" in result.output
    assert "open takeovers (0):" in result.output


@pytest.mark.component
def test_status_omits_an_unheld_pool_slot_from_held_environments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``GET /api/environments`` carries the whole configured pool (issue #106); an
    unheld slot must not leak into the CLI's held-environments section."""
    root = _init_runner(tmp_path)
    config_path = root / "blizzard-runner.toml"
    config_path.write_text(config_path.read_text().replace('workspace_envs = ["e1"]', 'workspace_envs = ["e1", "e2"]'))
    _no_hub(monkeypatch)
    store = _store(root)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)

    with _serve_local_api(root):
        result = CliRunner().invoke(runner_group, ["status", "--dir", str(root)])

    assert result.exit_code == 0, result.output
    assert "held environments (1):" in result.output
    assert "e1" in result.output
    assert "e2" not in result.output


@pytest.mark.unit
def test_status_reports_a_daemon_that_is_not_running(tmp_path: Path) -> None:
    root = _init_runner(tmp_path)  # initialized, but nothing is serving
    result = CliRunner().invoke(runner_group, ["status", "--dir", str(root)])

    assert result.exit_code != 0
    assert "no runner daemon is serving" in result.output
