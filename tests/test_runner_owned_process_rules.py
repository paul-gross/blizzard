"""The owned-process teardown policy pinned by value, over plain liveness readings."""

from __future__ import annotations

import pytest

from blizzard.runner.process.owned_process import KillTarget, OwnedProcess

pytestmark = pytest.mark.unit

_GROUPED = OwnedProcess(pid=10, process_start_time="100", pgid=10)
_UNGROUPED = OwnedProcess(pid=10, process_start_time="100", pgid=None)
_UNRECORDED = OwnedProcess(pid=None, process_start_time=None, pgid=10)


def test_unrecorded_identity_is_dead_and_never_signalled() -> None:
    assert _UNRECORDED.identity() is None
    assert not _UNRECORDED.alive(leader_alive=True, group_alive=True)
    assert _UNRECORDED.kill_target(leader_alive=True, group_alive=True) is None
    assert _UNRECORDED.interrupt_target(leader_alive=True) is None


def test_alive_is_leader_or_group() -> None:
    assert _GROUPED.alive(leader_alive=False, group_alive=True)
    assert not _UNGROUPED.alive(leader_alive=False, group_alive=True)
    assert not _GROUPED.alive(leader_alive=False, group_alive=False)


def test_kill_target_prefers_group() -> None:
    assert _GROUPED.kill_target(leader_alive=True, group_alive=False) == KillTarget("group", 10)
    assert _GROUPED.kill_target(leader_alive=False, group_alive=True) == KillTarget("group", 10)
    assert _UNGROUPED.kill_target(leader_alive=True, group_alive=False) == KillTarget("leader", 10)
    assert _GROUPED.kill_target(leader_alive=False, group_alive=False) is None


def test_interrupt_target_needs_pgid_and_live_leader() -> None:
    assert _GROUPED.interrupt_target(leader_alive=True) == 10
    assert _GROUPED.interrupt_target(leader_alive=False) is None
    assert _UNGROUPED.interrupt_target(leader_alive=True) is None
