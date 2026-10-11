"""``make_context``'s fallback elicitation directory lives only as long as the context that needs it."""

from __future__ import annotations

import gc
import os
from pathlib import Path

from blizzard.runner.harness.adapter import WorkerHandle
from tests.runner_fakes import FakeHarness, FakeHub, FakeProbe, FakeProvider, make_context, make_store


def _context(tmp_path: Path):  # type: ignore[no-untyped-def]
    harness = FakeHarness(handle=WorkerHandle(session_id="s", pid=1, process_start_time="t", pgid=1), verdict=None)
    return make_context(
        make_store(f"sqlite:///{tmp_path / 'runner.db'}"),
        hub=FakeHub(),
        provider=FakeProvider({}),
        harness=harness,
        probe=FakeProbe(),
    )


def test_discarding_a_context_removes_its_fallback_elicitation_directory(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    root = ctx.elicitation_files.root
    assert root and os.path.isdir(root)
    del ctx
    gc.collect()
    assert not os.path.exists(root)
