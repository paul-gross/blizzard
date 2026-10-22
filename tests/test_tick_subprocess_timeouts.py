"""No child a tick reaches may run unbounded — pinned on the launch seam's contract, not on a module list.

``PeriodicDriver.stop``'s unbounded join rests on every seam a tick touches being bounded. ``bzh:runner-child-launch``'s
structural gate makes the seam the runner's only route to ``subprocess``, and pyright holds each call site to the
seam's signature, so the seam's required timeout is the one fact left to pin."""

from __future__ import annotations

import inspect

import pytest

from blizzard.runner.process.child_processes import IChildProcesses

pytestmark = pytest.mark.unit


def test_the_launch_seams_run_to_completion_entry_requires_a_timeout() -> None:
    timeout = inspect.signature(IChildProcesses.run).parameters["timeout"]
    assert timeout.kind is inspect.Parameter.KEYWORD_ONLY
    assert timeout.default is inspect.Parameter.empty
    assert timeout.annotation == "float"
