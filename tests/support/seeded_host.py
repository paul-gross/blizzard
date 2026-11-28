"""A real host over a seeded store, its file written by :func:`write_minimal_host`, and its hosted app — shared by
the host record tests."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import SystemClock
from blizzard.runner.app import HostedApp, build_hosted_app
from blizzard.runner.cli.runtime import seed_host, seed_runners
from blizzard.runner.composition import HostProcess
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.store.internal.identity_store import RunnerIdentityStore
from tests.support.composed_host import build_host_process
from tests.support.config_inputs import load_host_config
from tests.support.host_files import TEST_RUNNER_ID, write_minimal_host
from tests.support.runner_fakes import make_store


@contextmanager
def seeded_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    top: str = "",
    workspace: str = "",
    runner: str = "",
    projects: str = "[]",
) -> Generator[tuple[HostProcess, HostedApp]]:
    """The host at ``tmp_path``, its runner registered and its file's records seeded, and its hosted app; the
    keyword arguments are :func:`write_minimal_host`'s."""
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"\n{top}', workspace=workspace, runner=runner, projects=projects)
    store = make_store(url, TEST_RUNNER_ID)
    config = load_host_config(tmp_path)
    RunnerIdentityStore(store.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, config.runners[0].name, datetime.now(UTC))
    )
    config = seed_host(config, {}, clock=SystemClock())
    ready, halted = seed_runners(config, clock=SystemClock())
    assert not halted
    process = build_host_process(config, ready)
    app = build_hosted_app(process.runners[0].config, process)
    try:
        yield process, app
    finally:
        app.close()
        process.close()
