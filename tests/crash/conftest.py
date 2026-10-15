"""Session scaffolding for the kill-9 sweep — one fixture mint, one forge, for all points.

Skipped unless ``BLIZZARD_CRASH_SWEEP=1``; run with ``BLIZZARD_CRASH_SWEEP=1 uv run pytest
-m crash_sweep``. See ``bzh:crash-point-registry`` for the once-per-session mint rationale.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from tests.crash.support import (
    CrashEnv,
    forge_daemon,
    mock_bin_dir,
)
from tests.fleet_world import mint_world
from tests.support import free_port

pytestmark = pytest.mark.crash_sweep


@pytest.fixture(scope="session")
def crash_env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[CrashEnv]:
    """Mint the shared fixture world once and front it with one mock forge daemon."""
    if os.environ.get("BLIZZARD_CRASH_SWEEP") != "1":
        pytest.skip("kill-9 sweep drives real subprocesses; set BLIZZARD_CRASH_SWEEP=1")
    bin_dir = mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")
    scratch = tmp_path_factory.mktemp("crash-scratch")
    workspace, origins, _bare = mint_world(bin_dir, scratch, fence="crash-sweep fence marker\n")

    forge_port = free_port()
    with forge_daemon(bin_dir, origins, forge_port) as forge:
        repo = forge.get("/repos/blizzard/toy-api")
        assert repo.status_code == 200 and repo.json()["default_branch"] == "main", repo.text
        yield CrashEnv(
            bin_dir=bin_dir,
            workspace=workspace,
            origins=origins,
            forge_port=forge_port,
            forge=forge,
        )
