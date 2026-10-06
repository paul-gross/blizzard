"""``SpawnCwd`` — the spawn-cwd rule's one owner.

Pure, no I/O — both branches, including the case the transcript service actually
hits: a closed lease's binding is released, so the fallback is legitimately
``None`` and ``path`` must answer ``None`` rather than raise or coerce.
"""

from __future__ import annotations

import pytest

from blizzard.runner.harness.spawn_cwd import SpawnCwd


@pytest.mark.unit
def test_workspace_root_wins_when_set() -> None:
    assert SpawnCwd("/ws/root", "/ws/root/e1").path == "/ws/root"


@pytest.mark.unit
def test_falls_back_to_the_workdir_when_workspace_root_is_empty() -> None:
    assert SpawnCwd("", "/ws/e1").path == "/ws/e1"


@pytest.mark.unit
def test_empty_workspace_root_and_no_fallback_is_none() -> None:
    assert SpawnCwd("", None).path is None
