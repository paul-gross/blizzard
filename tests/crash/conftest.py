"""Session scaffolding for the kill-9 sweep — one fixture mint, one forge, for all points.

The sweep is gated exactly like the e2e tier: it needs the sibling ``blizzard-mock``
worktree and a local winter source, and it drives real subprocesses and real signals,
so it is **skipped unless ``BLIZZARD_CRASH_SWEEP=1``** and the layout is discoverable —
the default ``pytest`` gate (unit + component) never runs it. Reproduce it with::

    BLIZZARD_CRASH_SWEEP=1 uv run pytest -m crash_sweep

The fixture world (bare origins + a real winter workspace) is minted **once per
session** (``bzh:crash-point-registry`` — keep per-point runtime tight); each point runs
against fresh hub + runner stores over that one workspace, landing a unique file so the
shared origins never collide.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator

import pytest

from tests.crash.support import (
    FIXTURE_ENV,
    CrashEnv,
    forge_daemon,
    free_port,
    mock_bin_dir,
    winter_source,
)

pytestmark = pytest.mark.crash_sweep


@pytest.fixture(scope="session")
def crash_env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[CrashEnv]:
    """Mint the shared fixture world once and front it with one mock forge daemon."""
    if os.environ.get("BLIZZARD_CRASH_SWEEP") != "1":
        pytest.skip("kill-9 sweep drives real subprocesses; set BLIZZARD_CRASH_SWEEP=1")
    bin_dir = mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")
    source = winter_source()
    if source is None:
        pytest.skip("no local winter source (set BLIZZARD_MOCK_WINTER_SOURCE)")

    scratch = tmp_path_factory.mktemp("crash-scratch")
    subprocess.run(
        [
            str(bin_dir / "blizzard-mock-fixture"),
            "reset",
            "--env",
            FIXTURE_ENV,
            "--scratch-root",
            str(scratch),
            "--winter-source",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    fixture_root = scratch / FIXTURE_ENV
    workspace = fixture_root / "workspace"
    origins = fixture_root / "origins"
    assert workspace.is_dir() and (origins / "toy-api.git").is_dir(), "fixture mint did not lay out the tree"

    # Fence the tree so the mock harness will run (arbitrary code execution is the
    # feature, gated on a marker file + BLIZZARD_MOCK_HARNESS_FENCE).
    (workspace / ".blizzard-mock-harness-fence").write_text("crash-sweep fence marker\n")

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
